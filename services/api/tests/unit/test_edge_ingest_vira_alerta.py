"""
O evento do edge tem de virar ALERTA — cruzando a fronteira HTTP.

O buraco medido: `POST /api/v1/edge/events/ingest` gravava só em
`public.edge_events`, e NENHUM código do produto lê essa tabela — a tela de
Eventos do operador lê `public.alerts`. Tudo que o box mandava morria numa
tabela sem leitor. De quebra, `alerts.site_id` estava NULL em 5.174/5.174
linhas: o ingest é o único caminho que SABE o site (vem do device token).

Por que pela ROTA e não chamando a função: a casa tem lição registrada de que
teste que não cruza a fronteira HTTP não prova portão (o cursor RFC 822 passou
por review e CI justamente assim). O arranjo — device token RS256 real +
`EdgeHeartbeatRepository` mockado — é o de
`tests/security/test_edge_events_ingest_tenant_isolation.py`.

Os portões exercitados aqui são os DE VERDADE: `_has_violation` roda contra
`yolo_classes` (via `AlertRepository.violation_class_names`, ADR-0065), o
escopo sai do deployment ativo e o limiar é `DETECTION_CONFIDENCE_THRESHOLD`.
Nada de mockar a decisão que o teste diz estar provando.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

import app.api.v1.edge_events.routes as evt_routes
from app.infrastructure.queue.tasks import inference as inference_mod
from tests.security._helpers_tenant import make_device_token

_HB_REPO = (
    "app.infrastructure.database.repositories.edge_heartbeat_repository"
    ".EdgeHeartbeatRepository"
)
_CAM_REPO = "app.infrastructure.database.repositories.camera_repository.CameraRepository"
_ALERT_REPO = "app.infrastructure.database.repositories.alert_repository.AlertRepository"
_DEPLOY_REPO = (
    "app.infrastructure.database.repositories.model_deployment_repository"
    ".ModelDeploymentRepository"
)
_DBPOOL = "app.infrastructure.database.connection.DatabasePool"

#: Classe de violação real da taxonomia do RVB (as de ausência começam com
#: "Sem "), não a `no_helmet` da era COCO que não existe em cliente nenhum.
_VIOLACAO = "Sem Luvas"
_PRESENCA = "Com Luvas"
_CAPTURA = "2026-09-01T10:00:00.123456Z"


@pytest.fixture(autouse=True)
def _sem_cache_de_polaridade():
    """A polaridade tem cache de 5 min por tenant — sem limpar, o veredito de
    um teste decidiria o do seguinte."""
    inference_mod._polaridade_cache.clear()
    yield
    inference_mod._polaridade_cache.clear()


class _Caso:
    """Um POST /ingest com um evento, com todos os repositórios mockados."""

    def __init__(
        self,
        *,
        detections: list[dict] | None = None,
        violacao: list[str] | None = None,
        presenca: list[str] | None = None,
        escopo_da_camera: list[str] | None = None,
        ja_existe_alerta: bool = False,
        tenant_da_camera_confere: bool = True,
        evidence_r2_key: str | None = "evidence/rvb/cam-1/2026-09-01.jpg",
        captura: str | None = _CAPTURA,
    ) -> None:
        self.tenant_id, self.site_id = uuid4(), uuid4()
        self.camera_id = str(uuid4())
        self.device_id = "dev-rvb-01"
        self.token, self.public_pem = make_device_token(
            self.tenant_id, self.site_id, self.device_id, scopes=["events:write"]
        )

        camera = {
            "id": self.camera_id,
            "tenant_id": str(self.tenant_id),
            "module_code": "epi",
            "active_module": "epi",
        }
        self.camera_repo = MagicMock()
        self.camera_repo.get_by_id_and_tenant.return_value = (
            camera if tenant_da_camera_confere else None
        )
        self.camera_repo.get_by_id.return_value = camera

        self.alert_repo = MagicMock()
        self.alert_repo.exists_at_capture.return_value = ja_existe_alerta
        self.alert_repo.violation_class_names.return_value = (
            violacao if violacao is not None else [_VIOLACAO.lower()]
        )
        self.alert_repo.presence_class_names.return_value = (
            presenca if presenca is not None else [_PRESENCA.lower()]
        )
        self.alert_repo.create.return_value = {"id": str(uuid4())}

        self.deploy_repo = MagicMock()
        self.deploy_repo.get_active_for_camera.return_value = (
            {"config": {"classes": escopo_da_camera}} if escopo_da_camera is not None
            else None
        )

        self.events_repo = MagicMock()
        self.events_repo.ingest.return_value = {"id": str(uuid4()), "received_at": "now"}

        payload: dict = {
            "camera_id": self.camera_id,
            "detections": detections
            if detections is not None
            else [{"class": _VIOLACAO, "confidence": 0.9, "bbox": [10, 20, 30, 40]}],
            "has_violation": True,
            "inferencia_ok": True,
        }
        if captura:
            payload["timestamp"] = captura
        self.event = {
            "event_type": "detection",
            "camera_id": self.camera_id,
            "payload": payload,
            "evidence_r2_key": evidence_r2_key,
            "occurred_at": "2026-09-01T10:00:00+00:00",
        }

    def post(self, client, monkeypatch, event: dict | None = None):
        monkeypatch.setattr(evt_routes, "_get_repo", lambda: self.events_repo)
        hb = MagicMock()
        hb.get_device_by_device_id.return_value = {
            "id": str(uuid4()),
            "tenant_id": str(self.tenant_id),
            "site_id": str(self.site_id),
            "device_id": self.device_id,
            "public_key_pem": self.public_pem,
            "revoked": False,
        }
        with ExitStack() as stack:
            stack.enter_context(patch(_HB_REPO, return_value=hb))
            dbpool = stack.enter_context(patch(_DBPOOL))
            dbpool.get_instance.return_value = MagicMock()
            stack.enter_context(patch(_CAM_REPO, return_value=self.camera_repo))
            stack.enter_context(patch(_ALERT_REPO, return_value=self.alert_repo))
            stack.enter_context(patch(_DEPLOY_REPO, return_value=self.deploy_repo))
            resp = client.post(
                "/api/v1/edge/events/ingest",
                json={"events": [event or self.event]},
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-Batch-Id": "batch-fixo-do-teste",
                },
            )
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()["data"]


class TestDeteccaoDoEdgeViraAlerta:
    def test_violacao_acima_do_limiar_nasce_em_alerts_com_site_id(
        self, client, monkeypatch
    ):
        """O elo que faltava: detecção de classe VIOLAÇÃO acima do limiar vira
        linha em `alerts` — e leva o `site_id` que só o ingest conhece."""
        caso = _Caso()
        data = caso.post(client, monkeypatch)

        assert data["ingested"] == 1
        assert data["alerts_created"] == 1
        assert data["alerts_failed"] == 0

        kw = caso.alert_repo.create.call_args.kwargs
        assert str(kw["site_id"]) == str(caso.site_id)
        assert str(kw["tenant_id"]) == str(caso.tenant_id)
        assert str(kw["camera_id"]) == caso.camera_id
        assert [v["class"] for v in kw["violations"]] == [_VIOLACAO]
        # Evidência: o agente já subiu; a nuvem só guarda a chave (ADR-0028).
        assert kw["evidence_key"] == caso.event["evidence_r2_key"]
        # Instante REAL da captura, não NOW() — é o que dá idempotência e o
        # que `ProcedenciaBadge` lê no front.
        assert kw["timestamp"].isoformat().startswith("2026-09-01T10:00:00.123456")

    def test_reenvio_do_mesmo_lote_nao_duplica(self, client, monkeypatch):
        """Idempotência por (câmera, instante de captura) — a mesma da
        inferência retroativa. Reenviar o lote não cria um segundo alerta."""
        caso = _Caso()
        primeira = caso.post(client, monkeypatch)
        assert primeira["alerts_created"] == 1
        assert caso.alert_repo.create.call_count == 1

        # 2ª rodada: o alerta da 1ª já está gravado.
        caso.alert_repo.exists_at_capture.return_value = True
        segunda = caso.post(client, monkeypatch)

        assert segunda["alerts_created"] == 0
        assert segunda["alerts_failed"] == 0
        assert caso.alert_repo.create.call_count == 1
        # O instante consultado é o da captura, não o do recebimento.
        _cam, captura = caso.alert_repo.exists_at_capture.call_args.args
        assert captura.isoformat().startswith("2026-09-01T10:00:00.123456")


class TestPortoesQueBarramSemFingirFalha:
    """Portão fechado ⇒ 0 alerta e 0 FALHA — mas o evento continua em
    `edge_events`. Nenhum destes é erro; erro tem contador próprio."""

    def test_abaixo_do_limiar_servido_nao_vira_alerta(self, client, monkeypatch):
        """DETECTION_CONFIDENCE_THRESHOLD (0.50) é o que vale na nuvem — o box
        tem o limiar dele, e não é ele que decide o que o operador vê."""
        caso = _Caso(detections=[{"class": _VIOLACAO, "confidence": 0.40}])
        data = caso.post(client, monkeypatch)

        assert data["ingested"] == 1
        assert (data["alerts_created"], data["alerts_failed"]) == (0, 0)
        caso.alert_repo.create.assert_not_called()

    def test_classe_sem_polaridade_de_violacao_nao_vira_alerta(
        self, client, monkeypatch
    ):
        """Só vira alerta o que o catálogo do tenant declara VIOLAÇÃO
        (`yolo_classes.is_violation`, ADR-0065). Presença não alerta."""
        caso = _Caso(
            detections=[{"class": _PRESENCA, "confidence": 0.95}],
            violacao=[_VIOLACAO.lower()],
            presenca=[_PRESENCA.lower()],
        )
        data = caso.post(client, monkeypatch)

        assert data["ingested"] == 1
        assert (data["alerts_created"], data["alerts_failed"]) == (0, 0)
        caso.alert_repo.create.assert_not_called()

    def test_fora_do_escopo_da_camera_nao_vira_alerta(self, client, monkeypatch):
        """#519: o escopo salvo na aba "Modelos por câmera" tem de valer também
        para o que chega do box. Aqui a câmera só reconhece 'Sem Óculos'."""
        caso = _Caso(escopo_da_camera=["Sem Óculos"])
        data = caso.post(client, monkeypatch)

        assert data["ingested"] == 1
        assert (data["alerts_created"], data["alerts_failed"]) == (0, 0)
        caso.alert_repo.create.assert_not_called()
        caso.deploy_repo.get_active_for_camera.assert_called_once()

    def test_escopo_ausente_nao_inventa_restricao(self, client, monkeypatch):
        """Deployment sem `config.classes` = dono nunca abriu a aba. None é
        "tudo passa"; silenciar aqui apagaria 28 câmeras de uma vez."""
        caso = _Caso(escopo_da_camera=None)
        assert caso.post(client, monkeypatch)["alerts_created"] == 1


class TestFalhaAltaNuncaEmSilencio:
    def test_camera_de_outro_tenant_conta_como_falha_e_nao_grava(
        self, client, monkeypatch
    ):
        """C-01: o camera_id vem de FORA. Câmera que não é do tenant do device
        não vira alerta — e a recusa APARECE no contador, não some."""
        caso = _Caso(tenant_da_camera_confere=False)
        data = caso.post(client, monkeypatch)

        assert data["ingested"] == 1  # o evento cru continua auditável
        assert data["alerts_created"] == 0
        assert data["alerts_failed"] == 1
        caso.alert_repo.create.assert_not_called()

    def test_evento_sem_instante_de_captura_falha_alto(self, client, monkeypatch):
        """Sem instante de captura não há idempotência: o reenvio duplicaria.
        Falhar é melhor que gravar com NOW() e duplicar em silêncio."""
        caso = _Caso(captura=None)
        evento = dict(caso.event)
        evento["occurred_at"] = None
        data = caso.post(client, monkeypatch, event=evento)

        assert data["alerts_failed"] == 1
        caso.alert_repo.create.assert_not_called()

    def test_evento_que_nao_e_deteccao_nao_tenta_alerta(self, client, monkeypatch):
        """`health_check`/`camera_offline` etc. seguem só para `edge_events` —
        não é falha, é que não há detecção nenhuma para virar alerta."""
        caso = _Caso()
        evento = dict(caso.event, event_type="health_check")
        data = caso.post(client, monkeypatch, event=evento)

        assert data["ingested"] == 1
        assert (data["alerts_created"], data["alerts_failed"]) == (0, 0)
        caso.alert_repo.create.assert_not_called()

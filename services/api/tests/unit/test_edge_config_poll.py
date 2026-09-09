"""
Unit — GET /api/v1/edge/config/poll (WS10).

Canal pull cloud→edge consumido pelo ConfigPoller do edge-sync-agent.
Contrato: body de PRIMEIRO NÍVEL {"cameras": [...]} (sem envelope success/data)
— o ConfigPoller aplica chaves top-level de forma parcial.

Segurança:
  - 401 sem device token (invariante /edge — C-05);
  - escopo site/tenant vem do enrollment do device (C-01);
  - resposta NUNCA contém username/password_encrypted.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock
from uuid import UUID

import app.api.v1.edge.routes as edge_routes
import app.core.device_auth as device_auth
import app.infrastructure.storage.local_storage as local_storage
from app.infrastructure.database.repositories.camera_repository import CameraRepository
from app.infrastructure.database.repositories.model_deployment_repository import (
    ModelDeploymentRepository,
)
from app.infrastructure.database.repositories.operation_repository import (
    OperationRepository,
)

TENANT = "11111111-1111-1111-1111-111111111111"
SITE_ID = "55555555-5555-5555-5555-555555555555"
DEVICE_ID = "device-abc123"

# Escopos como STRING literal (o que require_device_scope compara). Não usar
# DeviceTokenScope aqui: outra suíte pode ter mockado recognition_shared.enums
# em sys.modules, e o membro do enum viraria um MagicMock (pollution conhecida
# do full-suite — mesma classe das falhas pré-existentes de quality_inference).
CONFIG_READ = "config:read"
HEARTBEAT_WRITE = "heartbeat:write"


def _authed(*scopes: str):
    """Fake authenticate_device: device válido com os escopos (string) dados."""
    granted = list(scopes) or [CONFIG_READ]
    return lambda req: (TENANT, SITE_ID, DEVICE_ID, granted)


def _rule_row(**over) -> dict:
    """Linha de `operations` como o repo devolve — o que a tela de Cenário grava."""
    row = {
        "id": 7,
        "camera_id": UUID("44444444-4444-4444-4444-444444444444"),
        "module_id": "c925cab6-ed2b-43bb-8b4b-d1fd51b176f4",
        "type_id": "epi_zone",
        "template_id": "epi",
        "name": "Doca sem luva",
        "config": {
            "zone_points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            "watch_classes": ["Sem Luvas"],
            "persistence_s": 5,
        },
        "status": "active",
        "version": 1,
    }
    row.update(over)
    return row


def _module_row() -> dict:
    return {"module_code": "epi", "enabled": True}


def _class_row() -> dict:
    return {
        "class_id": 3,
        "class_name": "Sem Luvas",
        "display_name": "Sem luvas",
        "is_violation": True,
        "is_active": True,
    }


def _deployment_row(**over) -> dict:
    row = {
        "camera_id": UUID("44444444-4444-4444-4444-444444444444"),
        "module_code": "epi",
        "model_id": UUID("99999999-9999-9999-9999-999999999999"),
        "r2_onnx_key": "models/t/runpod/j/model.onnx",
        "framework": "rfdetr",
        "metrics": {"onnx_sha256": "a" * 64},
    }
    row.update(over)
    return row


def _mock_repos(monkeypatch, *, cameras=None, rules=None, deployments=None,
                modules=True, storage_url="https://r2.test/model.onnx"):
    """Fia TODOS os repos que o payload do config/poll consulta.

    Devolve os mocks para o teste checar chamadas/argumentos.
    """
    camera_repo = MagicMock()
    camera_repo.list_for_site_config.return_value = (
        [_camera_config_row()] if cameras is None else cameras
    )
    op_repo = MagicMock()
    op_repo.list_for_site_config.return_value = [] if rules is None else rules
    mod_repo = MagicMock()
    mod_repo.get_by_tenant.return_value = [_module_row()] if modules else []
    mod_repo.get_classes.return_value = [_class_row()]
    dep_repo = MagicMock()
    dep_repo.list_active_for_site.return_value = (
        [] if deployments is None else deployments
    )

    monkeypatch.setattr(edge_routes, "_get_camera_repo", lambda: camera_repo)
    monkeypatch.setattr(edge_routes, "_get_operation_repo", lambda: op_repo)
    monkeypatch.setattr(edge_routes, "_get_module_repo", lambda: mod_repo)
    monkeypatch.setattr(edge_routes, "_get_deployment_repo", lambda: dep_repo)

    storage = MagicMock()
    storage.generate_presigned_download_url.return_value = storage_url
    monkeypatch.setattr(local_storage, "get_storage", lambda *a, **k: storage)

    monkeypatch.setattr(device_auth, "authenticate_device", _authed(CONFIG_READ))
    return {
        "camera": camera_repo, "op": op_repo, "mod": mod_repo,
        "dep": dep_repo, "storage": storage,
    }


def _poll(client, etag=None):
    headers = {"Authorization": "Bearer device-token"}
    if etag:
        headers["If-None-Match"] = etag
    return client.get("/api/v1/edge/config/poll", headers=headers)


def _camera_config_row() -> dict:
    return {
        "id": UUID("44444444-4444-4444-4444-444444444444"),
        "name": "Cam Doca",
        "host": "10.0.0.20",
        "port": 554,
        "channel": 1,
        "subtype": 0,
        "rtsp_substream_url": None,
        "rtsp_url_override": None,
        "fps_target": 10,
        "quality_preset": "medium",
        "collection_subtype": 0,
        "is_active": True,
        "module_code": "epi",
    }


class TestEdgeConfigPoll:

    def test_no_token_returns_401(self, client):
        resp = client.get("/api/v1/edge/config/poll")
        assert resp.status_code == 401

    def test_malformed_token_returns_401(self, client):
        resp = client.get(
            "/api/v1/edge/config/poll",
            headers={"Authorization": "Bearer nao-e-um-jwt"},
        )
        assert resp.status_code == 401

    def test_returns_cameras_of_device_site_top_level(self, client, monkeypatch):
        """Body top-level {cameras} — formato que o ConfigPoller._apply consome."""
        repos = _mock_repos(monkeypatch)
        camera_repo = repos["camera"]

        resp = _poll(client)
        assert resp.status_code == 200
        body = resp.get_json()
        # SEM envelope success/data — contrato do ConfigPoller (top-level keys)
        assert "cameras" in body
        assert "data" not in body
        assert len(body["cameras"]) == 1
        cam = body["cameras"][0]
        assert cam["fps_target"] == 10
        assert cam["quality_preset"] == "medium"
        # collection_subtype (eixo COLETA, migration 114) propaga pelo MESMO
        # config/poll que já leva channel_map — sem rota/campo novo.
        assert cam["collection_subtype"] == 0
        # ADR-0058: `channel` é o mapa canal→câmera do gravador que o
        # edge-sync-agent (ConfigPoller) passa a cachear e preferir sobre
        # RECORDER_CHANNEL_MAP no .env — contrato travado aqui para não
        # regredir silenciosamente se _SELECT_COLS mudar.
        assert cam["channel"] == 1
        # Escopo site/tenant do enrollment (C-01)
        camera_repo.list_for_site_config.assert_called_once_with(SITE_ID, TENANT)

    def test_response_never_contains_credentials(self, client, monkeypatch):
        _mock_repos(monkeypatch, rules=[_rule_row()],
                    deployments=[_deployment_row()])

        resp = _poll(client)
        # C-05 vale para o PAYLOAD INTEIRO, não só para `cameras`.
        assert "password" not in resp.get_data(as_text=True).lower()
        assert "username" not in resp.get_data(as_text=True).lower()
        for cam in resp.get_json()["cameras"]:
            assert "username" not in cam
            assert "password" not in cam
            assert "password_encrypted" not in cam

    def test_device_not_authorized_returns_401(self, client, monkeypatch):
        monkeypatch.setattr(device_auth, "authenticate_device", lambda req: None)
        resp = client.get(
            "/api/v1/edge/config/poll",
            headers={"Authorization": "Bearer revogado"},
        )
        assert resp.status_code == 401

    def test_device_without_config_read_scope_returns_403(self, client, monkeypatch):
        """S1: device autenticado mas SEM config:read → 403 (menor privilégio)."""
        camera_repo = _mock_repos(monkeypatch)["camera"]
        # Token só com heartbeat:write não pode ler config
        monkeypatch.setattr(
            device_auth, "authenticate_device", _authed(HEARTBEAT_WRITE)
        )
        resp = client.get(
            "/api/v1/edge/config/poll",
            headers={"Authorization": "Bearer so-heartbeat"},
        )
        assert resp.status_code == 403
        camera_repo.list_for_site_config.assert_not_called()


class TestListForSiteConfigSql:
    """SELECT enxuto: NUNCA username/password na query enviada ao edge (C-05)."""

    @staticmethod
    def _repo_with_cursor():
        cur = MagicMock()
        cur.fetchall.return_value = []

        @contextmanager
        def _conn_ctx():
            conn = MagicMock()
            conn.cursor.return_value = cur
            yield conn

        pool = MagicMock()
        pool.get_connection.side_effect = _conn_ctx
        return CameraRepository(pool), cur

    def test_select_excludes_credentials(self):
        repo, cur = self._repo_with_cursor()
        repo.list_for_site_config(SITE_ID, TENANT)
        query = cur.execute.call_args[0][0].lower()
        assert "password" not in query
        assert "username" not in query
        assert "fps_target" in query
        assert "quality_preset" in query
        assert "collection_subtype" in query
        assert "tenant_id = %s" in query
        assert "site_id = %s" in query

    def test_params_are_site_and_tenant(self):
        repo, cur = self._repo_with_cursor()
        repo.list_for_site_config(SITE_ID, TENANT)
        assert cur.execute.call_args[0][1] == (SITE_ID, TENANT)


def test_config_poll_route_registered(app):
    """Rota registrada no blueprint /api/v1/edge (zero rota quebrada)."""
    rules = {str(r) for r in app.url_map.iter_rules()}
    assert "/api/v1/edge/config/poll" in rules


class TestConfigPollVersioning:
    """F1: config_version + ETag + 304 (If-None-Match)."""

    def _setup(self, monkeypatch, cameras):
        # Rota atrás de require_device_scope → mock em authenticate_device
        _mock_repos(monkeypatch, cameras=cameras)

    def test_200_returns_etag_and_config_version(self, client, monkeypatch):
        self._setup(monkeypatch, [_camera_config_row()])
        resp = client.get(
            "/api/v1/edge/config/poll", headers={"Authorization": "Bearer d"}
        )
        assert resp.status_code == 200
        assert resp.headers.get("ETag")
        assert resp.get_json()["config_version"]

    def test_if_none_match_returns_304(self, client, monkeypatch):
        self._setup(monkeypatch, [_camera_config_row()])
        first = client.get(
            "/api/v1/edge/config/poll", headers={"Authorization": "Bearer d"}
        )
        etag = first.headers["ETag"]
        second = client.get(
            "/api/v1/edge/config/poll",
            headers={"Authorization": "Bearer d", "If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.headers["ETag"] == etag
        assert second.get_data(as_text=True) == ""

    def test_changed_config_changes_etag(self, client, monkeypatch):
        self._setup(monkeypatch, [_camera_config_row()])
        first = client.get(
            "/api/v1/edge/config/poll", headers={"Authorization": "Bearer d"}
        )
        etag1 = first.headers["ETag"]
        # Config muda (fps diferente) → ETag muda → não é 304
        changed = _camera_config_row()
        changed["fps_target"] = 30
        self._setup(monkeypatch, [changed])
        second = client.get(
            "/api/v1/edge/config/poll",
            headers={"Authorization": "Bearer d", "If-None-Match": etag1},
        )
        assert second.status_code == 200
        assert second.headers["ETag"] != etag1


class TestPropagacaoDoQueMudaNoFront:
    """A nuvem manda ao box TUDO que muda no front — não só câmera.

    Antes desta suíte o payload era `{"cameras": [...]}` e a tela de Cenário
    prometia ao operador "SALVAR PROPAGA AO BOX DO SITE EM ~60 S" — promessa
    que nunca se cumpria porque o cenário jamais saía da nuvem.
    """

    # (a) arquivar câmera → some do que o box monitora ────────────────────
    def test_camera_arquivada_viaja_com_is_active_false(self, client, monkeypatch):
        """A nuvem NÃO filtra: manda a câmera arquivada com `is_active=False`.

        Decisão deliberada (ver `list_for_site_config`): quem filtra é o
        agente, em `_write_channel_map_cache`. O par deste teste vive em
        `services/edge-sync-agent/tests/test_config_poller.py` e prova que a
        câmera arquivada some do mapa de canais do gravador — os dois juntos
        são a garantia de que arquivar no front tira a câmera do box.
        """
        arquivada = _camera_config_row()
        arquivada["id"] = UUID("44444444-4444-4444-4444-44444444dead")
        arquivada["is_active"] = False
        _mock_repos(monkeypatch, cameras=[_camera_config_row(), arquivada])

        cams = _poll(client).get_json()["cameras"]
        por_id = {c["id"]: c for c in cams}
        assert por_id["44444444-4444-4444-4444-44444444dead"]["is_active"] is False
        assert por_id["44444444-4444-4444-4444-444444444444"]["is_active"] is True

    def test_arquivar_camera_muda_config_version(self, client, monkeypatch):
        """Arquivar tem de mudar a versão — senão o box recebe 304 e não aplica."""
        _mock_repos(monkeypatch)
        antes = _poll(client).headers["ETag"]

        arquivada = _camera_config_row()
        arquivada["is_active"] = False
        _mock_repos(monkeypatch, cameras=[arquivada])
        depois = _poll(client, etag=antes)

        assert depois.status_code == 200
        assert depois.headers["ETag"] != antes

    # (b) cenário → payload + versão ──────────────────────────────────────
    def test_regras_do_cenario_viajam_no_payload(self, client, monkeypatch):
        """`rules` carrega zona, escopo de classes e modo de aviso da tela."""
        _mock_repos(monkeypatch, rules=[_rule_row()])

        body = _poll(client).get_json()
        assert len(body["rules"]) == 1
        regra = body["rules"][0]
        assert regra["type_id"] == "epi_zone"
        assert regra["config"]["watch_classes"] == ["Sem Luvas"]
        assert regra["config"]["zone_points"][0] == [0.0, 0.0]
        assert regra["config"]["persistence_s"] == 5
        assert regra["status"] == "active"
        # camera_id serializado como string (o agente casa com cameras[].id)
        assert regra["camera_id"] == "44444444-4444-4444-4444-444444444444"

    def test_scenario_leva_modulos_e_classes(self, client, monkeypatch):
        _mock_repos(monkeypatch)
        scenario = _poll(client).get_json()["scenario"]
        assert scenario["modules"][0]["module_code"] == "epi"
        assert scenario["modules"][0]["classes"][0]["class_name"] == "Sem Luvas"

    def test_modulo_desabilitado_nao_vai_no_scenario(self, client, monkeypatch):
        _mock_repos(monkeypatch, modules=False)
        assert _poll(client).get_json()["scenario"]["modules"] == []

    def test_mudar_escopo_de_classes_muda_config_version(self, client, monkeypatch):
        """O bug mais fácil de introduzir aqui: versão derivada só de `cameras`.

        Com o cenário fora do hash, mudar o escopo devolveria 304 e o box
        NUNCA aplicaria a mudança que a tela disse ter propagado.
        """
        _mock_repos(monkeypatch, rules=[_rule_row()])
        antes = _poll(client).headers["ETag"]

        outra = _rule_row()
        outra["config"] = dict(outra["config"], watch_classes=["Sem Óculos"])
        _mock_repos(monkeypatch, rules=[outra])
        depois = _poll(client, etag=antes)

        assert depois.status_code == 200, "cenário mudou e a nuvem devolveu 304"
        assert depois.headers["ETag"] != antes
        assert depois.get_json()["rules"][0]["config"]["watch_classes"] == [
            "Sem Óculos"
        ]

    def test_mudar_classes_do_modulo_muda_config_version(self, client, monkeypatch):
        _mock_repos(monkeypatch)
        antes = _poll(client).headers["ETag"]

        repos = _mock_repos(monkeypatch)
        repos["mod"].get_classes.return_value = [
            dict(_class_row(), class_name="Sem Capacete")
        ]
        depois = _poll(client, etag=antes)

        assert depois.status_code == 200
        assert depois.headers["ETag"] != antes

    # (c) modelo ativo → manifesto ────────────────────────────────────────
    def test_modelo_ativo_vira_manifesto_com_sha256_real(self, client, monkeypatch):
        repos = _mock_repos(monkeypatch, deployments=[_deployment_row()])

        model = _poll(client).get_json()["model"]
        assert model["sha256"] == "a" * 64
        assert model["engine_type"] == "onnx"
        assert model["url"] == "https://r2.test/model.onnx"
        # a chave R2 crua não vaza no lugar da URL assinada
        assert "r2_key" not in model
        repos["storage"].generate_presigned_download_url.assert_called_once()
        assert (
            repos["storage"].generate_presigned_download_url.call_args[0][0]
            == "models/t/runpod/j/model.onnx"
        )

    def test_trocar_modelo_ativo_muda_manifesto_e_config_version(
        self, client, monkeypatch
    ):
        _mock_repos(monkeypatch, deployments=[_deployment_row()])
        antes = _poll(client).headers["ETag"]

        novo = _deployment_row(metrics={"onnx_sha256": "b" * 64})
        _mock_repos(monkeypatch, deployments=[novo])
        depois = _poll(client, etag=antes)

        assert depois.status_code == 200
        assert depois.headers["ETag"] != antes
        assert depois.get_json()["model"]["sha256"] == "b" * 64

    def test_url_assinada_nao_entra_no_config_version(self, client, monkeypatch):
        """A assinatura muda a cada geração; se entrasse no hash, o box baixaria
        o modelo em todo poll (nunca mais um 304)."""
        _mock_repos(monkeypatch, deployments=[_deployment_row()],
                    storage_url="https://r2.test/model.onnx?sig=UM")
        primeiro = _poll(client)

        _mock_repos(monkeypatch, deployments=[_deployment_row()],
                    storage_url="https://r2.test/model.onnx?sig=OUTRA")
        segundo = _poll(client, etag=primeiro.headers["ETag"])
        assert segundo.status_code == 304

    def test_modelo_sem_digest_nao_gera_manifesto(self, client, monkeypatch):
        """Sem SHA-256 real gravado, a nuvem NÃO manda `model` — e não inventa.

        Chave ausente é no-op no apply parcial do ConfigPoller; um digest
        fabricado seria uma armadilha (reprovaria todo download no dia em que
        alguém o conferisse).
        """
        _mock_repos(monkeypatch, deployments=[_deployment_row(metrics={})])
        assert "model" not in _poll(client).get_json()

    def test_modelos_distintos_no_site_nao_geram_manifesto(self, client, monkeypatch):
        """O contrato do agente tem UM modelo por box. Com dois modelos ativos
        não dá para dizer qual — omitir é melhor que mandar o errado."""
        outra_camera = _deployment_row(
            camera_id=UUID("44444444-4444-4444-4444-4444444444bb"),
            metrics={"onnx_sha256": "c" * 64},
        )
        _mock_repos(monkeypatch, deployments=[_deployment_row(), outra_camera])
        assert "model" not in _poll(client).get_json()

    def test_falha_no_storage_nao_derruba_o_poll(self, client, monkeypatch):
        """R2 fora do ar tira o manifesto, não as câmeras do box."""
        repos = _mock_repos(monkeypatch, deployments=[_deployment_row()])
        repos["storage"].generate_presigned_download_url.side_effect = RuntimeError("r2")

        resp = _poll(client)
        assert resp.status_code == 200
        body = resp.get_json()
        assert "model" not in body
        assert len(body["cameras"]) == 1

    # (d) nada mudou → 304 ────────────────────────────────────────────────
    def test_nada_mudou_devolve_304(self, client, monkeypatch):
        _mock_repos(monkeypatch, rules=[_rule_row()],
                    deployments=[_deployment_row()])
        primeiro = _poll(client)
        assert primeiro.status_code == 200

        _mock_repos(monkeypatch, rules=[_rule_row()],
                    deployments=[_deployment_row()])
        segundo = _poll(client, etag=primeiro.headers["ETag"])
        assert segundo.status_code == 304
        assert segundo.get_data(as_text=True) == ""

    # escopo de tenant/site (C-01) nas consultas novas ────────────────────
    def test_consultas_novas_escopadas_por_site_e_tenant(self, client, monkeypatch):
        repos = _mock_repos(monkeypatch)
        _poll(client)
        repos["op"].list_for_site_config.assert_called_once_with(SITE_ID, TENANT)
        repos["dep"].list_active_for_site.assert_called_once_with(SITE_ID, TENANT)
        repos["mod"].get_by_tenant.assert_called_once_with(TENANT)


class TestListForSiteConfigSqlDosNovosRepos:
    """SELECT enxuto e escopado nas duas consultas novas (C-01 + C-05)."""

    @staticmethod
    def _cursor_repo(cls):
        cur = MagicMock()
        cur.fetchall.return_value = []

        @contextmanager
        def _conn_ctx():
            conn = MagicMock()
            conn.cursor.return_value = cur
            yield conn

        pool = MagicMock()
        pool.get_connection.side_effect = _conn_ctx
        return cls(pool), cur

    def test_operations_query_escopada_e_sem_credencial(self):
        repo, cur = self._cursor_repo(OperationRepository)
        repo.list_for_site_config(SITE_ID, TENANT)
        query = cur.execute.call_args[0][0].lower()
        assert "password" not in query
        assert "username" not in query
        assert "c.site_id = %s" in query
        assert "o.tenant_id = %s" in query
        assert "c.tenant_id = %s" in query
        assert cur.execute.call_args[0][1] == (SITE_ID, TENANT, TENANT)

    def test_deployments_query_escopada_e_sem_credencial(self):
        repo, cur = self._cursor_repo(ModelDeploymentRepository)
        repo.list_active_for_site(SITE_ID, TENANT)
        query = cur.execute.call_args[0][0].lower()
        assert "password" not in query
        assert "username" not in query
        assert "c.site_id = %s" in query
        assert "md.tenant_id = %s" in query
        assert "c.tenant_id = %s" in query
        assert "status = 'active'" in query
        assert cur.execute.call_args[0][1] == (SITE_ID, TENANT, TENANT)


def test_heartbeat_e_poll_calculam_a_mesma_versao(client, monkeypatch):
    """O `config_version` do 304 e o que o heartbeat compara têm de ser O MESMO.

    Se divergirem, o log de divergência (ADR-0058) passa a gritar em todo
    heartbeat de um box que está perfeitamente em dia — e o aviso que existe
    para revelar problema vira o problema.
    """
    import app.api.v1.edge.routes as edge_routes

    _mock_repos(monkeypatch, rules=[_rule_row()], deployments=[_deployment_row()])
    etag_do_poll = _poll(client).headers["ETag"]

    versao_do_heartbeat = edge_routes._compute_config_version(
        edge_routes._build_edge_config_payload(SITE_ID, TENANT)
    )
    assert etag_do_poll == f'"{versao_do_heartbeat}"'

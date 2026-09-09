"""
Security — contrato edge→cloud de POST /api/v1/edge/events/ingest, cruzando a
fronteira HTTP com o CLIENTE REAL (services/edge-sync-agent/app/uploader.py).

Achado (mapa PR #523, edge-fleet.json P1): o Uploader do agente postava em
/api/v1/edge/detections — rota que NUNCA existiu na API — com corpo
{device_id, detections}. O único ingest é /api/v1/edge/events/ingest
(device auth RS256 + escopo events:write, corpo {"events": [...]}). Decisão
registrada: docs/edge/INTEGRACAO_EDGE_F0F2_2026-07-19.md §1 (canônico =
/events/ingest; /detections não é implementado).

Por que importar o uploader do agente aqui: um teste que só posta um JSON
escrito à mão na rota prova o contrato da ROTA, não que o CLIENTE o respeita
(memória: "valor ecoado pelo cliente precisa de teste pela rota"). Então o
request é CAPTURADO do Uploader real (http client fake) e REPRODUZIDO no
Flask test client, com device token RS256 de verdade.

O pacote do agente também se chama `app` — é carregado sob o nome sintético
`edge_agent` (importlib) pra não colidir com o `app` da API.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

import app.api.v1.edge_events.routes as evt_routes
from tests.security._helpers_tenant import make_device_token

_AGENT_APP_DIR = (
    Path(__file__).resolve().parents[3] / "edge-sync-agent" / "app"
)
_HB_REPO = (
    "app.infrastructure.database.repositories.edge_heartbeat_repository"
    ".EdgeHeartbeatRepository"
)


def _load_agent_uploader():
    """Carrega services/edge-sync-agent/app/{sqlite_buffer,uploader}.py como
    pacote `edge_agent` (imports relativos do uploader continuam válidos)."""
    assert _AGENT_APP_DIR.is_dir(), f"agente não encontrado em {_AGENT_APP_DIR}"
    if "edge_agent" not in sys.modules:
        pkg = types.ModuleType("edge_agent")
        pkg.__path__ = [str(_AGENT_APP_DIR)]  # type: ignore[attr-defined]
        sys.modules["edge_agent"] = pkg
    return importlib.import_module("edge_agent.uploader")


class _CapturingHttp:
    """http_client fake do Uploader: guarda (url, kwargs) e responde 200."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((url, kwargs))
        resp = MagicMock()
        resp.status_code = 200
        return resp


def _capture_agent_request(tmp_path, events):
    """Enfileira `events` no buffer SQLite real do agente, roda um ciclo do
    Uploader e devolve (url_path, json_body, headers) do request emitido."""
    up_mod = _load_agent_uploader()
    buf_mod = importlib.import_module("edge_agent.sqlite_buffer")
    buf = buf_mod.SQLiteBuffer(str(tmp_path / "buffer.db"))
    try:
        for event_type, camera_id, payload in events:
            buf.enqueue(event_type, camera_id, payload)
        http = _CapturingHttp()
        uploader = up_mod.Uploader(
            buffer=buf,
            http_client=http,
            cloud_url="https://cloud.test",
            device_id="dev-edge",
            token="",
            upload_interval_s=0.0,
        )
        assert uploader._try_upload(buf.dequeue_batch()) is True
    finally:
        buf.close()
    url, kwargs = http.calls[0]
    path = url.removeprefix("https://cloud.test")
    headers = {k: v for k, v in kwargs["headers"].items() if k != "Authorization"}
    return path, kwargs["json"], headers


def _device_record(tenant_id, site_id, device_id, public_pem):
    return {
        "id": str(uuid4()),
        "tenant_id": str(tenant_id),
        "site_id": str(site_id),
        "device_id": device_id,
        "public_key_pem": public_pem,
        "revoked": False,
    }


@pytest.fixture
def ingest_repo(monkeypatch):
    repo = MagicMock()
    repo.ingest.return_value = {"id": str(uuid4()), "received_at": "now"}
    monkeypatch.setattr(evt_routes, "_get_repo", lambda: repo)
    return repo


class TestUploaderRequestIsAcceptedByIngestRoute:
    def test_agent_request_lands_in_ingest_with_tenant_from_token(
        self, client, tmp_path, ingest_repo
    ):
        """O request EXATO do Uploader → 200 na rota real; tenant/site vêm do
        device token (C-01), nunca do corpo; campos do evento chegam ao repo."""
        tenant_id, site_id, device_id = uuid4(), uuid4(), "dev-edge"
        token, pub = make_device_token(
            tenant_id, site_id, device_id, scopes=["events:write"]
        )
        camera_id = str(uuid4())
        path, body, headers = _capture_agent_request(
            tmp_path, [("detection", camera_id, {"has_violation": True})]
        )

        hb = MagicMock()
        hb.get_device_by_device_id.return_value = _device_record(
            tenant_id, site_id, device_id, pub
        )
        with patch(_HB_REPO, return_value=hb):
            resp = client.post(
                path,
                json=body,
                headers={**headers, "Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200, (path, resp.get_json())
        data = resp.get_json()["data"]
        # `alerts_created`/`alerts_failed`: a detecção do box também tem de
        # virar linha em `alerts` (edge_events não tem leitor no produto).
        # Neste teste não há DatabasePool nem câmera, então o alerta FALHA — e
        # o contrato exige que a falha apareça na resposta em vez de sumir.
        # É exatamente esse número que prova que ela não some.
        assert data == {
            "ingested": 1,
            "submitted": 1,
            "batch_id": headers["X-Batch-Id"],
            "alerts_created": 0,
            "alerts_failed": 1,
        }
        kw = ingest_repo.ingest.call_args.kwargs
        assert kw["tenant_id"] == str(tenant_id)
        assert kw["site_id"] == str(site_id)
        assert kw["device_id"] == device_id
        assert kw["event_type"] == "detection"
        assert kw["camera_id"] == camera_id
        assert kw["payload"] == {"has_violation": True}
        assert kw["occurred_at"].endswith("+00:00")
        assert kw["batch_id"] == headers["X-Batch-Id"]
        assert kw["dedup_key"].startswith(headers["X-Batch-Id"] + ":")

    def test_forged_tenant_in_body_is_ignored(self, client, tmp_path, ingest_repo):
        """Evento com tenant_id/site_id de OUTRO tenant no corpo: o repo
        recebe o tenant do token. (C-01 — corpo nunca escolhe tenant.)"""
        tenant_a, site_a = uuid4(), uuid4()
        tenant_b, site_b = uuid4(), uuid4()
        token, pub = make_device_token(tenant_a, site_a, "dev-a", scopes=["events:write"])
        path, body, headers = _capture_agent_request(
            tmp_path, [("detection", str(uuid4()), {"x": 1})]
        )
        body["events"][0]["tenant_id"] = str(tenant_b)
        body["events"][0]["site_id"] = str(site_b)
        body["tenant_id"] = str(tenant_b)

        hb = MagicMock()
        hb.get_device_by_device_id.return_value = _device_record(tenant_a, site_a, "dev-a", pub)
        with patch(_HB_REPO, return_value=hb):
            resp = client.post(
                path, json=body, headers={**headers, "Authorization": f"Bearer {token}"}
            )

        assert resp.status_code == 200
        kw = ingest_repo.ingest.call_args.kwargs
        assert kw["tenant_id"] == str(tenant_a)
        assert kw["site_id"] == str(site_a)
        assert kw["tenant_id"] != str(tenant_b)

    def test_token_of_other_tenant_than_enrollment_is_401(
        self, client, tmp_path, ingest_repo
    ):
        """Device token assinado com claims do tenant B, mas o enrollment
        (linha device_tokens) é do tenant A → 401, nada persiste."""
        tenant_a, site_a = uuid4(), uuid4()
        tenant_b, site_b = uuid4(), uuid4()
        token_b, pub_b = make_device_token(tenant_b, site_b, "dev-x", scopes=["events:write"])
        path, body, headers = _capture_agent_request(
            tmp_path, [("detection", str(uuid4()), {})]
        )

        hb = MagicMock()
        hb.get_device_by_device_id.return_value = _device_record(tenant_a, site_a, "dev-x", pub_b)
        with patch(_HB_REPO, return_value=hb):
            resp = client.post(
                path, json=body, headers={**headers, "Authorization": f"Bearer {token_b}"}
            )

        assert resp.status_code == 401
        ingest_repo.ingest.assert_not_called()

    def test_retry_reuses_batch_id_and_identical_events(
        self, client, tmp_path, ingest_repo
    ):
        """Idempotência ponta a ponta: a rota deriva dedup_key de
        X-Batch-Id + sha256(evento). Dois ciclos do agente sobre o MESMO
        buffer têm que produzir as MESMAS dedup_keys no repo."""
        tenant_id, site_id = uuid4(), uuid4()
        token, pub = make_device_token(tenant_id, site_id, "dev-r", scopes=["events:write"])
        up_mod = _load_agent_uploader()
        buf_mod = importlib.import_module("edge_agent.sqlite_buffer")
        buf = buf_mod.SQLiteBuffer(str(tmp_path / "retry.db"))
        try:
            buf.enqueue("detection", str(uuid4()), {"n": 1})
            buf.enqueue("alert_triggered", str(uuid4()), {"n": 2})
            http = _CapturingHttp()
            # 1ª tentativa "falha" do ponto de vista do agente (ex.: timeout
            # após o servidor já ter gravado): mark_failed, evento fica.
            http.post = MagicMock(side_effect=OSError("timeout"))
            up = up_mod.Uploader(buf, http, "https://cloud.test", "dev-r", "", upload_interval_s=0.0)
            assert up._try_upload(buf.dequeue_batch()) is False
            first = http.post.call_args
            http.post = MagicMock(return_value=MagicMock(status_code=200))
            assert up._try_upload(buf.dequeue_batch()) is True
            second = http.post.call_args
        finally:
            buf.close()

        hb = MagicMock()
        hb.get_device_by_device_id.return_value = _device_record(tenant_id, site_id, "dev-r", pub)
        keys: list[list[str]] = []
        for call in (first, second):
            ingest_repo.ingest.reset_mock()
            url = call.args[0].removeprefix("https://cloud.test")
            hdrs = {k: v for k, v in call.kwargs["headers"].items() if k != "Authorization"}
            with patch(_HB_REPO, return_value=hb):
                resp = client.post(
                    url, json=call.kwargs["json"],
                    headers={**hdrs, "Authorization": f"Bearer {token}"},
                )
            assert resp.status_code == 200
            keys.append([c.kwargs["dedup_key"] for c in ingest_repo.ingest.call_args_list])

        assert keys[0] == keys[1]
        assert len(set(keys[0])) == 2  # dois eventos distintos, duas chaves

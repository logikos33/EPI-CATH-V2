"""
Unit — POST /api/v1/edge/cameras/<camera_id>/evidence.

É a rota que faz o alerta do box chegar COM frame: `alerta_de_evento_do_edge`
não sobe imagem (espera a chave pronta no evento) e o agente não tem — nem
terá — credencial de R2. Sem esta rota o box não tinha COMO produzir a chave,
e todo alerta nascia com `evidence_r2_key` NULL (medido em 09/09/2026: 9
linhas em public.edge_events, todas sem evidência).

Device auth com escopo `events:write` (o mesmo do ingest — evidência é parte
do fluxo do evento, não um segundo privilégio).
"""
import io
from unittest.mock import MagicMock

import app.api.v1.edge.routes as edge_routes
import app.core.device_auth as device_auth

TENANT = "11111111-1111-1111-1111-111111111111"
SITE_ID = "55555555-5555-5555-5555-555555555555"
DEVICE_ID = "pandora-rvb-agent"
CAMERA_ID = "44444444-4444-4444-4444-444444444444"

EVENTS_WRITE = "events:write"
FRAMES_WRITE = "frames:write"


def _authed(*scopes: str):
    granted = list(scopes) or [EVENTS_WRITE]
    return lambda req: (TENANT, SITE_ID, DEVICE_ID, granted)


def _tiny_jpeg_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 12), color=(10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _setup(monkeypatch, *, camera_found=True):
    camera_repo = MagicMock()
    camera_repo.get_by_id_and_tenant.return_value = {"id": CAMERA_ID} if camera_found else None
    monkeypatch.setattr(edge_routes, "_get_camera_repo", lambda: camera_repo)

    storage = MagicMock()
    import app.infrastructure.storage.local_storage as local_storage_mod
    monkeypatch.setattr(local_storage_mod, "get_storage", lambda *a, **k: storage)

    return camera_repo, storage


def _post(client, *, file_bytes=None):
    data = {}
    if file_bytes is not None:
        data["file"] = (io.BytesIO(file_bytes), "evidence.jpg")
    return client.post(
        f"/api/v1/edge/cameras/{CAMERA_ID}/evidence",
        data=data,
        headers={"Authorization": "Bearer d"},
        content_type="multipart/form-data",
    )


def test_route_registered(app):
    rules = {str(r) for r in app.url_map.iter_rules()}
    assert "/api/v1/edge/cameras/<camera_id>/evidence" in rules


def test_no_token_returns_401(client):
    assert client.post(f"/api/v1/edge/cameras/{CAMERA_ID}/evidence").status_code == 401


def test_wrong_scope_returns_403(client, monkeypatch):
    monkeypatch.setattr(device_auth, "authenticate_device", _authed(FRAMES_WRITE))
    assert _post(client, file_bytes=_tiny_jpeg_bytes()).status_code == 403


def test_camera_de_outro_tenant_da_404(client, monkeypatch):
    """C-01: cross-tenant some, não dá 403 — 403 vazaria a existência."""
    monkeypatch.setattr(device_auth, "authenticate_device", _authed(EVENTS_WRITE))
    _setup(monkeypatch, camera_found=False)
    assert _post(client, file_bytes=_tiny_jpeg_bytes()).status_code == 404


def test_lookup_usa_o_tenant_do_device(client, monkeypatch):
    monkeypatch.setattr(device_auth, "authenticate_device", _authed(EVENTS_WRITE))
    camera_repo, _ = _setup(monkeypatch)
    _post(client, file_bytes=_tiny_jpeg_bytes())
    camera_repo.get_by_id_and_tenant.assert_called_once_with(CAMERA_ID, TENANT)


def test_arquivo_ausente_vazio_ou_invalido_da_422(client, monkeypatch):
    monkeypatch.setattr(device_auth, "authenticate_device", _authed(EVENTS_WRITE))
    _setup(monkeypatch)
    assert _post(client, file_bytes=None).status_code == 422
    assert _post(client, file_bytes=b"").status_code == 422
    assert _post(client, file_bytes=b"nao e um jpeg").status_code == 422


def test_arquivo_grande_demais_da_413(client, monkeypatch):
    monkeypatch.setattr(device_auth, "authenticate_device", _authed(EVENTS_WRITE))
    _setup(monkeypatch)
    assert _post(client, file_bytes=b"\xff" * (5 * 1024 * 1024 + 1)).status_code == 413


def test_upload_devolve_201_com_chave_na_convencao_do_caminho_ao_vivo(client, monkeypatch):
    """A MESMA convenção de `inference._save_alert`: `evidence/{camera}/{ts}.jpg`.
    Um segundo dialeto para o mesmo tipo de objeto seria dívida sem ganho."""
    monkeypatch.setattr(device_auth, "authenticate_device", _authed(EVENTS_WRITE))
    _setup(monkeypatch)

    resp = _post(client, file_bytes=_tiny_jpeg_bytes())

    assert resp.status_code == 201
    r2_key = resp.get_json()["data"]["r2_key"]
    assert r2_key.startswith(f"evidence/{CAMERA_ID}/")
    assert r2_key.endswith(".jpg")


def test_upload_grava_no_storage_como_jpeg(client, monkeypatch):
    monkeypatch.setattr(device_auth, "authenticate_device", _authed(EVENTS_WRITE))
    _, storage = _setup(monkeypatch)

    resp = _post(client, file_bytes=_tiny_jpeg_bytes())

    key, _data, content_type = storage.upload_bytes.call_args[0]
    assert key == resp.get_json()["data"]["r2_key"]
    assert content_type == "image/jpeg"


def test_falha_de_storage_da_502(client, monkeypatch):
    from app.core.exceptions import StorageError

    monkeypatch.setattr(device_auth, "authenticate_device", _authed(EVENTS_WRITE))
    _, storage = _setup(monkeypatch)
    storage.upload_bytes.side_effect = StorageError("Access Denied")

    assert _post(client, file_bytes=_tiny_jpeg_bytes()).status_code == 502


def test_nao_mexe_no_cache_de_miniatura_da_triagem(client, monkeypatch):
    """Evidência de alerta não é a miniatura da câmera. Reusar a rota de
    snapshot faria a triagem passar a mostrar 'o último alerta'."""
    monkeypatch.setattr(device_auth, "authenticate_device", _authed(EVENTS_WRITE))
    _setup(monkeypatch)
    redis_client = MagicMock()
    monkeypatch.setattr(edge_routes, "_get_redis", lambda: redis_client)

    assert _post(client, file_bytes=_tiny_jpeg_bytes()).status_code == 201
    redis_client.setex.assert_not_called()

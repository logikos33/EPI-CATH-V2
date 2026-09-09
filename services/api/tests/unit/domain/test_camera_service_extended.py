"""
Tests: CameraService — build_rtsp_url, build_stream_url, update_camera,
delete_camera, get_camera, record_test_result (item-24).

Extends existing test_camera_service.py with the methods at lines 149–230.
"""
import pytest
from cryptography.fernet import Fernet
from unittest.mock import MagicMock
from uuid import uuid4

from app.core.exceptions import NotFoundError, ValidationError
from app.domain.services.camera_service import CameraService


def _make_service():
    repo = MagicMock()
    key = Fernet.generate_key().decode()
    return CameraService(repo, key), repo


def _cam(camera_id=None, tenant_id=None, **kwargs):
    camera_id = camera_id or uuid4()
    tenant_id = tenant_id or uuid4()
    base = {
        "id": camera_id,
        "tenant_id": tenant_id,
        "name": "Test",
        "host": "192.168.1.1",
        "port": 554,
        "username": "admin",
        "password_encrypted": "",
        "manufacturer": "generic",
        "channel": 1,
        "subtype": 0,
        "rtsp_url_override": None,
        "is_active": True,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# get_camera
# ---------------------------------------------------------------------------

class TestGetCamera:

    def test_get_camera_success(self):
        service, repo = _make_service()
        cam = _cam()
        repo.get_by_id.return_value = cam
        result = service.get_camera(cam["id"])
        assert result["id"] == str(cam["id"])
        assert "password_encrypted" not in result

    def test_get_camera_not_found_raises(self):
        service, repo = _make_service()
        repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            service.get_camera(uuid4())


# ---------------------------------------------------------------------------
# build_rtsp_url
# ---------------------------------------------------------------------------

class TestBuildRtspUrl:

    def test_rtsp_url_generic(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            manufacturer="generic", host="10.0.0.1", port=554,
            username="user",
        )
        url = service.build_rtsp_url(camera_id, tenant_id)
        assert url.startswith("rtsp://")
        assert "10.0.0.1" in url
        assert "/stream1" in url

    def test_rtsp_url_hikvision(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            manufacturer="hikvision", channel=1, subtype=0,
        )
        url = service.build_rtsp_url(camera_id, tenant_id)
        assert "Streaming/Channels" in url

    def test_rtsp_url_intelbras(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            manufacturer="intelbras",
        )
        url = service.build_rtsp_url(camera_id, tenant_id)
        assert "cam/realmonitor" in url

    def test_rtsp_url_dahua(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            manufacturer="dahua",
        )
        url = service.build_rtsp_url(camera_id, tenant_id)
        assert "cam/realmonitor" in url

    def test_rtsp_url_override_used_when_set(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            rtsp_url_override="rtsp://override.host/stream",
        )
        url = service.build_rtsp_url(camera_id, tenant_id)
        assert url == "rtsp://override.host/stream"

    def test_rtsp_url_not_found_raises(self):
        service, repo = _make_service()
        repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            service.build_rtsp_url(uuid4(), uuid4())

    def test_rtsp_url_wrong_user_raises(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        with pytest.raises(NotFoundError):
            service.build_rtsp_url(camera_id, uuid4())  # different user

    def test_rtsp_url_admin_can_access_other_tenant(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        # admin bypass
        url = service.build_rtsp_url(camera_id, uuid4(), is_admin=True)
        assert url.startswith("rtsp://")


# ---------------------------------------------------------------------------
# build_stream_url
# ---------------------------------------------------------------------------

class TestBuildStreamUrl:

    def test_stream_url_hikvision_non_554_returns_http(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            manufacturer="hikvision", port=8080,
        )
        url = service.build_stream_url(camera_id, tenant_id)
        assert url.startswith("http://")
        assert "ISAPI" in url

    def test_stream_url_hikvision_554_falls_back_to_rtsp(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        cam = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            manufacturer="hikvision", port=554,
        )
        repo.get_by_id.return_value = cam
        url = service.build_stream_url(camera_id, tenant_id)
        assert url.startswith("rtsp://")

    def test_stream_url_override_returns_override(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            rtsp_url_override="rtsp://override/stream",
        )
        url = service.build_stream_url(camera_id, tenant_id)
        assert url == "rtsp://override/stream"

    def test_stream_url_not_found_raises(self):
        service, repo = _make_service()
        repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            service.build_stream_url(uuid4(), uuid4())

    def test_stream_url_wrong_user_raises(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        with pytest.raises(NotFoundError):
            service.build_stream_url(camera_id, uuid4())


# ---------------------------------------------------------------------------
# update_camera
# ---------------------------------------------------------------------------

class TestUpdateCamera:

    def test_update_name_success(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        existing = _cam(camera_id=camera_id, tenant_id=tenant_id)
        updated = {**existing, "name": "New Name"}
        repo.get_by_id.return_value = existing
        repo.update.return_value = {**updated, "id": camera_id}

        result = service.update_camera(camera_id, tenant_id, {"name": "New Name"})
        assert result["id"] == str(camera_id)
        assert "password_encrypted" not in result

    def test_update_password_encrypts(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        repo.update.return_value = {"id": camera_id, "name": "Cam"}

        service.update_camera(camera_id, tenant_id, {"password": "newsecret"})
        call_data = repo.update.call_args[0][1]
        assert "password_encrypted" in call_data
        assert call_data["password_encrypted"] != "newsecret"

    def test_update_no_fields_raises(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)

        with pytest.raises(ValidationError, match="Nenhum campo"):
            service.update_camera(camera_id, tenant_id, {"unknown_field": "val"})

    def test_update_not_found_raises(self):
        service, repo = _make_service()
        repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            service.update_camera(uuid4(), uuid4(), {"name": "X"})

    def test_update_wrong_user_raises(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        with pytest.raises(NotFoundError):
            service.update_camera(camera_id, uuid4(), {"name": "X"})

    def test_update_admin_can_update_other_tenant(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        repo.update.return_value = {"id": camera_id, "name": "Updated"}

        result = service.update_camera(camera_id, uuid4(), {"name": "Updated"}, is_admin=True)
        assert result["id"] == str(camera_id)


# ---------------------------------------------------------------------------
# delete_camera
# ---------------------------------------------------------------------------

class TestDeleteCamera:

    def test_delete_success(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)

        service.delete_camera(camera_id, tenant_id)
        repo.soft_delete.assert_called_once_with(camera_id)

    def test_delete_not_found_raises(self):
        service, repo = _make_service()
        repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            service.delete_camera(uuid4(), uuid4())

    def test_delete_wrong_user_raises(self):
        # Cross-tenant → 404, nunca 403 (C-01 — não vazar existência).
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        with pytest.raises(NotFoundError):
            service.delete_camera(camera_id, uuid4())

    def test_delete_same_tenant_ok(self):
        # Regressão: a comparação era tenant_id != user_id, então o dono
        # legítimo não-admin recebia "Sem permissão" — o erro relatado.
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        service.delete_camera(camera_id, tenant_id)
        repo.soft_delete.assert_called_once_with(camera_id)

    def test_archive_nao_exclui(self):
        # Arquivar e excluir são ações DIFERENTES: arquivar é o UPDATE
        # reversível de is_active; excluir marca `deleted_at` e tira a câmera
        # de todas as telas para sempre. Confundir as duas é o defeito que
        # esta tela veio consertar — arquivar não pode chamar soft_delete.
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        service.archive_camera(camera_id, tenant_id)
        repo.set_active.assert_called_once_with(camera_id, is_active=False)
        repo.soft_delete.assert_not_called()

    def test_delete_nao_apaga_a_linha(self):
        # A trava do requisito: excluir NUNCA pode virar DELETE físico — ele
        # levaria alerta e evidência junto por CASCADE (registro histórico,
        # possível valor legal) e travaria por FK em câmera com frame de
        # treino. Só `soft_delete`.
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        service.delete_camera(camera_id, tenant_id)
        repo.soft_delete.assert_called_once_with(camera_id)
        # `delete` não existe mais no repositório. MagicMock aceita qualquer
        # chamada calada, então a trava é olhar o que foi de fato chamado.
        assert "delete" not in {c[0] for c in repo.method_calls}

    def test_archive_cross_tenant_404(self):
        service, repo = _make_service()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=uuid4())
        with pytest.raises(NotFoundError):
            service.archive_camera(camera_id, uuid4())
        repo.set_active.assert_not_called()

    def test_restore_reativa(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        service.restore_camera(camera_id, tenant_id)
        repo.set_active.assert_called_once_with(camera_id, is_active=True)

    def test_delete_admin_override(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        service.delete_camera(camera_id, uuid4(), is_admin=True)
        repo.soft_delete.assert_called_once_with(camera_id)


# ---------------------------------------------------------------------------
# record_test_result
# ---------------------------------------------------------------------------

class TestRecordTestResult:

    def test_record_success(self):
        service, repo = _make_service()
        camera_id = uuid4()
        service.record_test_result(camera_id, None)
        repo.update_last_tested.assert_called_once_with(camera_id, None)

    def test_record_with_error_message(self):
        service, repo = _make_service()
        camera_id = uuid4()
        service.record_test_result(camera_id, "Connection refused")
        repo.update_last_tested.assert_called_once_with(camera_id, "Connection refused")

    def test_record_swallows_exception(self):
        service, repo = _make_service()
        repo.update_last_tested.side_effect = Exception("DB unavailable")
        # Should not raise — best-effort
        service.record_test_result(uuid4(), None)


# ---------------------------------------------------------------------------
# task-067 — live_view_subtype (independent from detection `subtype`)
# ---------------------------------------------------------------------------

class TestCreateCameraLiveViewSubtypeDefault:
    """create_camera defaults live_view_subtype=1 (substream) — independent of subtype."""

    def test_default_live_view_subtype_is_1(self):
        service, repo = _make_service()
        repo.create.return_value = {"id": uuid4(), "name": "Cam"}
        service.create_camera(uuid4(), {"name": "Cam", "host": "10.0.0.1"})
        payload = repo.create.call_args[0][0]
        assert payload["live_view_subtype"] == 1
        # subtype (detection/recording) keeps its own independent default — untouched.
        assert payload["subtype"] == 0

    def test_explicit_live_view_subtype_respected(self):
        service, repo = _make_service()
        repo.create.return_value = {"id": uuid4(), "name": "Cam"}
        service.create_camera(
            uuid4(), {"name": "Cam", "host": "10.0.0.1", "live_view_subtype": 0}
        )
        payload = repo.create.call_args[0][0]
        assert payload["live_view_subtype"] == 0


class TestUpdateCameraLiveViewSubtype:
    """update_camera accepts live_view_subtype as an updatable field."""

    def test_update_live_view_subtype(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(camera_id=camera_id, tenant_id=tenant_id)
        repo.update.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id, live_view_subtype=0
        )
        service.update_camera(camera_id, tenant_id, {"live_view_subtype": 0})
        update_payload = repo.update.call_args[0][1]
        assert update_payload["live_view_subtype"] == 0


class TestResolveLiveViewSubtype:
    """Static helper: live_view_subtype takes priority; None falls back to subtype."""

    def test_prefers_live_view_subtype(self):
        service, _repo = _make_service()
        camera = {"subtype": 0, "live_view_subtype": 1}
        assert service._resolve_live_view_subtype(camera) == 1

    def test_falls_back_to_subtype_when_none(self):
        """Legacy row / old mock without live_view_subtype — defensive fallback."""
        service, _repo = _make_service()
        camera = {"subtype": 0, "live_view_subtype": None}
        assert service._resolve_live_view_subtype(camera) == 0

    def test_falls_back_to_subtype_default_when_key_absent(self):
        service, _repo = _make_service()
        camera = {}
        assert service._resolve_live_view_subtype(camera) == 0


class TestBuildStreamUrlForLiveView:
    """build_stream_url(for_live_view=True) uses live_view_subtype, not raw subtype.

    Connectivity test (/api/cameras/test) calls build_stream_url WITHOUT
    for_live_view — must keep testing the operator-configured subtype
    (regression guard against task-067 accidentally changing that behavior).
    """

    def test_for_live_view_uses_live_view_subtype_intelbras(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            manufacturer="intelbras", subtype=0, live_view_subtype=1,
        )
        url = service.build_stream_url(camera_id, tenant_id, for_live_view=True)
        assert "subtype=1" in url

    def test_without_for_live_view_uses_raw_subtype_intelbras(self):
        """Regression guard: connectivity test path must be unaffected by task-067."""
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            manufacturer="intelbras", subtype=0, live_view_subtype=1,
        )
        url = service.build_stream_url(camera_id, tenant_id)
        assert "subtype=0" in url

    def test_subtype_override_wins_over_for_live_view(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            manufacturer="intelbras", subtype=0, live_view_subtype=1,
        )
        url = service.build_stream_url(
            camera_id, tenant_id, for_live_view=True, subtype_override=0
        )
        assert "subtype=0" in url

    def test_for_live_view_hikvision_http_uses_live_view_subtype(self):
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            manufacturer="hikvision", port=8080, subtype=0, live_view_subtype=1,
        )
        url = service.build_stream_url(camera_id, tenant_id, for_live_view=True)
        # subtype 1 -> stream_id "10{1+1}" = "102"
        assert "channels/102/" in url

    def test_rtsp_url_override_ignores_for_live_view(self):
        """rtsp_url_override is a fixed URL — for_live_view must not alter it."""
        service, repo = _make_service()
        tenant_id = uuid4()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=tenant_id,
            rtsp_url_override="rtsp://override/stream",
        )
        url = service.build_stream_url(camera_id, tenant_id, for_live_view=True)
        assert url == "rtsp://override/stream"


class TestBuildStreamUrlForLazyStart:
    """serve_hls's real entry point — always resolves the live-view subtype."""

    def test_defaults_to_live_view_subtype(self):
        service, repo = _make_service()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, manufacturer="intelbras", subtype=0, live_view_subtype=1,
        )
        url = service.build_stream_url_for_lazy_start(camera_id)
        assert "subtype=1" in url

    def test_falls_back_to_raw_subtype_when_live_view_subtype_none(self):
        """Defensive fallback for rows predating migration 092."""
        service, repo = _make_service()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, manufacturer="intelbras", subtype=0, live_view_subtype=None,
        )
        url = service.build_stream_url_for_lazy_start(camera_id)
        assert "subtype=0" in url

    def test_subtype_override_builds_main_stream_url_cheaply(self):
        """subtype_override=0 computes the fallback URL without touching live_view_subtype."""
        service, repo = _make_service()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, manufacturer="intelbras", subtype=0, live_view_subtype=1,
        )
        url = service.build_stream_url_for_lazy_start(camera_id, subtype_override=0)
        assert "subtype=0" in url

    def test_no_ownership_check(self):
        """Intentionally unauthenticated — no AuthorizationError regardless of tenant."""
        service, repo = _make_service()
        camera_id = uuid4()
        repo.get_by_id.return_value = _cam(
            camera_id=camera_id, tenant_id=uuid4(), manufacturer="intelbras",
            subtype=0, live_view_subtype=1,
        )
        # Should not raise even though no user_id/tenant match is performed.
        url = service.build_stream_url_for_lazy_start(camera_id)
        assert url.startswith("rtsp://")

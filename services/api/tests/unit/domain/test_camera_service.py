"""Tests: CameraService."""
import pytest
from unittest.mock import MagicMock
from uuid import uuid4

from app.core.exceptions import (
    NotFoundError,
    ValidationError,
)
from app.domain.services.camera_service import CameraService


class TestCameraService:
    """Testes para CameraService."""

    def setup_method(self) -> None:
        self.camera_repo = MagicMock()
        # Generate a valid Fernet key for tests
        from cryptography.fernet import Fernet
        self.fernet_key = Fernet.generate_key().decode()
        self.service = CameraService(self.camera_repo, self.fernet_key)

    def test_create_camera_success(self) -> None:
        uid = uuid4()
        cam_id = uuid4()
        self.camera_repo.create.return_value = {
            "id": cam_id, "name": "Camera 1",
            "host": "192.168.1.100", "port": 554,
        }
        result = self.service.create_camera(uid, {
            "name": "Camera 1",
            "host": "192.168.1.100",
        })
        assert result["name"] == "Camera 1"
        assert result["id"] == str(cam_id)

    def test_create_camera_persists_site_id(self) -> None:
        """Sem site_id a câmera nasce ÓRFÃ de site e fica invisível pro edge:
        config_poll (list_for_site_config), sum_fps_demand, stream_info e
        live-view/wanted filtram todos por site_id. A coluna era lida em
        vários caminhos mas nenhuma rota sabia gravá-la."""
        site_id = uuid4()
        self.camera_repo.create.return_value = {"id": uuid4(), "name": "Cam"}

        self.service.create_camera(uuid4(), {
            "name": "Cam", "host": "192.168.1.100", "site_id": str(site_id),
        })

        assert self.camera_repo.create.call_args.args[0]["site_id"] == str(site_id)

    def test_create_camera_without_site_id_is_none(self) -> None:
        self.camera_repo.create.return_value = {"id": uuid4(), "name": "Cam"}
        self.service.create_camera(uuid4(), {"name": "Cam", "host": "10.0.0.1"})
        assert self.camera_repo.create.call_args.args[0]["site_id"] is None

    def test_update_camera_can_set_site_id(self) -> None:
        """Permite associar câmera já cadastrada a um site sem recriá-la."""
        tenant = uuid4()
        cam_id = uuid4()
        site_id = uuid4()
        self.camera_repo.get_by_id.return_value = {
            "id": cam_id, "tenant_id": str(tenant), "name": "Cam",
        }
        self.camera_repo.update.return_value = {"id": cam_id, "name": "Cam"}

        self.service.update_camera(cam_id, tenant, {"site_id": str(site_id)})

        assert self.camera_repo.update.call_args.args[1]["site_id"] == str(site_id)

    def test_create_camera_missing_name(self) -> None:
        with pytest.raises(ValidationError, match="obrigatórios"):
            self.service.create_camera(uuid4(), {"host": "192.168.1.1"})

    def test_create_camera_missing_host(self) -> None:
        with pytest.raises(ValidationError, match="obrigatórios"):
            self.service.create_camera(uuid4(), {"name": "Cam"})

    def test_create_camera_with_password(self) -> None:
        uid = uuid4()
        self.camera_repo.create.return_value = {
            "id": uuid4(), "name": "Cam",
        }
        self.service.create_camera(uid, {
            "name": "Cam", "host": "10.0.0.1", "password": "secret123",
        })
        call_args = self.camera_repo.create.call_args[0][0]
        assert "password_encrypted" in call_args
        assert call_args["password_encrypted"] != "secret123"

    def test_list_cameras_operator(self) -> None:
        uid = uuid4()
        self.camera_repo.get_by_user.return_value = [
            {"id": uuid4(), "name": "Cam 1"},
        ]
        result = self.service.list_cameras(uid, is_admin=False)
        assert len(result) == 1
        self.camera_repo.get_by_user.assert_called_once()

    def test_list_cameras_admin(self) -> None:
        self.camera_repo.get_all.return_value = [
            {"id": uuid4(), "name": "Cam 1"},
            {"id": uuid4(), "name": "Cam 2"},
        ]
        result = self.service.list_cameras(uuid4(), is_admin=True)
        assert len(result) == 2
        self.camera_repo.get_all.assert_called_once()

    def test_get_camera_success(self) -> None:
        cam_id = uuid4()
        self.camera_repo.get_by_id.return_value = {
            "id": cam_id, "name": "Cam",
            "password_encrypted": "should-be-removed",
        }
        result = self.service.get_camera(cam_id)
        assert "password_encrypted" not in result

    def test_get_camera_not_found(self) -> None:
        self.camera_repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            self.service.get_camera(uuid4())

    def test_delete_camera_success(self) -> None:
        uid = uuid4()
        cam_id = uuid4()
        self.camera_repo.get_by_id.return_value = {
            "id": cam_id, "tenant_id": uid,
        }
        self.service.delete_camera(cam_id, uid)
        self.camera_repo.soft_delete.assert_called_once()

    def test_delete_camera_wrong_user(self) -> None:
        # Cross-tenant responde 404, nunca 403 (C-01 — não vazar existência).
        # Antes a comparação era camera["tenant_id"] != user_id, dois
        # identificadores de entidades diferentes: negava SEMPRE para
        # não-admin, e era esse o erro ao tentar remover câmera.
        cam_id = uuid4()
        self.camera_repo.get_by_id.return_value = {
            "id": cam_id, "tenant_id": uuid4(),
        }
        with pytest.raises(NotFoundError):
            self.service.delete_camera(cam_id, uuid4())

    def test_delete_camera_same_tenant_ok(self) -> None:
        # O caso que ANTES falhava: dono legítimo, não-admin, mesmo tenant.
        cam_id, tenant_id = uuid4(), uuid4()
        self.camera_repo.get_by_id.return_value = {
            "id": cam_id, "tenant_id": tenant_id,
        }
        self.service.delete_camera(cam_id, tenant_id)
        self.camera_repo.soft_delete.assert_called_once_with(cam_id)

    def test_delete_camera_admin_override(self) -> None:
        cam_id = uuid4()
        self.camera_repo.get_by_id.return_value = {
            "id": cam_id, "tenant_id": uuid4(),
        }
        self.service.delete_camera(cam_id, uuid4(), is_admin=True)
        self.camera_repo.soft_delete.assert_called_once()

    def test_delete_camera_not_found(self) -> None:
        self.camera_repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            self.service.delete_camera(uuid4(), uuid4())

    def test_no_fernet_with_password_raises(self) -> None:
        svc = CameraService(self.camera_repo, "")
        with pytest.raises(ValidationError, match="CAMERA_SECRET_KEY"):
            svc.create_camera(uuid4(), {"name": "Cam", "host": "10.0.0.1", "password": "pw"})

    def test_encrypt_decrypt_roundtrip(self) -> None:
        secret = "my-rtsp-password"
        encrypted = self.service._encrypt_password(secret)
        assert encrypted != secret
        decrypted = self.service._decrypt_password(encrypted)
        assert decrypted == secret

    def test_decrypt_empty_returns_empty(self) -> None:
        assert self.service._decrypt_password("") == ""

    def test_decrypt_invalid_returns_empty(self) -> None:
        assert self.service._decrypt_password("not-valid-fernet") == ""

    def test_decrypt_no_fernet_returns_empty(self) -> None:
        svc = CameraService(self.camera_repo, "")
        assert svc._decrypt_password("anything") == ""

    def test_build_rtsp_url_with_override(self) -> None:
        cam_id = uuid4()
        uid = uuid4()
        self.camera_repo.get_by_id.return_value = {
            "id": cam_id, "tenant_id": uid,
            "rtsp_url_override": "rtsp://admin:pass@192.168.1.1:554/stream",
            "password_encrypted": None,
        }
        url = self.service.build_rtsp_url(cam_id, uid)
        assert url == "rtsp://admin:pass@192.168.1.1:554/stream"

    def test_build_rtsp_url_generated(self) -> None:
        cam_id = uuid4()
        uid = uuid4()
        encrypted = self.service._encrypt_password("secret")
        self.camera_repo.get_by_id.return_value = {
            "id": cam_id, "tenant_id": uid,
            "rtsp_url_override": None,
            "password_encrypted": encrypted,
            "username": "admin",
            "host": "192.168.1.100",
            "port": 554,
            "channel": 1,
            "subtype": 0,
        }
        from unittest.mock import patch
        with patch("app.domain.services.camera_service.RTSPUrlValidator.validate",
                   side_effect=lambda url: url):
            url = self.service.build_rtsp_url(cam_id, uid)
        assert "192.168.1.100" in url
        assert url.startswith("rtsp://")

    def test_build_rtsp_url_not_found(self) -> None:
        self.camera_repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            self.service.build_rtsp_url(uuid4(), uuid4())

    def test_build_rtsp_url_wrong_user_raises(self) -> None:
        cam_id = uuid4()
        owner = uuid4()
        other = uuid4()
        self.camera_repo.get_by_id.return_value = {
            "id": cam_id, "tenant_id": owner,
            "rtsp_url_override": "rtsp://admin:pass@10.0.0.1:554/s",
        }
        with pytest.raises(NotFoundError):
            self.service.build_rtsp_url(cam_id, other)

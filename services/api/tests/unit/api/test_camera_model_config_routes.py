"""
Testes — WS-C2: model-config por câmera (registry-level, geometria).

Cobre:
  GET  /api/cameras/<id>/model-config
  POST /api/cameras/<id>/model-config
  GET  /api/cameras/<id>/model-config/history
  POST /api/cameras/<id>/model-config/rollback

Estratégia: repositórios e Redis mockados — testes de camada de rota,
sem banco real (padrão de test_camera_model_routes.py).
"""
import uuid
from unittest.mock import MagicMock

import pytest
from flask_jwt_extended import create_access_token

import app.api.v1.cameras.model_config_handlers as handlers

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
CAMERA_ID = "44444444-4444-4444-4444-444444444444"
MODEL_ID = "55555555-5555-5555-5555-555555555555"
DEPLOYMENT_ID = "88888888-8888-8888-8888-888888888888"


def _token(app, role: str = "superadmin", tenant_id: str = TENANT) -> dict[str, str]:
    with app.app_context():
        token = create_access_token(
            identity=str(uuid.uuid4()),
            additional_claims={
                "tenant_id": tenant_id,
                "tenant_schema": "tenant_test",
                "role": role,
                "modules": ["epi"],
            },
        )
    return {"Authorization": f"Bearer {token}"}


def _camera_row(**overrides):
    row = {"id": CAMERA_ID, "tenant_id": TENANT, "active_module": "epi"}
    row.update(overrides)
    return row


def _model_row(**overrides):
    row = {"id": MODEL_ID, "tenant_id": TENANT, "name": "rfdetr_v1"}
    row.update(overrides)
    return row


def _deployment_row(**overrides):
    row = {
        "id": DEPLOYMENT_ID,
        "tenant_id": TENANT,
        "model_id": MODEL_ID,
        "camera_id": CAMERA_ID,
        "module_code": "epi",
        "config": {"classes": ["helmet"], "thresholds": {"helmet": 0.6}},
        "status": "active",
    }
    row.update(overrides)
    return row


def _valid_config():
    return {
        "roi": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]],
        "classes": ["helmet", "no_helmet"],
        "thresholds": {"helmet": 0.6},
    }


@pytest.fixture()
def mocked_repos(monkeypatch):
    camera_repo = MagicMock()
    deployment_repo = MagicMock()
    registry_repo = MagicMock()
    monkeypatch.setattr(handlers, "_get_camera_repo", lambda: camera_repo)
    monkeypatch.setattr(handlers, "_get_deployment_repo", lambda: deployment_repo)
    monkeypatch.setattr(handlers, "_get_registry_repo", lambda: registry_repo)
    monkeypatch.setattr(handlers, "_notify_model_change", MagicMock())
    # Escopo de módulo (migration 134). Default True = escopo NÃO declarado,
    # o estado de todo tenant no dia do deploy — a tela segue como hoje.
    # Quem quiser o caso "câmera fora do módulo" re-patcha (ver
    # TestModuleScope abaixo).
    monkeypatch.setattr(
        handlers, "_get_camera_module_repo", lambda: _module_repo(True)
    )
    camera_repo.get_by_id_and_tenant.return_value = _camera_row()
    registry_repo.get_for_tenant.return_value = _model_row()
    return camera_repo, deployment_repo, registry_repo


def _module_repo(serves: bool) -> MagicMock:
    repo = MagicMock()
    repo.camera_serves_module.return_value = serves
    return repo


# ---------------------------------------------------------------------------
# GET /api/cameras/<id>/model-config
# ---------------------------------------------------------------------------

class TestGetModelConfig:
    def test_no_auth_returns_401(self, client, mocked_repos):
        resp = client.get(f"/api/cameras/{CAMERA_ID}/model-config")
        assert resp.status_code == 401

    def test_camera_not_found_returns_404(self, app, client, mocked_repos):
        camera_repo, *_ = mocked_repos
        camera_repo.get_by_id_and_tenant.return_value = None
        resp = client.get(f"/api/cameras/{CAMERA_ID}/model-config", headers=_token(app))
        assert resp.status_code == 404

    def test_returns_active_deployment(self, app, client, mocked_repos):
        _, deployment_repo, _ = mocked_repos
        deployment_repo.get_active_for_camera.return_value = _deployment_row()
        resp = client.get(f"/api/cameras/{CAMERA_ID}/model-config", headers=_token(app))
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["deployment"]["id"] == DEPLOYMENT_ID

    def test_no_active_deployment_returns_null(self, app, client, mocked_repos):
        _, deployment_repo, _ = mocked_repos
        deployment_repo.get_active_for_camera.return_value = None
        resp = client.get(f"/api/cameras/{CAMERA_ID}/model-config", headers=_token(app))
        assert resp.status_code == 200
        assert resp.get_json()["data"]["deployment"] is None


# ---------------------------------------------------------------------------
# POST /api/cameras/<id>/model-config
# ---------------------------------------------------------------------------

class TestPostModelConfig:
    def test_no_auth_returns_401(self, client, mocked_repos):
        resp = client.post(f"/api/cameras/{CAMERA_ID}/model-config", json={})
        assert resp.status_code == 401

    def test_role_without_permission_returns_403(self, app, client, mocked_repos, monkeypatch):
        import app.core.auth as core_auth
        monkeypatch.setattr(core_auth, "_has_training_override", lambda *a, **k: False)
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            headers=_token(app, role="operator"),
            json={"model_id": MODEL_ID, "config": _valid_config()},
        )
        assert resp.status_code == 403

    def test_camera_not_found_returns_404(self, app, client, mocked_repos):
        camera_repo, *_ = mocked_repos
        camera_repo.get_by_id_and_tenant.return_value = None
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            headers=_token(app),
            json={"model_id": MODEL_ID, "config": _valid_config()},
        )
        assert resp.status_code == 404

    def test_missing_model_id_returns_400(self, app, client, mocked_repos):
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            headers=_token(app),
            json={"config": _valid_config()},
        )
        assert resp.status_code == 400

    def test_invalid_model_id_uuid_returns_400(self, app, client, mocked_repos):
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            headers=_token(app),
            json={"model_id": "not-a-uuid", "config": _valid_config()},
        )
        assert resp.status_code == 400

    def test_model_from_other_tenant_returns_404(self, app, client, mocked_repos):
        _, _, registry_repo = mocked_repos
        registry_repo.get_for_tenant.return_value = None
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            headers=_token(app),
            json={"model_id": MODEL_ID, "config": _valid_config()},
        )
        assert resp.status_code == 404

    def test_invalid_geometry_returns_400(self, app, client, mocked_repos):
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            headers=_token(app),
            json={"model_id": MODEL_ID, "config": {"roi": [[0.1, 0.1]], "classes": ["helmet"]}},
        )
        assert resp.status_code == 400

    def test_creates_deployment_and_deactivates_prior(self, app, client, mocked_repos):
        _, deployment_repo, _ = mocked_repos
        deployment_repo.create.return_value = _deployment_row()

        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            headers=_token(app),
            json={"model_id": MODEL_ID, "config": _valid_config()},
        )
        assert resp.status_code == 201
        data = resp.get_json()["data"]
        assert data["deployment"]["id"] == DEPLOYMENT_ID

        deployment_repo.deactivate_active_for_camera.assert_called_once()
        create_kwargs = deployment_repo.create.call_args.args[0]
        assert create_kwargs["model_id"] == MODEL_ID
        assert create_kwargs["module_code"] == "epi"
        assert create_kwargs["config"] == _valid_config()

    def test_notifies_redis_on_success(self, app, client, mocked_repos):
        _, deployment_repo, _ = mocked_repos
        deployment_repo.create.return_value = _deployment_row()
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            headers=_token(app),
            json={"model_id": MODEL_ID, "config": _valid_config()},
        )
        assert resp.status_code == 201
        handlers._notify_model_change.assert_called_once_with(CAMERA_ID)


# ---------------------------------------------------------------------------
# GET /api/cameras/<id>/model-config/history
# ---------------------------------------------------------------------------

class TestGetModelConfigHistory:
    def test_no_auth_returns_401(self, client, mocked_repos):
        resp = client.get(f"/api/cameras/{CAMERA_ID}/model-config/history")
        assert resp.status_code == 401

    def test_camera_not_found_returns_404(self, app, client, mocked_repos):
        camera_repo, *_ = mocked_repos
        camera_repo.get_by_id_and_tenant.return_value = None
        resp = client.get(
            f"/api/cameras/{CAMERA_ID}/model-config/history", headers=_token(app)
        )
        assert resp.status_code == 404

    def test_returns_history_list(self, app, client, mocked_repos):
        _, deployment_repo, _ = mocked_repos
        deployment_repo.list_for_camera.return_value = [
            _deployment_row(status="inactive"), _deployment_row(status="active"),
        ]
        resp = client.get(
            f"/api/cameras/{CAMERA_ID}/model-config/history", headers=_token(app)
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total"] == 2


# ---------------------------------------------------------------------------
# POST /api/cameras/<id>/model-config/rollback
# ---------------------------------------------------------------------------

class TestPostModelConfigRollback:
    def test_no_auth_returns_401(self, client, mocked_repos):
        resp = client.post(f"/api/cameras/{CAMERA_ID}/model-config/rollback", json={})
        assert resp.status_code == 401

    def test_role_without_permission_returns_403(self, app, client, mocked_repos, monkeypatch):
        import app.core.auth as core_auth
        monkeypatch.setattr(core_auth, "_has_training_override", lambda *a, **k: False)
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config/rollback",
            headers=_token(app, role="operator"),
            json={"deployment_id": DEPLOYMENT_ID},
        )
        assert resp.status_code == 403

    def test_missing_deployment_id_returns_400(self, app, client, mocked_repos):
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config/rollback",
            headers=_token(app), json={},
        )
        assert resp.status_code == 400

    def test_deployment_not_found_returns_404(self, app, client, mocked_repos):
        _, deployment_repo, _ = mocked_repos
        deployment_repo.get_by_id.return_value = None
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config/rollback",
            headers=_token(app), json={"deployment_id": DEPLOYMENT_ID},
        )
        assert resp.status_code == 404

    def test_deployment_from_other_camera_returns_404(self, app, client, mocked_repos):
        _, deployment_repo, _ = mocked_repos
        deployment_repo.get_by_id.return_value = _deployment_row(
            camera_id=str(uuid.uuid4())
        )
        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config/rollback",
            headers=_token(app), json={"deployment_id": DEPLOYMENT_ID},
        )
        assert resp.status_code == 404

    def test_rollback_creates_new_deployment_with_same_config(self, app, client, mocked_repos):
        _, deployment_repo, _ = mocked_repos
        old_config = {"classes": ["helmet"], "thresholds": {"helmet": 0.6}}
        deployment_repo.get_by_id.return_value = _deployment_row(config=old_config)
        deployment_repo.create.return_value = _deployment_row(
            id="99999999-9999-9999-9999-999999999999", config=old_config,
        )

        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config/rollback",
            headers=_token(app), json={"deployment_id": DEPLOYMENT_ID},
        )
        assert resp.status_code == 201
        deployment_repo.deactivate_active_for_camera.assert_called_once()
        create_kwargs = deployment_repo.create.call_args.args[0]
        assert create_kwargs["model_id"] == MODEL_ID
        assert create_kwargs["config"] == old_config


# ---------------------------------------------------------------------------
# _serialize — created_at sempre com offset (browser não adivinha UTC)
# ---------------------------------------------------------------------------

class TestSerializeDatetime:
    def test_naive_datetime_is_emitted_as_utc(self):
        from datetime import datetime, timezone
        out = handlers._serialize({"created_at": datetime(2026, 8, 21, 10, 0, 0)})
        assert out["created_at"] == "2026-08-21T10:00:00+00:00"
        aware = datetime(2026, 8, 21, 10, 0, 0, tzinfo=timezone.utc)
        assert handlers._serialize({"created_at": aware})["created_at"].endswith("+00:00")


# ---------------------------------------------------------------------------
# Escopo de módulo no DEPLOYMENT de modelo (public.camera_modules, migration 134)
# ---------------------------------------------------------------------------

class TestModuleScope:
    """RÉGUA — apontar modelo para câmera fora do módulo é RECUSADO ALTO.

    Escrita deliberada de humano. Ignorar em silêncio seria o pior desfecho:
    a tela diria "salvo", a inferência nunca usaria o deployment, e o dono
    ficaria esperando detecção de uma câmera que não está no módulo.
    """

    def test_camera_fora_do_modulo_recusa_422_e_nao_grava(
        self, app, client, mocked_repos, monkeypatch
    ):
        _, deployment_repo, _ = mocked_repos
        monkeypatch.setattr(
            handlers, "_get_camera_module_repo", lambda: _module_repo(False)
        )

        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            headers=_token(app),
            json={"model_id": MODEL_ID, "config": _valid_config()},
        )

        assert resp.status_code == 422
        assert "vinculada ao módulo" in resp.get_json()["error"]
        deployment_repo.create.assert_not_called()
        deployment_repo.deactivate_active_for_camera.assert_not_called()

    def test_escopo_nao_declarado_deixa_gravar(self, app, client, mocked_repos):
        """Sem nenhum vínculo declarado no módulo, a tela continua como hoje."""
        _, deployment_repo, _ = mocked_repos
        deployment_repo.create.return_value = _deployment_row()

        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            headers=_token(app),
            json={"model_id": MODEL_ID, "config": _valid_config()},
        )

        assert resp.status_code == 201
        deployment_repo.create.assert_called_once()


class TestDigestDoModeloParaOEdge:
    """Apontar modelo à câmera enfileira o cálculo do SHA-256 do ONNX.

    Sem esse digest o config/poll do edge OMITE o manifesto (não inventa um) —
    ou seja, sem esta chamada o modelo escolhido no front nunca chegaria ao box.
    """

    def test_deploy_enfileira_digest_do_modelo(
        self, client, app, mocked_repos, monkeypatch
    ):
        camera_repo, deployment_repo, registry_repo = mocked_repos
        camera_repo.get_by_id_and_tenant.return_value = _camera_row()
        registry_repo.get_for_tenant.return_value = _model_row()
        deployment_repo.create.return_value = _deployment_row()
        agendar = MagicMock()
        monkeypatch.setattr(handlers, "_agendar_digest_do_modelo", agendar)

        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config",
            json={"model_id": MODEL_ID, "config": _valid_config()},
            headers=_token(app),
        )

        assert resp.status_code == 201
        agendar.assert_called_once_with(MODEL_ID)

    def test_rollback_enfileira_digest_do_modelo(
        self, client, app, mocked_repos, monkeypatch
    ):
        camera_repo, deployment_repo, registry_repo = mocked_repos
        camera_repo.get_by_id_and_tenant.return_value = _camera_row()
        deployment_repo.get_by_id.return_value = _deployment_row()
        deployment_repo.create.return_value = _deployment_row()
        agendar = MagicMock()
        monkeypatch.setattr(handlers, "_agendar_digest_do_modelo", agendar)

        resp = client.post(
            f"/api/cameras/{CAMERA_ID}/model-config/rollback",
            json={"deployment_id": DEPLOYMENT_ID},
            headers=_token(app),
        )

        assert resp.status_code == 201
        agendar.assert_called_once_with(MODEL_ID)

    def test_broker_fora_do_ar_nao_derruba_o_deploy(self, monkeypatch):
        """Best-effort como `_notify_model_change`: a fila cair não pode
        impedir alguém de apontar um modelo a uma câmera."""
        import app.infrastructure.queue.tasks.model_validation as mv
        monkeypatch.setattr(
            mv.record_onnx_digest, "delay",
            MagicMock(side_effect=RuntimeError("broker off")),
        )
        handlers._agendar_digest_do_modelo(MODEL_ID)  # não levanta

"""
Testes — WS-A5: Registry/Models API + task validate_onnx.

Cobre:
  GET  /api/v1/models                (lista, filtros module/status)
  GET  /api/v1/models/<id>           (linhagem expandida + deployments ativos)
  POST /api/v1/models/<id>/activate  (gate de eval, force só admin, pin best-effort)
  GET  /api/v1/models/<id>/eval      (última avaliação)
  GET  /api/v1/models/<id>/drift     (janelas de drift)
  tasks.model_validation.validate_onnx (skip gracioso, sucesso, falha)

Estratégia: repositórios e storage são mockados — testes unitários de
camada de rota/task, sem banco real (padrão de tests/unit/api/*).
"""
import datetime
import hashlib
import sys
import uuid
from unittest.mock import MagicMock

import pytest
from flask_jwt_extended import create_access_token

import app.api.v1.models.registry_handlers as registry_handlers
import app.infrastructure.queue.tasks.model_validation as model_validation

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
MODEL_ID = "55555555-5555-5555-5555-555555555555"
JOB_ID = "66666666-6666-6666-6666-666666666666"
DSV_ID = "77777777-7777-7777-7777-777777777777"


def _token(app, role: str = "admin", tenant_id: str = TENANT) -> dict[str, str]:
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


def _model_row(**overrides) -> dict:
    row = {
        "id": MODEL_ID,
        "user_id": "33333333-3333-3333-3333-333333333333",
        "job_id": JOB_ID,
        "name": "rfdetr_v1",
        "model_path": "models/tenant/rfdetr_v1.pth",
        "map50": 0.82,
        "precision": 0.80,
        "recall": 0.75,
        "is_active": False,
        "created_at": datetime.datetime(2026, 7, 1, 12, 0, 0),
        "created_by": None,
        "origin": "vast_ai",
        "tenant_id": TENANT,
        "framework": "rfdetr",
        "r2_onnx_key": "models/tenant/rfdetr_v1.onnx",
        "r2_weights_key": "models/tenant/rfdetr_v1.pth",
        "metrics": {"map50": 0.82},
        "dataset_version_id": DSV_ID,
        "module_code": "epi",
    }
    row.update(overrides)
    return row


class Repos:
    """Container simples dos mocks de repositório."""

    def __init__(self):
        self.registry = MagicMock()
        self.deployments = MagicMock()
        self.evals = MagicMock()
        self.drift = MagicMock()
        self.datasets = MagicMock()
        self.training = MagicMock()
        self.rollout = MagicMock()


@pytest.fixture()
def repos(monkeypatch):
    r = Repos()
    monkeypatch.setattr(registry_handlers, "_get_registry_repo", lambda: r.registry)
    monkeypatch.setattr(registry_handlers, "_get_deployment_repo", lambda: r.deployments)
    monkeypatch.setattr(registry_handlers, "_get_eval_repo", lambda: r.evals)
    monkeypatch.setattr(registry_handlers, "_get_drift_repo", lambda: r.drift)
    monkeypatch.setattr(registry_handlers, "_get_dataset_repo", lambda: r.datasets)
    monkeypatch.setattr(registry_handlers, "_get_training_repo", lambda: r.training)
    monkeypatch.setattr(registry_handlers, "_get_rollout_repo", lambda: r.rollout)
    # Defaults inofensivos
    r.evals.get_latest_for_model.return_value = None
    r.deployments.list_by_model.return_value = []
    r.rollout.pin_model.return_value = ({"id": MODEL_ID}, None)
    return r


# ---------------------------------------------------------------------------
# GET /api/v1/models
# ---------------------------------------------------------------------------

class TestListModels:
    def test_no_auth_returns_401(self, client, repos):
        resp = client.get("/api/v1/models")
        assert resp.status_code == 401

    def test_lists_models_for_tenant(self, app, client, repos):
        repos.registry.list_for_tenant.return_value = [_model_row()]

        resp = client.get("/api/v1/models", headers=_token(app))
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total"] == 1
        model = data["models"][0]
        assert model["id"] == MODEL_ID
        assert model["framework"] == "rfdetr"
        assert model["created_at"] == "2026-07-01T12:00:00"

        repos.registry.list_for_tenant.assert_called_once_with(
            TENANT, module_code=None, is_active=None
        )

    def test_module_and_status_ativo_filters(self, app, client, repos):
        repos.registry.list_for_tenant.return_value = []

        resp = client.get(
            "/api/v1/models?module=epi&status=ativo", headers=_token(app)
        )
        assert resp.status_code == 200
        repos.registry.list_for_tenant.assert_called_once_with(
            TENANT, module_code="epi", is_active=True
        )

    def test_status_inativo_filter(self, app, client, repos):
        repos.registry.list_for_tenant.return_value = []

        resp = client.get("/api/v1/models?status=inativo", headers=_token(app))
        assert resp.status_code == 200
        repos.registry.list_for_tenant.assert_called_once_with(
            TENANT, module_code=None, is_active=False
        )

    def test_invalid_status_returns_400(self, app, client, repos):
        resp = client.get("/api/v1/models?status=banana", headers=_token(app))
        assert resp.status_code == 400
        repos.registry.list_for_tenant.assert_not_called()


# ---------------------------------------------------------------------------
# GET /api/v1/models/<id>
# ---------------------------------------------------------------------------

class TestGetModel:
    def test_invalid_uuid_returns_400(self, app, client, repos):
        resp = client.get("/api/v1/models/not-a-uuid", headers=_token(app))
        assert resp.status_code == 400

    def test_not_found_returns_404(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = None
        resp = client.get(f"/api/v1/models/{MODEL_ID}", headers=_token(app))
        assert resp.status_code == 404

    def test_returns_expanded_lineage(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.training.get_job_by_id.return_value = {
            "id": JOB_ID,
            "status": "completed",
            "tenant_id": TENANT,
            "dataset_version_id": DSV_ID,
            "callback_token": "SEGREDO-NUNCA-EXPOR",
        }
        repos.datasets.get_by_id.return_value = {
            "id": DSV_ID,
            "version": "v1",
            "tenant_id": TENANT,
            "status": "ready",
        }
        repos.deployments.list_by_model.return_value = [
            {"id": str(uuid.uuid4()), "status": "active", "camera_id": "c1"},
            {"id": str(uuid.uuid4()), "status": "inactive", "camera_id": "c2"},
        ]

        resp = client.get(f"/api/v1/models/{MODEL_ID}", headers=_token(app))
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["model"]["id"] == MODEL_ID
        assert data["lineage"]["job"]["id"] == JOB_ID
        # callback_token NUNCA sai na API (ajuste #5)
        assert "callback_token" not in data["lineage"]["job"]
        assert data["lineage"]["dataset_version"]["id"] == DSV_ID
        assert len(data["active_deployments"]) == 1
        assert data["active_deployments"][0]["status"] == "active"

    def test_cross_tenant_lineage_is_filtered(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.training.get_job_by_id.return_value = {
            "id": JOB_ID,
            "tenant_id": OTHER_TENANT,
        }
        repos.datasets.get_by_id.return_value = {
            "id": DSV_ID,
            "tenant_id": OTHER_TENANT,
        }

        resp = client.get(f"/api/v1/models/{MODEL_ID}", headers=_token(app))
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["lineage"]["job"] is None
        assert data["lineage"]["dataset_version"] is None


# ---------------------------------------------------------------------------
# POST /api/v1/models/<id>/activate
# ---------------------------------------------------------------------------

class TestActivateModel:
    def test_no_auth_returns_401(self, client, repos):
        resp = client.post(f"/api/v1/models/{MODEL_ID}/activate", json={})
        assert resp.status_code == 401

    def test_role_without_permission_returns_403(
        self, app, client, repos, monkeypatch
    ):
        import app.core.auth as core_auth
        monkeypatch.setattr(
            core_auth, "_has_training_override", lambda *a, **k: False
        )
        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/activate",
            headers=_token(app, role="operator"),
            json={},
        )
        assert resp.status_code == 403
        repos.registry.activate_for_tenant_module.assert_not_called()

    def test_admin_activates_without_eval(self, app, client, repos):
        """training:approve é exclusivo de superadmin no registry canônico —
        activate exercitado com superadmin (admin puro cobre-se em
        test_role_without_permission_returns_403 + test_auth_training_roles.py)."""
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.evals.get_latest_for_model.return_value = None
        repos.registry.activate_for_tenant_module.return_value = _model_row(
            is_active=True
        )

        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/activate",
            headers=_token(app, role="superadmin"), json={},
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["model"]["is_active"] is True
        assert data["rollout_synced"] is True

        args = repos.registry.activate_for_tenant_module.call_args[0]
        assert str(args[0]) == MODEL_ID
        assert args[1] == TENANT
        assert args[2] == "epi"
        repos.rollout.pin_model.assert_called_once_with(
            "tenant_test", MODEL_ID, "epi"
        )

    def test_eval_reject_without_force_returns_409(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.evals.get_latest_for_model.return_value = {"verdict": "reject"}

        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/activate",
            headers=_token(app, role="superadmin"), json={},
        )
        assert resp.status_code == 409
        assert resp.get_json()["error_code"] == "eval_rejected"
        repos.registry.activate_for_tenant_module.assert_not_called()

    def test_eval_reject_with_force_admin_activates(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.evals.get_latest_for_model.return_value = {"verdict": "reject"}
        repos.registry.activate_for_tenant_module.return_value = _model_row(
            is_active=True
        )

        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/activate",
            headers=_token(app, role="superadmin"),
            json={"force": True},
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["forced"] is True

    def test_eval_reject_force_non_admin_returns_403(
        self, app, client, repos, monkeypatch
    ):
        # trainer com override training:approve passa o decorator, mas o
        # override do gate de eval (force=true) é exclusivo de admin+
        import app.core.auth as core_auth
        monkeypatch.setattr(
            core_auth, "_has_training_override", lambda *a, **k: True
        )
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.evals.get_latest_for_model.return_value = {"verdict": "reject"}

        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/activate",
            headers=_token(app, role="trainer"),
            json={"force": True},
        )
        assert resp.status_code == 403
        repos.registry.activate_for_tenant_module.assert_not_called()

    def test_pin_failure_is_best_effort(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.registry.activate_for_tenant_module.return_value = _model_row(
            is_active=True
        )
        repos.rollout.pin_model.side_effect = RuntimeError("schema ausente")

        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/activate",
            headers=_token(app, role="superadmin"), json={},
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["rollout_synced"] is False

    def test_model_not_found_returns_404(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = None
        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/activate",
            headers=_token(app, role="superadmin"), json={},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/models/<id>/eval
# ---------------------------------------------------------------------------

class TestGetModelEval:
    def test_model_not_found_returns_404(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = None
        resp = client.get(f"/api/v1/models/{MODEL_ID}/eval", headers=_token(app))
        assert resp.status_code == 404

    def test_no_evaluation_returns_404(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.evals.get_latest_for_model.return_value = None
        resp = client.get(f"/api/v1/models/{MODEL_ID}/eval", headers=_token(app))
        assert resp.status_code == 404

    def test_returns_latest_evaluation(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.evals.get_latest_for_model.return_value = {
            "id": str(uuid.uuid4()),
            "model_id": MODEL_ID,
            "verdict": "promote",
            "metrics": {"map50": 0.85},
            "created_at": datetime.datetime(2026, 7, 2, 8, 0, 0),
        }

        resp = client.get(f"/api/v1/models/{MODEL_ID}/eval", headers=_token(app))
        assert resp.status_code == 200
        evaluation = resp.get_json()["data"]["evaluation"]
        assert evaluation["verdict"] == "promote"
        assert evaluation["created_at"] == "2026-07-02T08:00:00"


# ---------------------------------------------------------------------------
# POST /api/v1/models/<id>/evaluate (WS-C1)
# ---------------------------------------------------------------------------

class TestEvaluateModel:
    def test_no_auth_returns_401(self, client, repos):
        resp = client.post(f"/api/v1/models/{MODEL_ID}/evaluate", json={})
        assert resp.status_code == 401

    def test_role_without_permission_returns_403(self, app, client, repos, monkeypatch):
        import app.core.auth as core_auth
        monkeypatch.setattr(core_auth, "_has_training_override", lambda *a, **k: False)
        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/evaluate",
            headers=_token(app, role="operator"), json={},
        )
        assert resp.status_code == 403

    def test_model_not_found_returns_404(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = None
        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/evaluate",
            headers=_token(app, role="superadmin"), json={},
        )
        assert resp.status_code == 404

    def test_queues_task_returns_202(self, app, client, repos, monkeypatch):
        repos.registry.get_for_tenant.return_value = _model_row()
        mock_task = MagicMock()
        mock_task.id = "task-eval-123"
        monkeypatch.setattr(
            "app.infrastructure.queue.tasks.model_evaluation.evaluate_challenger_model.delay",
            MagicMock(return_value=mock_task),
        )
        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/evaluate",
            headers=_token(app, role="superadmin"), json={},
        )
        assert resp.status_code == 202
        data = resp.get_json()["data"]
        assert data["task_id"] == "task-eval-123"
        assert data["status"] == "queued"

    def test_passes_dataset_version_id_override(self, app, client, repos, monkeypatch):
        repos.registry.get_for_tenant.return_value = _model_row()
        mock_task = MagicMock()
        mock_task.id = "task-eval-456"
        mock_delay = MagicMock(return_value=mock_task)
        monkeypatch.setattr(
            "app.infrastructure.queue.tasks.model_evaluation.evaluate_challenger_model.delay",
            mock_delay,
        )
        resp = client.post(
            f"/api/v1/models/{MODEL_ID}/evaluate",
            headers=_token(app, role="superadmin"),
            json={"dataset_version_id": DSV_ID},
        )
        assert resp.status_code == 202
        mock_delay.assert_called_once_with(MODEL_ID, DSV_ID)


# ---------------------------------------------------------------------------
# GET /api/v1/models/<id>/drift
# ---------------------------------------------------------------------------

class TestGetModelDrift:
    def test_model_not_found_returns_404(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = None
        resp = client.get(f"/api/v1/models/{MODEL_ID}/drift", headers=_token(app))
        assert resp.status_code == 404

    def test_returns_drift_windows(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.drift.list_by_model.return_value = [
            {"id": str(uuid.uuid4()), "drift_score": 0.12, "detections_count": 40}
        ]

        resp = client.get(f"/api/v1/models/{MODEL_ID}/drift", headers=_token(app))
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["windows"][0]["drift_score"] == 0.12

        args, kwargs = repos.drift.list_by_model.call_args
        assert kwargs.get("limit") == 30

    def test_limit_param_is_clamped(self, app, client, repos):
        repos.registry.get_for_tenant.return_value = _model_row()
        repos.drift.list_by_model.return_value = []

        resp = client.get(
            f"/api/v1/models/{MODEL_ID}/drift?limit=9999", headers=_token(app)
        )
        assert resp.status_code == 200
        _, kwargs = repos.drift.list_by_model.call_args
        assert kwargs.get("limit") == 365

    def test_invalid_limit_returns_400(self, app, client, repos):
        resp = client.get(
            f"/api/v1/models/{MODEL_ID}/drift?limit=abc", headers=_token(app)
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Task validate_onnx (tasks.model_validation.validate_onnx)
# ---------------------------------------------------------------------------

class TestValidateOnnxTask:
    @pytest.fixture()
    def task_repo(self, monkeypatch):
        repo = MagicMock()
        monkeypatch.setattr(model_validation, "_get_registry_repo", lambda: repo)
        return repo

    @pytest.fixture()
    def task_storage(self, monkeypatch):
        storage = MagicMock()
        get_storage = MagicMock(return_value=storage)
        monkeypatch.setattr(model_validation, "_get_storage", get_storage)
        return storage

    def _fake_ort(self, monkeypatch, run_result=None, shape=None):
        fake_ort = MagicMock()
        session = fake_ort.InferenceSession.return_value
        first_input = MagicMock()
        first_input.name = "images"
        first_input.shape = shape if shape is not None else ["batch", 3, 640, 640]
        first_input.type = "tensor(float)"
        session.get_inputs.return_value = [first_input]
        session.run.return_value = run_result if run_result is not None else [object()]
        monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
        return fake_ort, session

    def test_skipped_when_onnxruntime_unavailable(
        self, monkeypatch, task_repo, task_storage
    ):
        # sys.modules[nome] = None faz `import onnxruntime` levantar ImportError
        monkeypatch.setitem(sys.modules, "onnxruntime", None)

        result = model_validation.validate_onnx.apply(args=(MODEL_ID,)).get()
        assert result["status"] == "skipped"
        assert result["reason"] == "onnxruntime_unavailable"
        task_repo.mark_validation.assert_not_called()

    def test_model_not_found_returns_error(
        self, monkeypatch, task_repo, task_storage
    ):
        self._fake_ort(monkeypatch)
        task_repo.get_by_id.return_value = None

        result = model_validation.validate_onnx.apply(args=(MODEL_ID,)).get()
        assert result["status"] == "error"
        task_repo.mark_validation.assert_not_called()

    def test_missing_onnx_key_marks_invalid(
        self, monkeypatch, task_repo, task_storage
    ):
        self._fake_ort(monkeypatch)
        task_repo.get_by_id.return_value = {"id": MODEL_ID, "r2_onnx_key": None}

        result = model_validation.validate_onnx.apply(args=(MODEL_ID,)).get()
        assert result["status"] == "failed"
        args, kwargs = task_repo.mark_validation.call_args
        assert args[0] == MODEL_ID
        assert args[1] is False

    def test_success_marks_validated_true(
        self, monkeypatch, task_repo, task_storage
    ):
        _, session = self._fake_ort(monkeypatch)
        tenant_id = "11111111-1111-1111-1111-111111111111"
        task_repo.get_by_id.return_value = {
            "id": MODEL_ID,
            "r2_onnx_key": "models/x.onnx",
            "tenant_id": tenant_id,
        }
        task_storage.download_bytes.return_value = b"fake-onnx-bytes"

        result = model_validation.validate_onnx.apply(args=(MODEL_ID,)).get()
        assert result["status"] == "completed"
        assert result["validated"] is True
        # dims dinâmicas resolvidas: batch→1, espaciais mantidas
        assert result["input_shape"] == [1, 3, 640, 640]
        task_repo.mark_validation.assert_called_once_with(MODEL_ID, True)
        session.run.assert_called_once()
        task_storage.download_bytes.assert_called_once_with("models/x.onnx")
        # get_storage recebe o tenant_id do model (fix R2/integrações —
        # download do ONNX deve resolver credenciais tenant-aware, não só env)
        model_validation._get_storage.assert_called_once_with(tenant_id)

    def test_failure_marks_validated_false_with_error(
        self, monkeypatch, task_repo, task_storage
    ):
        self._fake_ort(monkeypatch)
        task_repo.get_by_id.return_value = {
            "id": MODEL_ID,
            "r2_onnx_key": "models/x.onnx",
        }
        task_storage.download_bytes.side_effect = RuntimeError("R2 fora do ar")

        result = model_validation.validate_onnx.apply(args=(MODEL_ID,)).get()
        assert result["status"] == "failed"
        assert result["validated"] is False
        args, kwargs = task_repo.mark_validation.call_args
        assert args[0] == MODEL_ID
        assert args[1] is False
        assert "R2 fora do ar" in kwargs.get("error", "")

    def test_dummy_input_shape_helper(self):
        assert model_validation._dummy_input_shape(["batch", 3, 640, 640]) == [
            1, 3, 640, 640,
        ]
        assert model_validation._dummy_input_shape([1, 3, 320, 320]) == [
            1, 3, 320, 320,
        ]
        assert model_validation._dummy_input_shape([None, 3, None, None]) == [
            1, 3, 640, 640,
        ]
        assert model_validation._dummy_input_shape(None) == [1, 3, 640, 640]


# ---------------------------------------------------------------------------
# Task record_onnx_digest — a ÚNICA fonte de `metrics.onnx_sha256`, que é o
# que destrava o manifesto de modelo no config/poll do edge.
# ---------------------------------------------------------------------------

class TestRecordOnnxDigestTask:
    @pytest.fixture()
    def task_repo(self, monkeypatch):
        repo = MagicMock()
        monkeypatch.setattr(model_validation, "_get_registry_repo", lambda: repo)
        return repo

    @pytest.fixture()
    def task_storage(self, monkeypatch):
        storage = MagicMock()
        monkeypatch.setattr(
            model_validation, "_get_storage", MagicMock(return_value=storage)
        )
        return storage

    def test_grava_sha256_real_do_artefato(self, task_repo, task_storage):
        conteudo = b"onnx-de-verdade"
        task_repo.get_by_id.return_value = {
            "id": MODEL_ID, "r2_onnx_key": "models/x.onnx", "metrics": {},
            "tenant_id": "11111111-1111-1111-1111-111111111111",
        }
        task_storage.download_bytes.return_value = conteudo

        resultado = model_validation.record_onnx_digest.apply(args=(MODEL_ID,)).get()

        esperado = hashlib.sha256(conteudo).hexdigest()
        assert resultado["status"] == "completed"
        assert resultado["sha256"] == esperado
        task_repo.merge_metrics.assert_called_once_with(
            MODEL_ID, {"onnx_sha256": esperado, "onnx_bytes": len(conteudo)}
        )

    def test_idempotente_nao_rebaixa_artefato(self, task_repo, task_storage):
        task_repo.get_by_id.return_value = {
            "id": MODEL_ID, "r2_onnx_key": "models/x.onnx",
            "metrics": {"onnx_sha256": "f" * 64},
        }

        resultado = model_validation.record_onnx_digest.apply(args=(MODEL_ID,)).get()

        assert resultado["status"] == "skipped"
        task_storage.download_bytes.assert_not_called()
        task_repo.merge_metrics.assert_not_called()

    def test_sem_artefato_nao_grava_digest(self, task_repo, task_storage):
        task_repo.get_by_id.return_value = {
            "id": MODEL_ID, "r2_onnx_key": None, "metrics": {},
        }

        resultado = model_validation.record_onnx_digest.apply(args=(MODEL_ID,)).get()

        assert resultado["status"] == "failed"
        assert resultado["reason"] == "missing_onnx_key"
        task_repo.merge_metrics.assert_not_called()

    def test_falha_de_download_nao_grava_digest_falso(self, task_repo, task_storage):
        """Nunca gravar um digest que não veio dos bytes — seria pior que nenhum."""
        task_repo.get_by_id.return_value = {
            "id": MODEL_ID, "r2_onnx_key": "models/x.onnx", "metrics": {},
        }
        task_storage.download_bytes.side_effect = RuntimeError("R2 fora do ar")

        resultado = model_validation.record_onnx_digest.apply(args=(MODEL_ID,)).get()

        assert resultado["status"] == "failed"
        task_repo.merge_metrics.assert_not_called()

    def test_task_registrada_no_include_do_celery(self):
        """Sem estar no `include`, a task existe no código e não existe no worker
        — foi o caso de `validate_onnx` até esta rodada."""
        from app.infrastructure.queue.celery_app import celery
        assert (
            "app.infrastructure.queue.tasks.model_validation"
            in celery.conf.include
        )

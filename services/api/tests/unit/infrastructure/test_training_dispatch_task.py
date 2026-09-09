"""
Tests: tasks/training.py — dispatch_training (regressão do merge develop×
staging Cluster C / PR-1) + task "treino não pode mentir" + runner genérico
RunPod (substitui Vast.ai).

Cobre:
- dispatch_training delega inteiramente a get_training_compute(tenant_id)
  (precedência runpod/edge/erro testada em test_training_compute.py) — aqui só
  se testa que o resultado do compute é usado corretamente.
- "Treino não pode mentir": nenhum resultado vira 'completed'/INSERT em
  trained_models sem verify_model_artifact confirmar o artefato no storage.
- INSERT em trained_models propaga created_by/origin/tenant_id (migration 090)
  + framework/r2_onnx_key/dataset_version_id (migration 098, task-086)
- origin lido de result['source'] top-level ('runpod' | fallback 'unknown')
- toda conclusão bem-sucedida dispara evaluate_challenger_model (não há mais
  origin='simulated' a pular — _simulate_training foi deletado)
- job_handlers.get_current_job_status_handler: gpu_enabled aceita
  RUNPOD_API_KEY (substitui VAST_API_KEY/VAST_AI_API_KEY); NÃO aceita mais
  ULTRALYTICS_HUB_API_KEY (Hub foi deletado)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

# Garante import REAL de celery_app/training mesmo que outro arquivo de teste
# tenha deixado um stub de celery_app em sys.modules (padrão pré-existente de
# tests/unit/domain/test_versioning_helpers.py). Stubs via types.ModuleType
# não têm __file__ — é o marcador para evictar e reimportar o real.
_CELERY_APP_KEY = "app.infrastructure.queue.celery_app"
_TRAINING_KEY = "app.infrastructure.queue.tasks.training"
_loaded = sys.modules.get(_CELERY_APP_KEY)
if _loaded is not None and getattr(_loaded, "__file__", None) is None:
    for _key in (_TRAINING_KEY, _CELERY_APP_KEY):
        sys.modules.pop(_key, None)

from app.infrastructure.queue.tasks import training as training_mod  # noqa: E402

_JOB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_DSV_ID = "11111111-2222-3333-4444-555555555555"
_REAL_TENANT = "99999999-8888-7777-6666-555555555555"

_DEFAULT_RESULT = {
    "model_path": f"models/{_REAL_TENANT}/runpod/{_JOB_ID}/model.onnx",
    "metrics": {"mAP50": 0.5, "precision": 0.6, "recall": 0.4},
    "source": "runpod",
}


def _run_dispatch(
    monkeypatch,
    dispatch_result: dict | None = None,
    existing_model: dict | None = None,
    artifact_verified: bool = True,
):
    """Executa dispatch_training com repo/redis/compute mockados.

    `dispatch_result`: o que `compute.dispatch(...)` retorna — dispatch_training
    não sabe mais (nem precisa saber) qual provider produziu isso, a
    precedência runpod/edge/erro é testada isoladamente em
    test_training_compute.py.

    `artifact_verified`: task "treino não pode mentir" — controla o retorno
    de `verify_model_artifact` (o guard novo antes do INSERT em
    trained_models). Default True: a maioria dos testes aqui quer exercitar
    outra coisa (INSERT, guarda de duplicação, avaliação campeão) sem se
    preocupar com o guard de artefato — ele tem sua própria classe de teste
    (TestArtifactVerificationGuard).

    Retorna (repo_mock, mock_compute, result).
    """
    mock_compute = MagicMock()
    mock_compute.dispatch.return_value = dispatch_result or _DEFAULT_RESULT

    with patch.object(training_mod, "DatabasePool"), \
         patch.object(training_mod, "AnnotationRepository") as mock_repo_cls, \
         patch.object(training_mod, "_publish_progress"), \
         patch.object(
             training_mod, "get_training_compute", return_value=mock_compute,
         ), \
         patch.object(
             training_mod, "verify_model_artifact", return_value=artifact_verified,
         ):
        mock_repo_cls.return_value._execute_one.return_value = existing_model
        result = training_mod.dispatch_training(_JOB_ID, _DSV_ID, epochs=5)

    return mock_repo_cls.return_value, mock_compute, result


def _find_insert_call(repo_mock):
    """Localiza a chamada de INSERT INTO trained_models no repo mockado."""
    for call in repo_mock._execute_mutation_no_return.call_args_list:
        sql = call.args[0]
        if "INSERT INTO trained_models" in sql:
            return sql, call.args[1]
    raise AssertionError("INSERT INTO trained_models não foi executado")


class TestDispatchUsesComputeResult:
    """dispatch_training delega a get_training_compute(tenant_id) e usa o
    resultado — a precedência real (runpod/edge/erro) é testada em
    test_training_compute.py, não aqui."""

    def test_result_status_completed_when_compute_succeeds(self, monkeypatch) -> None:
        repo, mock_compute, result = _run_dispatch(monkeypatch)
        assert result["status"] == "completed"
        mock_compute.dispatch.assert_called_once()

    def test_no_provider_available_marks_job_failed(self, monkeypatch) -> None:
        """get_training_compute levantando (nenhum provedor real) propaga
        até dispatch_training marcar o job 'failed' com mensagem clara —
        nunca 'completed' fake."""
        with patch.object(training_mod, "DatabasePool"), \
             patch.object(training_mod, "AnnotationRepository") as mock_repo_cls, \
             patch.object(training_mod, "_publish_progress"), \
             patch.object(
                 training_mod, "get_training_compute",
                 side_effect=RuntimeError("Nenhum provedor de treino real disponível"),
             ):
            mock_repo_cls.return_value._execute_one.return_value = None
            try:
                training_mod.dispatch_training(_JOB_ID, _DSV_ID, epochs=5)
                raise AssertionError("deveria propagar via self.retry")
            except Exception as exc:  # noqa: BLE001
                assert "Nenhum provedor de treino real" in str(exc) or (
                    "Retry" in type(exc).__name__
                )

        failed_calls = [
            c for c in mock_repo_cls.return_value._execute_mutation_no_return.call_args_list
            if "SET status" in c.args[0] and c.args[1][0] == "failed"
        ]
        assert failed_calls, "job deveria ter sido marcado 'failed'"

    def test_async_running_status_returns_early_without_insert(self, monkeypatch) -> None:
        """EdgeProvider retorna status='running' — dispatch_training NÃO
        deve tentar verificar artefato nem inserir trained_models (o job
        continua em andamento, a finalização real depende de um callback)."""
        repo, mock_compute, result = _run_dispatch(
            monkeypatch,
            dispatch_result={"status": "running", "source": "edge"},
        )
        assert result == {"job_id": _JOB_ID, "status": "running", "source": "edge"}
        inserts = [
            c for c in repo._execute_mutation_no_return.call_args_list
            if "INSERT INTO trained_models" in c.args[0]
        ]
        assert inserts == []


class TestArtifactVerificationGuard:
    """Task "treino não pode mentir": nenhum job vira 'completed'/ganha uma
    linha em trained_models sem verify_model_artifact confirmar o artefato
    no storage — mesmo que o compute/provider tenha relatado sucesso."""

    def test_unverified_artifact_marks_job_failed_never_completed(self, monkeypatch) -> None:
        with patch.object(training_mod, "DatabasePool"), \
             patch.object(training_mod, "AnnotationRepository") as mock_repo_cls, \
             patch.object(training_mod, "_publish_progress"), \
             patch.object(
                 training_mod, "get_training_compute",
                 return_value=MagicMock(dispatch=MagicMock(return_value=_DEFAULT_RESULT)),
             ), \
             patch.object(training_mod, "verify_model_artifact", return_value=False):
            mock_repo_cls.return_value._execute_one.return_value = None
            try:
                training_mod.dispatch_training(_JOB_ID, _DSV_ID, epochs=5)
                raise AssertionError("deveria falhar sem artefato verificado")
            except Exception as exc:  # noqa: BLE001
                assert "artefato" in str(exc).lower() or "Retry" in type(exc).__name__

            repo = mock_repo_cls.return_value

        inserts = [
            c for c in repo._execute_mutation_no_return.call_args_list
            if "INSERT INTO trained_models" in c.args[0]
        ]
        assert inserts == [], "nunca deve inserir trained_models sem artefato confirmado"
        failed_calls = [
            c for c in repo._execute_mutation_no_return.call_args_list
            if "SET status" in c.args[0] and c.args[1][0] == "failed"
        ]
        assert failed_calls, "job deveria ter sido marcado 'failed'"

    def test_verified_artifact_proceeds_to_insert(self, monkeypatch) -> None:
        repo, *_ = _run_dispatch(monkeypatch, artifact_verified=True)
        sql, _params = _find_insert_call(repo)
        assert "INSERT INTO trained_models" in sql

    def test_verify_called_with_tenant_and_model_path(self, monkeypatch) -> None:
        with patch.object(training_mod, "DatabasePool"), \
             patch.object(training_mod, "AnnotationRepository") as mock_repo_cls, \
             patch.object(training_mod, "_publish_progress"), \
             patch.object(
                 training_mod, "get_training_compute",
                 return_value=MagicMock(dispatch=MagicMock(return_value=_DEFAULT_RESULT)),
             ), \
             patch.object(
                 training_mod, "verify_model_artifact", return_value=True,
             ) as mock_verify:
            mock_repo_cls.return_value._execute_one.return_value = {
                "tenant_id": _REAL_TENANT,
            }
            training_mod.dispatch_training(_JOB_ID, _DSV_ID, epochs=5)

        mock_verify.assert_called_once_with(_REAL_TENANT, _DEFAULT_RESULT["model_path"])


class TestTrainedModelInsertPropagation:
    """INSERT em trained_models propaga created_by/origin/tenant_id (migration 090)."""

    def test_insert_propagates_lineage_columns(self, monkeypatch) -> None:
        repo, *_ = _run_dispatch(monkeypatch)
        sql, params = _find_insert_call(repo)
        # Colunas de linhagem (090) presentes no INSERT
        assert "created_by, origin, tenant_id" in sql
        # created_by/tenant_id resolvidos do job real (não hardcoded)
        assert "tj.user_id" in sql
        assert "u.tenant_id" in sql
        assert "JOIN users u ON u.id = tj.user_id" in sql
        # model_path do resultado do dispatch
        assert _DEFAULT_RESULT["model_path"] in params

    def test_origin_runpod_read_from_result_source_top_level(self, monkeypatch) -> None:
        repo, *_ = _run_dispatch(monkeypatch)
        _, params = _find_insert_call(repo)
        assert "runpod" in params

    def test_origin_defaults_to_unknown_when_source_missing(self, monkeypatch) -> None:
        repo, *_ = _run_dispatch(
            monkeypatch,
            dispatch_result={
                "model_path": f"models/{_REAL_TENANT}/runpod/{_JOB_ID}/model.onnx",
                "metrics": {},
            },
        )
        _, params = _find_insert_call(repo)
        assert "unknown" in params

    def test_insert_propagates_dataset_version_id(self, monkeypatch) -> None:
        """task-086: dataset_version_id (migration 098) — linhagem completa
        dataset_version → job → modelo."""
        repo, *_ = _run_dispatch(monkeypatch)
        sql, params = _find_insert_call(repo)
        assert "dataset_version_id" in sql
        assert _DSV_ID in params

    def test_insert_propagates_framework_column(self, monkeypatch) -> None:
        """framework (training_jobs.framework, NOT NULL DEFAULT 'rfdetr') é
        selecionado via join — não hardcoded no INSERT."""
        repo, *_ = _run_dispatch(monkeypatch)
        sql, _ = _find_insert_call(repo)
        assert "tj.framework" in sql

    def test_r2_onnx_key_set_for_runpod_origin(self, monkeypatch) -> None:
        """r2_onnx_key só é preenchido quando o artefato é de fato um objeto
        R2 real (source='runpod' — model_path == r2_onnx_key, ver
        runpod_runner.run_runpod_job)."""
        repo, *_ = _run_dispatch(monkeypatch)
        _, params = _find_insert_call(repo)
        assert _DEFAULT_RESULT["model_path"] in params

    def test_r2_onnx_key_none_for_unknown_origin(self, monkeypatch) -> None:
        repo, *_ = _run_dispatch(
            monkeypatch,
            dispatch_result={
                "model_path": f"models/{_REAL_TENANT}/runpod/{_JOB_ID}/model.onnx",
                "metrics": {},
                "source": "unknown",
            },
        )
        _, params = _find_insert_call(repo)
        model_path = f"models/{_REAL_TENANT}/runpod/{_JOB_ID}/model.onnx"
        # model_path aparece 1x (coluna model_path); r2_onnx_key fica None
        # pra qualquer origin != 'runpod' — não deve duplicar o valor.
        assert params.count(model_path) == 1


class TestBuildModelName:
    """_build_model_name: unidade pura — nome honesto por framework (fix
    "nome interno honesto"; era f-string 'YOLO26 {model_size}' hardcoded,
    resto da era Ultralytics Hub, batizando artefatos rfdetr de YOLO26)."""

    def test_rfdetr_never_mentions_yolo26(self) -> None:
        name = training_mod._build_model_name("rfdetr", "yolo26n", _JOB_ID)
        assert name.startswith("RF-DETR"), name
        assert "YOLO26" not in name, name
        # model_size ('yolo26n') é lixo herdado do TrainingService pra
        # frameworks != yolo26 — nunca deve aparecer no nome.
        assert "yolo26n" not in name, name

    def test_yolox_never_mentions_yolo26(self) -> None:
        name = training_mod._build_model_name("yolox", "yolo26n", _JOB_ID)
        assert name.startswith("YOLOX"), name
        assert "YOLO26" not in name, name

    def test_yolo26_keeps_model_size_no_regression(self) -> None:
        name = training_mod._build_model_name("yolo26", "yolo26s", _JOB_ID)
        assert name == f"YOLO26 yolo26s - Job {_JOB_ID[:8]}", name

    def test_unknown_framework_falls_back_to_upper(self) -> None:
        name = training_mod._build_model_name("outro", "yolo26n", _JOB_ID)
        assert name.startswith("OUTRO"), name


class TestBuildDisplayName:
    """_build_display_name: regra de nomenclatura do modelo no nascimento
    (dono do produto, REQUISITO — substitui o alias legado "Logikos V<n> ·
    DD/MM" da task D3): "Logikos <MÓDULO> <escopo> · DD/MM HHhMM". NUNCA
    framework/".py"/job-id/UUID."""

    def test_format_is_modulo_escopo_data_hora(self) -> None:
        when = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
        name = training_mod._build_display_name(
            2, when, module_code="epi", escopo="Completo",
        )
        assert name == "Logikos EPI Completo · 04/09 14h30", name

    def test_format_with_named_subset_scope(self) -> None:
        when = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
        name = training_mod._build_display_name(
            1, when, module_code="epi", escopo="Luvas+Máscara",
        )
        assert name == "Logikos EPI Luvas+Máscara · 04/09 14h30", name

    def test_hour_is_zero_padded_hhhmm(self) -> None:
        when = datetime(2026, 9, 4, 8, 5, tzinfo=timezone.utc)
        name = training_mod._build_display_name(1, when, escopo="Completo")
        assert "08h05" in name, name

    def test_module_code_translated_to_pt_upper(self) -> None:
        when = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
        name = training_mod._build_display_name(
            1, when, module_code="quality", escopo="Completo",
        )
        assert name.startswith("Logikos QUALIDADE "), name

    def test_unknown_module_code_falls_back_to_upper(self) -> None:
        when = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
        name = training_mod._build_display_name(
            1, when, module_code="novomodulo", escopo="Completo",
        )
        assert name.startswith("Logikos NOVOMODULO "), name

    def test_scope_none_uses_honest_fallback_never_completo(self) -> None:
        """escopo=None (informação genuinamente indisponível) não pode virar
        "Completo" por acidente — seria mentir sobre cobertura."""
        when = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
        name = training_mod._build_display_name(1, when, module_code="epi")
        assert "Completo" not in name, name
        assert name == "Logikos EPI Personalizado · 04/09 14h30", name

    def test_defaults_to_now_when_no_date_given(self) -> None:
        name = training_mod._build_display_name(1, escopo="Completo")
        assert name.startswith("Logikos EPI Completo · "), name

    def test_no_jargon_leaks_into_name(self) -> None:
        when = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
        name = training_mod._build_display_name(
            2, when, module_code="epi", escopo="Completo",
        )
        assert "RF-DETR" not in name
        assert "YOLOX" not in name
        assert "YOLO26" not in name
        assert _JOB_ID not in name
        assert ".py" not in name
        assert "V2" not in name  # versão sequencial saiu do texto (regra nova)


class TestDeriveScope:
    """_derive_scope: "Completo" quando as classes treinadas cobrem TODO o
    catálogo ativo do módulo; senão, nomes das classes treinadas juntados
    com "+"; acima do teto, "N classes"."""

    _CATALOGO_EPI = {
        "helmet": "Capacete",
        "vest": "Colete",
        "gloves": "Luvas",
        "mask": "Máscara",
    }

    def test_covers_entire_catalog_is_completo(self) -> None:
        escopo = training_mod._derive_scope(
            list(self._CATALOGO_EPI), self._CATALOGO_EPI,
        )
        assert escopo == "Completo"

    def test_covers_more_than_catalog_is_still_completo(self) -> None:
        """Treinou tudo do módulo + 1 classe custom do tenant — ainda cobre
        o módulo inteiro, então "Completo" continua honesto."""
        escopo = training_mod._derive_scope(
            [*self._CATALOGO_EPI, "capacete_customizado"], self._CATALOGO_EPI,
        )
        assert escopo == "Completo"

    def test_named_subset_uses_display_names_joined(self) -> None:
        escopo = training_mod._derive_scope(["gloves", "mask"], self._CATALOGO_EPI)
        assert escopo == "Luvas+Máscara"

    def test_subset_order_is_deterministic(self) -> None:
        """Mesmo conjunto, ordem de entrada diferente -> mesmo escopo (senão
        dois treinos idênticos nasceriam com nomes diferentes)."""
        a = training_mod._derive_scope(["mask", "gloves"], self._CATALOGO_EPI)
        b = training_mod._derive_scope(["gloves", "mask"], self._CATALOGO_EPI)
        assert a == b == "Luvas+Máscara"

    def test_teto_de_classes_vira_contagem(self) -> None:
        """Acima do teto (_ESCOPO_TETO_CLASSES), nomear cada classe vira
        ilegível — usa "N classes" em vez de um nome quilométrico.

        Catálogo maior que o treinado (5 de 7) — testa o ramo de teto sem
        cair em "Completo".
        """
        treinadas = {"a": "Alfa", "b": "Beta", "c": "Gama", "d": "Delta", "e": "Epsilon"}
        catalogo_maior = {**treinadas, "f": "Fi", "g": "Gi"}
        escopo = training_mod._derive_scope(list(treinadas), catalogo_maior)
        assert escopo == "5 classes", escopo

    def test_class_without_catalog_entry_gets_prettified(self) -> None:
        """Classe custom do tenant sem display_name cadastrado: usa o
        class_name prettificado, nunca o snake_case cru."""
        escopo = training_mod._derive_scope(["no_ear_protection"], {})
        assert escopo == "No Ear Protection"

    def test_empty_trained_classes_uses_honest_fallback(self) -> None:
        escopo = training_mod._derive_scope([], self._CATALOGO_EPI)
        assert escopo == training_mod._ESCOPO_PERSONALIZADO

    def test_empty_catalog_never_claims_completo(self) -> None:
        """Catálogo vazio (lookup falhou ou módulo sem classes cadastradas)
        nunca pode produzir "Completo" — sem o total, não há como provar
        cobertura."""
        escopo = training_mod._derive_scope(["gloves", "mask"], {})
        assert escopo != "Completo"


class TestClassesTreinadas:
    """_classes_treinadas: extrai class_name reais de class_distribution,
    filtrando a chave reservada de versioning_v2.py."""

    def test_filters_reserved_sem_suporte_treino_key(self) -> None:
        dist = {"helmet": 10, "gloves": 5, "__sem_suporte_treino__": ["mask"]}
        classes = training_mod._classes_treinadas(dist)
        assert set(classes) == {"helmet", "gloves"}

    def test_none_or_empty_returns_empty_list(self) -> None:
        assert training_mod._classes_treinadas(None) == []
        assert training_mod._classes_treinadas({}) == []


class TestResolveDisplayNameHumanPrecedence:
    """_resolve_display_name: precedência humano > auto-gerado (regra do
    dono do produto) — um display_name já definido por humano NUNCA é
    sobrescrito pelo auto-gerado."""

    def test_human_display_name_is_preserved(self) -> None:
        resolved = training_mod._resolve_display_name(
            "Câmera 3 — Expedição", "Logikos EPI Completo · 04/09 14h30",
        )
        assert resolved == "Câmera 3 — Expedição"

    def test_auto_generated_used_when_nothing_set_yet(self) -> None:
        resolved = training_mod._resolve_display_name(
            None, "Logikos EPI Completo · 04/09 14h30",
        )
        assert resolved == "Logikos EPI Completo · 04/09 14h30"

    def test_empty_string_treated_as_unset(self) -> None:
        resolved = training_mod._resolve_display_name(
            "", "Logikos EPI Completo · 04/09 14h30",
        )
        assert resolved == "Logikos EPI Completo · 04/09 14h30"


class TestNoInternalJargonInAnyDisplayName:
    """⛔ Nenhum UUID, hash, nome de framework ou ".py" pode aparecer em
    display_name — cobre o gerador de ponta a ponta com dados realistas."""

    def test_full_uuid_never_appears(self) -> None:
        job_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        name = training_mod._build_display_name(
            3, module_code="epi", escopo="Completo",
        )
        assert job_uuid not in name

    def test_framework_names_never_appear(self) -> None:
        name = training_mod._build_display_name(
            3, module_code="epi", escopo="Completo",
        )
        for jargon in ("rfdetr", "RF-DETR", "yolox", "YOLOX", "yolo26", "YOLO26"):
            assert jargon not in name, name

    def test_dot_py_never_appears(self) -> None:
        name = training_mod._build_display_name(
            3, module_code="epi", escopo="Completo",
        )
        assert ".py" not in name


class TestTrainedModelInsertDisplayName:
    """Fail-antes/passa-depois (regra de nomenclatura no nascimento): o
    INSERT em trained_models grava `display_name` no formato
    "Logikos <MÓDULO> <escopo> · DD/MM HHhMM" — nunca o `name` interno
    (framework/job-id), e só quando o tenant é resolvível."""

    # Sentinel — distingue "parâmetro não passado" (default) de "passado
    # como None explicitamente" (simula dataset_version sem linha/lookup
    # falho), já que None é um valor de teste legítimo aqui.
    _NAO_INFORMADO = object()

    def _run(
        self, monkeypatch, existing_count: int = 4, tenant: str | None = _REAL_TENANT,
        dataset_version_row=_NAO_INFORMADO,
        module_classes_rows=_NAO_INFORMADO,
    ):
        dsv_row = (
            {
                "module_code": "epi",
                "class_distribution": {
                    "helmet": 10, "vest": 8, "gloves": 5, "mask": 3,
                },
            }
            if dataset_version_row is self._NAO_INFORMADO
            else dataset_version_row
        )
        catalogo_rows = (
            [
                {"class_name": "helmet", "display_name": "Capacete"},
                {"class_name": "vest", "display_name": "Colete"},
                {"class_name": "gloves", "display_name": "Luvas"},
                {"class_name": "mask", "display_name": "Máscara"},
            ]
            if module_classes_rows is self._NAO_INFORMADO
            else module_classes_rows
        )

        def _execute_one_side_effect(sql, params=()):
            if "SELECT COUNT(*) AS cnt FROM trained_models" in sql:
                return {"cnt": existing_count}
            if "SELECT id FROM trained_models WHERE job_id" in sql:
                return None  # guarda anti-duplicação: nenhum modelo existente
            if "SELECT framework FROM training_jobs" in sql:
                return {"framework": "rfdetr"}
            if "FROM dataset_versions" in sql:
                return dsv_row
            return {"tenant_id": tenant}  # _get_job_tenant_id

        def _execute_side_effect(sql, params=()):
            if "FROM module_classes" in sql:
                return catalogo_rows
            return []

        with patch.object(training_mod, "DatabasePool"), \
             patch.object(training_mod, "AnnotationRepository") as mock_repo_cls, \
             patch.object(training_mod, "_publish_progress"), \
             patch.object(
                 training_mod, "get_training_compute",
                 return_value=MagicMock(dispatch=MagicMock(return_value=_DEFAULT_RESULT)),
             ), \
             patch.object(training_mod, "verify_model_artifact", return_value=True):
            mock_repo_cls.return_value._execute_one.side_effect = _execute_one_side_effect
            mock_repo_cls.return_value._execute.side_effect = _execute_side_effect
            training_mod.dispatch_training(_JOB_ID, _DSV_ID, epochs=5)
            repo = mock_repo_cls.return_value

        return _find_insert_call(repo)

    def test_insert_includes_display_name_column(self, monkeypatch) -> None:
        sql, _ = self._run(monkeypatch)
        assert "name, display_name, model_path" in sql

    def test_display_name_reflects_module_and_completo_scope(self, monkeypatch) -> None:
        """4 classes treinadas == as 4 classes ativas do catálogo -> Completo."""
        _, params = self._run(monkeypatch)
        [display_name] = [
            p for p in params if isinstance(p, str) and p.startswith("Logikos ")
        ]
        assert display_name.startswith("Logikos EPI Completo · "), display_name

    def test_display_name_reflects_named_subset_scope(self, monkeypatch) -> None:
        _, params = self._run(
            monkeypatch,
            dataset_version_row={
                "module_code": "epi",
                "class_distribution": {"gloves": 5, "mask": 3},
            },
        )
        [display_name] = [
            p for p in params if isinstance(p, str) and p.startswith("Logikos ")
        ]
        assert display_name.startswith("Logikos EPI Luvas+Máscara · "), display_name

    def test_display_name_never_leaks_internal_jargon(self, monkeypatch) -> None:
        """Mutação-alvo: reinstalar o nome cru (framework/job-id/".py") no
        display_name reprova este teste."""
        _, params = self._run(monkeypatch, existing_count=0)
        [display_name] = [
            p for p in params if isinstance(p, str) and p.startswith("Logikos ")
        ]
        assert "RF-DETR" not in display_name
        assert "YOLOX" not in display_name
        assert "YOLO26" not in display_name
        assert _JOB_ID not in display_name
        assert ".py" not in display_name

    def test_display_name_stays_none_when_tenant_unresolvable(self, monkeypatch) -> None:
        """Sem tenant resolvível não há como contar "modelo nº quantos" —
        nunca inventa um alias sem dado real (front cai em "Logikos")."""
        _, params = self._run(monkeypatch, tenant=None)
        assert None in params
        assert not any(
            isinstance(p, str) and p.startswith("Logikos ") for p in params
        )

    def test_display_name_falls_back_honestly_when_dataset_version_missing(
        self, monkeypatch,
    ) -> None:
        """dataset_version sem linha (lookup falhou) -> nunca inventa
        "Completo" nem um subconjunto — cai no rótulo honesto."""
        _, params = self._run(monkeypatch, dataset_version_row=None)
        [display_name] = [
            p for p in params if isinstance(p, str) and p.startswith("Logikos ")
        ]
        assert "Completo" not in display_name
        assert "Personalizado" in display_name


class TestTrainedModelInsertNameReflectsRealFramework:
    """Fail-antes/passa-depois (task "nome interno honesto"): o INSERT em
    trained_models grava `name` batendo com o `framework` REAL do job
    (training_jobs.framework via SELECT dedicado), nunca 'YOLO26' fixo.
    """

    def _run_with_framework(self, monkeypatch, framework: str, model_size="yolo26n"):
        def _execute_one_side_effect(sql, params=()):
            if "trained_models" in sql:
                return None  # guarda anti-duplicação: nenhum modelo existente
            if "SELECT framework FROM training_jobs" in sql:
                return {"framework": framework}
            return {"tenant_id": _REAL_TENANT}  # _get_job_tenant_id

        with patch.object(training_mod, "DatabasePool"), \
             patch.object(training_mod, "AnnotationRepository") as mock_repo_cls, \
             patch.object(training_mod, "_publish_progress"), \
             patch.object(
                 training_mod, "get_training_compute",
                 return_value=MagicMock(dispatch=MagicMock(return_value=_DEFAULT_RESULT)),
             ), \
             patch.object(training_mod, "verify_model_artifact", return_value=True):
            mock_repo_cls.return_value._execute_one.side_effect = _execute_one_side_effect
            training_mod.dispatch_training(
                _JOB_ID, _DSV_ID, model_size=model_size, epochs=5,
            )
            repo = mock_repo_cls.return_value

        _, params = _find_insert_call(repo)
        return params

    def test_rfdetr_job_gets_rfdetr_name_never_yolo26(self, monkeypatch) -> None:
        params = self._run_with_framework(monkeypatch, "rfdetr")
        [name] = [p for p in params if isinstance(p, str) and p.startswith(("RF-DETR", "YOLO"))]
        assert name.startswith("RF-DETR"), name
        assert "YOLO26" not in name, name

    def test_yolo26_job_still_gets_yolo26_name(self, monkeypatch) -> None:
        params = self._run_with_framework(monkeypatch, "yolo26", model_size="yolo26m")
        [name] = [p for p in params if isinstance(p, str) and p.startswith(("RF-DETR", "YOLO"))]
        assert name == f"YOLO26 yolo26m - Job {_JOB_ID[:8]}", name


class TestChallengerEvalAlwaysTriggeredOnSuccess:
    """Toda conclusão bem-sucedida dispara avaliação campeão×desafiante — não
    há mais origin='simulated' a pular (_simulate_training foi deletado,
    task "treino não pode mentir"); o artefato já foi confirmado real pelo
    guard de verificação antes de chegar aqui."""

    def test_runpod_origin_triggers_evaluation(self, monkeypatch) -> None:
        with patch(
            "app.infrastructure.queue.tasks.model_evaluation.evaluate_challenger_model"
        ) as mock_eval:
            _run_dispatch(monkeypatch)
        mock_eval.delay.assert_called_once()


class TestRegisterDuplicationGuard:
    """Ajuste vinculante #2: job_id sem UNIQUE — modelo existente pula o INSERT."""

    def test_existing_model_skips_insert(self, monkeypatch) -> None:
        repo, *_ = _run_dispatch(
            monkeypatch, existing_model={"id": "model-ja-registrado"},
        )
        inserts = [
            call for call in repo._execute_mutation_no_return.call_args_list
            if "INSERT INTO trained_models" in call.args[0]
        ]
        assert inserts == []

    def test_guard_queries_by_job_id_before_insert(self, monkeypatch) -> None:
        """A guarda (_execute_one) roda ANTES do INSERT — não necessariamente a
        primeira chamada geral a _execute_one, já que _get_job_tenant_id
        também usa _execute_one antes disso pra saber o tenant do job. Usa
        mock_calls (ordem cronológica real entre métodos diferentes do mesmo
        mock) em vez de assumir índice fixo."""
        repo, *_ = _run_dispatch(monkeypatch)

        def _is_guard_call(call) -> bool:
            return call[0] == "_execute_one" and "trained_models" in call.args[0]

        def _is_insert_call(call) -> bool:
            return (
                call[0] == "_execute_mutation_no_return"
                and "INSERT INTO trained_models" in call.args[0]
            )

        guard_idx = next(i for i, c in enumerate(repo.mock_calls) if _is_guard_call(c))
        insert_idx = next(i for i, c in enumerate(repo.mock_calls) if _is_insert_call(c))
        assert guard_idx < insert_idx, "guarda deve rodar antes do INSERT"


class TestDatasetAusenteFailsLoud:
    """C1/ADR-0017 (task "treino honesto") — dataset ausente (sem
    coco_r2_key resolvível) é erro alto com mensagem clara do que faltou.
    NUNCA desvia pra outro dataset (o fluxo legado que fazia isso,
    `_dispatch_vast_ai_legacy`, foi deletado na task "treino não pode
    mentir")."""

    def test_dataset_ausente_raises_clear_message(self) -> None:
        with patch.object(
                 training_mod, "_third_party_cloud_training_enabled",
                 return_value=True,
             ), \
             patch.object(training_mod, "_get_runpod_training_context", return_value=None), \
             patch.object(training_mod, "resolve_runpod_api_key", return_value="a-key"), \
             pytest.raises(RuntimeError, match="dataset sem exportação COCO"):
            training_mod._dispatch_runpod_train(
                _JOB_ID, "rfdetr", epochs=1, imgsz=640, batch=8,
                update_fn=MagicMock(), tenant_id=_REAL_TENANT,
            )

    def test_no_api_key_resolvable_raises_distinct_message(self) -> None:
        """ctx None por falta de API key (não de dataset) tem mensagem
        distinta e precisa — nunca confunde as duas causas."""
        with patch.object(
                 training_mod, "_third_party_cloud_training_enabled",
                 return_value=True,
             ), \
             patch.object(training_mod, "_get_runpod_training_context", return_value=None), \
             patch.object(training_mod, "resolve_runpod_api_key", return_value=""), \
             pytest.raises(RuntimeError, match="Nenhuma chave RunPod resolvível"):
            training_mod._dispatch_runpod_train(
                _JOB_ID, "rfdetr", epochs=1, imgsz=640, batch=8,
                update_fn=MagicMock(), tenant_id=_REAL_TENANT,
            )


class TestGetJobTenantId:
    def test_returns_tenant_from_job_row(self) -> None:
        tenant = uuid4()
        with patch.object(training_mod, "DatabasePool"), \
             patch.object(training_mod, "AnnotationRepository") as mock_repo_cls:
            mock_repo_cls.return_value._execute_one.return_value = {"tenant_id": tenant}
            assert training_mod._get_job_tenant_id(_JOB_ID) == str(tenant)

    def test_returns_none_when_job_missing(self) -> None:
        with patch.object(training_mod, "DatabasePool"), \
             patch.object(training_mod, "AnnotationRepository") as mock_repo_cls:
            mock_repo_cls.return_value._execute_one.return_value = None
            assert training_mod._get_job_tenant_id(_JOB_ID) is None

    def test_returns_none_on_db_error(self) -> None:
        with patch.object(training_mod, "DatabasePool") as mock_pool:
            mock_pool.get_instance.side_effect = RuntimeError("db down")
            assert training_mod._get_job_tenant_id(_JOB_ID) is None


class TestRunpodOnnxArtifactKey:
    """Chave determinística compartilhada entre dispatch (training.py) e
    reverificação pós-callback (job_handlers.py) — task "treino não pode
    mentir"."""

    def test_deterministic_format(self) -> None:
        key = training_mod.runpod_onnx_artifact_key(_REAL_TENANT, _JOB_ID)
        assert key == f"models/{_REAL_TENANT}/runpod/{_JOB_ID}/model.onnx"


class TestGpuEnabledFlag:
    """job_handlers: gpu_enabled deve aceitar a var que o dispatch realmente
    usa (RUNPOD_API_KEY). ULTRALYTICS_HUB_API_KEY NÃO habilita mais (Hub foi
    deletado); VAST_API_KEY/VAST_AI_API_KEY também não (Vast.ai deletado)."""

    def _call_handler(self, app):
        from app.api.v1.training.job_handlers import get_current_job_status_handler

        with app.test_request_context("/api/training/jobs/current"), \
             patch(
                 "app.api.v1.training.job_handlers.get_current_user_id",
                 return_value=str(uuid4()),
             ), \
             patch("app.api.v1.training.job_handlers.get_training_service") as mock_svc:
            mock_svc.return_value.get_current_running_job.return_value = None
            response, status = get_current_job_status_handler()
        assert status == 200
        return response.get_json()["data"]

    def _clear_gpu_env(self, monkeypatch) -> None:
        for var in (
            "RUNPOD_API_KEY", "VAST_API_KEY", "VAST_AI_API_KEY", "ULTRALYTICS_HUB_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_gpu_enabled_with_runpod_api_key(self, app, monkeypatch) -> None:
        self._clear_gpu_env(monkeypatch)
        monkeypatch.setenv("RUNPOD_API_KEY", "runpod-key")
        data = self._call_handler(app)
        assert data["gpu_enabled"] is True

    def test_gpu_disabled_without_keys(self, app, monkeypatch) -> None:
        self._clear_gpu_env(monkeypatch)
        data = self._call_handler(app)
        assert data["gpu_enabled"] is False

    def test_gpu_disabled_with_only_hub_key(self, app, monkeypatch) -> None:
        """Regressão (task "treino não pode mentir"): Ultralytics Hub foi
        deletado — a env sozinha nunca mais habilita gpu_enabled."""
        self._clear_gpu_env(monkeypatch)
        monkeypatch.setenv("ULTRALYTICS_HUB_API_KEY", "hub-key")
        data = self._call_handler(app)
        assert data["gpu_enabled"] is False

    def test_gpu_disabled_with_only_legacy_vast_keys(self, app, monkeypatch) -> None:
        """Regressão (decisão do dono — RunPod substitui Vast.ai): as vars
        antigas do Vast.ai sozinhas NUNCA mais habilitam gpu_enabled."""
        self._clear_gpu_env(monkeypatch)
        monkeypatch.setenv("VAST_API_KEY", "vast-key")
        monkeypatch.setenv("VAST_AI_API_KEY", "legacy-key")
        data = self._call_handler(app)
        assert data["gpu_enabled"] is False


class TestPrimeiraMetrica:
    """`trained_models.map50` lia uma chave que runner nenhum emite.

    Medido em 02/09: o job 04508616 fechou com mAP EMA 0,4386 e a linha do
    modelo (6ca25ee9) nasceu com map50=0/precision=0/recall=0 — como TODOS os
    modelos deste sistema, porque `metrics.get("mAP50")` é grafia herdada do
    fluxo Ultralytics Hub (deletado) e o RF-DETR reporta `map50`.
    """

    def test_aceita_a_grafia_que_o_runner_emite(self) -> None:
        from app.infrastructure.queue.tasks.training import _primeira_metrica

        assert _primeira_metrica({"map50": 0.562}, "mAP50", "map50") == 0.562

    def test_preferencia_pela_primeira_chave(self) -> None:
        from app.infrastructure.queue.tasks.training import _primeira_metrica

        assert _primeira_metrica({"mAP50": 0.9, "map50": 0.1}, "mAP50", "map50") == 0.9

    def test_sem_metrica_devolve_zero_e_nao_explode(self) -> None:
        from app.infrastructure.queue.tasks.training import _primeira_metrica

        assert _primeira_metrica({}, "mAP50", "map50") == 0.0
        assert _primeira_metrica({"map50": "n/a"}, "mAP50", "map50") == 0.0


class TestRegistroNasceComClasses:
    """O registro do modelo tem de nascer sabendo QUE CLASSES ele detecta.

    Antes disto, `trained_models.metrics` saía do treino só com `map50`
    agregado: no DEV, os 4 modelos mais recentes não registram classe nenhuma,
    e descobrir o que cada um detecta exigiu caçar `dataset_version_id` e ler
    `class_distribution` à mão. Como o NOME mente (`6ca25ee9` = "5 classes",
    todas de PRESENÇA, incapaz de acusar violação), não havia atalho honesto.
    """

    @staticmethod
    def _repo_com_distribuicao(distribuicao):
        """`_execute_one` responde por CONSULTA (o helper padrão devolve o
        mesmo valor pra todas, o que esconderia a leitura nova)."""
        def responder(sql, params=None):
            if "class_distribution" in sql:
                return {"class_distribution": distribuicao}
            if "FROM trained_models" in sql:
                return None  # guard anti-duplicação: nenhum modelo ainda
            if "framework" in sql:
                return {"framework": "rfdetr"}
            return {"cnt": 0}
        return responder

    def _rodar(self, distribuicao):
        mock_compute = MagicMock()
        mock_compute.dispatch.return_value = _DEFAULT_RESULT
        with patch.object(training_mod, "DatabasePool"), \
             patch.object(training_mod, "AnnotationRepository") as mock_repo_cls, \
             patch.object(training_mod, "_publish_progress"), \
             patch.object(training_mod, "get_training_compute", return_value=mock_compute), \
             patch.object(training_mod, "verify_model_artifact", return_value=True):
            mock_repo_cls.return_value._execute_one.side_effect = (
                self._repo_com_distribuicao(distribuicao)
            )
            training_mod.dispatch_training(_JOB_ID, _DSV_ID, epochs=5)
        _, params = _find_insert_call(mock_repo_cls.return_value)
        return json.loads([p for p in params if isinstance(p, str) and p.startswith("{")][0])

    def test_classes_gravadas_no_metrics(self) -> None:
        gravado = self._rodar({"Botas": 531, "Sem Luvas": 951})
        assert gravado["classes"] == ["Botas", "Sem Luvas"]
        assert gravado["classes_origem"] == "dataset_versions.class_distribution"
        # Aditivo: as métricas do provider continuam ali, intactas.
        assert gravado["mAP50"] == 0.5

    def test_chave_reservada_nao_vira_classe(self) -> None:
        """`__sem_suporte_treino__` lista classes EXCLUÍDAS do treino."""
        gravado = self._rodar({"Botas": 531, "__sem_suporte_treino__": ["Sem botas"]})
        assert gravado["classes"] == ["Botas"]

    def test_sem_distribuicao_nao_inventa_campo(self) -> None:
        """Sem fato registrado, o campo simplesmente não existe — nunca uma
        lista vazia que pareça 'modelo sem classes'."""
        gravado = self._rodar({})
        assert "classes" not in gravado
        assert "classes_origem" not in gravado

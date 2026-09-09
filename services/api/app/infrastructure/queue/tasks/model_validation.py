"""
Recognition — Model Validation Task (WS-A5).

Valida artefato ONNX registrado em trained_models: baixa r2_onnx_key do
storage para arquivo temporário, abre onnxruntime.InferenceSession, roda
um dummy input com as dims declaradas pelo modelo e grava o resultado em
trained_models.metrics via ModelRegistryRepository.mark_validation
(validated=true | validated=false + validation_error).

Import de onnxruntime/numpy é LAZY e gracioso: worker sem as libs (image
da API não instala ML) NÃO quebra — task retorna status 'skipped' com
warning e a validação fica pendente (metrics.validated não é gravado).
"""
import hashlib
import logging
import os
import tempfile
from typing import Any

from app.infrastructure.queue.celery_app import celery

logger = logging.getLogger(__name__)

_ERROR_MAX_LEN = 500

# Mapa tipo ONNX → dtype numpy (default float32 para tipos não mapeados)
_ORT_DTYPES = {
    "tensor(float)": "float32",
    "tensor(float16)": "float16",
    "tensor(double)": "float64",
    "tensor(uint8)": "uint8",
    "tensor(int32)": "int32",
    "tensor(int64)": "int64",
}


def _get_registry_repo():  # type: ignore[no-untyped-def]
    from app.infrastructure.database.connection import DatabasePool
    from app.infrastructure.database.repositories.model_registry_repository import (
        ModelRegistryRepository,
    )
    return ModelRegistryRepository(DatabasePool.get_instance())


def _get_storage(tenant_id: str | None = None):  # type: ignore[no-untyped-def]
    from app.infrastructure.storage.local_storage import get_storage
    return get_storage(tenant_id)


def _dummy_input_shape(raw_shape: Any) -> list[int]:
    """Resolve dims dinâmicas ('batch', None, -1) para valores concretos.

    Heurística: dim 0 dinâmica → 1 (batch); demais dinâmicas → 640
    (espacial típico de detectores). Sem shape declarado → [1, 3, 640, 640].
    """
    shape: list[int] = []
    for idx, dim in enumerate(raw_shape or []):
        if isinstance(dim, int) and dim > 0:
            shape.append(dim)
        else:
            shape.append(1 if idx == 0 else 640)
    return shape or [1, 3, 640, 640]


@celery.task(
    bind=True,
    max_retries=2,
    queue="inference",
    name="tasks.model_validation.validate_onnx",
)
def validate_onnx(self, model_id: str) -> dict:
    """Valida o ONNX de um trained_model e marca metrics.validated.

    Retornos:
      {"status": "skipped"}   — onnxruntime indisponível (validação pendente)
      {"status": "error"}     — modelo inexistente no registry
      {"status": "failed"}    — download/sessão/inferência falhou
                                (metrics.validated=false + validation_error)
      {"status": "completed"} — dummy input OK (metrics.validated=true)
    """
    try:
        import numpy as np  # noqa: PLC0415
        import onnxruntime as ort  # noqa: PLC0415
    except ImportError as exc:
        logger.warning(
            "validate_onnx_skipped: onnxruntime/numpy indisponível (%s) — "
            "validação pendente model=%s",
            exc, model_id,
        )
        return {
            "status": "skipped",
            "model_id": model_id,
            "reason": "onnxruntime_unavailable",
        }

    repo = _get_registry_repo()
    model = repo.get_by_id(model_id)
    if model is None:
        logger.error("validate_onnx_model_not_found: model=%s", model_id)
        return {"status": "error", "model_id": model_id, "reason": "model_not_found"}

    onnx_key = model.get("r2_onnx_key")
    if not onnx_key:
        repo.mark_validation(model_id, False, error="r2_onnx_key ausente no registro")
        return {
            "status": "failed",
            "model_id": model_id,
            "validated": False,
            "error": "missing_onnx_key",
        }

    tmp_path = None
    try:
        data = _get_storage(model.get("tenant_id")).download_bytes(onnx_key)
        fd, tmp_path = tempfile.mkstemp(suffix=".onnx")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)

        session = ort.InferenceSession(tmp_path, providers=["CPUExecutionProvider"])
        inputs = session.get_inputs()
        if not inputs:
            raise ValueError("Modelo ONNX sem inputs declarados")

        first = inputs[0]
        shape = _dummy_input_shape(first.shape)
        dtype = _ORT_DTYPES.get(first.type, "float32")
        dummy = np.zeros(shape, dtype=np.dtype(dtype))
        outputs = session.run(None, {first.name: dummy})

        repo.mark_validation(model_id, True)
        logger.info(
            "validate_onnx_ok: model=%s input=%s shape=%s outputs=%d",
            model_id, first.name, shape, len(outputs),
        )
        return {
            "status": "completed",
            "model_id": model_id,
            "validated": True,
            "input_shape": shape,
            "outputs": len(outputs),
        }
    except Exception as exc:
        message = str(exc)[:_ERROR_MAX_LEN]
        logger.error(
            "validate_onnx_failed: model=%s err=%s", model_id, message, exc_info=True
        )
        try:
            repo.mark_validation(model_id, False, error=message)
        except Exception as mark_exc:
            logger.error(
                "validate_onnx_mark_failed: model=%s err=%s", model_id, mark_exc
            )
        return {
            "status": "failed",
            "model_id": model_id,
            "validated": False,
            "error": message,
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError as unlink_exc:
                logger.debug("validate_onnx_tmp_cleanup_failed: %s", unlink_exc)


@celery.task(
    bind=True,
    max_retries=2,
    queue="inference",
    name="tasks.model_validation.record_onnx_digest",
)
def record_onnx_digest(self, model_id: str) -> dict:
    """Grava o SHA-256 REAL do artefato ONNX em trained_models.metrics.

    POR QUE existe: o config/poll do edge só manda o manifesto de modelo ao
    box quando conhece o digest do artefato — `ModelManifest.sha256` é o que o
    agente usa para saber se já tem aquele modelo e para conferir o download.
    O registry não tem coluna de digest e o ONNX é enviado ao R2 pelo PRÓPRIO
    pod da RunPod (presigned PUT), então a API nunca vê os bytes no caminho de
    treino. Esta task é o único ponto onde o digest passa a existir; sem ela o
    manifesto seria omitido para sempre — e inventar um hash de identidade no
    lugar dele plantaria uma armadilha (pareceria digest e reprovaria todo
    download no dia em que alguém o conferisse).

    Idempotente: com `metrics.onnx_sha256` já gravado, não baixa nada.

    ponytail: baixa o ONNX inteiro para hashear. Se algum dia o digest vier do
    próprio treino (o pod já tem os bytes na mão), esta task some.
    """
    repo = _get_registry_repo()
    model = repo.get_by_id(model_id)
    if model is None:
        logger.error("record_onnx_digest_model_not_found: model=%s", model_id)
        return {"status": "error", "model_id": model_id, "reason": "model_not_found"}

    metrics = model.get("metrics") or {}
    if isinstance(metrics, dict) and metrics.get("onnx_sha256"):
        return {
            "status": "skipped",
            "model_id": model_id,
            "reason": "already_recorded",
            "sha256": metrics["onnx_sha256"],
        }

    onnx_key = model.get("r2_onnx_key")
    if not onnx_key:
        logger.warning(
            "record_onnx_digest_sem_artefato: model=%s — r2_onnx_key ausente, "
            "modelo não pode ser propagado ao edge", model_id,
        )
        return {"status": "failed", "model_id": model_id, "reason": "missing_onnx_key"}

    try:
        data = _get_storage(model.get("tenant_id")).download_bytes(onnx_key)
    except Exception as exc:
        logger.error(
            "record_onnx_digest_download_falhou: model=%s key=%s err=%s",
            model_id, onnx_key, exc,
        )
        return {"status": "failed", "model_id": model_id, "reason": "download_failed"}

    sha256 = hashlib.sha256(data).hexdigest()
    repo.merge_metrics(model_id, {"onnx_sha256": sha256, "onnx_bytes": len(data)})
    logger.info(
        "record_onnx_digest_ok: model=%s sha256=%s bytes=%d",
        model_id, sha256[:12], len(data),
    )
    return {"status": "completed", "model_id": model_id, "sha256": sha256}

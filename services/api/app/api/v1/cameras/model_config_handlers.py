"""
Recognition — Model-config por câmera (WS-C2, ADR-0032/0037).

Lado de ESCRITA do registry-level `model_deployments` (migration 100) — o
lado de leitura (cascata de resolução de modelo pra inferência ao vivo) já
existe em `tasks/inference.py::_resolve_camera_model`. Este módulo é
ADITIVO ao Task 045 (`model_handlers.py`, atribuição simples via
`cameras.model_{module}_id`): aqui o deploy carrega histórico completo,
geometria (roi/line), classes habilitadas e thresholds por classe, com
suporte a rollback.

Handlers:
  GET  /api/cameras/<id>/model-config           → deployment ativo (ou null)
  POST /api/cameras/<id>/model-config           → cria deployment (upsert:
                                                   desativa o ativo, cria novo)
  GET  /api/cameras/<id>/model-config/history   → histórico de deployments
  POST /api/cameras/<id>/model-config/rollback  → reativa uma config anterior
                                                   (cria novo deployment com os
                                                   mesmos model_id/config)
"""
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from flask import request
from flask_jwt_extended import jwt_required

from app.core.auth import get_current_user_id, get_tenant_id
from app.core.tenant import require_permission
from app.core.exceptions import EpiMonitorError, NotFoundError, ValidationError
from app.core.responses import error, success
from app.domain.services.geometry_validation import validate_deployment_config
from app.infrastructure.database.connection import DatabasePool
from app.infrastructure.database.repositories.camera_module_repository import (
    CameraModuleRepository,
)
from app.infrastructure.database.repositories.camera_repository import CameraRepository
from app.infrastructure.database.repositories.model_deployment_repository import (
    ModelDeploymentRepository,
)
from app.infrastructure.database.repositories.model_registry_repository import (
    ModelRegistryRepository,
)

from .helpers import _get_redis

logger = logging.getLogger(__name__)

_DEFAULT_MODULE = "epi"


def _get_camera_repo() -> CameraRepository:
    return CameraRepository(DatabasePool.get_instance())


def _get_deployment_repo() -> ModelDeploymentRepository:
    return ModelDeploymentRepository(DatabasePool.get_instance())


def _get_registry_repo() -> ModelRegistryRepository:
    return ModelRegistryRepository(DatabasePool.get_instance())


def _get_camera_module_repo() -> CameraModuleRepository:
    return CameraModuleRepository(DatabasePool.get_instance())


def _serialize(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            # TIMESTAMPTZ chega aware; naive (ex.: fixture/driver sem TZ) é
            # UTC por contrato — senão o browser interpreta como hora local.
            aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            out[key] = aware.isoformat()
        elif isinstance(value, date):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _classes_fora_do_modelo(
    config: dict[str, Any], declaradas: Optional[list[str]]
) -> list[str]:
    """Classes pedidas no escopo que o modelo NÃO declara prever.

    `declaradas is None` devolve `[]` de propósito: modelo sem linhagem de
    dataset não prova que classe nenhuma seja inválida, e recusar aí travaria
    todo tenant que registrou modelo fora do pipeline formal. Ausência de
    medida não é medida de ausência — a recusa só vale onde há o que medir.
    """
    if not declaradas:
        return []
    pedidas = config.get("classes")
    if not isinstance(pedidas, list):
        return []
    conhecidas = set(declaradas)
    # `dict.fromkeys` = ordem de digitação preservada, sem repetir na mensagem.
    return list(dict.fromkeys(
        c for c in pedidas if isinstance(c, str) and c not in conhecidas
    ))


def _notify_model_change(camera_id: str) -> None:
    """Invalida o detector cacheado da câmera (mesmo canal do Task 045 —
    tasks/inference.py assina camera:model_change:{camera_id})."""
    try:
        r = _get_redis()
        r.publish(f"camera:model_change:{camera_id}", "1")
        r.close()
    except Exception as exc:
        logger.warning("model_config_notify_failed: camera=%s err=%s", camera_id, exc)


def _agendar_digest_do_modelo(model_id: str) -> None:
    """Enfileira o cálculo do SHA-256 do ONNX (config/poll do edge depende dele).

    Apontar um modelo a uma câmera é o momento em que o box precisa passar a
    conhecer aquele artefato. O manifesto que o config/poll manda ao edge só
    sai com digest REAL (`trained_models.metrics.onnx_sha256`); sem esta
    chamada o digest nunca existiria e o modelo nunca chegaria ao box.

    Best-effort, como `_notify_model_change`: broker fora do ar não pode
    derrubar o deploy — a task é idempotente e um novo deploy a reenfileira.
    """
    try:
        from app.infrastructure.queue.tasks.model_validation import (  # noqa: PLC0415
            record_onnx_digest,
        )
        record_onnx_digest.delay(str(model_id))
    except Exception as exc:
        logger.warning(
            "model_config_digest_enqueue_failed: model=%s err=%s", model_id, exc
        )


@jwt_required()
def list_camera_model_configs():  # type: ignore[no-untyped-def]
    """GET /api/cameras/model-config?module=epi — todos os deployments ativos.

    Existe porque a versão por câmera não escala: a aba "Modelos por câmera"
    disparava um GET por câmera em `Promise.all` e, com as 28 do RVB, estourava
    o pool de conexões da API — a tela quebrava exatamente no tenant de tamanho
    real (medido no DEV: "connection pool exhausted", com o banco folgado em 5
    conexões de 500). Uma requisição, uma consulta, o mesmo dado.

    Escopado por tenant no WHERE (C-01): não existe caminho para pedir o
    deployment de outro tenant, nem por engano.
    """
    try:
        tenant_id = get_tenant_id()
        module_code = (request.args.get("module") or _DEFAULT_MODULE).strip()
        deployments = _get_deployment_repo().list_active_for_tenant(
            str(tenant_id), module_code
        )
        return success({
            "deployments": {
                str(d["camera_id"]): _serialize(d) for d in deployments
            }
        })
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("list_camera_model_configs_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@jwt_required()
def get_camera_model_config(camera_id: str):  # type: ignore[no-untyped-def]
    """GET /api/cameras/<id>/model-config?module=epi"""
    try:
        tenant_id = get_tenant_id()
        module_code = (request.args.get("module") or _DEFAULT_MODULE).strip()

        if _get_camera_repo().get_by_id_and_tenant(camera_id, tenant_id) is None:
            return error("Câmera não encontrada", 404)

        deployment = _get_deployment_repo().get_active_for_camera(
            tenant_id, uuid.UUID(camera_id), module_code
        )
        return success({"deployment": _serialize(deployment)})
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("get_camera_model_config_error camera=%s: %s", camera_id, exc, exc_info=True)
        return error("Erro interno", 500)


@jwt_required()
@require_permission("cameras:configure")
def post_camera_model_config(camera_id: str):  # type: ignore[no-untyped-def]
    """POST /api/cameras/<id>/model-config

    Body: {model_id, module_code? (default 'epi'), config: {roi?, line?, classes, thresholds?}}

    Upsert: desativa o deployment ativo da câmera+módulo (se houver) e cria
    um novo — histórico completo preservado (nunca UPDATE no lugar).
    """
    try:
        tenant_id = get_tenant_id()
        camera = _get_camera_repo().get_by_id_and_tenant(camera_id, tenant_id)
        if camera is None:
            return error("Câmera não encontrada", 404)

        data = request.get_json(silent=True) or {}
        model_id = data.get("model_id")
        module_code = (data.get("module_code") or _DEFAULT_MODULE).strip()
        config = data.get("config") or {}

        if not model_id:
            raise ValidationError("model_id é obrigatório")
        try:
            model_uuid = uuid.UUID(str(model_id))
        except ValueError as exc:
            raise ValidationError("model_id deve ser um UUID válido") from exc

        if _get_registry_repo().get_for_tenant(model_uuid, tenant_id) is None:
            return error("Modelo não encontrado", 404)

        # ESCOPO DE MÓDULO — recusa ALTO, como a ingestão e ao contrário do
        # pool. Aqui é uma ESCRITA deliberada de um humano apontando um modelo
        # a uma câmera: ignorar em silêncio seria o pior dos mundos — a tela
        # diria "salvo", o deployment não valeria para a inferência, e o dono
        # ficaria esperando detecção de uma câmera que nunca entrou no módulo.
        # Recusa explícita transforma isso numa pergunta respondível.
        if not _get_camera_module_repo().camera_serves_module(
            str(tenant_id), camera_id, module_code
        ):
            return error(
                f"Câmera não está vinculada ao módulo '{module_code}' — "
                "vincule na tela de atribuição de módulos antes de "
                "apontar um modelo",
                422,
            )

        config_errors = validate_deployment_config(config)
        if config_errors:
            return error("Config de geometria inválida: " + "; ".join(config_errors), 400)

        # ESCOPO ⊆ CLASSES DO MODELO — recusa ALTO, pelo mesmo motivo do escopo
        # de módulo logo acima. `validate_deployment_config` só exigia "lista
        # não vazia de strings": dava para gravar "Capacete" no escopo de uma
        # câmera cujo modelo não tem essa classe. A tela então exibia o escopo
        # salvo, `_filtrar_por_escopo` comparava o nome contra detecções que
        # nunca trariam ele, e o resultado era zero alerta de capacete — lido
        # como "não houve violação", que é o pior desfecho possível num
        # produto de segurança. Aceitar em silêncio é afirmar uma capacidade
        # que não existe; recusar transforma em pergunta respondível.
        declaradas = _get_registry_repo().get_declared_classes(model_uuid, tenant_id)
        fora = _classes_fora_do_modelo(config, declaradas)
        if fora:
            return error(
                "Classes fora do que este modelo prevê: "
                + ", ".join(fora)
                + ". O detector nunca emite essas classes — no escopo elas não "
                "gerariam alerta nenhum. Desmarque-as ou aponte um modelo "
                "treinado com elas.",
                422,
            )

        deployment_repo = _get_deployment_repo()
        deployment_repo.deactivate_active_for_camera(
            tenant_id, uuid.UUID(camera_id), module_code
        )
        created = deployment_repo.create({
            "tenant_id": tenant_id,
            "model_id": str(model_uuid),
            "camera_id": camera_id,
            "module_code": module_code,
            "config": config,
            "deployed_by": str(get_current_user_id()),
        })

        _notify_model_change(camera_id)
        _agendar_digest_do_modelo(str(model_uuid))
        logger.info(
            "camera_model_config_deployed: camera=%s module=%s model=%s",
            camera_id, module_code, model_id,
        )
        return success({"deployment": _serialize(created)}, "Deployment criado", 201)
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("post_camera_model_config_error camera=%s: %s", camera_id, exc, exc_info=True)
        return error("Erro interno", 500)


@jwt_required()
def get_camera_model_config_history(camera_id: str):  # type: ignore[no-untyped-def]
    """GET /api/cameras/<id>/model-config/history?module="""
    try:
        tenant_id = get_tenant_id()
        module_code = (request.args.get("module") or "").strip() or None

        if _get_camera_repo().get_by_id_and_tenant(camera_id, tenant_id) is None:
            return error("Câmera não encontrada", 404)

        history = _get_deployment_repo().list_for_camera(
            tenant_id, uuid.UUID(camera_id), module_code=module_code
        )
        return success({"deployments": [_serialize(d) for d in history], "total": len(history)})
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error(
            "get_camera_model_config_history_error camera=%s: %s",
            camera_id, exc, exc_info=True,
        )
        return error("Erro interno", 500)


@jwt_required()
@require_permission("cameras:configure")
def post_camera_model_config_rollback(camera_id: str):  # type: ignore[no-untyped-def]
    """POST /api/cameras/<id>/model-config/rollback

    Body: {deployment_id} — reativa uma config anterior. Implementado como
    um NOVO deployment com o mesmo model_id/config do alvo (preserva o
    histórico completo — nunca reescreve a linha antiga).
    """
    try:
        tenant_id = get_tenant_id()
        if _get_camera_repo().get_by_id_and_tenant(camera_id, tenant_id) is None:
            return error("Câmera não encontrada", 404)

        data = request.get_json(silent=True) or {}
        deployment_id = data.get("deployment_id")
        if not deployment_id:
            raise ValidationError("deployment_id é obrigatório")
        try:
            deployment_uuid = uuid.UUID(str(deployment_id))
        except ValueError as exc:
            raise ValidationError("deployment_id deve ser um UUID válido") from exc

        deployment_repo = _get_deployment_repo()
        target = deployment_repo.get_by_id(deployment_uuid, tenant_id)
        if target is None or str(target.get("camera_id")) != str(camera_id):
            raise NotFoundError("Deployment", str(deployment_id))

        module_code = target.get("module_code") or _DEFAULT_MODULE
        deployment_repo.deactivate_active_for_camera(
            tenant_id, uuid.UUID(camera_id), module_code
        )
        restored = deployment_repo.create({
            "tenant_id": tenant_id,
            "model_id": str(target["model_id"]),
            "camera_id": camera_id,
            "module_code": module_code,
            "config": target.get("config") or {},
            "deployed_by": str(get_current_user_id()),
        })

        _notify_model_change(camera_id)
        _agendar_digest_do_modelo(str(target["model_id"]))
        logger.info(
            "camera_model_config_rolled_back: camera=%s from_deployment=%s",
            camera_id, deployment_id,
        )
        return success({"deployment": _serialize(restored)}, "Rollback aplicado", 201)
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error(
            "post_camera_model_config_rollback_error camera=%s: %s",
            camera_id, exc, exc_info=True,
        )
        return error("Erro interno", 500)

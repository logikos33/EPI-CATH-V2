"""
Recognition — CRUD handlers for camera routes.

Handlers: list_cameras, create_camera, get_camera, update_camera, delete_camera.

Gate de permissão (registry canônico — app/core/permissions.py):
  cameras:write   create / update / archive / restore  (superadmin, admin)
  cameras:delete  delete                               (superadmin, admin)

Antes só havia @jwt_required(): QUALQUER papel do tenant (viewer, operator,
trainer, analyst) cadastrava, editava e APAGAVA câmera — e o DELETE leva
junto alerts/events/operations por CASCADE. As chaves já existiam no registry
e o front (app/epi/Cameras.tsx) já escondia os botões por `cameras:write`;
faltava a fiação no backend, que era quem de fato precisava barrar.
Prova: tests/security/test_lote_p0_permission_gates.py.
"""
import json as _json
import logging
from uuid import UUID

from flask import request
from flask_jwt_extended import jwt_required

from app.core.auth import get_current_user_id, get_tenant_id
from app.core.exceptions import EpiMonitorError
from app.core.responses import success, error
from app.core.tenant import require_permission
from app.domain.services.platform_flags import platform_flag_enabled
from app.infrastructure.database.connection import DatabasePool
from app.infrastructure.database.repositories.camera_repository import CameraRepository
from app.infrastructure.database.repositories.tenant_policy_repository import (
    TenantPolicyRepository,
)

from .helpers import _get_camera_service, _is_admin, _get_redis

logger = logging.getLogger(__name__)

ENFORCE_PLAN_LIMITS_FLAG = "enforce_plan_limits"


def _plan_camera_cap_error(tenant_id: str):  # type: ignore[no-untyped-def]
    """Retorna error(409) se o tenant atingiu o limite de câmeras do plano.

    effective_max = COALESCE(tenants.contract_cameras, plans.max_cameras).
    Fail-open: tenant/plano sem limite conhecido ou erro de leitura → None.
    Só é chamado com a flag 'enforce_plan_limits' ON.
    """
    try:
        pool = DatabasePool.get_instance()
        if pool is None:
            return None
        entitlements = TenantPolicyRepository(pool).get_plan_entitlements(tenant_id)
        if not entitlements:
            return None
        effective_max = entitlements.get("tenant_max_cameras")
        if effective_max is None:
            effective_max = entitlements.get("max_cameras_plan")
        if effective_max is None:
            return None
        current = CameraRepository(pool).count_all(tenant_id)
    except Exception as exc:
        logger.warning("plan_camera_cap_read_error: tenant=%s err=%s", tenant_id, exc)
        return None
    if int(current) >= int(effective_max):
        logger.info(
            "plan_camera_cap_reached: tenant=%s usadas=%s max=%s",
            tenant_id, current, effective_max,
        )
        return error(
            f"Limite de câmeras do plano atingido ({current}/{effective_max}). "
            "Ajuste o plano ou contate o suporte.",
            409,
        )
    return None


@jwt_required()
def list_cameras():  # type: ignore[no-untyped-def]
    """---
    tags: [cameras]
    summary: Listar câmeras do usuário
    security: [{Bearer: []}]
    responses: {200: {description: Lista de câmeras}}
    """
    try:
        user_id = get_current_user_id()
        tenant_id = UUID(get_tenant_id())
        service = _get_camera_service()
        cameras = service.list_cameras(tenant_id, _is_admin(user_id))
        try:
            r = _get_redis()
            gw_raw = r.get("service:gateway:health")
            inf_raw = r.get("service:inference:health")
            gateway_status = _json.loads(gw_raw) if gw_raw else {"status": "offline"}
            inference_status = _json.loads(inf_raw) if inf_raw else {"status": "offline"}
        except Exception:
            gateway_status = {"status": "unavailable"}
            inference_status = {"status": "unavailable"}
        return success({
            "cameras": cameras,
            "gateway_status": gateway_status,
            "inference_status": inference_status,
        })
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("list_cameras_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@jwt_required()
@require_permission("cameras:write")
def create_camera():  # type: ignore[no-untyped-def]
    """---
    tags: [cameras]
    summary: Criar nova câmera IP
    security: [{Bearer: []}]
    parameters:
      - {in: body, name: body, required: true, schema: {required: [name, host],
         properties: {name: {type: string}, host: {type: string},
         manufacturer: {type: string},
         port: {type: integer, minimum: 1, maximum: 65535},
         channel: {type: integer, minimum: 1, maximum: 64},
         subtype: {type: integer, enum: [0, 1]},
         live_view_subtype: {type: integer, enum: [0, 1]},
         username: {type: string}, password: {type: string},
         site_id: {type: string, description: "Site edge da câmera — SEM ele a
           câmera fica invisível pro edge (config_poll, live view, fps demand
           filtram por site_id)"},
         detection_stream_url: {type: string},
         video_codec: {type: string, enum: [h264, h265]},
         max_auth_failures: {type: integer, minimum: 1}}}}
    responses: {201: {description: Câmera criada}, 400: {description: Dados inválidos (fora de faixa)}}
    """
    try:
        user_id = get_current_user_id()
        tenant_id = UUID(get_tenant_id())
        data = request.get_json() or {}
        if platform_flag_enabled(ENFORCE_PLAN_LIMITS_FLAG):
            cap_error = _plan_camera_cap_error(str(tenant_id))
            if cap_error is not None:
                return cap_error
        service = _get_camera_service()
        camera = service.create_camera(tenant_id, data, created_by=user_id)
        return success(camera, status=201)
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("create_camera_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@jwt_required()
def get_camera(camera_id: str):  # type: ignore[no-untyped-def]
    """---
    tags: [cameras]
    summary: Obter câmera por ID
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: camera_id, type: string, required: true}
    responses: {200: {description: Dados da câmera (sem senha)},
                404: {description: Câmera não encontrada}}
    """
    try:
        user_id = get_current_user_id()
        tenant_id = get_tenant_id()
        service = _get_camera_service()
        camera = service.get_camera(UUID(camera_id))
        if camera.get("tenant_id") and str(camera["tenant_id"]) != str(tenant_id) and not _is_admin(user_id):
            # C-01: cross-tenant → 404, nunca 403 (não vazar existência)
            return error("Câmera não encontrada", 404)
        return success(camera)
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("get_camera_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@jwt_required()
@require_permission("cameras:write")
def update_camera(camera_id: str):  # type: ignore[no-untyped-def]
    """---
    tags: [cameras]
    summary: Atualizar câmera
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: camera_id, type: string, required: true}
      - {in: body, name: body, schema: {properties: {name: {type: string},
         host: {type: string},
         port: {type: integer, minimum: 1, maximum: 65535},
         channel: {type: integer, minimum: 1, maximum: 64},
         subtype: {type: integer, enum: [0, 1]},
         live_view_subtype: {type: integer, enum: [0, 1]},
         username: {type: string},
         password: {type: string}, manufacturer: {type: string},
         location: {type: string}, rtsp_url_override: {type: string},
         site_id: {type: string, description: "Associa/move a câmera para um
           site edge (ver POST)"},
         detection_stream_url: {type: string},
         video_codec: {type: string, enum: [h264, h265]},
         max_auth_failures: {type: integer, minimum: 1}}}}
    responses: {200: {description: Câmera atualizada},
                400: {description: Dados inválidos (fora de faixa)},
                403: {description: Sem permissão}, 404: {description: Câmera não encontrada}}
    """
    try:
        user_id = get_current_user_id()
        tenant_id = UUID(get_tenant_id())
        data = request.get_json() or {}
        service = _get_camera_service()
        camera = service.update_camera(UUID(camera_id), tenant_id, data, _is_admin(user_id))
        return success(camera)
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("update_camera_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@jwt_required()
@require_permission("cameras:delete")
def delete_camera(camera_id: str):  # type: ignore[no-untyped-def]
    """---
    tags: [cameras]
    summary: Excluir câmera do sistema (irreversível pela UI)
    description: |
      Exclusão LÓGICA (`cameras.deleted_at`, migration 138). A câmera some da
      lista, do Ao Vivo, do grid, dos seletores, do inventário e do config que
      o edge baixa. O que NÃO some: alertas, evidências gravadas e frames de
      treino já anotados — registro histórico, com possível valor legal.
      O DELETE físico não é usado porque levaria alerta e evidência junto por
      CASCADE e travaria por FK em qualquer câmera com frame de treino.
      Não há rota de desfazer: quem quiser a câmera de volta cadastra de novo.
      Para o reversível existe `POST /<id>/archive`.
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: camera_id, type: string, required: true}
    responses: {200: {description: Câmera excluída},
                403: {description: Sem permissão}, 404: {description: Câmera não encontrada}}
    """
    try:
        user_id = get_current_user_id()
        tenant_id = UUID(get_tenant_id())
        service = _get_camera_service()
        service.delete_camera(UUID(camera_id), tenant_id, _is_admin(user_id))
        return success({"deleted": True})
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("delete_camera_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@jwt_required()
@require_permission("cameras:write")
def archive_camera(camera_id: str):  # type: ignore[no-untyped-def]
    """---
    tags: [cameras]
    summary: Arquivar câmera (is_active=false) — reversível, não apaga nada
    description: |
      Caminho correto para tirar do reconhecimento uma câmera que não faz
      parte dele. Os frames já coletados dela saem da fila de anotação e do
      export de dataset (deixam de treinar o modelo), mas continuam no banco.
      `POST .../restore` desfaz.
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: camera_id, type: string, required: true}
    responses: {200: {description: Câmera arquivada},
                404: {description: Câmera não encontrada}}
    """
    try:
        tenant_id = UUID(get_tenant_id())
        camera = _get_camera_service().archive_camera(UUID(camera_id), tenant_id)
        return success({"camera": camera})
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("archive_camera_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@jwt_required()
@require_permission("cameras:write")
def restore_camera(camera_id: str):  # type: ignore[no-untyped-def]
    """---
    tags: [cameras]
    summary: Desarquivar câmera (is_active=true)
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: camera_id, type: string, required: true}
    responses: {200: {description: Câmera restaurada},
                404: {description: Câmera não encontrada}}
    """
    try:
        tenant_id = UUID(get_tenant_id())
        camera = _get_camera_service().restore_camera(UUID(camera_id), tenant_id)
        return success({"camera": camera})
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("restore_camera_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)

"""
Recognition — Camera Routes.

Thin router: all logic lives in handler modules.
Senhas SEMPRE criptografadas — NUNCA retornadas na API.
"""
from flask import Blueprint

from .config_handler import patch_camera_config
from .crud_handlers import (
    archive_camera,
    create_camera,
    delete_camera,
    get_camera,
    list_cameras,
    restore_camera,
    update_camera,
)
from .health_context_handler import get_camera_health_context
from .model_config_handlers import (
    list_camera_model_configs,
    get_camera_model_config,
    get_camera_model_config_history,
    post_camera_model_config,
    post_camera_model_config_rollback,
)
from .model_handlers import (
    get_available_models,
    get_camera_model,
    get_camera_models,
    get_effective_model,
    put_camera_models,
    set_camera_model,
)
from .module_handler import get_camera_module_current, patch_camera_module, put_camera_schedule
from .modules_handler import list_camera_modules, put_camera_modules
from .probe_handler import probe_camera
from .retention_handler import get_camera_retention, put_camera_retention
from .snapshot_handlers import get_camera_snapshot, refresh_camera_snapshot
from .stream_handlers import serve_hls, start_stream, stop_stream, stream_info, stream_status
from .tenant_retention_handler import get_tenant_retention, put_tenant_retention
from .test_handler import test_camera

cameras_bp = Blueprint("cameras", __name__, url_prefix="/api/cameras")

# CRUD
cameras_bp.add_url_rule("", view_func=list_cameras, methods=["GET"])
cameras_bp.add_url_rule("", view_func=create_camera, methods=["POST"])
cameras_bp.add_url_rule("/<camera_id>", view_func=get_camera, methods=["GET"])
cameras_bp.add_url_rule("/<camera_id>", view_func=update_camera, methods=["PUT"])
cameras_bp.add_url_rule("/<camera_id>", view_func=delete_camera, methods=["DELETE"])
# Duas ações DIFERENTES, de propósito:
#   DELETE /<id>          exclui do sistema — some de todas as telas, do grid
#                         e do config do edge, e não tem volta pela UI. É
#                         exclusão LÓGICA (`cameras.deleted_at`, migration
#                         138): alerta, evidência e frame de treino ficam.
#   POST   /<id>/archive  tira do reconhecimento e VOLTA com /restore.
cameras_bp.add_url_rule("/<camera_id>/archive", view_func=archive_camera, methods=["POST"])
cameras_bp.add_url_rule("/<camera_id>/restore", view_func=restore_camera, methods=["POST"])

# Stream
cameras_bp.add_url_rule("/<camera_id>/stream/start", view_func=start_stream, methods=["POST"])
cameras_bp.add_url_rule("/<camera_id>/stream/stop", view_func=stop_stream, methods=["POST"])
cameras_bp.add_url_rule("/<camera_id>/stream/status", view_func=stream_status, methods=["GET"])
cameras_bp.add_url_rule("/<camera_id>/stream/info", view_func=stream_info, methods=["GET"])
cameras_bp.add_url_rule("/<camera_id>/stream/<path:filename>", view_func=serve_hls, methods=["GET"])
# S2: rota tokenizada — token de playback assinado no path (endpoint distinto)
cameras_bp.add_url_rule(
    "/<camera_id>/stream/s/<token>/<path:filename>",
    endpoint="serve_hls_tokenized",
    view_func=serve_hls,
    methods=["GET"],
)

# Snapshot (Bloco A — miniatura de triagem, D-85: ONVIF GetSnapshotUri)
cameras_bp.add_url_rule("/<camera_id>/snapshot", view_func=get_camera_snapshot, methods=["GET"])
cameras_bp.add_url_rule(
    "/<camera_id>/snapshot/refresh", view_func=refresh_camera_snapshot, methods=["POST"]
)

# Probe (onboarding — antes de salvar câmera)
cameras_bp.add_url_rule("/probe", view_func=probe_camera, methods=["POST"])

# Test
cameras_bp.add_url_rule("/<camera_id>/test", view_func=test_camera, methods=["POST"])

# Model (legacy GET Redis-only + Task 045 PUT persistente)
cameras_bp.add_url_rule("/<camera_id>/model", view_func=get_camera_model, methods=["GET"])
cameras_bp.add_url_rule("/<camera_id>/model", view_func=set_camera_model, methods=["PUT"])

# Model — Task 045: atribuição por módulo explícito
cameras_bp.add_url_rule("/<camera_id>/models", view_func=get_camera_models, methods=["GET"])
cameras_bp.add_url_rule("/<camera_id>/models", view_func=put_camera_models, methods=["PUT"])

# FPS / Quality config (deliverable j)
cameras_bp.add_url_rule("/<camera_id>/config", view_func=patch_camera_config, methods=["PATCH"])

# Health context — telemetria do site para aviso health-aware de FPS (WS10)
cameras_bp.add_url_rule(
    "/<camera_id>/health-context", view_func=get_camera_health_context, methods=["GET"]
)

# Model — Task 045: available-models e effective-model
cameras_bp.add_url_rule(
    "/<camera_id>/available-models", view_func=get_available_models, methods=["GET"]
)
cameras_bp.add_url_rule(
    "/<camera_id>/effective-model", view_func=get_effective_model, methods=["GET"]
)

# Model-config — WS-C2 (registry-level, geometria + histórico + rollback)
cameras_bp.add_url_rule(
    # ⚠️ ANTES da rota com <camera_id>: senão "model-config" casa como id.
    "/model-config", view_func=list_camera_model_configs, methods=["GET"]
)
cameras_bp.add_url_rule(
    "/<camera_id>/model-config", view_func=get_camera_model_config, methods=["GET"]
)
cameras_bp.add_url_rule(
    "/<camera_id>/model-config", view_func=post_camera_model_config, methods=["POST"]
)
cameras_bp.add_url_rule(
    "/<camera_id>/model-config/history",
    view_func=get_camera_model_config_history, methods=["GET"],
)
cameras_bp.add_url_rule(
    "/<camera_id>/model-config/rollback",
    view_func=post_camera_model_config_rollback, methods=["POST"],
)

# Vínculo N:N câmera↔módulo (migration 134) — a tela de atribuição.
# ⚠️ ANTES das rotas com <camera_id>, pelo mesmo motivo de "/model-config":
# senão "modules" casa como id de câmera.
cameras_bp.add_url_rule("/modules", view_func=list_camera_modules, methods=["GET"])
cameras_bp.add_url_rule("/modules", view_func=put_camera_modules, methods=["PUT"])

# Module + Schedule
cameras_bp.add_url_rule("/<camera_id>/module", view_func=patch_camera_module, methods=["PATCH"])
cameras_bp.add_url_rule("/<camera_id>/schedule", view_func=put_camera_schedule, methods=["PUT"])
cameras_bp.add_url_rule(
    "/<camera_id>/module/current", view_func=get_camera_module_current, methods=["GET"]
)

# Retention tiers (task-047)
cameras_bp.add_url_rule("/<camera_id>/retention", view_func=get_camera_retention, methods=["GET"])
cameras_bp.add_url_rule("/<camera_id>/retention", view_func=put_camera_retention, methods=["PUT"])

# Tenant-level retention default
cameras_bp.add_url_rule("/tenant/retention", view_func=get_tenant_retention, methods=["GET"])
cameras_bp.add_url_rule("/tenant/retention", view_func=put_tenant_retention, methods=["PUT"])

# ---------------------------------------------------------------------------
# v1-versioned aliases — probe, effective-model, config.
# O Blueprint principal está em /api/cameras (legado). Clientes que usam o
# prefixo /api/v1/cameras eram servidos pelo catch-all → 405/200.
# Estes aliases corrigem as rotas sem alterar o Blueprint existente.
# ---------------------------------------------------------------------------
cameras_v1_bp = Blueprint("cameras_v1", __name__, url_prefix="/api/v1/cameras")
cameras_v1_bp.add_url_rule(
    "/probe",
    endpoint="probe_camera_v1",
    view_func=probe_camera,
    methods=["POST"],
)
cameras_v1_bp.add_url_rule(
    "/<camera_id>/effective-model",
    endpoint="get_effective_model_v1",
    view_func=get_effective_model,
    methods=["GET"],
)
cameras_v1_bp.add_url_rule(
    "/<camera_id>/config",
    endpoint="patch_camera_config_v1",
    view_func=patch_camera_config,
    methods=["PATCH"],
)
cameras_v1_bp.add_url_rule(
    "/<camera_id>/health-context",
    endpoint="get_camera_health_context_v1",
    view_func=get_camera_health_context,
    methods=["GET"],
)
cameras_v1_bp.add_url_rule(
    "/<camera_id>/model-config",
    endpoint="get_camera_model_config_v1",
    view_func=get_camera_model_config,
    methods=["GET"],
)
cameras_v1_bp.add_url_rule(
    "/<camera_id>/model-config",
    endpoint="post_camera_model_config_v1",
    view_func=post_camera_model_config,
    methods=["POST"],
)
cameras_v1_bp.add_url_rule(
    "/<camera_id>/model-config/history",
    endpoint="get_camera_model_config_history_v1",
    view_func=get_camera_model_config_history,
    methods=["GET"],
)
cameras_v1_bp.add_url_rule(
    "/<camera_id>/model-config/rollback",
    endpoint="post_camera_model_config_rollback_v1",
    view_func=post_camera_model_config_rollback,
    methods=["POST"],
)

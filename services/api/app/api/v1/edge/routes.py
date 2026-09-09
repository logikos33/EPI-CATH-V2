"""Blueprint /api/v1/edge/ — endpoints do edge-sync-agent."""
import hashlib
import io
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg2.errors
from flask import Blueprint, g, jsonify, make_response, request
from pydantic import ValidationError
from recognition_shared.device import EnrollmentRequest
from recognition_shared.enums import DeviceTokenScope
from recognition_shared.heartbeat import Heartbeat

from app.constants import FrameSource, R2Prefix
from app.core.auth import get_role, get_tenant_id, jwt_required_custom
from app.core.device_auth import (
    extract_device_id_unverified,
    require_device_scope,
    verify_device_token,
)
from app.core.edge_offline import (
    OFFLINE_THRESHOLD_SECONDS,
    derive_site_health_status,
    is_site_offline,
)
from app.core.exceptions import AuthenticationError, StorageError
from app.core.responses import error, success
from app.core.tenant import has_permission
from app.domain.services.crop_origin import parse_crop_origin
from app.infrastructure.database.connection import DatabasePool
from app.infrastructure.database.repositories.edge_heartbeat_repository import (
    EdgeHeartbeatRepository,
)
from app.infrastructure.database.repositories.camera_module_repository import (
    CameraModuleRepository,
)
from app.infrastructure.database.repositories.camera_repository import CameraRepository
from app.infrastructure.database.repositories.edge_site_repository import (
    _DEVICE_ALREADY_ENROLLED,
    EdgeSiteRepository,
)
from app.infrastructure.database.repositories.edge_software_channel_repository import (
    EdgeSoftwareChannelRepository,
)
from app.infrastructure.database.repositories.model_deployment_repository import (
    ModelDeploymentRepository,
)
from app.infrastructure.database.repositories.module_repository import ModuleRepository
from app.infrastructure.database.repositories.operation_repository import (
    OperationRepository,
)
from app.core.rate_limiting import get_ip_identifier
from app.extensions import limiter
from app.infrastructure.database.repositories.frame_repository import FrameRepository
from app.infrastructure.database.repositories.recorder_repository import RecorderRepository
from app.api.v1.cameras.helpers import _get_binary_redis, _get_redis

edge_bp = Blueprint("edge", __name__, url_prefix="/api/v1/edge")
logger = logging.getLogger(__name__)

_VALID_DEPLOYMENT_MODES = {"cloud", "edge", "hybrid"}
_VALID_SITE_STATUSES = {"active", "inactive", "maintenance", "provisioning"}
# WS7: gate por permissão — default_roles de edge:manage == {admin, superadmin}
# (idêntico ao _ADMIN_ROLES inline anterior; paridade coberta por teste)
_MANAGE_PERMISSION = "edge:manage"

# TTL da URL assinada do modelo no config/poll: folgado o bastante para o box
# baixar um ONNX de centenas de MB num link ruim, e ainda assim expirar bem
# antes do próximo ciclo de vida do modelo.
_MODEL_URL_TTL_SECONDS = 3 * 3600     # 3 h

_DEFAULT_WINDOW_SECONDS = 24 * 3600   # 24 h
_MAX_WINDOW_SECONDS = 7 * 24 * 3600   # 7 d

# Shadow mode on-site (Onda 2): frame capturado pelo edge (NVR fora do
# alcance direto da nuvem, ADR-0020) — mesmo formato que nvr_extraction.py
# produz, só troca quem inicia o upload.
_ALLOWED_FRAME_EXTS = frozenset({"jpg", "jpeg"})
_MAX_FRAME_BYTES = 10 * 1024 * 1024  # 10 MB — mesmo teto de image_handlers.py

# Bloco A: miniatura de triagem (CameraTriagePage) — um JPEG de canal único
# (ONVIF GetSnapshotUri ou fallback de frame RTSP), bem menor que um frame de
# treino em resolução alta; teto generoso mas propositalmente menor que
# _MAX_FRAME_BYTES.
_MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024  # 5 MB

# Item 1.5 do mutirão: teto de upload precisa proteger MEMÓRIA, não só
# rejeitar depois de já ter materializado o corpo inteiro. Duas camadas:
#  1) Content-Length (se presente) rejeita ANTES de tocar em request.files —
#     folga de framing do multipart (boundary/headers) pra não falsear um
#     upload exatamente no teto.
#  2) leitura em chunks que aborta assim que a soma ultrapassa o teto —
#     cobre Content-Length ausente/mentiroso, sem nunca acumular o corpo
#     inteiro em memória.
_UPLOAD_CHUNK_BYTES = 64 * 1024  # 64 KB por chunk
_CONTENT_LENGTH_SLACK_BYTES = 4096  # folga generosa pro overhead de multipart

# Live view via push do edge (LV-1): câmera atrás de NVR numa LAN isolada
# (ADR-0020) — a nuvem nunca alcança a câmera direto, então em vez do
# LocalStreamManager tentar puxar RTSP (stream_handlers.py, só funciona pra
# câmera com IP roteável da nuvem), o edge roda o FFmpeg e EMPURRA os
# segmentos .ts + a playlist .m3u8 pra cá. Guardados em Redis com TTL curto
# (mesmo espírito de epi:stream:{id}:active) — sem job de limpeza, sem
# storage novo: um push que para de chegar simplesmente expira.
_SAFE_HLS_FILENAME = re.compile(r"^[a-zA-Z0-9_.-]+$")
_MAX_SEGMENT_BYTES = 5 * 1024 * 1024  # 5 MB — generoso pra um segmento de 1-2s

# Bucket DEDICADO de ingestão de máquina do live view (rodada 11/08, D-85).
#
# Sem decorator próprio, POST /live-view/<id>/segment caía no piso anônimo do
# bucket geral (DEFAULT_IP_LIMIT = 900/min/IP — device token não carrega JWT de
# usuário, então is_anonymous_request()==True e só o piso por IP se aplicava):
# 8 câmeras ≈ 720 req/min já eram 80% do teto, ~10 câmeras estouram, e as 29 do
# iNVD cheio (D-85) seriam ~2.610/min = 429 em massa derrubando segmento — com
# sintoma idêntico ao congelamento recém-caçado (#325–#331), só que vindo do
# rate limiter. Pior: dividia o balde com heartbeat/config-poll/frame-upload e
# com qualquer navegação de usuário atrás do mesmo IP de fábrica.
#
# Decorar a rota tira ela dos 3 buckets gerais (override_defaults do
# flask-limiter — mesmo mecanismo do serve_hls/#291) e dá escopo próprio:
# ingestão de máquina não divide balde com navegação de usuário. Chave por IP
# (o box do site; g.device_ctx só existe DEPOIS do require_device_scope, e o
# path não carrega identidade de device — mesmo trade-off do piso geral).
#
# Dimensionado para o GRAVADOR CHEIO (32 canais), não para as 29 de hoje:
# cadência medida é ~90 req/min por câmera transmitindo (m3u8 + .ts a cada
# ~1,3–2s; segmento de 2s é PISO preso ao GOP — ver live_view_loop.py), então
# 32 × 90 = 2.880/min. 3.600/min dá 25% de folga pra variância de GOP e rajada
# de reconexão, sem abrir pra abuso (callable, não literal — monkeypatch em
# teste, mesmo padrão dos buckets hls-video-*).
EDGE_LIVE_INGEST_IP_LIMIT = "3600 per minute"


def _edge_live_ingest_limit() -> str:
    """Callable (não string literal) — permite monkeypatch em teste."""
    return EDGE_LIVE_INGEST_IP_LIMIT
# Precisa ser MAIOR que a janela que a playlist do edge anuncia
# (LIVE_VIEW_LIST_SIZE × LIVE_VIEW_SEGMENT_SECONDS = 10×2s = 20s), senão o
# manifesto aponta pra segmento já expirado e o player leva 425/404. O valor
# antigo (20s) empatava com o ciclo do uploader em rodízio e era um dos
# amplificadores do congelamento cíclico do live view (D-74/D-75).
# Não subir além do necessário: este Redis é COMPARTILHADO com o resto da API
# (SEGMENTS_REDIS_URL == REDIS_URL no DEV) e segmento é payload de ~1MB.
_HLS_SEGMENT_TTL = 30


def _get_repo() -> EdgeHeartbeatRepository:
    pool = DatabasePool.get_instance()
    return EdgeHeartbeatRepository(pool)  # type: ignore[arg-type]


def _get_software_channel_repo() -> EdgeSoftwareChannelRepository:
    pool = DatabasePool.get_instance()
    return EdgeSoftwareChannelRepository(pool)  # type: ignore[arg-type]


# Campos de telemetria do Heartbeat espelhados no dashboard integrado (Pilha B).
_TELEMETRY_FIELDS = (
    "cpu_pct", "mem_pct", "gpu_pct", "gpu_mem_pct", "disk_pct",
    "inference_fps", "inference_latency_ms", "cameras_online", "cameras_total",
    "queue_depth", "upload_kbps", "download_kbps", "gpu_temp_c", "cpu_temp_c",
    "decode_fps", "decode_pct", "dropped_frames",
)


def _heartbeat_to_telemetry_payload(hb: Heartbeat) -> dict:
    """Extrai os campos de telemetria do Heartbeat como dict JSON-safe.

    Decimals viram float (json.dumps não serializa Decimal); enums viram str.
    """
    payload: dict = {}
    for field in _TELEMETRY_FIELDS:
        value = getattr(hb, field, None)
        if value is not None:
            payload[field] = float(value) if not isinstance(value, int) else value
    payload["status"] = hb.status.value
    if hb.edge_version:
        payload["edge_version"] = hb.edge_version
    return payload


def _bridge_heartbeat_to_telemetry(
    tenant_id: str, site_id: str, hb: Heartbeat, sampled_at: str
) -> None:
    """Alimenta a Pilha B (dashboard integrado ao vivo) a partir do heartbeat real.

    Best-effort e late-import: um erro aqui NUNCA derruba a ingestão do heartbeat
    (Pilha A é a fonte da verdade). Reusa DashboardEdgeService.ingest_edge_telemetry
    (insere em edge_telemetry_samples + publica em edge_telemetry:{tenant_id} → /monitor).
    """
    try:
        from app.domain.services.dashboard_edge_service import DashboardEdgeService
        from app.infrastructure.database.repositories.edge_telemetry_repository import (
            EdgeTelemetryRepository,
        )
        from app.infrastructure.database.repositories.model_training_metrics_repository import (
            ModelTrainingMetricsRepository,
        )

        pool = DatabasePool.get_instance()
        service = DashboardEdgeService(
            ModelTrainingMetricsRepository(pool), EdgeTelemetryRepository(pool)
        )
        sample = {
            "sampled_at": sampled_at,
            "label": hb.status.value,
            "payload": _heartbeat_to_telemetry_payload(hb),
        }
        service.ingest_edge_telemetry(tenant_id, site_id, [sample])
    except Exception as exc:  # noqa: BLE001 — bridge best-effort, nunca falha o heartbeat
        logger.warning("heartbeat_telemetry_bridge_failed: %s", exc)


# Janela de supressão do aviso de divergência de config (#477). A divergência é
# um ESTADO que dura minutos ou horas; repetir o aviso na frequência do
# heartbeat não acrescenta informação depois da primeira vez — só afoga o log.
_DIVERGENCE_WINDOW_S = float(os.environ.get("EDGE_CONFIG_DIVERGENCE_WINDOW_S", "900"))

# ponytail: estado em memória do processo. Com N workers gunicorn a supressão é
# POR WORKER — reduz o volume em ~N×, não zera. É honesto para o objetivo
# (parar de afogar o log) e não vale um round-trip a Redis por heartbeat.
# Trocar por Redis só se o volume ainda incomodar com a janela ajustada.
_divergence_state: dict[tuple[str, str], dict] = {}


def _reset_divergence_state() -> None:
    """Zera o estado de supressão. Existe para os testes — cada caso precisa
    partir de um processo 'que nunca viu divergência nenhuma'."""
    _divergence_state.clear()


def _log_config_divergence_if_any(
    tenant_id: str, site_id: str, device_id: str, config_version_applied: "str | None"
) -> None:
    """ADR-0058: compara o config_version que o device diz ter aplicado contra
    o config_version CORRENTE do site (mesma fórmula de `poll_edge_config`) e
    loga um WARNING quando divergem — o sinal que torna visível o caso que
    motivou esta ADR ("config/poll diz cameras=2, o box só monitora 1").

    O aviso é RATE-LIMITADO (#477), e o desenho evita a armadilha de trocar
    ruído por cegueira:

      - primeira vez, ou mudou o CONTEÚDO da divergência: avisa alto
      - repetição idêntica dentro da janela: suprime, e conta
      - passou a janela: avisa de novo, dizendo há quanto tempo dura
      - resolveu: UMA linha dizendo que resolveu, com a duração e quantas
        repetições foram suprimidas

    ⚠️ É a última que impede a supressão de virar esquecimento: quem lê o log
    sabe que houve um período silencioso e de que tamanho. Sem ela, isto seria
    o mesmo defeito do #436 (falha de infra contada como janela vazia).

    Silenciosamente no-op quando o device não manda o campo (agente antigo)
    ou manda vazio (fonte "env"/fallback, sem config_version pra comparar —
    nesse caso o próprio fato de estar em fallback já é visível nos logs do
    device). Best-effort: qualquer erro aqui (DB, o que for) NUNCA derruba o
    ingest do heartbeat, mesmo padrão de `_bridge_heartbeat_to_telemetry`.
    """
    if not config_version_applied:
        return
    try:
        payload_now = _build_edge_config_payload(site_id, tenant_id)
        current_version = _compute_config_version(payload_now)
        cameras_now = payload_now["cameras"]
        chave = (site_id, device_id)
        agora = time.monotonic()

        if config_version_applied == current_version:
            anterior = _divergence_state.pop(chave, None)
            if anterior is not None:
                logger.warning(
                    "edge_config_divergence_resolvida: device=%s site=%s "
                    "durou=%.0fs repeticoes_suprimidas=%d version=%s",
                    device_id, site_id[:8], agora - anterior["primeiro_em"],
                    anterior["suprimidos"], current_version,
                )
            return

        # `applied` e `current` são HASHES de config, não a config em si — o
        # device manda só o hash que aplicou. Então o "diff" possível aqui é
        # hash contra hash mais a contagem de câmeras que o cloud enxerga; não
        # dá para imprimir o delta campo a campo sem o device mandar a config.
        estado = _divergence_state.get(chave)
        mesma_divergencia = (
            estado is not None
            and estado["applied"] == config_version_applied
            and estado["current"] == current_version
        )

        if mesma_divergencia and agora - estado["ultimo_log_em"] < _DIVERGENCE_WINDOW_S:
            estado["suprimidos"] += 1
            return

        if mesma_divergencia:
            logger.warning(
                "edge_config_divergence: device=%s site=%s applied=%s current=%s "
                "cameras=%d — PERSISTE ha %.0fs (%d repeticoes suprimidas na janela); "
                "device pode precisar reiniciar para aplicar a config mais recente "
                "(ADR-0058)",
                device_id, site_id[:8], config_version_applied, current_version,
                len(cameras_now), agora - estado["primeiro_em"], estado["suprimidos"],
            )
            estado["ultimo_log_em"] = agora
            estado["suprimidos"] = 0
            return

        logger.warning(
            "edge_config_divergence: device=%s site=%s applied=%s current=%s "
            "cameras=%d — device pode precisar reiniciar para aplicar a config "
            "mais recente (ADR-0058)",
            device_id, site_id[:8], config_version_applied, current_version,
            len(cameras_now),
        )
        _divergence_state[chave] = {
            "applied": config_version_applied,
            "current": current_version,
            "primeiro_em": agora,
            "ultimo_log_em": agora,
            "suprimidos": 0,
        }
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca falha o heartbeat
        logger.warning("edge_config_divergence_check_failed: %s", exc)


def _get_site_repo() -> EdgeSiteRepository:
    pool = DatabasePool.get_instance()
    return EdgeSiteRepository(pool)  # type: ignore[arg-type]


def _get_operation_repo() -> OperationRepository:
    return OperationRepository(DatabasePool.get_instance())


def _get_module_repo() -> ModuleRepository:
    return ModuleRepository(DatabasePool.get_instance())


def _get_deployment_repo() -> ModelDeploymentRepository:
    return ModelDeploymentRepository(DatabasePool.get_instance())


def _assinar_url_do_modelo(payload: dict, tenant_id: str) -> None:
    """Troca `model.r2_key` por `model.url` (presigned GET) no payload servido.

    Feito DEPOIS do hash e do 304: a assinatura muda a cada chamada e, dentro
    do hash, faria o config_version mudar em todo poll — 200 eterno, download
    de modelo em loop. Falha de storage não derruba o poll: o manifesto sai do
    payload (e o agente não toca no estado do modelo) em vez de o box ficar
    sem câmera nenhuma por causa do R2.
    """
    model = payload.get("model")
    if not model:
        return
    key = model.pop("r2_key", None)
    try:
        from app.infrastructure.storage.local_storage import get_storage
        model["url"] = get_storage(tenant_id).generate_presigned_download_url(
            key, ttl=_MODEL_URL_TTL_SECONDS
        )
    except Exception as exc:
        logger.warning(
            "edge_config_model_url_falhou: key=%s err=%s — manifesto omitido",
            key, exc,
        )
        payload.pop("model", None)


def _get_camera_repo() -> CameraRepository:
    pool = DatabasePool.get_instance()
    return CameraRepository(pool)  # type: ignore[arg-type]


def _get_recorder_repo() -> RecorderRepository:
    pool = DatabasePool.get_instance()
    return RecorderRepository(pool)  # type: ignore[arg-type]


def _get_frame_repo() -> FrameRepository:
    pool = DatabasePool.get_instance()
    return FrameRepository(pool)  # type: ignore[arg-type]


def _get_camera_module_repo() -> CameraModuleRepository:
    pool = DatabasePool.get_instance()
    return CameraModuleRepository(pool)  # type: ignore[arg-type]


def _image_dimensions(data: bytes) -> "tuple[int, int] | None":
    """Extrai (width, height) via PIL. None se bytes não formam imagem válida.
    Mesmo padrão de training/image_handlers.py — import local, PIL só é
    necessário aqui."""
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(io.BytesIO(data)) as img:
            return int(img.size[0]), int(img.size[1])
    except Exception:
        return None


def _reject_if_content_length_exceeds(limit: int) -> "tuple | None":
    """Guarda de memória (camada 1): se o Content-Length declarado já
    estourar `limit` (+ folga de framing do multipart), rejeita com 413 SEM
    tocar em `request.files`/`request.form` — o corpo nem chega a ser
    parseado. Content-Length pode faltar ou mentir; por isso o teto exato
    continua sendo aplicado depois, byte a byte, por `_read_bounded`."""
    content_length = request.content_length
    if content_length is not None and content_length > limit + _CONTENT_LENGTH_SLACK_BYTES:
        limit_mb = limit // (1024 * 1024)
        return error(f"Corpo da requisição excede o limite de {limit_mb}MB", 413)
    return None


def _read_bounded(file_storage, limit: int) -> "bytes | None":
    """Guarda de memória (camada 2): lê o upload em chunks de
    `_UPLOAD_CHUNK_BYTES`, sem nunca acumular mais que ~`limit` bytes em
    memória. Retorna `None` assim que o total ultrapassa `limit` (o caller
    decide o 413); do contrário, os bytes completos — idêntico ao que
    `file_storage.read()` entregava antes, para uploads dentro do teto."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file_storage.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _serialize_site(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "description": row.get("description"),
        "location": row.get("location"),
        "deployment_mode": row["deployment_mode"],
        "status": row["status"],
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
        "created_by": str(row["created_by"]) if row.get("created_by") else None,
    }


def _serialize_heartbeat_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "received_at": str(row["received_at"]) if row.get("received_at") else None,
        "status": row.get("status"),
        "inference_fps": float(row["inference_fps"]) if row.get("inference_fps") is not None else None,
        "cameras_online": row.get("cameras_online"),
        "cameras_total": row.get("cameras_total"),
        "cpu_pct": float(row["cpu_pct"]) if row.get("cpu_pct") is not None else None,
        "gpu_pct": float(row["gpu_pct"]) if row.get("gpu_pct") is not None else None,
        "queue_depth": row.get("queue_depth"),
        "gpu_temp_c": float(row["gpu_temp_c"]) if row.get("gpu_temp_c") is not None else None,
        "decode_pct": float(row["decode_pct"]) if row.get("decode_pct") is not None else None,
        "edge_version": row.get("edge_version"),
        "cpu_temp_c": float(row["cpu_temp_c"]) if row.get("cpu_temp_c") is not None else None,
        "decode_fps": float(row["decode_fps"]) if row.get("decode_fps") is not None else None,
        "dropped_frames": row.get("dropped_frames"),
        "config_version_applied": row.get("config_version_applied"),
    }


def _parse_window_seconds(window: str | None) -> int:
    """Parse '24h' / '7d' / '30m' → seconds; clamp 1..._MAX_WINDOW_SECONDS."""
    if not window:
        return _DEFAULT_WINDOW_SECONDS
    w = window.strip().lower()
    try:
        if w.endswith("d"):
            secs = int(w[:-1]) * 86400
        elif w.endswith("h"):
            secs = int(w[:-1]) * 3600
        elif w.endswith("m"):
            secs = int(w[:-1]) * 60
        else:
            secs = int(w)
    except (ValueError, IndexError):
        secs = _DEFAULT_WINDOW_SECONDS
    return min(max(secs, 1), _MAX_WINDOW_SECONDS)


def _derive_token_status(token: dict) -> str:
    """Deriva status de um enrollment token: active | used | expired."""
    if token.get("used_at") is not None:
        return "used"
    expires_at = token.get("expires_at")
    if expires_at is None:
        return "expired"
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return "expired"
    return "active"


# ---------------------------------------------------------------------------
# Device heartbeat ingest (RS256 device auth — no JWT)
# ---------------------------------------------------------------------------

@edge_bp.route("/heartbeat", methods=["POST"])
def ingest_heartbeat() -> tuple:
    """Recebe telemetria periódica do edge-sync-agent (RS256 device auth)."""
    # 1. Extract bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return error("Authorization header ausente ou inválido", 401)
    token = auth_header.removeprefix("Bearer ")

    # 2. Extract device_id without verifying (for DB lookup only)
    try:
        device_id = extract_device_id_unverified(token)
    except AuthenticationError as exc:
        logger.warning("edge_heartbeat: token decode failed")
        return error(str(exc), 401)

    # 3. Lookup device + public key
    repo = _get_repo()
    device = repo.get_device_by_device_id(device_id)
    if device is None:
        logger.warning("edge_heartbeat: unknown device_id")
        return error("Dispositivo não encontrado", 401)

    # 4. Check revoked before touching the signature
    if device.get("revoked"):
        logger.warning("edge_heartbeat: revoked device")
        return error("Dispositivo revogado", 403)

    # 5. Verify RS256 signature + expiry
    try:
        claims = verify_device_token(token, device["public_key_pem"])
    except AuthenticationError as exc:
        logger.warning("edge_heartbeat: token verification failed")
        return error(str(exc), 401)

    # 6. Defense-in-depth: claims tenant/site must match enrollment record
    enrollment_tenant = UUID(str(device["tenant_id"]))
    enrollment_site = UUID(str(device["site_id"]))
    if claims.tenant_id != enrollment_tenant or claims.site_id != enrollment_site:
        logger.warning(
            "edge_heartbeat: claims divergem do enrollment device=%s "
            "claims_tenant=%s enrollment_tenant=%s",
            device_id,
            str(claims.tenant_id)[:8],
            str(enrollment_tenant)[:8],
        )
        return error("Token adulterado: claims divergem do enrollment", 403)

    # 6b. Escopo mínimo: só um token com heartbeat:write ingere heartbeat (S1/ADR-0019).
    # Compara pelo VALOR string (membros de DeviceTokenScope são str) — robusto a
    # identidade de classe do enum.
    if "heartbeat:write" not in {getattr(s, "value", s) for s in claims.scopes}:
        logger.warning("edge_heartbeat: device sem escopo heartbeat:write")
        return error("Escopo insuficiente para esta operação", 403)

    # 7. Validate body with Heartbeat (Pydantic v2)
    body = request.get_json(silent=True) or {}
    try:
        hb = Heartbeat(**body)
    except ValidationError:
        return error("Payload inválido", 422, error_code="INVALID_PAYLOAD")

    # 8. Persist heartbeat + update last_seen (tenant/site/device from enrollment, never claims)
    enrolled_device_id = device["device_id"]
    row = repo.insert_heartbeat(enrollment_tenant, enrollment_site, enrolled_device_id, hb)
    repo.update_last_seen(enrolled_device_id, enrollment_tenant)

    # ADR-0058: divergência observável — se o device reportou o config_version
    # que tem efetivamente aplicado (novo, opcional — agente antigo não manda),
    # compara contra o config_version CORRENTE do site e loga quando diferem
    # (ex.: o box precisa reiniciar pra pegar uma câmera nova). Best-effort:
    # nunca falha o ingest do heartbeat (mesmo padrão de
    # _bridge_heartbeat_to_telemetry logo abaixo).
    _log_config_divergence_if_any(
        str(enrollment_tenant), str(enrollment_site), enrolled_device_id,
        getattr(hb, "config_version_applied", None),
    )

    # Pilha A→B: espelha a telemetria do heartbeat no dashboard integrado ao vivo
    # (edge_telemetry_samples + socket /monitor). Best-effort — ver decisão do Vitor
    # (heartbeat alimenta a Pilha B) em docs/edge/DIAGNOSTICO_OBSERVABILIDADE_2026-07-21.md.
    _bridge_heartbeat_to_telemetry(
        str(enrollment_tenant), str(enrollment_site), hb, str(row["received_at"])
    )

    logger.info(
        "edge_heartbeat: device=%s tenant=%s status=%s",
        enrolled_device_id,
        str(enrollment_tenant)[:8],
        hb.status.value,
    )

    return success(
        {"id": row["id"], "received_at": str(row["received_at"])},
        status=201,
    )


# ---------------------------------------------------------------------------
# Config poll (WS10) — canal pull cloud→edge consumido pelo ConfigPoller
# do edge-sync-agent (services/edge-sync-agent/app/config_poller.py).
# ---------------------------------------------------------------------------


def _build_edge_config_payload(site_id: str, tenant_id: str) -> dict:
    """Monta o payload INTEIRO que o site do device deve estar rodando.

    Chaves de PRIMEIRO NÍVEL, exatamente as que o `ConfigPoller` do
    edge-sync-agent aplica parcialmente (`config_poller.py::_apply`):

      cameras  — inventário de câmeras do site (com `is_active`)
      rules    — operações das câmeras do site (o que a tela de Cenário grava)
      scenario — módulos habilitados do tenant + catálogo de classes
      model    — manifesto do modelo ativo, SÓ quando há digest real

    `model` sai com `r2_key` (estável) e não com a URL assinada: a assinatura
    muda a cada geração e entraria no hash, quebrando o 304 em TODO poll. Quem
    serve o 200 (`poll_edge_config`) troca `r2_key` pela URL na saída.

    ponytail: 4 consultas por chamada, e `_log_config_divergence_if_any` chama
    isto a cada heartbeat com `config_version_applied`. Índices cobrem as 4;
    se a frota crescer a ponto de doer, o caminho é cachear o payload por
    (site, versão) em Redis — não recortar o que é enviado.
    """
    cameras = _get_camera_repo().list_for_site_config(site_id, tenant_id)
    for cam in cameras:
        cam["id"] = str(cam["id"])

    rules = _get_operation_repo().list_for_site_config(site_id, tenant_id)
    for rule in rules:
        rule["camera_id"] = str(rule["camera_id"])

    payload: dict = {
        "cameras": cameras,
        "rules": rules,
        "scenario": _build_scenario(tenant_id),
    }
    model = _build_model_manifest(site_id, tenant_id)
    if model is not None:
        payload["model"] = model
    return payload


def _build_scenario(tenant_id: str) -> dict:
    """Módulos habilitados do tenant + classes de cada um.

    É a parte do cenário que NÃO está nas regras: habilitar/desabilitar um
    módulo e criar/desativar classe no Estúdio são mudanças de front que hoje
    nunca chegavam ao box.
    """
    mod_repo = _get_module_repo()
    modules = []
    for tm in mod_repo.get_by_tenant(str(tenant_id)):
        if not tm.get("enabled"):
            continue
        module_code = str(tm["module_code"])
        modules.append({
            "module_code": module_code,
            "classes": [
                {
                    "class_id": c["class_id"],
                    "class_name": c["class_name"],
                    "display_name": c.get("display_name"),
                    "is_violation": bool(c.get("is_violation")),
                    "is_active": bool(c.get("is_active", True)),
                }
                for c in mod_repo.get_classes(module_code)
            ],
        })
    modules.sort(key=lambda m: m["module_code"])
    return {"modules": modules}


def _build_model_manifest(site_id: str, tenant_id: str) -> "dict | None":
    """Manifesto do modelo ativo do site — ou None, sem inventar nada.

    ⚠️ O digest é OBRIGATÓRIO e tem de ser REAL. `ModelManifest.sha256` é o que
    o agente usa para decidir se já tem o modelo e (quando
    `model_manager.download_and_swap` existir) para conferir o download. Não
    existe coluna de digest no registry: a fonte é
    `trained_models.metrics.onnx_sha256`, gravado pela task
    `tasks.model_validation.record_onnx_digest`. Sem digest gravado → NENHUM
    `model` no payload (o apply parcial do agente não toca o estado) + uma
    linha de log dizendo qual modelo ficou de fora. Preencher esse campo com um hash de
    identidade (id + chave R2) seria plantar uma armadilha: pareceria um
    digest e reprovaria todo download no dia em que alguém o conferisse.

    O contrato do consumidor tem UM manifesto por box, não um por câmera. Se as
    câmeras do site apontam para modelos DIFERENTES, não há como dizer isso
    neste formato — omitimos e avisamos, em vez de eleger um e mandar o modelo
    errado para o resto das câmeras.
    """
    deployments = _get_deployment_repo().list_active_for_site(site_id, tenant_id)
    if not deployments:
        return None

    distintos: dict[str, dict] = {}
    sem_digest: list[str] = []
    for dep in deployments:
        metrics = dep.get("metrics") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except ValueError:
                metrics = {}
        sha256 = (metrics or {}).get("onnx_sha256")
        r2_key = dep.get("r2_onnx_key")
        if not sha256 or not r2_key:
            sem_digest.append(str(dep.get("model_id")))
            continue
        distintos[str(sha256)] = {
            "sha256": str(sha256),
            "r2_key": str(r2_key),
            "engine_type": "onnx",
        }

    if sem_digest:
        # INFO e não WARNING: é um ESTADO contínuo (fica assim até o digest ser
        # gravado), e `_log_config_divergence_if_any` monta este mesmo payload a
        # cada heartbeat — em WARNING viraria uma linha de alarme por minuto,
        # por device, para uma condição que não mudou.
        logger.info(
            "edge_config_model_sem_digest: site=%s modelos=%s — manifesto "
            "omitido (metrics.onnx_sha256 ausente; roda record_onnx_digest)",
            str(site_id)[:8], sorted(set(sem_digest)),
        )
    if len(distintos) != 1:
        if len(distintos) > 1:
            logger.warning(
                "edge_config_model_ambiguo: site=%s modelos_distintos=%d — "
                "manifesto omitido (contrato do agente tem 1 modelo por box)",
                str(site_id)[:8], len(distintos),
            )
        return None
    return next(iter(distintos.values()))


def _compute_config_version(payload: dict) -> str:
    """Hash de conteúdo do payload INTEIRO — ETag/config_version de
    `poll_edge_config`, reusado por `ingest_heartbeat` (ADR-0058) para comparar
    contra o `config_version_applied` que o device reporta.

    Cobre payload inteiro, não só `cameras`: enquanto derivava só das câmeras,
    mudar o cenário deixava a versão igual, o device recebia 304 e NUNCA
    aplicava a mudança — o jeito mais fácil de a promessa "salvar propaga ao
    box" continuar mentindo mesmo com o cenário já no payload.
    """
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@edge_bp.route("/config/poll", methods=["GET"])
@require_device_scope("config:read")  # DeviceTokenScope.config_read
def poll_edge_config() -> tuple:
    """Config das câmeras do site do device (device auth RS256 — sem JWT).

    CONTRATO DO CONSUMIDOR: o ConfigPoller aplica chaves de PRIMEIRO NÍVEL do
    body ({cameras, rules, scenario, model} — apply parcial), por isso esta
    rota NÃO usa o envelope success()/data. Chave ausente não é tocada no
    estado do agente — por isso `model` só aparece quando há digest real.

    As quatro chaves saem daqui (antes só `cameras` saía, e a tela de Cenário
    prometia ao operador uma propagação que nunca acontecia).

    Segurança: exige escopo config:read (S1); escopo site/tenant vem do
    enrollment do device (C-01); o SELECT é enxuto e NUNCA inclui
    username/password_encrypted (C-05) — o device usa credenciais locais.

    ADR-0058: `cameras[].channel` (já presente neste payload desde antes
    desta ADR) É o mapa canal→câmera do gravador que o edge-sync-agent
    passa a preferir sobre RECORDER_CHANNEL_MAP no .env — nenhuma mudança de
    payload foi necessária aqui, só a fiação do lado do agente.
    """
    tenant_id, site_id, device_id = g.device_ctx
    try:
        payload = _build_edge_config_payload(site_id, tenant_id)

        # F1 — versionamento por conteúdo: config_version + ETag derivados do
        # payload (sem migration). Device manda If-None-Match; se nada mudou →
        # 304 (o caso comum; evita 28 câmeras × poll × payload grande).
        config_version = _compute_config_version(payload)
        etag = f'"{config_version}"'

        if request.headers.get("If-None-Match") == etag:
            logger.info(
                "edge_config_poll: 304 device=%s site=%s v=%s",
                device_id, site_id[:8], config_version,
            )
            resp = make_response("", 304)
            resp.headers["ETag"] = etag
            resp.headers["Cache-Control"] = "no-cache"
            return resp

        payload["config_version"] = config_version
        _assinar_url_do_modelo(payload, tenant_id)
        logger.info(
            "edge_config_poll: 200 device=%s site=%s cameras=%d regras=%d "
            "modelo=%s v=%s",
            device_id, site_id[:8], len(payload["cameras"]),
            len(payload["rules"]),
            payload.get("model", {}).get("sha256", "-")[:12],
            config_version,
        )
        resp = make_response(jsonify(payload), 200)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    except Exception:
        logger.exception("edge_config_poll_error")
        return error("Erro ao consultar config", 500)


# ---------------------------------------------------------------------------
# OTA bare-metal: target_ref por canal (ADR-0057 item 10, sem Docker — ver
# docs/edge/REGRAS_PLATAFORMA_JETSON.md §3.4). A nuvem só INDICA; o agente
# decide se atualiza (app/ota/updater.py).
# ---------------------------------------------------------------------------


@edge_bp.route("/software/target", methods=["GET"])
@require_device_scope("config:read")  # DeviceTokenScope.config_read — mesmo escopo do config-poll
def get_software_target() -> tuple:
    """Ref-alvo do canal de software do device (device auth RS256).

    O canal vem do registro do device (`device_tokens.channel`, migration
    106) — não é escolhido pelo device via query param, é atribuído por um
    admin (`PUT /admin/software-channels/<channel>`). Sem target_ref
    publicado pro canal ainda -> `data: null` (o agente trata como "sem
    atualização", não como erro).
    """
    tenant_id, site_id, device_id = g.device_ctx
    device = _get_repo().get_device_by_device_id(device_id)
    if device is None or str(device["tenant_id"]) != tenant_id:
        return error("Dispositivo não encontrado", 404)

    channel = device.get("channel") or "dev"
    target_ref = _get_software_channel_repo().get_target_ref(channel)
    logger.info(
        "edge_software_target: device=%s channel=%s target_ref=%s",
        device_id, channel, target_ref,
    )
    return success({"channel": channel, "target_ref": target_ref})


# ---------------------------------------------------------------------------
# Shadow mode on-site (Onda 2, task-092-sequel): frame extraído pelo edge do
# NVR do cliente e enviado pro pool de anotação. O NVR fica numa VLAN
# isolada (ADR-0020) que a nuvem não alcança — a extração roda no edge
# (que está na LAN do gravador) e sobe o resultado, em vez da nuvem puxar
# o NVR diretamente (o que `nvr_extraction.py`/`extract_nvr_frames` faz
# para sites cloud-only). Mesmo destino (storage + training_frames,
# source='nvr') — não duplica schema/storage, só troca quem inicia o upload.
# ---------------------------------------------------------------------------


@edge_bp.route("/frames", methods=["POST"])
@require_device_scope("frames:write")
def upload_edge_frame() -> tuple:
    """Recebe um frame capturado on-site pelo edge (multipart).

    Campos: `file` (imagem, obrigatório) + form `camera_id`/`recorder_id`
    (obrigatórios — precisam pertencer ao MESMO tenant do device, C-01) +
    `captured_at` (opcional, ISO 8601) + `module_code` (opcional, default 'epi').
    """
    tenant_id, site_id, device_id = g.device_ctx

    rejection = _reject_if_content_length_exceeds(_MAX_FRAME_BYTES)
    if rejection is not None:
        return rejection

    file = request.files.get("file")
    if file is None:
        return error("Campo 'file' obrigatório (multipart)", 422)

    fname = file.filename or ""
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext not in _ALLOWED_FRAME_EXTS:
        return error(
            f"Extensão não suportada: {ext!r} (aceita: {sorted(_ALLOWED_FRAME_EXTS)})", 422
        )

    data = _read_bounded(file, _MAX_FRAME_BYTES)
    if data is None:
        return error(f"Arquivo excede o limite de {_MAX_FRAME_BYTES // (1024 * 1024)}MB", 413)
    if not data:
        return error("Arquivo vazio", 422)

    dimensions = _image_dimensions(data)
    if dimensions is None:
        return error("Arquivo não é uma imagem válida", 422)
    width, height = dimensions

    camera_id_raw = request.form.get("camera_id")
    recorder_id_raw = request.form.get("recorder_id")
    if not camera_id_raw or not recorder_id_raw:
        return error("camera_id e recorder_id são obrigatórios", 422)
    try:
        recorder_uuid = UUID(recorder_id_raw)
        camera_uuid = UUID(camera_id_raw)
    except ValueError:
        return error("camera_id/recorder_id devem ser UUID", 422)

    captured_at = None
    captured_at_raw = request.form.get("captured_at")
    if captured_at_raw:
        try:
            captured_at = datetime.fromisoformat(captured_at_raw.replace("Z", "+00:00"))
        except ValueError:
            return error("captured_at inválido (ISO 8601 esperado)", 422)

    # Vínculo do RECORTE com o frame de onde ele saiu (migration 132). Ausente
    # = a imagem é o quadro inteiro (fallback do coletor quando o detector está
    # off/indeterminado): `crop_origin` fica NULL, e isso é a leitura certa, não
    # um dado faltando.
    try:
        crop_origin = parse_crop_origin(request.form.get("crop_origin"))
    except ValueError as exc:
        return error(f"crop_origin inválido: {exc}", 422)

    # C-01: camera_id/recorder_id devem pertencer ao tenant do device
    # (do enrollment, nunca do payload) — cross-tenant -> 404, não vaza existência.
    if _get_camera_repo().get_by_id_and_tenant(camera_id_raw, tenant_id) is None:
        return error("Câmera não encontrada", 404)
    if _get_recorder_repo().get_by_id(recorder_uuid, tenant_id) is None:
        return error("Gravador não encontrado", 404)

    module_code = request.form.get("module_code") or "epi"

    # ESCOPO DE MÓDULO NA INGESTÃO — recusa ALTO, e antes do R2.
    #
    # Aqui a recusa é ruidosa de propósito, ao contrário do pool de anotação
    # (que filtra em silêncio e devolve a contagem). O modo de falha decide:
    #
    #  - descartar em silêncio: o coletor do edge continuaria subindo o mesmo
    #    frame a cada ciclo, para sempre, contra uma câmera que o dono não
    #    declarou. Custo de banda e de R2 real, invisível nos dois lados —
    #    ninguém aprende que a câmera não está vinculada. E como o upload
    #    acontece ANTES do INSERT, cada descarte silencioso deixaria um objeto
    #    órfão no bucket.
    #  - recusar alto: o agente do edge vê o 422 no log, para de tentar
    #    naquela câmera, e o dono tem uma pergunta concreta para responder na
    #    tela de atribuição. Nada se perde: não há trabalho humano nesse frame
    #    ainda — é o único ponto do fluxo onde a recusa não custa anotação.
    #
    # 422 e não 404: a câmera EXISTE e é deste tenant (acabou de ser conferida
    # acima). 404 aqui misturaria "não é sua" com "não é deste módulo" e o edge
    # não teria como distinguir — cross-tenant continua sendo 404, C-01 intacto.
    #
    # Enquanto o dono não declarar NENHUMA câmera para o módulo, camera_serves_
    # module devolve True para todas: escopo não declarado não bloqueia coleta.
    if not _get_camera_module_repo().camera_serves_module(
        tenant_id, camera_id_raw, module_code
    ):
        logger.warning(
            "edge_frame_fora_do_modulo: device=%s camera=%s module=%s (recusado)",
            device_id, camera_id_raw, module_code,
        )
        return error(
            f"Câmera não está vinculada ao módulo '{module_code}' — "
            "vincule na tela de atribuição de módulos antes de coletar",
            422,
        )

    r2_key = f"{R2Prefix.TRAINING_IMAGES}/{tenant_id}/nvr/{recorder_id_raw}/{uuid4()}.jpg"

    # Storage e banco em blocos separados: falha de storage é operacional
    # (credencial R2 errada, bucket sem permissão) e precisa ser distinguível
    # de falha de persistência — antes os dois viravam o mesmo 500 opaco, e o
    # edge não tinha como saber que o problema era o R2.
    try:
        from app.infrastructure.storage.local_storage import get_storage  # noqa: PLC0415

        get_storage(tenant_id).upload_bytes(r2_key, data, "image/jpeg")
    except StorageError as exc:
        # StorageError já vem sem credencial embutida (ver r2_storage.py e
        # local_storage.get_storage) — seguro pro log e pra resposta.
        logger.error(
            "edge_frame_storage_error device=%s camera=%s r2_key=%s err=%s",
            device_id, camera_id_raw, r2_key, exc,
        )
        return error(f"Falha no storage ao gravar o frame: {exc}", 502)
    except Exception:
        logger.exception(
            "edge_frame_storage_unexpected device=%s camera=%s r2_key=%s",
            device_id, camera_id_raw, r2_key,
        )
        return error("Falha inesperada no storage ao gravar o frame", 502)

    try:
        frame = _get_frame_repo().create(
            video_id=None,
            frame_number=0,
            filename=r2_key,
            source=FrameSource.NVR,
            r2_key=r2_key,
            camera_id=camera_uuid,
            recorder_id=recorder_uuid,
            width=width,
            height=height,
            captured_at=captured_at,
            tenant_id=tenant_id,
            module_code=module_code,
            crop_origin=crop_origin,
        )
    except Exception:
        # Objeto já está no R2 mas a linha não entrou: fica órfão. Logado com
        # a r2_key pra permitir reconciliação/limpeza depois.
        logger.exception(
            "edge_frame_persist_error device=%s camera=%s recorder=%s r2_key=%s "
            "(objeto órfão no storage)",
            device_id, camera_id_raw, recorder_id_raw, r2_key,
        )
        return error("Erro ao registrar o frame", 500)

    logger.info(
        "edge_frame_uploaded: device=%s camera=%s recorder=%s frame_id=%s",
        device_id, camera_id_raw, recorder_id_raw, frame.get("id"),
    )
    return success({"frame_id": str(frame["id"]), "r2_key": r2_key}, status=201)


@edge_bp.route("/cameras/<camera_id>/snapshot", methods=["POST"])
@require_device_scope("snapshot:write")  # DeviceTokenScope.snapshot_write
def upload_camera_snapshot(camera_id) -> tuple:
    """Recebe o JPEG de uma captura de snapshot (Bloco A: miniatura de
    triagem, CameraTriagePage) — multipart `file`, sem campos de form (a
    câmera já vem no path, diferente de /frames).

    Origem do JPEG no edge: RecorderClient.get_snapshot (ONVIF GetSnapshotUri,
    D-85, com fallback pro grab de frame RTSP — ver snapshot_executor.py),
    disparado por um edge_command `capture_snapshot`
    (app.api.v1.cameras.snapshot_handlers.refresh_camera_snapshot).

    Sobe pro R2 em snapshots/{tenant_id}/{camera_id}/{timestamp}.jpg e
    atualiza o cache (camera_snapshot_state.write_ready) que
    GET /api/cameras/<id>/snapshot lê — o cache é best-effort: se o Redis
    falhar aqui, o objeto já está no R2 (upload não é desfeito) e a próxima
    leitura do GET simplesmente vê "none" até uma nova captura.
    """
    tenant_id, site_id, device_id = g.device_ctx

    # C-01: câmera precisa pertencer ao tenant do device (do enrollment,
    # nunca do path) — cross-tenant -> 404, não vaza existência.
    if _get_camera_repo().get_by_id_and_tenant(camera_id, tenant_id) is None:
        return error("Câmera não encontrada", 404)

    rejection = _reject_if_content_length_exceeds(_MAX_SNAPSHOT_BYTES)
    if rejection is not None:
        return rejection

    file = request.files.get("file")
    if file is None:
        return error("Campo 'file' obrigatório (multipart)", 422)

    data = _read_bounded(file, _MAX_SNAPSHOT_BYTES)
    if data is None:
        return error(
            f"Arquivo excede o limite de {_MAX_SNAPSHOT_BYTES // (1024 * 1024)}MB", 413
        )
    if not data:
        return error("Arquivo vazio", 422)

    if _image_dimensions(data) is None:
        return error("Arquivo não é uma imagem válida", 422)

    r2_key = f"{R2Prefix.SNAPSHOTS}/{tenant_id}/{camera_id}/{int(time.time() * 1000)}.jpg"

    try:
        from app.infrastructure.storage.local_storage import get_storage  # noqa: PLC0415

        get_storage(tenant_id).upload_bytes(r2_key, data, "image/jpeg")
    except StorageError as exc:
        logger.error(
            "camera_snapshot_storage_error device=%s camera=%s r2_key=%s err=%s",
            device_id, camera_id, r2_key, exc,
        )
        return error(f"Falha no storage ao gravar o snapshot: {exc}", 502)
    except Exception:
        logger.exception(
            "camera_snapshot_storage_unexpected device=%s camera=%s r2_key=%s",
            device_id, camera_id, r2_key,
        )
        return error("Falha inesperada no storage ao gravar o snapshot", 502)

    try:
        from app.domain.services import camera_snapshot_state as snap_state  # noqa: PLC0415

        snap_state.write_ready(_get_redis(), tenant_id, camera_id, r2_key)
    except Exception:
        # Best-effort: objeto já subiu, cache é só uma otimização de leitura
        # (GET recalcula "none" até a próxima captura se o Redis falhar aqui).
        logger.warning(
            "camera_snapshot_cache_write_failed camera=%s r2_key=%s", camera_id, r2_key,
            exc_info=True,
        )

    logger.info(
        "camera_snapshot_uploaded: device=%s camera=%s r2_key=%s", device_id, camera_id, r2_key,
    )
    return success({"r2_key": r2_key}, status=201)


@edge_bp.route("/live-view/<camera_id>/segment", methods=["POST"])
@limiter.shared_limit(
    _edge_live_ingest_limit, scope="edge-live-ingest", key_func=get_ip_identifier,
)
@require_device_scope("stream:write")
def upload_live_view_segment(camera_id) -> tuple:
    """Recebe um segmento HLS (.ts) ou a playlist (.m3u8) que o edge está
    empurrando pro live-view — multipart `file` + form `filename` (nome que
    o hls.js do navegador vai pedir de volta, ex. "stream.m3u8"/"segment3.ts").

    Guardado em Redis (binário, TTL curto) sob `epi:edge_hls:{camera_id}:{filename}`
    — stream_handlers.py::serve_hls lê daqui antes de tentar o caminho antigo
    (gateway/LocalStreamManager, que não alcança uma câmera atrás de NVR numa
    LAN isolada). Sem storage novo, sem job de limpeza: um push que para de
    chegar expira sozinho.
    """
    tenant_id, site_id, device_id = g.device_ctx

    try:
        camera_uuid = UUID(camera_id)
    except ValueError:
        return error("camera_id deve ser UUID", 422)

    # C-01: câmera precisa pertencer ao tenant do device (do enrollment, nunca
    # do payload) — cross-tenant -> 404, não vaza existência.
    if _get_camera_repo().get_by_id_and_tenant(str(camera_uuid), tenant_id) is None:
        return error("Câmera não encontrada", 404)

    rejection = _reject_if_content_length_exceeds(_MAX_SEGMENT_BYTES)
    if rejection is not None:
        return rejection

    file = request.files.get("file")
    if file is None:
        return error("Campo 'file' obrigatório (multipart)", 422)

    filename = (request.form.get("filename") or file.filename or "").strip()
    if not filename or not _SAFE_HLS_FILENAME.match(filename):
        return error("filename inválido", 422)

    data = _read_bounded(file, _MAX_SEGMENT_BYTES)
    if data is None:
        return error(
            f"Segmento excede o limite de {_MAX_SEGMENT_BYTES // (1024 * 1024)}MB", 413
        )
    if not data:
        return error("Arquivo vazio", 422)

    try:
        r = _get_binary_redis()
        r.setex(f"epi:edge_hls:{camera_id}:{filename}", _HLS_SEGMENT_TTL, data)
        # LV-3: a resposta do push carrega o "ainda tem alguém assistindo?".
        # Enquanto transmite, o edge NÃO precisa de request separado pra saber
        # se deve continuar — economiza um poll por ciclo (ver live_view_loop).
        still_wanted = bool(r.exists(f"epi:stream:{camera_id}:active"))
    except Exception:
        logger.exception(
            "edge_live_view_segment_error device=%s camera=%s filename=%s",
            device_id, camera_id, filename,
        )
        return error("Erro ao processar segmento", 500)

    return success({"stored": True, "still_wanted": still_wanted}, status=201)


@edge_bp.route("/live-view/wanted", methods=["GET"])
@require_device_scope("stream:write")
def list_live_view_wanted() -> tuple:
    """Câmeras deste site com espectador ativo agora — sinal do LV-3.

    Fonte da verdade é a MESMA chave que o resto do sistema já usa
    (`epi:stream:{camera_id}:active`): criada por POST /stream/start (o botão
    da UI) e renovada por serve_hls a cada segmento que o player pede. Ou
    seja, "tem alguém assistindo" = alguém pediu E continua pedindo.

    Sem espectador, o edge não sobe FFmpeg nem empurra segmento — foi o
    consumo contínuo que motivou o LV-3 (a API roda com 1 worker gunicorn +
    `--max-requests`, então request contínuo recicla o worker).
    """
    tenant_id, site_id, device_id = g.device_ctx

    cameras = _get_camera_repo().list_for_site_config(site_id, tenant_id)
    if not cameras:
        return success({"camera_ids": []})

    try:
        r = _get_binary_redis()
        pipe = r.pipeline()
        for cam in cameras:
            pipe.exists(f"epi:stream:{cam['id']}:active")
        flags = pipe.execute()
    except Exception:
        logger.exception("edge_live_view_wanted_error device=%s", device_id)
        return error("Erro ao consultar espectadores", 500)

    wanted = [str(cam["id"]) for cam, active in zip(cameras, flags) if active]
    return success({"camera_ids": wanted})


# ---------------------------------------------------------------------------
# Observability: sites health (task-005)
# NOTE: must be registered before <site_id> dynamic routes to avoid ambiguity.
# ---------------------------------------------------------------------------

@edge_bp.route("/sites/health", methods=["GET"])
@jwt_required_custom
def get_sites_health(current_user_id) -> tuple:
    """Lista saúde dos sites do tenant com status derivado (O1).

    Status derivado: 'offline' se sem heartbeat ou heartbeat stale (> OFFLINE_THRESHOLD);
    caso contrário, usa o status do heartbeat (healthy/degraded/critical).
    """
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    repo = _get_repo()
    rows = repo.get_last_heartbeat_per_site(tenant_id)

    sites_health = []
    for row in rows:
        last_hb_at = row.get("received_at")
        derived_status = derive_site_health_status(last_hb_at, row.get("heartbeat_status"))
        sites_health.append({
            "site_id": str(row["site_id"]),
            "name": row["site_name"],
            "deployment_mode": row["deployment_mode"],
            "derived_status": derived_status,
            "last_heartbeat_at": str(last_hb_at) if last_hb_at else None,
            "inference_fps": float(row["inference_fps"]) if row.get("inference_fps") is not None else None,
            "cameras_online": row.get("cameras_online"),
            "cameras_total": row.get("cameras_total"),
            "cpu_pct": float(row["cpu_pct"]) if row.get("cpu_pct") is not None else None,
            "gpu_pct": float(row["gpu_pct"]) if row.get("gpu_pct") is not None else None,
            "gpu_mem_pct": (
                float(row["gpu_mem_pct"]) if row.get("gpu_mem_pct") is not None else None
            ),
            "queue_depth": row.get("queue_depth"),
            "gpu_temp_c": (
                float(row["gpu_temp_c"]) if row.get("gpu_temp_c") is not None else None
            ),
            "decode_pct": (
                float(row["decode_pct"]) if row.get("decode_pct") is not None else None
            ),
            "edge_version": row.get("edge_version"),
            "decode_fps": float(row["decode_fps"]) if row.get("decode_fps") is not None else None,
        })

    return success({"sites": sites_health})


# ---------------------------------------------------------------------------
# Observability: fleet overview (task-016)
# ---------------------------------------------------------------------------

@edge_bp.route("/overview", methods=["GET"])
@jwt_required_custom
def get_fleet_overview(current_user_id) -> tuple:
    """Contagens agregadas da frota edge do tenant (tela inicial do painel).

    Retorna:
      sites_total, sites_por_status, devices_total, devices_online,
      devices_revoked, sites_offline.

    sites_offline usa a MESMA regra de derive_site_health_status que /sites/health
    (C-05 — fonte única de verdade). Sites em 'provisioning' não contam como offline.
    """
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    hb_repo = _get_repo()
    site_repo = _get_site_repo()

    # Sites por status (tenant-scoped)
    status_rows = site_repo.get_site_status_counts(tenant_id)
    sites_por_status: dict[str, int] = {r["status"]: r["count"] for r in status_rows}
    sites_total = sum(sites_por_status.values())

    # Devices (tenant-scoped)
    device_counts = site_repo.get_device_fleet_counts(tenant_id, OFFLINE_THRESHOLD_SECONDS)
    devices_total = device_counts["total"]
    devices_online = device_counts["online"]
    devices_revoked = device_counts["revoked"]

    # Sites offline — mesma lógica de derive_site_health_status (C-05 fonte única)
    # Usa get_last_heartbeat_per_site_with_status para ter s.status disponível
    hb_rows = hb_repo.get_last_heartbeat_per_site_with_status(tenant_id)
    sites_offline = sum(
        1
        for row in hb_rows
        if is_site_offline(row.get("received_at"), row.get("heartbeat_status"), row["site_status"])
    )

    return success({
        "sites_total": sites_total,
        "sites_por_status": sites_por_status,
        "devices_total": devices_total,
        "devices_online": devices_online,
        "devices_revoked": devices_revoked,
        "sites_offline": sites_offline,
    })


# ---------------------------------------------------------------------------
# Admin: edge sites + enrollment tokens (task-003)
# ---------------------------------------------------------------------------

@edge_bp.route("/sites", methods=["POST"])
@jwt_required_custom
def create_site(current_user_id) -> tuple:
    """Cria edge site para o tenant do JWT (admin/superadmin only)."""
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    location = (body.get("location") or "").strip() or None
    deployment_mode = (body.get("deployment_mode") or "").strip()

    if not name:
        return error("Campo 'name' obrigatório", 400)
    if deployment_mode not in _VALID_DEPLOYMENT_MODES:
        return error("deployment_mode deve ser 'cloud', 'edge' ou 'hybrid'", 400)

    repo = _get_site_repo()
    site = repo.create_site(tenant_id, name, location, deployment_mode, str(current_user_id))
    logger.info("edge_site_created: tenant=%s site=%s", tenant_id[:8], site["id"])
    return success({"site": _serialize_site(site)}, status=201)


@edge_bp.route("/sites", methods=["GET"])
@jwt_required_custom
def list_sites(current_user_id) -> tuple:
    """Lista sites do tenant do JWT (admin/superadmin only)."""
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    repo = _get_site_repo()
    sites = repo.list_sites(tenant_id)
    return success({"sites": [_serialize_site(s) for s in sites]})


@edge_bp.route("/sites/<site_id>", methods=["GET"])
@jwt_required_custom
def get_site_detail(site_id, current_user_id) -> tuple:
    """Detalhe de um site: campos + nº de devices + saúde derivada (task-017)."""
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    repo = _get_site_repo()
    row = repo.get_site_detail(site_id, tenant_id)
    if row is None:
        return error("Site não encontrado", 404)

    derived_health = derive_site_health_status(
        row.get("last_heartbeat_at"), row.get("heartbeat_status")
    )

    return success({
        "site": {
            **_serialize_site(row),
            "device_count": int(row["device_count"]),
            "derived_health": derived_health,
            "last_heartbeat_at": (
                str(row["last_heartbeat_at"]) if row.get("last_heartbeat_at") else None
            ),
        }
    })


@edge_bp.route("/sites/<site_id>", methods=["PATCH"])
@jwt_required_custom
def update_site(site_id, current_user_id) -> tuple:
    """Atualização parcial de site: name, location, status, deployment_mode (task-017).

    tenant_id é imutável — ignorado se presente no body.
    Enums inválidos → 400. Site de outro tenant → 404 (C-01).
    """
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    body = request.get_json(silent=True) or {}
    body.pop("tenant_id", None)  # tenant_id imutável — nunca aceitar do body

    updates = {}

    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            return error("Campo 'name' não pode ser vazio", 400)
        updates["name"] = name

    if "location" in body:
        updates["location"] = (body["location"] or "").strip() or None

    if "status" in body:
        status = (body["status"] or "").strip()
        if status not in _VALID_SITE_STATUSES:
            return error(
                "status deve ser 'active', 'inactive', 'maintenance' ou 'provisioning'", 400
            )
        updates["status"] = status

    if "deployment_mode" in body:
        deployment_mode = (body["deployment_mode"] or "").strip()
        if deployment_mode not in _VALID_DEPLOYMENT_MODES:
            return error("deployment_mode deve ser 'cloud', 'edge' ou 'hybrid'", 400)
        updates["deployment_mode"] = deployment_mode

    repo = _get_site_repo()
    row = repo.update_site(site_id, tenant_id, updates)
    if row is None:
        return error("Site não encontrado", 404)

    logger.info(
        "edge_site_updated: tenant=%s site=%s user=%s fields=%s",
        tenant_id[:8], site_id, str(current_user_id)[:8], sorted(updates.keys()),
    )

    return success({"site": _serialize_site(row)})


@edge_bp.route("/sites/<site_id>/enrollment-tokens", methods=["POST"])
@jwt_required_custom
def create_enrollment_token(site_id, current_user_id) -> tuple:
    """Gera enrollment token one-time para o site (admin/superadmin only).

    Retorna plaintext UMA vez; no banco fica apenas o SHA-256 hash.
    """
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    repo = _get_site_repo()
    site = repo.get_site_by_id(site_id, tenant_id)
    if site is None:
        return error("Site não encontrado", 404)

    plaintext = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    record = repo.create_enrollment_token(
        site_id, tenant_id, token_hash, expires_at, str(current_user_id)
    )
    logger.info(
        "enrollment_token_created: tenant=%s site=%s token_id=%s",
        tenant_id[:8], site_id, record["id"],
    )
    return success(
        {
            "token": plaintext,
            "token_id": str(record["id"]),
            "site_id": site_id,
            "expires_at": str(record["expires_at"]),
            "used_at": None,
        },
        status=201,
    )


# ---------------------------------------------------------------------------
# Enrollment token management: list + revoke (task-012)
# ---------------------------------------------------------------------------

@edge_bp.route("/sites/<site_id>/enrollment-tokens", methods=["GET"])
@jwt_required_custom
def list_enrollment_tokens(site_id, current_user_id) -> tuple:
    """Lista enrollment tokens do site com status derivado — sem hash/plaintext (C-05)."""
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    repo = _get_site_repo()
    site = repo.get_site_by_id(site_id, tenant_id)
    if site is None:
        return error("Site não encontrado", 404)

    tokens = repo.list_enrollment_tokens(tenant_id, site_id)
    result = []
    for t in tokens:
        result.append({
            "id": str(t["id"]),
            "created_at": str(t["created_at"]) if t.get("created_at") else None,
            "expires_at": str(t["expires_at"]) if t.get("expires_at") else None,
            "used_at": str(t["used_at"]) if t.get("used_at") else None,
            "used_by_device_id": t.get("used_by_device_id"),
            "status": _derive_token_status(t),
        })
    return success({"tokens": result})


@edge_bp.route("/enrollment-tokens/<token_id>/revoke", methods=["POST"])
@jwt_required_custom
def revoke_enrollment_token(token_id, current_user_id) -> tuple:
    """Revoga enrollment token não utilizado (invalida por expires_at = now()).

    - Token já usado → 409
    - Token inexistente/cross-tenant → 404
    - Token já expirado → 200 no-op (idempotente)
    """
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    repo = _get_site_repo()
    token = repo.get_enrollment_token_by_id(token_id, tenant_id)
    if token is None:
        return error("Token não encontrado", 404)

    if token.get("used_at") is not None:
        return error("Token já foi utilizado e não pode ser revogado", 409)

    repo.revoke_enrollment_token_if_unused(token_id, tenant_id)
    logger.info(
        "enrollment_token_revoked: tenant=%s token_id=%s user=%s",
        tenant_id[:8], token_id, str(current_user_id)[:8],
    )
    return success({"revoked": True, "token_id": token_id})


# ---------------------------------------------------------------------------
# Device enrollment (task-004)
# ---------------------------------------------------------------------------

_DEFAULT_SCOPES = [s.value for s in DeviceTokenScope]


@edge_bp.route("/enroll", methods=["POST"])
def enroll_device() -> tuple:
    """Device registration: consume one-time enrollment token, persist public key.

    tenant_id/site_id come exclusively from the enrollment_tokens row (C-01).
    """
    body = request.get_json(silent=True) or {}
    try:
        req = EnrollmentRequest(**body)
    except ValidationError:
        return error("Payload inválido", 422, error_code="INVALID_PAYLOAD")

    token_hash = hashlib.sha256(req.enrollment_token.encode()).hexdigest()
    fingerprint = hashlib.sha256(req.public_key_pem.encode()).hexdigest()

    repo = _get_site_repo()
    try:
        device = repo.enroll_device(
            token_hash=token_hash,
            device_id=req.device_id,
            device_name=req.device_name,
            public_key_pem=req.public_key_pem,
            fingerprint=fingerprint,
        )
    except ValueError as exc:
        if str(exc) == _DEVICE_ALREADY_ENROLLED:
            logger.warning("edge_enroll: device_id=%s já ativo (não revogado)", req.device_id)
            return error("Dispositivo já cadastrado e ativo neste tenant", 409)
        logger.warning("edge_enroll: invalid/used/expired token device=%s", req.device_id)
        return error("Enrollment token inválido, expirado ou já utilizado", 401)
    except psycopg2.errors.UniqueViolation:
        logger.warning("edge_enroll: duplicate device_id=%s", req.device_id)
        return error("Dispositivo já cadastrado neste tenant", 409)

    logger.info(
        "edge_enrolled: device=%s tenant=%s site=%s",
        req.device_id,
        str(device["tenant_id"])[:8],
        str(device["site_id"])[:8],
    )
    return success(
        {
            "tenant_id": str(device["tenant_id"]),
            "site_id": str(device["site_id"]),
            "device_id": device["device_id"],
            "scopes": _DEFAULT_SCOPES,
        },
        status=201,
    )


# ---------------------------------------------------------------------------
# Observability: heartbeats history por site (task-009)
# ---------------------------------------------------------------------------

@edge_bp.route("/sites/<site_id>/heartbeats", methods=["GET"])
@jwt_required_custom
def list_site_heartbeats(site_id, current_user_id) -> tuple:
    """Série temporal de heartbeats de um site (paginada por cursor temporal).

    Query params:
      limit  — número de registros (default 100, máx 500)
      before — ISO timestamp exclusivo (cursor de paginação)
    """
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    # Validate site ownership (C-01 — 404 se site não pertencer ao tenant)
    site_repo = _get_site_repo()
    site = site_repo.get_site_by_id(site_id, tenant_id)
    if site is None:
        return error("Site não encontrado", 404)

    try:
        limit = int(request.args.get("limit", 100))
    except (ValueError, TypeError):
        limit = 100
    limit = min(limit, 500)
    before = request.args.get("before") or None

    repo = _get_repo()
    rows = repo.list_heartbeats(tenant_id, site_id, limit=limit, before=before)
    return success({"heartbeats": [_serialize_heartbeat_row(r) for r in rows]})


# ---------------------------------------------------------------------------
# Observability: heartbeat summary por site (task-018)
# ---------------------------------------------------------------------------

@edge_bp.route("/sites/<site_id>/heartbeat-summary", methods=["GET"])
@jwt_required_custom
def get_heartbeat_summary(site_id, current_user_id) -> tuple:
    """Métricas agregadas de saúde de um site numa janela temporal (task-018).

    Query params:
      window — duração da janela (ex: 24h, 7d); default 24h, máx 7d
    """
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    site_repo = _get_site_repo()
    site = site_repo.get_site_by_id(site_id, tenant_id)
    if site is None:
        return error("Site não encontrado", 404)

    window_seconds = _parse_window_seconds(request.args.get("window"))

    repo = _get_repo()
    summary = repo.summary_for_site(tenant_id, site_id, window_seconds)

    derived_status = derive_site_health_status(
        summary.get("last_received_at"), summary.get("last_status")
    )

    return success({
        "site_id": site_id,
        "window_seconds": window_seconds,
        "heartbeat_count": int(summary["heartbeat_count"]),
        "avg_inference_fps": (
            float(summary["avg_inference_fps"])
            if summary.get("avg_inference_fps") is not None else None
        ),
        "max_inference_fps": (
            float(summary["max_inference_fps"])
            if summary.get("max_inference_fps") is not None else None
        ),
        "avg_inference_latency_ms": (
            float(summary["avg_inference_latency_ms"])
            if summary.get("avg_inference_latency_ms") is not None else None
        ),
        "uptime_pct": (
            float(summary["uptime_pct"])
            if summary.get("uptime_pct") is not None else None
        ),
        "last_status": summary.get("last_status"),
        "last_received_at": (
            str(summary["last_received_at"]) if summary.get("last_received_at") else None
        ),
        "derived_status": derived_status,
    })


# ---------------------------------------------------------------------------
# Device management: list + revoke (task-010, task-011)
# ---------------------------------------------------------------------------

@edge_bp.route("/sites/<site_id>/devices", methods=["GET"])
@jwt_required_custom
def list_site_devices(site_id, current_user_id) -> tuple:
    """Lista devices enrollados no site — sem public_key_pem/fingerprint (C-05)."""
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    repo = _get_site_repo()
    site = repo.get_site_by_id(site_id, tenant_id)
    if site is None:
        return error("Site não encontrado", 404)

    devices = repo.list_devices(tenant_id, site_id)
    result = []
    for d in devices:
        result.append({
            "id": str(d["id"]),
            "device_id": d["device_id"],
            "device_name": d.get("device_name"),
            "revoked": d["revoked"],
            "last_seen_at": str(d["last_seen_at"]) if d.get("last_seen_at") else None,
            "enrolled_at": str(d["enrolled_at"]) if d.get("enrolled_at") else None,
        })
    return success({"devices": result})


@edge_bp.route("/devices/<device_pk>/revoke", methods=["POST"])
@jwt_required_custom
def revoke_device(device_pk, current_user_id) -> tuple:
    """Revoga dispositivo — corta acesso imediato (heartbeat passa a retornar 403).

    Idempotente: revogar device já revogado → 200 no-op.
    Cross-tenant → 404 (não vaza existência — C-01).
    """
    try:
        get_role()  # valida claim role — 401 se token sem role
        tenant_id = get_tenant_id()
    except AuthenticationError as exc:
        return error(str(exc), 401)

    if not has_permission(_MANAGE_PERMISSION):
        return error("Acesso negado: requer role admin ou superadmin", 403)

    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip() or None

    repo = _get_site_repo()
    row = repo.revoke_device(device_pk, tenant_id, str(current_user_id), reason)
    if row is None:
        return error("Dispositivo não encontrado", 404)

    logger.info(
        "edge_device_revoked: tenant=%s device_pk=%s revoked_by=%s",
        tenant_id[:8], device_pk, str(current_user_id)[:8],
    )
    return success({
        "revoked": True,
        "device_id": row.get("device_id"),
        "revoked_at": str(row["revoked_at"]) if row.get("revoked_at") else None,
    })

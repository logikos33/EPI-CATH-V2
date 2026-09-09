"""Blueprint /api/v1/edge/events — ingest e query de eventos do edge (migration 055).

POST /api/v1/edge/events/ingest   device auth — batch ingest com dedup
GET  /api/v1/edge/events          JWT — listar eventos do tenant
"""
import logging
from uuid import uuid4

from flask import Blueprint, g, request

from app.core.auth import get_tenant_id, jwt_required_custom
from app.core.device_auth import require_device_scope
from app.core.responses import error, success
from app.infrastructure.database.connection import DatabasePool
from app.infrastructure.database.repositories.edge_event_repository import (
    EdgeEventRepository,
)

edge_events_bp = Blueprint("edge_events", __name__, url_prefix="/api/v1/edge/events")
logger = logging.getLogger(__name__)

_VALID_EVENT_TYPES = {
    "detection", "alert_triggered", "model_loaded", "stream_started",
    "stream_stopped", "camera_offline", "camera_online", "health_check",
}


def _get_repo() -> EdgeEventRepository:
    return EdgeEventRepository(DatabasePool.get_instance())  # type: ignore[arg-type]


def _virar_alerta(tenant_id: str, site_id: str | None, evt: dict) -> bool:
    """Delega ao escritor único de alerta (inference.`alerta_de_evento_do_edge`).

    Import tardio: `...queue.tasks.inference` puxa o app do Celery, e o
    blueprint é importado no boot da API. Só a rota tem o `site_id` (vem do
    device token), e é por isso que ele atravessa daqui até `alerts.site_id`.
    """
    from app.infrastructure.queue.tasks.inference import (  # noqa: PLC0415
        alerta_de_evento_do_edge,
    )

    return alerta_de_evento_do_edge(
        tenant_id,
        site_id,
        evt.get("camera_id"),
        evt.get("payload") or {},
        evidence_r2_key=evt.get("evidence_r2_key"),
        occurred_at=evt.get("occurred_at"),
    )


@edge_events_bp.route("/ingest", methods=["POST"])
@require_device_scope("events:write")  # DeviceTokenScope.events_write
def ingest_events() -> tuple:
    """Ingest de batch de eventos vindo do edge (device auth + escopo events:write)."""
    tenant_id, site_id, device_id = g.device_ctx
    batch_id = request.headers.get("X-Batch-Id") or str(uuid4())

    body = request.get_json(silent=True) or {}
    events = body.get("events", [])
    if not isinstance(events, list) or len(events) > 500:
        return error("events deve ser lista com máximo 500 itens", 422)

    repo = _get_repo()
    ingested = 0
    alerts_created = 0
    alerts_failed = 0
    for evt in events:
        if not isinstance(evt, dict):
            continue
        event_type = evt.get("event_type", "")
        if not event_type:
            continue
        import hashlib, json as _json
        raw = _json.dumps(evt, sort_keys=True, default=str)
        dedup_key = f"{batch_id}:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
        row = repo.ingest(
            tenant_id=tenant_id,
            site_id=site_id,
            device_id=device_id,
            camera_id=evt.get("camera_id"),
            module=evt.get("module"),
            event_type=event_type,
            payload=evt.get("payload") or {},
            evidence_r2_key=evt.get("evidence_r2_key"),
            occurred_at=evt.get("occurred_at"),
            batch_id=batch_id,
            dedup_key=dedup_key,
        )
        if row:
            ingested += 1

        if event_type != "detection":
            continue
        # A detecção do box também tem de virar ALERTA — `edge_events` não tem
        # leitor no produto (a tela do operador lê `public.alerts`) e tudo que
        # chegava aqui morria numa tabela que ninguém consulta.
        #
        # Uma falha NÃO derruba o batch (até 500 eventos; os outros precisam
        # entrar) mas TAMBÉM não pode sumir: vai para o log em nível de erro e
        # para os contadores da resposta.
        try:
            if _virar_alerta(tenant_id, site_id, evt):
                alerts_created += 1
        except Exception as exc:  # noqa: BLE001 — um evento ruim não pode matar o lote
            alerts_failed += 1
            logger.error(
                "edge_event_alerta_falhou: batch=%s camera=%s err=%s",
                batch_id, evt.get("camera_id"), exc, exc_info=True,
            )

    if alerts_failed:
        logger.error(
            "edge_ingest_alertas_com_falha: batch=%s criados=%d falharam=%d",
            batch_id, alerts_created, alerts_failed,
        )
    return success({
        "ingested": ingested,
        "submitted": len(events),
        "batch_id": batch_id,
        "alerts_created": alerts_created,
        "alerts_failed": alerts_failed,
    })


@edge_events_bp.route("", methods=["GET"])
@jwt_required_custom
def list_events(current_user_id: str) -> tuple:
    """Lista eventos do tenant com filtros opcionais."""
    try:
        tenant_id = get_tenant_id()
        site_id = request.args.get("site_id")
        if not site_id:
            return error("site_id é obrigatório", 422)
        limit = min(int(request.args.get("limit", 100)), 500)
        before = request.args.get("before")
        event_type = request.args.get("event_type")
        rows = _get_repo().list_by_site(
            tenant_id=tenant_id,
            site_id=site_id,
            limit=limit,
            before=before,
            event_type=event_type,
        )
        return success({"events": rows, "count": len(rows)})
    except Exception:
        logger.exception("list_events_error")
        return error("Erro ao listar eventos", 500)

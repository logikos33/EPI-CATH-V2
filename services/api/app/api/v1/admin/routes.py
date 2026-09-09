"""
Recognition — Admin Routes (superadmin only).

Todos os endpoints protegidos por @require_superadmin exceto /workers/heartbeat
que usa X-Worker-Secret header.

Grupos de endpoints:
  Dashboard     GET  /api/v1/admin/dashboard
  Tenants       GET|POST /tenants, GET|PATCH /tenants/<id>
                POST /tenants/<id>/suspend|reactivate
                GET  /tenants/<id>/plan-history, GET /tenants/<id>/overview
  Users         GET|POST /users, GET|PATCH /users/<id>
                POST /users/<id>/deactivate|reactivate|force-password-reset
                GET|DELETE /users/<id>/sessions
                GET  /permissions/matrix
  Approvals     GET|POST /training-approvals, GET /training-approvals/<id>
                POST /training-approvals/<id>/approve|reject
  Workers       GET /workers, GET /workers/<schema>
                POST /workers/<schema>/restart
                GET  /workers/<schema>/metrics
                POST /workers/heartbeat  (X-Worker-Secret, sem JWT)
  Plans         GET|POST /plans, PATCH /plans/<id>
                GET  /plans/<id>/tenants
  Flags         GET|PATCH /feature-flags
                GET|PATCH /feature-flags/tenant/<id>
  Tickets       GET /tickets, GET|PATCH /tickets/<id>
                POST /tickets/<id>/reply
                GET  /tickets/stats
  Audit         GET /audit-log, GET /audit-log/export (CSV)
  Announcements GET|POST|PATCH|DELETE /announcements
  Health        GET /health/platform, GET /health/metrics

Endpoints cliente (não-admin):
  GET  /api/v1/announcements          (JWT de qualquer role)
  POST /api/v1/announcements/<id>/read
"""
import csv
import io
import json
import logging
import os
import re
import secrets
import uuid
from datetime import datetime

import redis as _redis
from flask import Blueprint, Response, current_app, request
from flask_jwt_extended import jwt_required
from psycopg2 import errors as pg_errors

from app.constants import PLATFORM_MODULES
from app.core.auth import get_current_user_id, get_role, hash_password
from app.core.responses import error, success
from app.core.tenant import (
    invalidate_schema_cache,
    log_audit,
    require_superadmin,
    set_search_path,
    validate_schema,
)
from app.infrastructure.database.connection import DatabasePool
from app.infrastructure.database.repositories.edge_software_channel_repository import (
    EdgeSoftwareChannelRepository,
)

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/v1/admin")
client_bp = Blueprint("client_announcements", __name__, url_prefix="/api/v1")

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pool():
    pool = DatabasePool.get_instance()
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    return pool


def _get_redis():
    return _redis.from_url(_REDIS_URL, decode_responses=True)


def _row_to_dict(cur, row):
    """Converte linha de cursor em dict usando description."""
    return _clean_row(dict(row))


def _rows_to_list(cur, rows):
    return [_clean_row(dict(r)) for r in rows]


def _serialize(obj):
    """Serializa UUIDs e datetimes para JSON."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "hex"):
        return str(obj)
    return obj


def _clean_row(row: dict) -> dict:
    """Converte UUIDs e datetimes para strings em um dict."""
    result = {}
    for k, v in row.items():
        if v is None:
            result[k] = None
        elif hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        elif hasattr(v, "hex") and not isinstance(v, (int, float, bool)):
            result[k] = str(v)
        else:
            result[k] = v
    return result


def _get_actor():
    """Retorna (actor_id, actor_role) do JWT atual."""
    try:
        actor_id = get_current_user_id()
        actor_role = get_role()
        return actor_id, actor_role
    except Exception:
        return None, "superadmin"


def _get_ip():
    return request.remote_addr


def _get_ua():
    return request.headers.get("User-Agent", "")[:500]


def _page_params():
    """Extrai page e per_page da querystring."""
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    offset = (page - 1) * per_page
    return page, per_page, offset


def _validate_tenant_policy_updates(updates: dict) -> str | None:
    """Valida campos de política de plataforma do tenant (WS6 — 051/079).

    Retorna mensagem de erro pt-BR ou None se tudo válido.
    """
    from app.api.v1.retention.routes import ALLOWED_TIERS

    if "max_seats" in updates:
        v = updates["max_seats"]
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
            return "max_seats deve ser um inteiro maior que zero ou nulo (ilimitado)"
    if "single_session" in updates and not isinstance(updates["single_session"], bool):
        return "single_session deve ser booleano"
    if "rate_limit_per_minute" in updates:
        v = updates["rate_limit_per_minute"]
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
            return "rate_limit_per_minute deve ser um inteiro maior que zero ou nulo (herda do plano)"
    if "default_retention_days" in updates:
        v = updates["default_retention_days"]
        if v is not None and (
            isinstance(v, bool) or not isinstance(v, int) or v not in ALLOWED_TIERS
        ):
            return (
                "default_retention_days deve ser um dos tiers permitidos: "
                f"{sorted(ALLOWED_TIERS)}"
            )
    return None


# ---------------------------------------------------------------------------
# GET /api/v1/admin/dashboard
# ---------------------------------------------------------------------------
@admin_bp.route("/dashboard", methods=["GET"])
@require_superadmin
def get_dashboard():
    try:
        pool = _pool()
        with pool.get_connection() as conn:  # noqa: SIM117
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM tenants WHERE is_active = true")
                tenants_active = cur.fetchone()["count"]

                cur.execute("SELECT COUNT(*) FROM users")
                users_total = cur.fetchone()["count"]

                cur.execute(
                    "SELECT COUNT(*) FROM training_approvals WHERE status = 'pending'"
                )
                approvals_pending = cur.fetchone()["count"]

                cur.execute(
                    "SELECT COUNT(*) FROM support_tickets WHERE status = 'open'"
                    if False else
                    "SELECT 0"
                )

                # Audit log: últimos 10 eventos críticos
                cur.execute("""
                    SELECT al.id, al.action, al.target_type, al.actor_role,
                           al.created_at, u.email AS actor_email,
                           t.name AS tenant_name
                    FROM public.audit_log al
                    LEFT JOIN public.users u ON u.id = al.actor_id
                    LEFT JOIN public.tenants t ON t.id = al.tenant_id
                    WHERE al.action IN ('suspended','worker_restart','training_rejected','deactivated')
                    ORDER BY al.created_at DESC LIMIT 10
                """)  # noqa: E501
                critical_rows = cur.fetchall()
                critical_events = [_clean_row(dict(r)) for r in critical_rows]

                # Workers status
                from app.infrastructure.queue.worker_registry import get_all_workers_status
                workers_list = get_all_workers_status()
                workers_summary = {"online": 0, "fallback": 0, "offline": 0}
                for w in workers_list:
                    s = w.get("status", "offline")
                    if s == "onpremise":
                        workers_summary["online"] += 1
                    elif s == "railway":
                        workers_summary["fallback"] += 1
                    else:
                        workers_summary["offline"] += 1

                cur.execute("""
                    SELECT t.name, COUNT(u.id) AS user_count
                    FROM tenants t
                    LEFT JOIN users u ON u.tenant_id = t.id
                    WHERE t.is_active = true
                    GROUP BY t.id, t.name
                    ORDER BY user_count DESC LIMIT 5
                """)
                top_rows = cur.fetchall()
                top_tenants = [{"tenant_name": r["name"], "user_count": r["user_count"]} for r in top_rows]  # noqa: E501

        return success({
            "tenants_active": tenants_active,
            "users_total": users_total,
            "cameras_online": 0,
            "alerts_24h": 0,
            "training_approvals_pending": approvals_pending,
            "tickets_open": 0,
            "mrr_estimated": 0,
            "workers": workers_summary,
            "recent_critical_events": critical_events,
            "top_tenants_users": top_tenants,
        })
    except Exception as exc:
        logger.error("admin_dashboard_error: %s", exc, exc_info=True)
        return error("Erro ao carregar dashboard", 500)


# ---------------------------------------------------------------------------
# Tenants — list
# ---------------------------------------------------------------------------
@admin_bp.route("/tenants", methods=["GET"])
@require_superadmin
def list_tenants():
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT
                        t.id, t.slug, t.name, t.plan, t.schema_name,
                        t.is_active, t.modules_enabled, t.created_at,
                        t.suspended_at,
                        COUNT(u.id) AS user_count
                    FROM tenants t
                    LEFT JOIN users u ON u.tenant_id = t.id
                    GROUP BY t.id
                    ORDER BY t.created_at DESC
                """)
            rows = cur.fetchall()
            tenants = []
            for row in rows:
                r = _clean_row(dict(row))
                if isinstance(r.get("modules_enabled"), str):
                    try:
                        r["modules_enabled"] = json.loads(r["modules_enabled"])
                    except Exception:
                        r["modules_enabled"] = []
                tenants.append(r)

        return success({"tenants": tenants})
    except Exception as exc:
        logger.error("list_tenants_error: %s", exc, exc_info=True)
        return error("Erro ao listar tenants", 500)


# ---------------------------------------------------------------------------
# Tenants — create
# ---------------------------------------------------------------------------
@admin_bp.route("/tenants", methods=["POST"])
@require_superadmin
def create_tenant():
    try:
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        slug = (data.get("slug") or "").strip().lower()
        plan = data.get("plan", "standard")
        modules_enabled = data.get("modules_enabled", ["epi", "counting", "basic"])

        if not name or not slug:
            return error("name e slug são obrigatórios", 400)
        if not slug.replace("-", "").isalnum():
            return error("slug inválido: use apenas letras, números e hifens", 400)

        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM tenants WHERE slug = %s", (slug,))
                if cur.fetchone():
                    return error(f"Slug '{slug}' já existe", 409)

                tenant_id = str(uuid.uuid4())
                modules_json = json.dumps(modules_enabled)
                cur.execute("""
                    INSERT INTO tenants
                      (id, slug, name, plan, schema_name, modules_enabled, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, true)
                    RETURNING id, slug, name, plan, schema_name, is_active
                """, (tenant_id, slug, name, plan, slug, modules_json))
                tenant = _clean_row(dict(cur.fetchone()))

                cur.execute("SELECT public.create_tenant_schema(%s)", (slug,))

                admin_email = f"admin@{slug}.epimonitor.local"
                # SEC (task-076 / achado #4): senha aleatória forte, nunca
                # derivada do slug (público) — token_urlsafe(12) gera ~16
                # chars / 96 bits de entropia. force_password_reset=true força
                # troca no 1º login (mesma coluna usada em create_user/
                # force_password_reset, migration 029_admin_panel.sql).
                temp_password = secrets.token_urlsafe(12)
                password_hash = hash_password(temp_password)
                cur.execute("""
                    INSERT INTO users
                      (email, password_hash, name, role, tenant_id,
                       is_active, force_password_reset)
                    VALUES (%s, %s, %s, 'admin', %s, true, true)
                    ON CONFLICT (email) DO NOTHING
                """, (admin_email, password_hash, f"Admin {name}", tenant_id))

            conn.commit()

        invalidate_schema_cache()
        actor_id, actor_role = _get_actor()
        log_audit(actor_id, actor_role, tenant_id, "tenant", tenant_id,
                  "created", new_value={"slug": slug, "plan": plan},
                  ip_address=_get_ip(), user_agent=_get_ua())

        return success({
            "tenant": tenant,
            "admin_email": admin_email,
            "temp_password": temp_password,
        }, status=201)
    except Exception as exc:
        logger.error("create_tenant_error: %s", exc, exc_info=True)
        return error("Erro ao criar tenant", 500)


# ---------------------------------------------------------------------------
# Tenants — get detail
# ---------------------------------------------------------------------------
@admin_bp.route("/tenants/<tenant_id>", methods=["GET"])
@require_superadmin
def get_tenant(tenant_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT t.*, COUNT(u.id) AS user_count
                    FROM tenants t
                    LEFT JOIN users u ON u.tenant_id = t.id
                    WHERE t.id = %s
                    GROUP BY t.id
                """, (tenant_id,))
            row = cur.fetchone()
            if not row:
                return error("Tenant não encontrado", 404)
            tenant = _clean_row(dict(row))
            if isinstance(tenant.get("modules_enabled"), str):
                try:
                    tenant["modules_enabled"] = json.loads(tenant["modules_enabled"])
                except Exception:
                    tenant["modules_enabled"] = []

            # Usuários do tenant (custom_role_id + override count p/ badge
            # 'Customizado' — WS6/WS7)
            cur.execute("""
                    SELECT u.id, u.email, u.name, u.role, u.is_active,
                           u.created_at, u.last_login_at, u.login_count,
                           u.custom_role_id,
                           (SELECT COUNT(*)
                              FROM public.user_permission_overrides o
                             WHERE o.user_id = u.id) AS permission_override_count
                    FROM users u WHERE u.tenant_id = %s ORDER BY u.created_at
                """, (tenant_id,))
            users_rows = cur.fetchall()
            tenant["users"] = [_clean_row(dict(r)) for r in users_rows]

            # Aprovações de treinamento pendentes
            cur.execute("""
                    SELECT id, module, job_name, status, created_at
                    FROM public.training_approvals
                    WHERE tenant_id = %s AND status = 'pending'
                    ORDER BY created_at DESC LIMIT 5
                """, (tenant_id,))
            ap_rows = cur.fetchall()
            tenant["pending_approvals"] = [
                _clean_row(dict(r)) for r in ap_rows
            ]

            # WS6 — políticas de plataforma: assentos em uso + efetivos do plano
            cur.execute(
                "SELECT COUNT(*) AS count FROM users "
                "WHERE tenant_id = %s AND is_active = true",
                (tenant_id,),
            )
            seats_row = cur.fetchone()
            tenant["seats_in_use"] = int(seats_row["count"]) if seats_row else 0

            cur.execute(
                "SELECT api_rate_per_minute, video_retention_days "
                "FROM public.plans WHERE slug = %s",
                (tenant.get("plan"),),
            )
            plan_row = cur.fetchone()
            tenant["plan_rate_limit_per_minute"] = (
                plan_row["api_rate_per_minute"] if plan_row else None
            )
            tenant["plan_retention_days"] = (
                plan_row["video_retention_days"] if plan_row else None
            )

        # Worker status
        schema = tenant.get("schema_name")
        if schema:
            from app.infrastructure.queue.worker_registry import (
                get_worker_metrics,
                get_worker_status,
            )
            tenant["worker_status"] = get_worker_status(schema)
            tenant["worker_metrics"] = get_worker_metrics(schema)

        return success({"tenant": tenant})
    except Exception as exc:
        logger.error("get_tenant_error: %s", exc, exc_info=True)
        return error("Erro ao buscar tenant", 500)


# ---------------------------------------------------------------------------
# Tenants — update
# ---------------------------------------------------------------------------
@admin_bp.route("/tenants/<tenant_id>", methods=["PATCH"])
@require_superadmin
def update_tenant(tenant_id: str):
    try:
        data = request.get_json() or {}
        allowed_fields = {"plan", "modules_enabled", "active",
                          "requires_training_approval", "internal_notes",
                          "mrr_per_camera", "contract_cameras", "max_cameras",
                          "video_retention_days",
                          # WS6 — políticas de plataforma (migrations 051/079):
                          # colunas já existiam, faltava a escrita
                          "max_seats", "single_session",
                          "rate_limit_per_minute", "default_retention_days"}
        updates = {k: v for k, v in data.items() if k in allowed_fields}
        if not updates:
            return error("Nenhum campo válido para atualizar", 400)

        policy_error = _validate_tenant_policy_updates(updates)
        if policy_error:
            return error(policy_error, 400)

        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                # Ler estado anterior para audit
                cur.execute(
                    "SELECT plan, modules_enabled, is_active FROM tenants WHERE id = %s",
                    (tenant_id,),
                )
                old_row = cur.fetchone()
                if not old_row:
                    return error("Tenant não encontrado", 404)
                old_plan = old_row["plan"]

                if "plan" in updates:
                    cur.execute(
                        "UPDATE tenants SET plan = %s WHERE id = %s",
                        (updates["plan"], tenant_id),
                    )
                    if updates["plan"] != old_plan:
                        actor_id, _ = _get_actor()
                        cur.execute("""
                            INSERT INTO public.tenant_plan_history
                              (tenant_id, old_plan, new_plan, changed_by, notes)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (tenant_id, old_plan, updates["plan"],
                              str(actor_id) if actor_id else None,
                              data.get("plan_change_notes")))

                if "modules_enabled" in updates:
                    cur.execute(
                        "UPDATE tenants SET modules_enabled = %s::jsonb WHERE id = %s",
                        (json.dumps(updates["modules_enabled"]), tenant_id),
                    )
                if "active" in updates:
                    cur.execute(
                        "UPDATE tenants SET is_active = %s WHERE id = %s",
                        (bool(updates["active"]), tenant_id),
                    )
                for field in ("requires_training_approval", "internal_notes",
                              "mrr_per_camera", "contract_cameras",
                              "max_cameras", "video_retention_days",
                              "max_seats", "single_session",
                              "rate_limit_per_minute",
                              "default_retention_days"):
                    if field in updates:
                        val = updates[field]
                        if field == "mrr_per_camera":
                            cur.execute(
                                f"UPDATE tenants SET {field} = %s::jsonb WHERE id = %s",  # noqa: S608
                                (json.dumps(val), tenant_id),
                            )
                        else:
                            cur.execute(
                                f"UPDATE tenants SET {field} = %s WHERE id = %s",  # noqa: S608
                                (val, tenant_id),
                            )
            conn.commit()

        actor_id, actor_role = _get_actor()
        log_audit(actor_id, actor_role, tenant_id, "tenant", tenant_id,
                  "updated", new_value=updates,
                  ip_address=_get_ip(), user_agent=_get_ua())

        return success({"updated": True})
    except Exception as exc:
        logger.error("update_tenant_error: %s", exc, exc_info=True)
        return error("Erro ao atualizar tenant", 500)


# ---------------------------------------------------------------------------
# Tenants — suspend
# ---------------------------------------------------------------------------
@admin_bp.route("/tenants/<tenant_id>/suspend", methods=["POST"])
@require_superadmin
def suspend_tenant(tenant_id: str):
    try:
        data = request.get_json() or {}
        reason = data.get("reason", "")

        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM tenants WHERE id = %s AND is_active = true",
                    (tenant_id,),
                )
                if not cur.fetchone():
                    return error("Tenant não encontrado ou já suspenso", 404)

                cur.execute("""
                    UPDATE tenants
                    SET is_active = false, suspended_at = NOW(), suspended_by = %s
                    WHERE id = %s
                """, (str(actor_id) if actor_id else None, tenant_id))

                # Revogar sessões ativas do tenant
                cur.execute("""
                    UPDATE public.active_sessions
                    SET revoked_at = NOW(), revoked_by = %s
                    WHERE tenant_id = %s AND revoked_at IS NULL AND expires_at > NOW()
                    RETURNING jti, expires_at
                """, (str(actor_id) if actor_id else None, tenant_id))
                revoked_rows = cur.fetchall()
            conn.commit()

        # P1 fix: sem isso, o UPDATE acima marcava a sessão revogada no Postgres
        # mas o JWT (até 24h) continuava passando no token_in_blocklist_loader —
        # o tenant suspenso seguia com acesso pleno até o token expirar sozinho.
        if revoked_rows:
            from app.domain.services.session_service import blocklist_jtis  # noqa: PLC0415
            blocklist_jtis([dict(r) for r in revoked_rows])

        log_audit(actor_id, actor_role, tenant_id, "tenant", tenant_id,
                  "suspended", new_value={"reason": reason},
                  ip_address=_get_ip(), user_agent=_get_ua())
        return success({"suspended": True})
    except Exception as exc:
        logger.error("suspend_tenant_error: %s", exc, exc_info=True)
        return error("Erro ao suspender tenant", 500)


# ---------------------------------------------------------------------------
# Tenants — reactivate
# ---------------------------------------------------------------------------
@admin_bp.route("/tenants/<tenant_id>/reactivate", methods=["POST"])
@require_superadmin
def reactivate_tenant(tenant_id: str):
    try:
        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE tenants
                    SET is_active = true, suspended_at = NULL, suspended_by = NULL
                    WHERE id = %s
                """, (tenant_id,))
            conn.commit()

        log_audit(actor_id, actor_role, tenant_id, "tenant", tenant_id,
                  "reactivated", ip_address=_get_ip(), user_agent=_get_ua())
        return success({"reactivated": True})
    except Exception as exc:
        logger.error("reactivate_tenant_error: %s", exc, exc_info=True)
        return error("Erro ao reativar tenant", 500)


# ---------------------------------------------------------------------------
# Tenants — overview (read-only no schema do tenant)
# ---------------------------------------------------------------------------
@admin_bp.route("/tenants/<tenant_id>/overview", methods=["GET"])
@require_superadmin
def tenant_overview(tenant_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, slug, plan, schema_name FROM tenants WHERE id = %s",
                (tenant_id,),
            )
            row = cur.fetchone()
            if not row:
                return error("Tenant não encontrado", 404)
            tenant = _clean_row(dict(row))
            schema_name = tenant.get("schema_name", "public")

        if not validate_schema(schema_name):
            return error(f"Schema inválido: {schema_name}", 400)

        cameras, recent_alerts, training_jobs = [], [], []
        with pool.get_connection() as conn2, conn2.cursor() as cur2:
            set_search_path(conn2, schema_name)
            try:
                cur2.execute(
                    "SELECT id, name, status, active_module FROM cameras LIMIT 50"
                )
                cameras = [_clean_row(dict(r)) for r in cur2.fetchall()]
            except Exception:  # noqa: S110
                pass
            try:
                cur2.execute("""
                        SELECT id, violation_type, confidence, created_at
                        FROM alerts
                        WHERE created_at >= NOW() - INTERVAL '24 hours'
                        ORDER BY created_at DESC LIMIT 20
                    """)
                recent_alerts = [_clean_row(dict(r)) for r in cur2.fetchall()]
            except Exception:  # noqa: S110
                pass
            try:
                cur2.execute("""
                        SELECT id, name, status, module, created_at
                        FROM training_jobs ORDER BY created_at DESC LIMIT 10
                    """)
                training_jobs = [_clean_row(dict(r)) for r in cur2.fetchall()]
            except Exception:  # noqa: S110
                pass

        return success({
            "tenant": tenant,
            "cameras": cameras,
            "recent_alerts": recent_alerts,
            "training_jobs": training_jobs,
        })
    except Exception as exc:
        logger.error("tenant_overview_error: %s", exc, exc_info=True)
        return error("Erro ao carregar overview do tenant", 500)


# ---------------------------------------------------------------------------
# Tenants — plan history
# ---------------------------------------------------------------------------
@admin_bp.route("/tenants/<tenant_id>/plan-history", methods=["GET"])
@require_superadmin
def tenant_plan_history(tenant_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT tph.*, u.email AS changed_by_email
                    FROM public.tenant_plan_history tph
                    LEFT JOIN public.users u ON u.id = tph.changed_by
                    WHERE tph.tenant_id = %s
                    ORDER BY tph.created_at DESC
                """, (tenant_id,))
            rows = cur.fetchall()
            history = [_clean_row(dict(r)) for r in rows]
        return success({"history": history})
    except Exception as exc:
        logger.error("plan_history_error: %s", exc, exc_info=True)
        return error("Erro ao buscar histórico de planos", 500)


# ---------------------------------------------------------------------------
# Users — list
# ---------------------------------------------------------------------------
@admin_bp.route("/users", methods=["GET"])
@require_superadmin
def list_users():
    try:
        _, per_page, offset = _page_params()
        tenant_filter = request.args.get("tenant_id")
        role_filter = request.args.get("role")
        active_filter = request.args.get("active")
        search = request.args.get("search", "").strip()

        conditions = ["1=1"]
        params: list = []
        if tenant_filter:
            conditions.append("u.tenant_id = %s")
            params.append(tenant_filter)
        if role_filter:
            conditions.append("u.role = %s")
            params.append(role_filter)
        if active_filter is not None:
            conditions.append("u.is_active = %s")
            params.append(active_filter.lower() == "true")
        if search:
            conditions.append("(u.email ILIKE %s OR u.name ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])
        where = " AND ".join(conditions)

        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM users u WHERE {where}", tuple(params)  # noqa: S608
            )
            total = cur.fetchone()["count"]

            cur.execute(f"""
                    SELECT u.id, u.email, u.name, u.role, u.tenant_id,
                           u.is_active, u.created_at, u.last_login_at,
                           u.login_count, u.force_password_reset,
                           u.custom_role_id,
                           (SELECT COUNT(*)::int
                              FROM public.user_permission_overrides o
                             WHERE o.user_id = u.id) AS permission_override_count,
                           t.name AS tenant_name
                    FROM users u
                    LEFT JOIN tenants t ON t.id = u.tenant_id
                    WHERE {where}
                    ORDER BY u.created_at DESC
                    LIMIT %s OFFSET %s
                """, tuple(params) + (per_page, offset))  # noqa: S608
            rows = cur.fetchall()
            users = [_clean_row(dict(r)) for r in rows]

        return success({"items": users, "total": total})
    except Exception as exc:
        logger.error("list_users_error: %s", exc, exc_info=True)
        return error("Erro ao listar usuários", 500)


# ---------------------------------------------------------------------------
# Users — get detail
# ---------------------------------------------------------------------------
@admin_bp.route("/users/<user_id>", methods=["GET"])
@require_superadmin
def get_user(user_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT u.*, t.name AS tenant_name
                    FROM users u
                    LEFT JOIN tenants t ON t.id = u.tenant_id
                    WHERE u.id = %s
                """, (user_id,))
            row = cur.fetchone()
            if not row:
                return error("Usuário não encontrado", 404)
            user = _clean_row(dict(row))
            user.pop("password_hash", None)

            # Últimas 10 ações no audit_log
            cur.execute("""
                    SELECT action, target_type, target_id, created_at, ip_address
                    FROM public.audit_log
                    WHERE actor_id = %s
                    ORDER BY created_at DESC LIMIT 10
                """, (user_id,))
            al_rows = cur.fetchall()
            user["recent_actions"] = [
                _clean_row(dict(r)) for r in al_rows
            ]

        return success({"user": user})
    except Exception as exc:
        logger.error("get_user_error: %s", exc, exc_info=True)
        return error("Erro ao buscar usuário", 500)


# ---------------------------------------------------------------------------
# Users — create
# ---------------------------------------------------------------------------
@admin_bp.route("/users", methods=["POST"])
@require_superadmin
def create_user():
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        role = data.get("role", "operator")
        tenant_id = data.get("tenant_id")
        access_expires_at = data.get("access_expires_at")
        # Nome de gente. Sem ele o cadastro caía em `email.split("@")[0]`, e o
        # operador via "vitorsantu8" ao validar um evento — o crachá do sistema
        # virava o pedaço do e-mail. Continua opcional: scripts de seed e
        # chamadas antigas seguem funcionando com o fallback.
        nome = (data.get("name") or "").strip()

        if not email or not tenant_id:
            return error("email e tenant_id são obrigatórios", 400)

        valid_roles = ["superadmin", "admin", "operator", "analyst", "trainer", "viewer"]
        if role not in valid_roles:
            return error(f"Role inválido: {role}", 400)

        # Validação de existência do tenant ANTES do seat check (WS9):
        # UUID malformado ou tenant inexistente falham com 400 claro,
        # em vez de erro genérico mais adiante no INSERT.
        try:
            uuid.UUID(str(tenant_id))
        except (ValueError, TypeError):
            return error("Tenant não encontrado", 400)

        with _pool().get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM public.tenants WHERE id = %s", (tenant_id,))
                if not cur.fetchone():
                    return error("Tenant não encontrado", 400)

        # Enforcement de assentos (tenants.max_seats — migration 051)
        from app.core.exceptions import ConflictError
        from app.domain.services.seat_service import check_seat_available
        from app.infrastructure.database.repositories.tenant_policy_repository import (
            TenantPolicyRepository,
        )
        try:
            check_seat_available(TenantPolicyRepository(_pool()), tenant_id)
        except ConflictError as seat_exc:
            return error(seat_exc.message, 409)

        temp_password = secrets.token_urlsafe(12)
        password_hash = hash_password(temp_password)
        user_id = str(uuid.uuid4())

        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                if cur.fetchone():
                    return error("Email já cadastrado", 409)

                cur.execute("""
                    INSERT INTO users
                      (id, email, password_hash, name, role, tenant_id,
                       is_active, force_password_reset, access_expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, true, true, %s)
                    RETURNING id, email, role, tenant_id, is_active, created_at
                """, (user_id, email, password_hash, nome or email.split("@")[0],
                      role, tenant_id, access_expires_at))
                user = _clean_row(dict(cur.fetchone()))
            conn.commit()

        # Token de primeiro acesso no Redis — TTL 48h
        try:
            r = _get_redis()
            first_access_token = str(uuid.uuid4())
            r.setex(f"first_access:{first_access_token}", 172800, user_id)
            r.close()
        except Exception:
            first_access_token = None

        actor_id, actor_role = _get_actor()
        log_audit(actor_id, actor_role, tenant_id, "user", user_id,
                  "created", new_value={"email": email, "role": role,
                                        "name": nome or email.split("@")[0]},
                  ip_address=_get_ip(), user_agent=_get_ua())

        return success({
            "user": user,
            "temp_password": temp_password,
            "first_access_token": first_access_token,
        }, status=201)
    except Exception as exc:
        logger.error("create_user_error: %s", exc, exc_info=True)
        return error("Erro ao criar usuário", 500)


# ---------------------------------------------------------------------------
# Users — update
# ---------------------------------------------------------------------------
@admin_bp.route("/users/<user_id>", methods=["PATCH"])
@require_superadmin
def update_user(user_id: str):
    try:
        data = request.get_json() or {}
        allowed = {"role", "access_expires_at", "name"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return error("Nenhum campo válido para atualizar", 400)

        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT role, tenant_id, name FROM users WHERE id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return error("Usuário não encontrado", 404)
                old_role, tenant_id = row["role"], row["tenant_id"]
                old_name = row["name"]

                if "role" in updates:
                    cur.execute(
                        "UPDATE users SET role = %s WHERE id = %s",
                        (updates["role"], user_id),
                    )
                if "access_expires_at" in updates:
                    cur.execute(
                        "UPDATE users SET access_expires_at = %s WHERE id = %s",
                        (updates["access_expires_at"], user_id),
                    )
                if "name" in updates:
                    # Nome vazio volta ao fallback do e-mail em vez de gravar
                    # string vazia — a tela nunca deve mostrar pessoa sem crachá.
                    novo_nome = (updates["name"] or "").strip()
                    cur.execute(
                        "UPDATE users SET name = %s WHERE id = %s",
                        (novo_nome or None, user_id),
                    )
            conn.commit()

        actor_id, actor_role = _get_actor()
        log_audit(actor_id, actor_role, str(tenant_id) if tenant_id else None,
                  "user", user_id, "updated",
                  old_value={"role": old_role, "name": old_name},
                  new_value=updates,
                  ip_address=_get_ip(), user_agent=_get_ua())

        return success({"updated": True})
    except Exception as exc:
        logger.error("update_user_error: %s", exc, exc_info=True)
        return error("Erro ao atualizar usuário", 500)


# ---------------------------------------------------------------------------
# Users — deactivate
# ---------------------------------------------------------------------------
@admin_bp.route("/users/<user_id>/deactivate", methods=["POST"])
@require_superadmin
def deactivate_user(user_id: str):
    try:
        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tenant_id FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                if not row:
                    return error("Usuário não encontrado", 404)
                tenant_id = row["tenant_id"]

                cur.execute("""
                    UPDATE users
                    SET is_active = false, deactivated_at = NOW(), deactivated_by = %s
                    WHERE id = %s
                """, (str(actor_id) if actor_id else None, user_id))

                cur.execute("""
                    UPDATE public.active_sessions
                    SET revoked_at = NOW(), revoked_by = %s
                    WHERE user_id = %s AND revoked_at IS NULL
                    RETURNING jti, expires_at
                """, (str(actor_id) if actor_id else None, user_id))
                revoked_rows = cur.fetchall()
            conn.commit()

        # P1 fix: `session_revoked:{user_id}` nunca teve subscriber — o UPDATE
        # acima marcava a sessão revogada no Postgres, mas o JWT (até 24h)
        # continuava passando no token_in_blocklist_loader. blocklist_jtis é o
        # mesmo mecanismo já usado por login (single_session) e reset de senha.
        if revoked_rows:
            from app.domain.services.session_service import blocklist_jtis  # noqa: PLC0415
            blocklist_jtis([dict(r) for r in revoked_rows])

        try:
            r = _get_redis()
            r.publish(f"session_revoked:{user_id}", "deactivated")
            r.close()
        except Exception:  # noqa: S110
            pass

        log_audit(actor_id, actor_role, str(tenant_id) if tenant_id else None,
                  "user", user_id, "deactivated",
                  ip_address=_get_ip(), user_agent=_get_ua())
        return success({"deactivated": True})
    except Exception as exc:
        logger.error("deactivate_user_error: %s", exc, exc_info=True)
        return error("Erro ao desativar usuário", 500)


# ---------------------------------------------------------------------------
# Users — reactivate
# ---------------------------------------------------------------------------
@admin_bp.route("/users/<user_id>/reactivate", methods=["POST"])
@require_superadmin
def reactivate_user(user_id: str):
    try:
        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tenant_id FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                if not row:
                    return error("Usuário não encontrado", 404)
                tenant_id = row["tenant_id"]

        # Reativar também consome assento (tenants.max_seats — migration 051)
        if tenant_id:
            from app.core.exceptions import ConflictError
            from app.domain.services.seat_service import check_seat_available
            from app.infrastructure.database.repositories.tenant_policy_repository import (
                TenantPolicyRepository,
            )
            try:
                check_seat_available(TenantPolicyRepository(pool), str(tenant_id))
            except ConflictError as seat_exc:
                return error(seat_exc.message, 409)

        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE users
                    SET is_active = true, deactivated_at = NULL, deactivated_by = NULL
                    WHERE id = %s
                """, (user_id,))
            conn.commit()

        log_audit(actor_id, actor_role, str(tenant_id) if tenant_id else None,
                  "user", user_id, "reactivated",
                  ip_address=_get_ip(), user_agent=_get_ua())
        return success({"reactivated": True})
    except Exception as exc:
        logger.error("reactivate_user_error: %s", exc, exc_info=True)
        return error("Erro ao reativar usuário", 500)


# ---------------------------------------------------------------------------
# Users — force password reset
# ---------------------------------------------------------------------------
@admin_bp.route("/users/<user_id>/force-password-reset", methods=["POST"])
@require_superadmin
def force_password_reset(user_id: str):
    try:
        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tenant_id FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                if not row:
                    return error("Usuário não encontrado", 404)
                tenant_id = row["tenant_id"]
                cur.execute(
                    "UPDATE users SET force_password_reset = true WHERE id = %s",
                    (user_id,),
                )
            conn.commit()

        try:
            r = _get_redis()
            r.set(f"force_reset:{user_id}", "1", ex=86400)
            r.close()
        except Exception:  # noqa: S110
            pass

        log_audit(actor_id, actor_role, str(tenant_id) if tenant_id else None,
                  "user", user_id, "force_password_reset",
                  ip_address=_get_ip(), user_agent=_get_ua())
        return success({"forced": True})
    except Exception as exc:
        logger.error("force_password_reset_error: %s", exc, exc_info=True)
        return error("Erro ao forçar reset de senha", 500)


# ---------------------------------------------------------------------------
# Users — reset password (gera senha temporária, retorna uma única vez)
# ---------------------------------------------------------------------------
@admin_bp.route("/users/<user_id>/reset-password", methods=["POST"])
@require_superadmin
def reset_password(user_id: str):
    """Reseta a senha de um usuário existente.

    Gera uma senha temporária, atualiza o hash bcrypt, marca
    force_password_reset e retorna a senha temporária UMA ÚNICA VEZ
    (bcrypt é irreversível — não há como recuperá-la depois).
    """
    import secrets

    try:
        actor_id, actor_role = _get_actor()
        temp_password = secrets.token_urlsafe(12)
        password_hash = hash_password(temp_password)

        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email, tenant_id FROM users WHERE id = %s", (user_id,)
                )
                row = cur.fetchone()
                if not row:
                    return error("Usuário não encontrado", 404)
                email = row["email"]
                tenant_id = row["tenant_id"]
                cur.execute(
                    "UPDATE users SET password_hash = %s, "
                    "force_password_reset = true, updated_at = NOW() WHERE id = %s",
                    (password_hash, user_id),
                )
            conn.commit()

        # Marca reset forçado (consistente com force-password-reset)
        try:
            r = _get_redis()
            r.set(f"force_reset:{user_id}", "1", ex=86400)
            r.close()
        except Exception:  # noqa: S110
            pass

        log_audit(actor_id, actor_role, str(tenant_id) if tenant_id else None,
                  "user", user_id, "reset_password",
                  ip_address=_get_ip(), user_agent=_get_ua())
        return success({"email": email, "temp_password": temp_password})
    except Exception as exc:
        logger.error("reset_password_error: %s", exc, exc_info=True)
        return error("Erro ao resetar senha", 500)


# ---------------------------------------------------------------------------
# Users — sessions
# ---------------------------------------------------------------------------
@admin_bp.route("/users/<user_id>/sessions", methods=["GET"])
@require_superadmin
def get_user_sessions(user_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT id, jti, ip_address, user_agent, created_at, expires_at
                    FROM public.active_sessions
                    WHERE user_id = %s AND revoked_at IS NULL AND expires_at > NOW()
                    ORDER BY created_at DESC
                """, (user_id,))
            rows = cur.fetchall()
            sessions = [_clean_row(dict(r)) for r in rows]
        return success({"sessions": sessions})
    except Exception as exc:
        logger.error("get_sessions_error: %s", exc, exc_info=True)
        return error("Erro ao buscar sessões", 500)


@admin_bp.route("/users/<user_id>/sessions", methods=["DELETE"])
@require_superadmin
def revoke_user_sessions(user_id: str):
    try:
        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tenant_id FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                if not row:
                    return error("Usuário não encontrado", 404)
                tenant_id = row["tenant_id"]
                cur.execute("""
                    UPDATE public.active_sessions
                    SET revoked_at = NOW(), revoked_by = %s
                    WHERE user_id = %s AND revoked_at IS NULL
                    RETURNING jti, expires_at
                """, (str(actor_id) if actor_id else None, user_id))
                revoked_rows = cur.fetchall()
            conn.commit()

        # P1 fix: `session_revoked:{user_id}` nunca teve subscriber — o UPDATE
        # acima marcava a sessão revogada no Postgres, mas o JWT (até 24h)
        # continuava passando no token_in_blocklist_loader. blocklist_jtis é o
        # mesmo mecanismo já usado por login (single_session) e reset de senha.
        if revoked_rows:
            from app.domain.services.session_service import blocklist_jtis  # noqa: PLC0415
            blocklist_jtis([dict(r) for r in revoked_rows])

        try:
            r = _get_redis()
            r.publish(f"session_revoked:{user_id}", "admin_revoked")
            r.close()
        except Exception:  # noqa: S110
            pass

        log_audit(actor_id, actor_role, str(tenant_id) if tenant_id else None,
                  "user", user_id, "sessions_revoked",
                  ip_address=_get_ip(), user_agent=_get_ua())
        return success({"revoked": True})
    except Exception as exc:
        logger.error("revoke_sessions_error: %s", exc, exc_info=True)
        return error("Erro ao revogar sessões", 500)


# ---------------------------------------------------------------------------
# Permissions matrix
# ---------------------------------------------------------------------------
@admin_bp.route("/permissions/matrix", methods=["GET"])
@require_superadmin
def permissions_matrix():
    from app.constants import ROLE_PERMISSIONS
    return success({"matrix": ROLE_PERMISSIONS})


# ---------------------------------------------------------------------------
# Modules — catálogo canônico (WS6)
# ---------------------------------------------------------------------------
@admin_bp.route("/modules/catalog", methods=["GET"])
@require_superadmin
def modules_catalog():
    """Catálogo canônico de módulos {code,label,description,status}.

    Fonte única (app/constants.py MODULE_CATALOG) — elimina listas hardcoded
    divergentes no frontend.
    """
    from app.constants import MODULE_CATALOG
    return success({"modules": MODULE_CATALOG})


# ---------------------------------------------------------------------------
# Training Approvals — list
# ---------------------------------------------------------------------------
@admin_bp.route("/training-approvals", methods=["GET"])
@require_superadmin
def list_training_approvals():
    try:
        _, per_page, offset = _page_params()
        status_filter = request.args.get("status", "pending")
        tenant_filter = request.args.get("tenant_id")
        module_filter = request.args.get("module")

        conditions = ["1=1"]
        params: list = []
        if status_filter:
            conditions.append("ta.status = %s")
            params.append(status_filter)
        if tenant_filter:
            conditions.append("ta.tenant_id = %s")
            params.append(tenant_filter)
        if module_filter:
            conditions.append("ta.module = %s")
            params.append(module_filter)
        where = " AND ".join(conditions)

        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM public.training_approvals ta WHERE {where}",  # noqa: S608
                tuple(params),
            )
            total = cur.fetchone()["count"]

            cur.execute(f"""
                    SELECT ta.*, t.name AS tenant_name, u.email AS reviewer_email
                    FROM public.training_approvals ta
                    LEFT JOIN public.tenants t ON t.id = ta.tenant_id
                    LEFT JOIN public.users u ON u.id = ta.reviewed_by
                    WHERE {where}
                    ORDER BY ta.created_at DESC
                    LIMIT %s OFFSET %s
                """, tuple(params) + (per_page, offset))  # noqa: S608
            rows = cur.fetchall()
            items = [_clean_row(dict(r)) for r in rows]

        return success({"items": items, "total": total})
    except Exception as exc:
        logger.error("list_approvals_error: %s", exc, exc_info=True)
        return error("Erro ao listar aprovações", 500)


# ---------------------------------------------------------------------------
# Training Approvals — get detail
# ---------------------------------------------------------------------------
@admin_bp.route("/training-approvals/<approval_id>", methods=["GET"])
@require_superadmin
def get_training_approval(approval_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT ta.*, t.name AS tenant_name
                    FROM public.training_approvals ta
                    LEFT JOIN public.tenants t ON t.id = ta.tenant_id
                    WHERE ta.id = %s
                """, (approval_id,))
            row = cur.fetchone()
            if not row:
                return error("Aprovação não encontrada", 404)
            approval = _clean_row(dict(row))

        # Gerar presigned URLs para imagens do dataset
        sample_keys = approval.get("dataset_sample_keys") or []
        if isinstance(sample_keys, str):
            try:
                sample_keys = json.loads(sample_keys)
            except Exception:
                sample_keys = []

        sample_urls = []
        if sample_keys:
            try:
                from app.infrastructure.storage.r2_storage import R2Storage
                r2 = R2Storage()
                for key in sample_keys[:10]:
                    url = r2.generate_presigned_download_url(key, ttl=300)
                    sample_urls.append(url)
            except Exception as e:
                logger.warning("r2_presigned_failed: %s", e)

        approval["dataset_sample_urls"] = sample_urls
        return success({"approval": approval})
    except Exception as exc:
        logger.error("get_approval_error: %s", exc, exc_info=True)
        return error("Erro ao buscar aprovação", 500)


# ---------------------------------------------------------------------------
# Training Approvals — approve
# ---------------------------------------------------------------------------
@admin_bp.route("/training-approvals/<approval_id>/approve", methods=["POST"])
@require_superadmin
def approve_training(approval_id: str):
    try:
        data = request.get_json() or {}
        notes = data.get("notes", "")
        actor_id, actor_role = _get_actor()

        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tenant_id, training_job_id, tenant_schema FROM public.training_approvals WHERE id = %s",  # noqa: E501
                    (approval_id,),
                )
                row = cur.fetchone()
                if not row:
                    return error("Aprovação não encontrada", 404)
                tenant_id = row["tenant_id"]
                job_id = row["training_job_id"]
                tenant_schema = row["tenant_schema"]

                cur.execute("""
                    UPDATE public.training_approvals
                    SET status = 'approved', reviewed_by = %s,
                        reviewed_at = NOW(), reviewer_notes = %s
                    WHERE id = %s
                """, (str(actor_id) if actor_id else None, notes, approval_id))
            conn.commit()

        try:
            r = _get_redis()
            r.publish(f"training_approved:{job_id}", approval_id)
            r.publish(f"notification:{tenant_schema}",
                      json.dumps({"type": "training_approved", "job_id": str(job_id)}))
            r.close()
        except Exception:  # noqa: S110
            pass

        log_audit(actor_id, actor_role, str(tenant_id), "training_approval",
                  approval_id, "approved", new_value={"notes": notes},
                  ip_address=_get_ip(), user_agent=_get_ua())
        return success({"approved": True})
    except Exception as exc:
        logger.error("approve_training_error: %s", exc, exc_info=True)
        return error("Erro ao aprovar treinamento", 500)


# ---------------------------------------------------------------------------
# Training Approvals — reject
# ---------------------------------------------------------------------------
@admin_bp.route("/training-approvals/<approval_id>/reject", methods=["POST"])
@require_superadmin
def reject_training(approval_id: str):
    try:
        data = request.get_json() or {}
        reason = (data.get("reason") or "").strip()
        if not reason:
            return error("Motivo de rejeição é obrigatório", 400)

        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tenant_id, training_job_id, tenant_schema FROM public.training_approvals WHERE id = %s",  # noqa: E501
                    (approval_id,),
                )
                row = cur.fetchone()
                if not row:
                    return error("Aprovação não encontrada", 404)
                tenant_id = row["tenant_id"]
                job_id = row["training_job_id"]
                tenant_schema = row["tenant_schema"]

                cur.execute("""
                    UPDATE public.training_approvals
                    SET status = 'rejected', reviewed_by = %s,
                        reviewed_at = NOW(), rejection_reason = %s
                    WHERE id = %s
                """, (str(actor_id) if actor_id else None, reason, approval_id))
            conn.commit()

        try:
            r = _get_redis()
            r.publish(f"training_rejected:{job_id}", reason)
            r.publish(f"notification:{tenant_schema}",
                      json.dumps({"type": "training_rejected", "job_id": str(job_id), "reason": reason}))  # noqa: E501
            r.close()
        except Exception:  # noqa: S110
            pass

        log_audit(actor_id, actor_role, str(tenant_id), "training_approval",
                  approval_id, "training_rejected", new_value={"reason": reason},
                  ip_address=_get_ip(), user_agent=_get_ua())
        return success({"rejected": True})
    except Exception as exc:
        logger.error("reject_training_error: %s", exc, exc_info=True)
        return error("Erro ao rejeitar treinamento", 500)


# ---------------------------------------------------------------------------
# Workers — list all
# ---------------------------------------------------------------------------
@admin_bp.route("/workers", methods=["GET"])
@require_superadmin
def list_workers():
    try:
        from app.infrastructure.queue.worker_registry import get_all_workers_status
        workers = get_all_workers_status()
        return success({"workers": workers})
    except Exception as exc:
        logger.error("list_workers_error: %s", exc, exc_info=True)
        return error("Erro ao buscar workers", 500)


# ---------------------------------------------------------------------------
# Workers — detail by schema
# ---------------------------------------------------------------------------
@admin_bp.route("/workers/<tenant_schema>", methods=["GET"])
@require_superadmin
def get_worker_detail(tenant_schema: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT wr.*, t.name AS tenant_name
                    FROM public.worker_registry wr
                    JOIN public.tenants t ON t.id = wr.tenant_id
                    WHERE wr.tenant_schema = %s AND wr.active = true
                """, (tenant_schema,))
            row = cur.fetchone()
            worker = _clean_row(dict(row)) if row else {}

            # Métricas das últimas 24h
            if row:
                worker_id = str(row["id"])
                cur.execute("""
                        SELECT gpu_pct, vram_used_gb, fps_avg, cameras_active, recorded_at
                        FROM public.worker_metrics
                        WHERE worker_id = %s AND recorded_at >= NOW() - INTERVAL '24 hours'
                        ORDER BY recorded_at DESC LIMIT 200
                    """, (worker_id,))
                metric_rows = cur.fetchall()
                worker["metrics_24h"] = [_clean_row(dict(r)) for r in metric_rows]

        from app.infrastructure.queue.worker_registry import get_worker_metrics, get_worker_status
        worker["status"] = get_worker_status(tenant_schema)
        worker["live_metrics"] = get_worker_metrics(tenant_schema)

        return success({"worker": worker})
    except Exception as exc:
        logger.error("get_worker_detail_error: %s", exc, exc_info=True)
        return error("Erro ao buscar worker", 500)


# ---------------------------------------------------------------------------
# Workers — restart (send command via Redis)
# ---------------------------------------------------------------------------
@admin_bp.route("/workers/<tenant_schema>/restart", methods=["POST"])
@require_superadmin
def restart_worker(tenant_schema: str):
    try:
        actor_id, actor_role = _get_actor()
        r = _get_redis()
        r.set(f"worker_command:{tenant_schema}", "restart", ex=120)
        r.close()

        log_audit(actor_id, actor_role, None, "worker", tenant_schema,
                  "worker_restart", ip_address=_get_ip(), user_agent=_get_ua())
        return success({"command_sent": "restart", "schema": tenant_schema})
    except Exception as exc:
        logger.error("restart_worker_error: %s", exc, exc_info=True)
        return error("Erro ao enviar comando de restart", 500)


# ---------------------------------------------------------------------------
# Workers — metrics history
# ---------------------------------------------------------------------------
@admin_bp.route("/workers/<tenant_schema>/metrics", methods=["GET"])
@require_superadmin
def get_worker_metrics_history(tenant_schema: str):
    try:
        period = request.args.get("period", "24h")
        intervals = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}
        interval = intervals.get(period, "24 hours")

        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM public.worker_registry WHERE tenant_schema = %s AND active = true",
                (tenant_schema,),
            )
            row = cur.fetchone()
            if not row:
                return success({"metrics": []})
            worker_id = str(row["id"])

            cur.execute(f"""
                    SELECT gpu_pct, vram_used_gb, fps_avg, cameras_active, recorded_at
                    FROM public.worker_metrics
                    WHERE worker_id = %s AND recorded_at >= NOW() - INTERVAL '{interval}'
                    ORDER BY recorded_at ASC
                """, (worker_id,))  # noqa: S608
            rows = cur.fetchall()
            metrics = [_clean_row(dict(r)) for r in rows]

        return success({"metrics": metrics})
    except Exception as exc:
        logger.error("worker_metrics_error: %s", exc, exc_info=True)
        return error("Erro ao buscar métricas do worker", 500)


# ---------------------------------------------------------------------------
# Workers — heartbeat (X-Worker-Secret, sem JWT)
# ---------------------------------------------------------------------------
@admin_bp.route("/workers/heartbeat", methods=["POST"])
def worker_heartbeat():
    """
    Endpoint para worker on-premise publicar heartbeat.
    Autenticado por X-Worker-Secret header (não JWT).
    """
    try:
        worker_secret = current_app.config.get("WORKER_SECRET", "")
        provided_secret = request.headers.get("X-Worker-Secret", "")

        if not worker_secret or provided_secret != worker_secret:
            return error("Não autorizado", 401)

        data = request.get_json() or {}
        tenant_schema = (data.get("tenant_schema") or "").strip()
        if not tenant_schema:
            return error("tenant_schema é obrigatório", 400)

        metrics = {
            "hostname": data.get("hostname"),
            "software_version": data.get("software_version"),
            "gpu_model": data.get("gpu_model"),
            "gpu_pct": data.get("gpu_pct", 0),
            "vram_used_gb": data.get("vram_used_gb", 0),
            "fps_avg": data.get("fps_avg", 0),
            "cameras_active": data.get("cameras_active", 0),
        }

        from app.infrastructure.queue.worker_registry import publish_heartbeat
        publish_heartbeat(tenant_schema, metrics)

        # Per-camera liveness check — best-effort (never raises)
        try:
            from app.domain.services.liveness_service import check_camera_liveness

            cameras_online = int(data.get("cameras_active", 0))
            # cameras_total: prefer explicit payload field; fall back to DB count
            cameras_total_raw = data.get("cameras_total")
            pool_inst = _pool()

            with pool_inst.get_connection() as _conn, _conn.cursor() as _cur:
                _cur.execute(
                    "SELECT id FROM tenants WHERE schema_name = %s LIMIT 1",
                    (tenant_schema,),
                )
                _tenant_row = _cur.fetchone()

            if _tenant_row:
                _tenant_id = str(_tenant_row["id"])
                if cameras_total_raw is None:
                    with pool_inst.get_connection() as _conn, _conn.cursor() as _cur:
                        _cur.execute(
                            "SELECT COUNT(*) AS cnt FROM cameras "
                            "WHERE tenant_id = %s AND is_active = TRUE",
                            (_tenant_id,),
                        )
                        _cnt_row = _cur.fetchone()
                        cameras_total = int(_cnt_row["cnt"]) if _cnt_row else 0
                else:
                    cameras_total = int(cameras_total_raw)
                check_camera_liveness(
                    _tenant_id, tenant_schema, cameras_online, cameras_total, pool_inst
                )
        except Exception as _lv_exc:  # noqa: S110
            logger.warning(
                "liveness_check_failed: schema=%s err=%s", tenant_schema, _lv_exc
            )

        # Verificar e retornar comando pendente
        command = None
        try:
            r = _get_redis()
            cmd = r.get(f"worker_command:{tenant_schema}")
            if cmd:
                command = cmd
                r.delete(f"worker_command:{tenant_schema}")
            r.close()
        except Exception:  # noqa: S110
            pass

        return success({"command": command})
    except Exception as exc:
        logger.error("worker_heartbeat_error: %s", exc, exc_info=True)
        return error("Erro no heartbeat", 500)


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
_PLAN_SLUG_RE = re.compile(r"^[a-z0-9_-]{2,50}$")
_PLAN_INT_FIELDS = ("max_cameras", "max_users", "video_retention_days", "api_rate_per_minute")
_PLAN_JSONB_FIELDS = ("modules_allowed", "price_per_camera", "module_features")

# tenant_count por plano (join por slug — ligação real plano↔tenant)
_PLANS_WITH_TENANT_COUNT_SQL = """
    SELECT p.*,
           (SELECT COUNT(*) FROM tenants t WHERE t.plan = p.slug) AS tenant_count
    FROM public.plans p
"""


def _validate_plan_payload(data: dict) -> str | None:
    """Valida campos de plano (create/update). Retorna mensagem de erro ou None."""
    if "name" in data and not str(data.get("name") or "").strip():
        return "Nome do plano é obrigatório"
    if "name" in data and len(str(data["name"])) > 100:
        return "Nome do plano deve ter no máximo 100 caracteres"

    for field in _PLAN_INT_FIELDS:
        if field not in data:
            continue
        val = data[field]
        if val is None:
            if field == "max_users":
                continue  # NULL = ilimitado
            return f"Campo {field} não pode ser vazio"
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            return f"Campo {field} deve ser um número inteiro maior ou igual a zero"

    if "modules_allowed" in data:
        mods = data["modules_allowed"]
        if not isinstance(mods, list) or any(not isinstance(m, str) for m in mods):
            return "modules_allowed deve ser uma lista de módulos"
        invalid = [m for m in mods if m not in PLATFORM_MODULES]
        if invalid:
            return f"Módulo inválido: {', '.join(invalid)}"

    if "module_features" in data:
        feats = data["module_features"]
        if not isinstance(feats, dict):
            return "module_features deve ser um objeto {módulo: [funcionalidades]}"
        for mod, keys in feats.items():
            if mod not in PLATFORM_MODULES:
                return f"Módulo inválido em module_features: {mod}"
            if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
                return f"Funcionalidades de {mod} devem ser uma lista"
            valid_keys = {f["key"] for f in PLATFORM_MODULES[mod]["features"]}
            bad = [k for k in keys if k not in valid_keys]
            if bad:
                return f"Funcionalidade inválida em {mod}: {', '.join(bad)}"

    if "price_per_camera" in data:
        prices = data["price_per_camera"]
        if not isinstance(prices, dict):
            return "price_per_camera deve ser um objeto {módulo: valor}"
        for mod, val in prices.items():
            if mod not in PLATFORM_MODULES:
                return f"Módulo inválido em price_per_camera: {mod}"
            if isinstance(val, bool) or not isinstance(val, (int, float)) or val < 0:
                return f"Valor de cobrança inválido para o módulo {mod}"

    return None


@admin_bp.route("/modules-registry", methods=["GET"])
@require_superadmin
def get_modules_registry():
    """Registry canônico de módulos/funcionalidades — fonte única do picker do admin."""
    modules = [
        {"module_code": code, "label": meta["label"], "features": meta["features"]}
        for code, meta in PLATFORM_MODULES.items()
    ]
    return success({"modules": modules})


@admin_bp.route("/plans", methods=["GET"])
@require_superadmin
def list_plans():
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(_PLANS_WITH_TENANT_COUNT_SQL + " ORDER BY p.name")
            rows = cur.fetchall()
            plans = [_clean_row(dict(r)) for r in rows]
        return success({"plans": plans})
    except Exception as exc:
        logger.error("list_plans_error: %s", exc, exc_info=True)
        return error("Erro ao listar planos", 500)


@admin_bp.route("/plans/<plan_id>", methods=["GET"])
@require_superadmin
def get_plan(plan_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(_PLANS_WITH_TENANT_COUNT_SQL + " WHERE p.id = %s", (plan_id,))
            row = cur.fetchone()
            if not row:
                return error("Plano não encontrado", 404)
            plan = _clean_row(dict(row))
        return success({"plan": plan})
    except Exception as exc:
        logger.error("get_plan_error: %s", exc, exc_info=True)
        return error("Erro ao buscar plano", 500)


@admin_bp.route("/plans", methods=["POST"])
@require_superadmin
def create_plan():
    try:
        data = request.get_json() or {}

        slug = str(data.get("slug") or "").strip()
        if not slug:
            return error("Slug é obrigatório", 400)
        if not _PLAN_SLUG_RE.match(slug):
            return error(
                "Slug inválido: use 2-50 caracteres com letras minúsculas, "
                "números, hífen ou underscore", 400,
            )
        if not str(data.get("name") or "").strip():
            return error("Nome do plano é obrigatório", 400)
        validation_error = _validate_plan_payload(data)
        if validation_error:
            return error(validation_error, 400)

        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                # Pre-check de slug duplicado (mesmo padrão do update_plan).
                # NÃO confiar só no except: o DatabasePool re-lança psycopg2.Error
                # como DatabaseError, então UniqueViolation nunca chega direto aqui.
                cur.execute("SELECT id FROM public.plans WHERE slug = %s", (slug,))
                if cur.fetchone():
                    return error("Slug já existe", 409)
                cur.execute("""
                    INSERT INTO public.plans
                      (slug, name, modules_allowed, max_cameras, max_users,
                       video_retention_days, requires_training_approval,
                       price_per_camera, module_features, api_rate_per_minute)
                    VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    RETURNING *
                """, (
                    slug, data.get("name"),
                    json.dumps(data.get("modules_allowed", [])),
                    data.get("max_cameras", 10),
                    data.get("max_users"),
                    data.get("video_retention_days", 7),
                    data.get("requires_training_approval", False),
                    json.dumps(data.get("price_per_camera", {})),
                    json.dumps(data.get("module_features", {})),
                    data.get("api_rate_per_minute", 120),
                ))
                plan = _clean_row(dict(cur.fetchone()))
            conn.commit()

        log_audit(actor_id, actor_role, None, "plan", plan["id"], "created",
                  new_value=data, ip_address=_get_ip(), user_agent=_get_ua())
        return success({"plan": plan}, status=201)
    except pg_errors.UniqueViolation:
        return error("Slug já existe", 409)
    except Exception as exc:
        # Corrida entre pre-check e INSERT: o pool embrulha psycopg2.Error em
        # DatabaseError (raise ... from exc) — inspecionar a causa original.
        if isinstance(getattr(exc, "__cause__", None), pg_errors.UniqueViolation):
            return error("Slug já existe", 409)
        logger.error("create_plan_error: %s", exc, exc_info=True)
        return error("Erro ao criar plano", 500)


@admin_bp.route("/plans/<plan_id>", methods=["PATCH"])
@require_superadmin
def update_plan(plan_id: str):
    try:
        data = request.get_json() or {}
        actor_id, actor_role = _get_actor()
        # slug fica FORA do allowed — imutável (joins por slug em tenants/retention/rate-limit)
        allowed = {"name", "modules_allowed", "max_cameras", "max_users",
                   "video_retention_days", "requires_training_approval",
                   "price_per_camera", "module_features", "api_rate_per_minute", "active"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return error("Nenhum campo válido", 400)

        validation_error = _validate_plan_payload(updates)
        if validation_error:
            return error(validation_error, 400)

        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM public.plans WHERE id = %s", (plan_id,))
                if not cur.fetchone():
                    return error("Plano não encontrado", 404)
                for field, val in updates.items():
                    # `field` vem SEMPRE da whitelist `allowed` — nunca do usuário
                    if field in _PLAN_JSONB_FIELDS:
                        cur.execute(
                            f"UPDATE public.plans SET {field} = %s::jsonb WHERE id = %s",  # noqa: S608
                            (json.dumps(val), plan_id),
                        )
                    else:
                        cur.execute(
                            f"UPDATE public.plans SET {field} = %s WHERE id = %s",  # noqa: S608
                            (val, plan_id),
                        )
            conn.commit()

        log_audit(actor_id, actor_role, None, "plan", plan_id, "updated",
                  new_value=updates, ip_address=_get_ip(), user_agent=_get_ua())
        return success({"updated": True})
    except Exception as exc:
        logger.error("update_plan_error: %s", exc, exc_info=True)
        return error("Erro ao atualizar plano", 500)


@admin_bp.route("/plans/<plan_id>/tenants", methods=["GET"])
@require_superadmin
def get_plan_tenants(plan_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT slug FROM public.plans WHERE id = %s", (plan_id,)
            )
            row = cur.fetchone()
            if not row:
                return error("Plano não encontrado", 404)
            slug = row["slug"]
            cur.execute(
                "SELECT id, name, slug, is_active, created_at FROM tenants WHERE plan = %s",
                (slug,),
            )
            rows = cur.fetchall()
            tenants = [_clean_row(dict(r)) for r in rows]
        return success({"tenants": tenants})
    except Exception as exc:
        logger.error("plan_tenants_error: %s", exc, exc_info=True)
        return error("Erro ao buscar tenants do plano", 500)


# ---------------------------------------------------------------------------
# Feature Flags — global
# ---------------------------------------------------------------------------
@admin_bp.route("/feature-flags", methods=["GET"])
@require_superadmin
def list_feature_flags():
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM public.platform_feature_flags ORDER BY flag_key")
            rows = cur.fetchall()
            flags = [_clean_row(dict(r)) for r in rows]
        return success({"flags": flags})
    except Exception as exc:
        logger.error("list_flags_error: %s", exc, exc_info=True)
        return error("Erro ao listar feature flags", 500)


@admin_bp.route("/feature-flags/<flag_key>", methods=["PATCH"])
@require_superadmin
def update_feature_flag(flag_key: str):
    try:
        data = request.get_json() or {}
        flag_value = bool(data.get("value", False))
        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.platform_feature_flags
                      (flag_key, flag_value, updated_by)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (flag_key) DO UPDATE
                      SET flag_value = EXCLUDED.flag_value,
                          updated_by = EXCLUDED.updated_by,
                          updated_at = NOW()
                """, (flag_key, flag_value, str(actor_id) if actor_id else None))
            conn.commit()

        log_audit(actor_id, actor_role, None, "feature_flag", flag_key, "updated",
                  new_value={"value": flag_value}, ip_address=_get_ip(), user_agent=_get_ua())
        return success({"updated": True})
    except Exception as exc:
        logger.error("update_flag_error: %s", exc, exc_info=True)
        return error("Erro ao atualizar feature flag", 500)


@admin_bp.route("/software-channels", methods=["GET"])
@require_superadmin
def list_software_channels():
    """Canais de OTA bare-metal (ADR-0057 item 10) e seus target_ref publicados."""
    try:
        repo = EdgeSoftwareChannelRepository(_pool())
        channels = [_clean_row(c) for c in repo.list_channels()]
        return success({"channels": channels})
    except Exception as exc:
        logger.error("list_software_channels_error: %s", exc, exc_info=True)
        return error("Erro ao listar canais de software", 500)


@admin_bp.route("/software-channels/<channel>", methods=["PUT"])
@require_superadmin
def set_software_channel_target(channel: str):
    """Publica o target_ref (git ref) de um canal — o que os devices desse
    canal vão buscar em GET /api/v1/edge/software/target e, se diferente do
    que rodam, puxar (app/ota/updater.py). A nuvem só indica; nunca aplica
    nada no device diretamente."""
    try:
        data = request.get_json() or {}
        target_ref = (data.get("target_ref") or "").strip()
        if not target_ref:
            return error("target_ref é obrigatório", 422)

        actor_id, actor_role = _get_actor()
        repo = EdgeSoftwareChannelRepository(_pool())
        row = repo.set_target_ref(channel, target_ref, str(actor_id) if actor_id else None)

        log_audit(
            actor_id, actor_role, None, "edge_software_channel", channel, "updated",
            new_value={"target_ref": target_ref}, ip_address=_get_ip(), user_agent=_get_ua(),
        )
        logger.info(
            "software_channel_updated: channel=%s target_ref=%s by=%s",
            channel, target_ref, str(actor_id)[:8] if actor_id else "?",
        )
        return success(_clean_row(row))
    except Exception as exc:
        logger.error("set_software_channel_error: %s", exc, exc_info=True)
        return error("Erro ao publicar canal de software", 500)


@admin_bp.route("/feature-flags/tenant/<tenant_id>", methods=["GET"])
@require_superadmin
def get_tenant_feature_flags(tenant_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT feature_flags FROM tenants WHERE id = %s", (tenant_id,)
            )
            row = cur.fetchone()
            if not row:
                return error("Tenant não encontrado", 404)
            flags = row["feature_flags"] or {}
            if isinstance(flags, str):
                flags = json.loads(flags)
        return success({"flags": flags})
    except Exception as exc:
        logger.error("tenant_flags_error: %s", exc, exc_info=True)
        return error("Erro ao buscar feature flags do tenant", 500)


@admin_bp.route("/feature-flags/tenant/<tenant_id>", methods=["PATCH"])
@require_superadmin
def update_tenant_feature_flag(tenant_id: str):
    try:
        data = request.get_json() or {}
        flag_key = data.get("key")
        flag_value = data.get("value")
        if flag_key is None or flag_value is None:
            return error("key e value são obrigatórios", 400)

        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE tenants
                    SET feature_flags = COALESCE(feature_flags, '{}')::jsonb
                        || %s::jsonb
                    WHERE id = %s
                """, (json.dumps({flag_key: flag_value}), tenant_id))
            conn.commit()

        log_audit(actor_id, actor_role, tenant_id, "tenant_feature_flag", tenant_id,
                  "updated", new_value={flag_key: flag_value},
                  ip_address=_get_ip(), user_agent=_get_ua())
        return success({"updated": True})
    except Exception as exc:
        logger.error("update_tenant_flag_error: %s", exc, exc_info=True)
        return error("Erro ao atualizar feature flag do tenant", 500)


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
_SLA_HOURS = {"critical": 1, "high": 4, "normal": 24, "low": 72}


@admin_bp.route("/tickets", methods=["GET"])
@require_superadmin
def list_tickets():
    try:
        _, per_page, offset = _page_params()
        now = datetime.utcnow()

        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT st.id, st.tenant_id, st.subject, st.category,
                           st.priority, st.status, st.created_at, st.updated_at,
                           st.first_responded_at,
                           t.name AS tenant_name
                    FROM public.support_tickets st
                    LEFT JOIN public.tenants t ON t.id = st.tenant_id
                    ORDER BY st.created_at DESC
                    LIMIT %s OFFSET %s
                """, (per_page, offset))
            rows = cur.fetchall()
            tickets = []
            for row in rows:
                t = _clean_row(dict(row))
                sla_h = _SLA_HOURS.get(t.get("priority", "normal"), 24)
                if t.get("created_at") and not t.get("first_responded_at"):
                    created = row["created_at"]
                    if created and hasattr(created, "replace"):
                        elapsed = (now - created.replace(tzinfo=None)).total_seconds() / 3600
                        t["sla_breached"] = elapsed > sla_h
                    else:
                        t["sla_breached"] = False
                else:
                    t["sla_breached"] = False
                tickets.append(t)

            cur.execute("SELECT COUNT(*) FROM public.support_tickets")
            total = cur.fetchone()["count"]

        return success({"items": tickets, "total": total})
    except Exception as exc:
        logger.error("list_tickets_error: %s", exc, exc_info=True)
        return error("Erro ao listar tickets", 500)


@admin_bp.route("/tickets/stats", methods=["GET"])
@require_superadmin
def ticket_stats():
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'open') AS open_count,
                        COUNT(*) FILTER (WHERE priority = 'critical') AS critical_count,
                        COUNT(*) FILTER (WHERE priority = 'high') AS high_count,
                        COUNT(*) FILTER (WHERE priority = 'normal') AS normal_count,
                        COUNT(*) FILTER (WHERE priority = 'low') AS low_count
                    FROM public.support_tickets
                """)
            row = cur.fetchone()
            stats = _clean_row(dict(row))
        return success({"stats": stats})
    except Exception as exc:
        logger.error("ticket_stats_error: %s", exc, exc_info=True)
        return error("Erro ao buscar stats de tickets", 500)


@admin_bp.route("/tickets/<ticket_id>", methods=["GET"])
@require_superadmin
def get_ticket(ticket_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT st.*, t.name AS tenant_name
                    FROM public.support_tickets st
                    LEFT JOIN public.tenants t ON t.id = st.tenant_id
                    WHERE st.id = %s
                """, (ticket_id,))
            row = cur.fetchone()
            if not row:
                return error("Ticket não encontrado", 404)
            ticket = _clean_row(dict(row))

            cur.execute("""
                    SELECT tm.*, u.email AS author_email
                    FROM public.ticket_messages tm
                    LEFT JOIN public.users u ON u.id = tm.author_id
                    WHERE tm.ticket_id = %s
                    ORDER BY tm.created_at
                """, (ticket_id,))
            msg_rows = cur.fetchall()
            ticket["messages"] = [_clean_row(dict(r)) for r in msg_rows]

        return success({"ticket": ticket})
    except Exception as exc:
        logger.error("get_ticket_error: %s", exc, exc_info=True)
        return error("Erro ao buscar ticket", 500)


@admin_bp.route("/tickets/<ticket_id>/reply", methods=["POST"])
@require_superadmin
def reply_ticket(ticket_id: str):
    try:
        data = request.get_json() or {}
        content = (data.get("content") or "").strip()
        is_internal = bool(data.get("is_internal", False))
        if not content:
            return error("content é obrigatório", 400)

        actor_id, _ = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.ticket_messages
                      (ticket_id, author_id, content, is_internal)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, created_at
                """, (ticket_id, str(actor_id) if actor_id else None, content, is_internal))
                msg_row = cur.fetchone()

                cur.execute(
                    "UPDATE public.support_tickets SET updated_at = NOW() WHERE id = %s",
                    (ticket_id,),
                )
            conn.commit()

        return success({"message_id": str(msg_row["id"])}, status=201)
    except Exception as exc:
        logger.error("reply_ticket_error: %s", exc, exc_info=True)
        return error("Erro ao responder ticket", 500)


@admin_bp.route("/tickets/<ticket_id>", methods=["PATCH"])
@require_superadmin
def update_ticket(ticket_id: str):
    try:
        data = request.get_json() or {}
        allowed = {"status", "priority", "assigned_to"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return error("Nenhum campo válido", 400)

        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                for field, val in updates.items():
                    cur.execute(
                        f"UPDATE public.support_tickets SET {field} = %s, updated_at = NOW() WHERE id = %s",  # noqa: E501, S608
                        (val, ticket_id),
                    )
            conn.commit()

        return success({"updated": True})
    except Exception as exc:
        logger.error("update_ticket_error: %s", exc, exc_info=True)
        return error("Erro ao atualizar ticket", 500)


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------
@admin_bp.route("/audit-log", methods=["GET"])
@require_superadmin
def list_audit_log():
    try:
        _, per_page, offset = _page_params()
        tenant_filter = request.args.get("tenant_id")
        actor_filter = request.args.get("actor_id")
        action_filter = request.args.get("action")
        target_type_filter = request.args.get("target_type")
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")

        conditions = ["1=1"]
        params: list = []
        if tenant_filter:
            conditions.append("al.tenant_id = %s")
            params.append(tenant_filter)
        if actor_filter:
            conditions.append("al.actor_id = %s")
            params.append(actor_filter)
        if action_filter:
            conditions.append("al.action = %s")
            params.append(action_filter)
        if target_type_filter:
            conditions.append("al.target_type = %s")
            params.append(target_type_filter)
        if date_from:
            conditions.append("al.created_at >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("al.created_at <= %s")
            params.append(date_to)
        where = " AND ".join(conditions)

        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM public.audit_log al WHERE {where}", tuple(params)  # noqa: S608
            )
            total = cur.fetchone()["count"]

            cur.execute(f"""
                    SELECT al.id, al.action, al.target_type, al.target_id,
                           al.actor_role, al.created_at, al.ip_address,
                           u.email AS actor_email,
                           t.name AS tenant_name
                    FROM public.audit_log al
                    LEFT JOIN public.users u ON u.id = al.actor_id
                    LEFT JOIN public.tenants t ON t.id = al.tenant_id
                    WHERE {where}
                    ORDER BY al.created_at DESC
                    LIMIT %s OFFSET %s
                """, tuple(params) + (per_page, offset))  # noqa: S608
            rows = cur.fetchall()
            items = [_clean_row(dict(r)) for r in rows]

        return success({"items": items, "total": total})
    except Exception as exc:
        logger.error("list_audit_log_error: %s", exc, exc_info=True)
        return error("Erro ao buscar audit log", 500)


@admin_bp.route("/audit-log/export", methods=["GET"])
@require_superadmin
def export_audit_log():
    try:
        tenant_filter = request.args.get("tenant_id")
        action_filter = request.args.get("action")
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")

        conditions = ["1=1"]
        params: list = []
        if tenant_filter:
            conditions.append("al.tenant_id = %s")
            params.append(tenant_filter)
        if action_filter:
            conditions.append("al.action = %s")
            params.append(action_filter)
        if date_from:
            conditions.append("al.created_at >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("al.created_at <= %s")
            params.append(date_to)
        where = " AND ".join(conditions)

        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(f"""
                    SELECT al.created_at, u.email AS actor_email, al.actor_role,
                           t.name AS tenant_name, al.target_type, al.target_id,
                           al.action, al.ip_address
                    FROM public.audit_log al
                    LEFT JOIN public.users u ON u.id = al.actor_id
                    LEFT JOIN public.tenants t ON t.id = al.tenant_id
                    WHERE {where}
                    ORDER BY al.created_at DESC
                    LIMIT 10000
                """, tuple(params))  # noqa: S608
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(cols)
        for row in rows:
            writer.writerow([
                v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else "")
                for v in row.values()
            ])

        date_str = datetime.utcnow().strftime("%Y%m%d")
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=audit-log-{date_str}.csv"
            },
        )
    except Exception as exc:
        logger.error("export_audit_log_error: %s", exc, exc_info=True)
        return error("Erro ao exportar audit log", 500)


# ---------------------------------------------------------------------------
# Announcements (admin)
# ---------------------------------------------------------------------------
@admin_bp.route("/announcements", methods=["GET"])
@require_superadmin
def list_announcements():
    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT pa.*, u.email AS created_by_email
                    FROM public.platform_announcements pa
                    LEFT JOIN public.users u ON u.id = pa.created_by
                    WHERE pa.expires_at IS NULL OR pa.expires_at > NOW()
                    ORDER BY pa.created_at DESC
                """)
            rows = cur.fetchall()
            items = [_clean_row(dict(r)) for r in rows]
        return success({"announcements": items})
    except Exception as exc:
        logger.error("list_announcements_error: %s", exc, exc_info=True)
        return error("Erro ao listar comunicados", 500)


@admin_bp.route("/announcements", methods=["POST"])
@require_superadmin
def create_announcement():
    try:
        data = request.get_json() or {}
        actor_id, actor_role = _get_actor()
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                data.get("scheduled_at") or "NOW()"
                cur.execute("""
                    INSERT INTO public.platform_announcements
                      (title, content, type, target, target_tenant_id,
                       scheduled_at, published_at, expires_at, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s,
                            CASE WHEN %s IS NULL THEN NOW() ELSE %s::timestamptz END,
                            %s, %s)
                    RETURNING id, title, created_at
                """, (
                    data.get("title"), data.get("content"),
                    data.get("type", "info"), data.get("target", "all"),
                    data.get("target_tenant_id"),
                    data.get("scheduled_at"),
                    data.get("scheduled_at"), data.get("scheduled_at"),
                    data.get("expires_at"),
                    str(actor_id) if actor_id else None,
                ))
                row = cur.fetchone()
                announcement = _clean_row(dict(row))
            conn.commit()

        try:
            r = _get_redis()
            target = data.get("target_tenant_id") or "all"
            r.publish(f"announcement:{target}", json.dumps({
                "id": announcement["id"], "title": announcement["title"]
            }))
            r.close()
        except Exception:  # noqa: S110
            pass

        return success({"announcement": announcement}, status=201)
    except Exception as exc:
        logger.error("create_announcement_error: %s", exc, exc_info=True)
        return error("Erro ao criar comunicado", 500)


@admin_bp.route("/announcements/<announcement_id>", methods=["PATCH"])
@require_superadmin
def update_announcement(announcement_id: str):
    try:
        data = request.get_json() or {}
        allowed = {"title", "content", "type", "target", "expires_at", "scheduled_at"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return error("Nenhum campo válido", 400)

        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                for field, val in updates.items():
                    cur.execute(
                        f"UPDATE public.platform_announcements SET {field} = %s WHERE id = %s",  # noqa: S608
                        (val, announcement_id),
                    )
            conn.commit()
        return success({"updated": True})
    except Exception as exc:
        logger.error("update_announcement_error: %s", exc, exc_info=True)
        return error("Erro ao atualizar comunicado", 500)


@admin_bp.route("/announcements/<announcement_id>", methods=["DELETE"])
@require_superadmin
def delete_announcement(announcement_id: str):
    try:
        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                # Soft delete via expires_at = NOW()
                cur.execute(
                    "UPDATE public.platform_announcements SET expires_at = NOW() WHERE id = %s",
                    (announcement_id,),
                )
            conn.commit()
        return success({"deleted": True})
    except Exception as exc:
        logger.error("delete_announcement_error: %s", exc, exc_info=True)
        return error("Erro ao deletar comunicado", 500)


# ---------------------------------------------------------------------------
# Platform Health
# ---------------------------------------------------------------------------
@admin_bp.route("/health/platform", methods=["GET"])
@require_superadmin
def platform_health():
    import time
    services = {}

    # PostgreSQL
    try:
        start = time.time()
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        latency = round((time.time() - start) * 1000, 1)
        services["database"] = {"status": "healthy", "latency_ms": latency}
    except Exception as exc:
        services["database"] = {"status": "critical", "details": str(exc)}

    # Redis
    try:
        start = time.time()
        r = _get_redis()
        r.ping()
        r.close()
        latency = round((time.time() - start) * 1000, 1)
        services["redis"] = {"status": "healthy", "latency_ms": latency}
    except Exception as exc:
        services["redis"] = {"status": "critical", "details": str(exc)}

    # R2
    try:
        from app.infrastructure.storage.r2_storage import R2Storage
        start = time.time()
        r2 = R2Storage()
        r2.exists("health-check-probe")
        latency = round((time.time() - start) * 1000, 1)
        services["r2"] = {"status": "healthy", "latency_ms": latency}
    except Exception as exc:
        services["r2"] = {"status": "degraded", "details": str(exc)}

    # Celery (via Redis queue length)
    celery_queues = {}
    try:
        r = _get_redis()
        for q in ["inference", "extraction", "training", "versioning", "quality_recording"]:
            celery_queues[q] = r.llen(q) or 0
        r.close()
        services["celery"] = {"status": "healthy"}
    except Exception as exc:
        services["celery"] = {"status": "degraded", "details": str(exc)}

    statuses = [s.get("status", "unknown") for s in services.values()]
    if "critical" in statuses:
        overall = "critical"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return success({
        "status": overall,
        "services": services,
        "celery_queues": celery_queues,
    })


@admin_bp.route("/health/metrics", methods=["GET"])
@require_superadmin
def health_metrics():
    try:
        celery_queues = {}
        try:
            r = _get_redis()
            for q in ["inference", "extraction", "training", "versioning",
                      "quality_recording", "quality_clips", "quality_cep"]:
                celery_queues[q] = r.llen(q) or 0
            r.close()
        except Exception:  # noqa: S110
            pass

        # Contadores RED reais (app/core/request_metrics.py) — mesmo shape
        # de resposta de antes (aditivo; era hardcoded 0)
        from app.core.request_metrics import aggregate_last_hours
        agg = aggregate_last_hours(24)

        return success({
            "errors_24h": int(agg["err_4xx"] + agg["err_5xx"]),
            "avg_response_ms": agg["avg_ms"],
            "celery_queues": celery_queues,
        })
    except Exception as exc:
        logger.error("health_metrics_error: %s", exc, exc_info=True)
        return error("Erro ao buscar métricas de saúde", 500)


# ---------------------------------------------------------------------------
# Inventory — GET /api/v1/admin/inventory
# ---------------------------------------------------------------------------
@admin_bp.route("/inventory", methods=["GET"])
@require_superadmin
def get_inventory():
    """
    Retorna inventário de câmeras, edge_devices e sites.

    Query params opcionais:
      tenant_id   — filtrar por tenant
      camera_id   — filtrar por câmera (UUID)
      site_id     — filtrar por site
      brand       — filtrar por marca
      probe_status — filtrar por status de probe (pending|ok|error|timeout)
    """
    try:
        tenant_filter = request.args.get("tenant_id")
        camera_id_filter = request.args.get("camera_id")
        site_id_filter = request.args.get("site_id")
        brand_filter = request.args.get("brand")
        probe_status_filter = request.args.get("probe_status")

        if camera_id_filter:
            try:
                uuid.UUID(str(camera_id_filter))
            except (ValueError, TypeError):
                return error("camera_id inválido", 400)

        conditions = ["1=1"]
        params: list = []
        if tenant_filter:
            conditions.append("c.tenant_id = %s")
            params.append(tenant_filter)
        if camera_id_filter:
            conditions.append("c.id = %s")
            params.append(camera_id_filter)
        if site_id_filter:
            conditions.append("c.site_id = %s")
            params.append(site_id_filter)
        if brand_filter:
            conditions.append("c.brand ILIKE %s")
            params.append(f"%{brand_filter}%")
        if probe_status_filter:
            conditions.append("c.probe_status = %s")
            params.append(probe_status_filter)
        where = " AND ".join(conditions)

        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    c.id, c.tenant_id, c.name, c.location, c.description,
                    c.manufacturer, c.host, c.port, c.username,
                    c.channel, c.subtype, c.is_active, c.active_module,
                    c.last_seen, c.last_error, c.last_tested_at,
                    c.created_at, c.updated_at,
                    c.site_id, c.brand, c.model, c.ip,
                    c.rtsp_substream_url, c.codec_detected, c.substream_ok,
                    c.max_connections, c.last_probe_at, c.probe_status, c.notes,
                    t.name AS tenant_name, t.slug AS tenant_slug
                FROM public.cameras c
                LEFT JOIN public.tenants t ON t.id = c.tenant_id
                WHERE {where}
                ORDER BY c.created_at DESC
                LIMIT 500
            """, tuple(params))  # noqa: S608
            camera_rows = cur.fetchall()
            cameras = [_clean_row(dict(r)) for r in camera_rows]

        return success({
            "cameras": cameras,
            "edge_devices": [],   # reservado para fase futura
            "sites": [],          # reservado para fase futura
            "total": len(cameras),
        })
    except Exception as exc:
        logger.error("get_inventory_error: %s", exc, exc_info=True)
        return error("Erro ao carregar inventário", 500)


# ---------------------------------------------------------------------------
# Camera Import — POST /api/v1/admin/cameras/import
# ---------------------------------------------------------------------------
@admin_bp.route("/cameras/import", methods=["POST"])
@require_superadmin
def import_cameras():
    """
    Importação em lote de câmeras (cria como draft — is_active=False).

    Body JSON:
      { "cameras": [ { name, brand, ip, port, username, module, tenant_id, ... } ] }

    Retorna: { created: N, errors: [{ row: N, reason: "..." }] }
    """
    try:
        data = request.get_json() or {}
        camera_list = data.get("cameras", [])
        if not isinstance(camera_list, list) or not camera_list:
            return error("Body deve conter 'cameras' como lista não vazia", 400)
        if len(camera_list) > 200:
            return error("Máximo de 200 câmeras por importação", 400)

        pool = _pool()
        created = 0
        errors: list[dict] = []

        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                for idx, cam in enumerate(camera_list):
                    row_num = idx + 1
                    try:
                        name = (cam.get("name") or "").strip()
                        ip = (cam.get("ip") or "").strip()
                        tenant_id = (cam.get("tenant_id") or "").strip()

                        if not name:
                            errors.append({"row": row_num, "reason": "Campo 'name' é obrigatório"})
                            continue
                        if not ip:
                            errors.append({"row": row_num, "reason": "Campo 'ip' é obrigatório"})
                            continue
                        if not tenant_id:
                            errors.append({"row": row_num, "reason": "Campo 'tenant_id' é obrigatório"})
                            continue

                        # Validar tenant_id
                        try:
                            uuid.UUID(tenant_id)
                        except ValueError:
                            errors.append({"row": row_num, "reason": f"tenant_id inválido: {tenant_id}"})
                            continue

                        # Verificar se tenant existe
                        cur.execute(
                            "SELECT id FROM public.tenants WHERE id = %s AND is_active = true",
                            (tenant_id,),
                        )
                        if not cur.fetchone():
                            errors.append({"row": row_num, "reason": f"tenant_id não encontrado: {tenant_id}"})
                            continue

                        port = int(cam.get("port") or 554)
                        username = (cam.get("username") or "admin").strip()
                        brand = (cam.get("brand") or "").strip() or None
                        model = (cam.get("model") or "").strip() or None
                        manufacturer = (cam.get("manufacturer") or brand or "generic").strip()
                        module = (cam.get("module") or "epi").strip()
                        location = (cam.get("location") or "").strip() or None
                        notes = (cam.get("notes") or "").strip() or None

                        cur.execute("""
                            INSERT INTO public.cameras (
                                tenant_id, name, location, manufacturer,
                                host, port, username,
                                active_module, is_active,
                                brand, model, ip, notes, probe_status
                            ) VALUES (
                                %s, %s, %s, %s,
                                %s, %s, %s,
                                %s, false,
                                %s, %s, %s, %s, 'pending'
                            )
                        """, (
                            tenant_id, name, location, manufacturer,
                            ip, port, username,
                            module,
                            brand, model, ip, notes,
                        ))
                        created += 1

                    except Exception as row_exc:
                        errors.append({"row": row_num, "reason": str(row_exc)})

            conn.commit()

        actor_id, actor_role = _get_actor()
        log_audit(actor_id, actor_role, None, "camera", "batch_import",
                  "cameras_imported", new_value={"created": created, "errors": len(errors)},
                  ip_address=_get_ip(), user_agent=_get_ua())

        return success({"created": created, "errors": errors}, status=207 if errors else 201)

    except Exception as exc:
        logger.error("import_cameras_error: %s", exc, exc_info=True)
        return error("Erro ao importar câmeras", 500)


# ---------------------------------------------------------------------------
# Camera Probe — POST /api/v1/admin/cameras/<id>/probe
# ---------------------------------------------------------------------------
@admin_bp.route("/cameras/<camera_id>/probe", methods=["POST"])
@require_superadmin
def probe_camera(camera_id: str):
    """
    Testa conectividade de uma câmera e atualiza campos de probe no banco.

    Anti-SSRF: host validado via socket (apenas IPs/hosts resolvíveis).
    Atualiza: codec_detected, substream_ok, probe_status, last_probe_at.
    """
    import socket  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    try:
        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, host, port, rtsp_substream_url FROM public.cameras WHERE id = %s",
                (camera_id,),
            )
            cam_row = cur.fetchone()

        if not cam_row:
            return error("Câmera não encontrada", 404)

        host = cam_row["host"] or ""
        port = int(cam_row["port"] or 554)
        substream_url = cam_row["rtsp_substream_url"]

        probe_status = "error"
        codec_detected = None
        substream_ok = False
        error_detail = None

        # Step 1: Resolver host (anti-SSRF básico)
        try:
            resolved_ip = socket.gethostbyname(host)
            # Bloquear loopback, link-local e multicast
            import ipaddress  # noqa: PLC0415
            ip_obj = ipaddress.ip_address(resolved_ip)
            if ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_unspecified:
                return error(f"Host não permitido (endereço reservado): {host}", 400)
        except socket.gaierror:
            error_detail = f"Host não resolvível: {host}"
            _update_probe_fields(pool, camera_id, "error", None, False, error_detail)
            return success({"camera_id": camera_id, "probe_status": "error", "detail": error_detail})

        # Step 2: Porta aberta (TCP)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            port_result = sock.connect_ex((host, port))
        finally:
            sock.close()

        if port_result != 0:
            error_detail = f"Porta {port} fechada ou bloqueada"
            _update_probe_fields(pool, camera_id, "error", None, False, error_detail)
            return success({"camera_id": camera_id, "probe_status": "error", "detail": error_detail})

        probe_status = "ok"

        # Step 3: ffprobe para detectar codec (best-effort)
        if substream_url:
            try:
                proc = subprocess.run(
                    ["ffprobe", "-v", "error",
                     "-rtsp_transport", "tcp",
                     "-i", substream_url,
                     "-show_entries", "stream=codec_name,codec_type",
                     "-of", "csv=p=0",
                     "-timeout", "5000000"],
                    capture_output=True, timeout=10,
                )
                if proc.returncode == 0:
                    output = proc.stdout.decode(errors="replace").strip()
                    # Procurar linha de vídeo: "video,h264" ou similar
                    for line in output.splitlines():
                        parts = line.split(",")
                        if len(parts) >= 2 and parts[0] == "video":
                            codec_detected = parts[1].upper()
                            break
                    substream_ok = True
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass  # ffprobe não disponível ou timeout — continua como ok (TCP passou)

        _update_probe_fields(pool, camera_id, probe_status, codec_detected, substream_ok, None)

        return success({
            "camera_id": camera_id,
            "probe_status": probe_status,
            "codec_detected": codec_detected,
            "substream_ok": substream_ok,
        })

    except Exception as exc:
        logger.error("probe_camera_error: %s", exc, exc_info=True)
        return error("Erro ao fazer probe da câmera", 500)


def _update_probe_fields(
    pool,
    camera_id: str,
    probe_status: str,
    codec_detected,
    substream_ok: bool,
    last_error,
) -> None:
    """Grava resultado de probe na câmera (best-effort — não levanta exceção)."""
    try:
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE public.cameras
                    SET probe_status   = %s,
                        codec_detected = %s,
                        substream_ok   = %s,
                        last_probe_at  = NOW(),
                        last_error     = %s
                    WHERE id = %s
                """, (probe_status, codec_detected, substream_ok, last_error, camera_id))
            conn.commit()
    except Exception as exc:
        logger.warning("update_probe_fields_failed camera=%s: %s", camera_id, exc)


# ---------------------------------------------------------------------------
# Camera Probe-Batch — POST /api/v1/admin/cameras/probe-batch
# ---------------------------------------------------------------------------
@admin_bp.route("/cameras/probe-batch", methods=["POST"])
@require_superadmin
def probe_cameras_batch():
    """
    Testa conectividade de múltiplas câmeras em paralelo.

    Body: { "camera_ids": ["uuid1", "uuid2", ...] }
    Limite: max 50 câmeras por chamada, max 5 simultâneos.

    Retorna: { results: [{ camera_id, probe_status, codec_detected, substream_ok }] }
    """
    import socket  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import threading  # noqa: PLC0415

    try:
        data = request.get_json() or {}
        camera_ids = data.get("camera_ids", [])
        if not isinstance(camera_ids, list) or not camera_ids:
            return error("Body deve conter 'camera_ids' como lista não vazia", 400)
        if len(camera_ids) > 50:
            return error("Máximo de 50 câmeras por probe-batch", 400)

        pool = _pool()

        # Buscar dados de todas as câmeras de uma vez
        with pool.get_connection() as conn, conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(camera_ids))
            cur.execute(
                f"SELECT id, host, port, rtsp_substream_url "  # noqa: S608
                f"FROM public.cameras WHERE id IN ({placeholders})",
                tuple(camera_ids),
            )
            cam_rows = {str(r["id"]): dict(r) for r in cur.fetchall()}

        results: list[dict] = []
        results_lock = threading.Lock()
        semaphore = threading.Semaphore(5)  # max 5 simultâneos

        def _probe_one(cid: str) -> None:
            with semaphore:
                cam = cam_rows.get(cid)
                if not cam:
                    with results_lock:
                        results.append({
                            "camera_id": cid,
                            "probe_status": "error",
                            "codec_detected": None,
                            "substream_ok": False,
                            "detail": "Câmera não encontrada",
                        })
                    return

                host = cam.get("host") or ""
                port = int(cam.get("port") or 554)
                probe_status = "error"
                codec_detected = None
                substream_ok = False

                try:
                    # TCP check
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(5)
                    try:
                        rc = sock.connect_ex((host, port))
                    finally:
                        sock.close()

                    if rc == 0:
                        probe_status = "ok"
                        substream_url = cam.get("rtsp_substream_url")
                        if substream_url:
                            try:
                                proc = subprocess.run(
                                    ["ffprobe", "-v", "error",
                                     "-rtsp_transport", "tcp",
                                     "-i", substream_url,
                                     "-show_entries", "stream=codec_name,codec_type",
                                     "-of", "csv=p=0",
                                     "-timeout", "5000000"],
                                    capture_output=True, timeout=8,
                                )
                                if proc.returncode == 0:
                                    output = proc.stdout.decode(errors="replace").strip()
                                    for line in output.splitlines():
                                        parts = line.split(",")
                                        if len(parts) >= 2 and parts[0] == "video":
                                            codec_detected = parts[1].upper()
                                            break
                                    substream_ok = True
                            except (subprocess.TimeoutExpired, FileNotFoundError):
                                pass

                    _update_probe_fields(pool, cid, probe_status, codec_detected, substream_ok, None)

                except Exception as probe_exc:
                    logger.warning("probe_batch_camera_error camera=%s: %s", cid, probe_exc)
                    probe_status = "error"

                with results_lock:
                    results.append({
                        "camera_id": cid,
                        "probe_status": probe_status,
                        "codec_detected": codec_detected,
                        "substream_ok": substream_ok,
                    })

        threads = [threading.Thread(target=_probe_one, args=(cid,)) for cid in camera_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        return success({"results": results})

    except Exception as exc:
        logger.error("probe_batch_error: %s", exc, exc_info=True)
        return error("Erro ao fazer probe em lote", 500)


# ---------------------------------------------------------------------------
# Client-facing — Announcements (qualquer role autenticada)
# ---------------------------------------------------------------------------
@client_bp.route("/announcements", methods=["GET"])
@jwt_required()
def get_client_announcements():
    try:
        from app.core.auth import get_tenant_id
        tenant_id = get_tenant_id()

        pool = _pool()
        with pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT pa.id, pa.title, pa.content, pa.type, pa.published_at
                    FROM public.platform_announcements pa
                    WHERE pa.published_at <= NOW()
                      AND (pa.expires_at IS NULL OR pa.expires_at > NOW())
                      AND (pa.target = 'all' OR pa.target_tenant_id = %s)
                      AND NOT EXISTS (
                        SELECT 1 FROM public.announcement_reads ar
                        WHERE ar.announcement_id = pa.id AND ar.tenant_id = %s
                      )
                    ORDER BY pa.published_at DESC
                """, (str(tenant_id), str(tenant_id)))
            rows = cur.fetchall()
            items = [_clean_row(dict(r)) for r in rows]

        return success({"announcements": items})
    except Exception as exc:
        logger.error("client_announcements_error: %s", exc, exc_info=True)
        return error("Erro ao buscar comunicados", 500)


@client_bp.route("/announcements/<announcement_id>/read", methods=["POST"])
@jwt_required()
def mark_announcement_read(announcement_id: str):
    try:
        from app.core.auth import get_tenant_id
        tenant_id = get_tenant_id()

        pool = _pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.announcement_reads (announcement_id, tenant_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, (announcement_id, str(tenant_id)))
            conn.commit()

        return success({"read": True})
    except Exception as exc:
        logger.error("mark_read_error: %s", exc, exc_info=True)
        return error("Erro ao marcar comunicado como lido", 500)

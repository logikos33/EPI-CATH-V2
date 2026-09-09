"""
Recognition — Celery Factory.

Celery integrado ao contexto Flask via make_celery().
Entry point do worker: celery -A app.infrastructure.queue.celery_app:celery worker
"""
import logging
import os

from celery import Celery
from celery.schedules import crontab

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Celery Beat — schedule CURADO.
#
# SAFE_BEAT_SCHEDULE é o que o serviço beat (SERVICE_TYPE=beat) agenda de fato:
# tarefas seguras E cuja fila de destino é consumida por um worker neste deploy
# (o worker consome reports + quality_cep + training — ver railway_start.py):
#   - compliance-daily-report → fila "reports". Sem deleção; PDF datado no R2.
#   - quality-cep-baseline → fila "quality_cep". UPSERT de UCL/LCL; sem deleção.
#   - quality-shift-reports → fila "quality_cep". Lê inspeções, grava no Redis.
#   - model-drift-check → fila "training". Grava métricas de drift (insert
#     barato, não destrutivo). Cost-safe: o ÚNICO gatilho de treino é
#     check_auto_retraining.delay(), no-op salvo AUTO_TRAIN_ENABLED=true — logo
#     ZERO GPU/treino por padrão (confirmado em tasks/model_drift.py).
#   - runpod-reconcile-pods → fila "training". Camada 3/3 de garantia de
#     morte de pods GPU (ver infrastructure/gpu/runpod_runner.py) — só
#     TERMINA pods RunPod órfãos/expirados/de jobs terminais; nunca cria
#     GPU nem mexe em dado do produto. Sem RUNPOD_API_KEY, é no-op
#     (ver tasks/gpu_reconciler.py).
#   - edge-propagation-reconcile-timeouts → fila "training". Honestidade de
#     estado pra propagação no EDGE (task "propagação no edge"): não há pod
#     pra matar (o box roda local, sem watchdog Celery bloqueante) — só
#     marca 'failed' um job 'running' há mais que
#     EDGE_PROPAGATION_TIMEOUT_SECONDS (default 7200s) sem callback final do
#     executor. UPDATE não-destrutivo em propagation_jobs (mesma tabela do
#     reconciler acima); nunca cria/mexe em GPU (ver tasks/gpu_reconciler.py).
#
# DEFERRED_BEAT_SCHEDULE fica DELIBERADAMENTE FORA do schedule ativo. NÃO mover
# nada para SAFE_BEAT_SCHEDULE sem ler o motivo:
#   - quality-cleanup-recordings (48h) / quality-cleanup-clips (7d): NÃO ligar
#     sem contexto. A retenção é HARDCODED (QUALITY_BUFFER_HOURS=48 /
#     QUALITY_CLIP_RETENTION_DAYS=7) e CONFLITA com os tiers de retenção
#     CONFIGURÁVEIS do ADR-0047. No 1º disparo elas APAGARIAM em massa o backlog
#     acumulado no R2 — deleção IRREVERSÍVEL. Reconciliar com o ADR-0047 antes
#     de agendar (follow-up: tools/agent-driver/tasks/task-077-*).
#   - quality-wiser-retry: push a cada 5 min a um sistema externo (Wiser/MES);
#     só com a integração ativa por tenant.
#   - auto-retraining-check: dispara treino/GPU ($$); já é no-op salvo
#     AUTO_TRAIN_ENABLED=true; fora do schedule até validação de custo (o
#     model-drift ainda o enfileira via .delay() ao detectar drift — no-op).
# ---------------------------------------------------------------------------
SAFE_BEAT_SCHEDULE = {
    # Backup do banco 2x/dia, com DRILL a cada execução (tasks/backup.py).
    # Auditado em 25/08: a spec de 20/08 pedia isto e NADA existia — no R2
    # havia um único dump manual, de 5 dias antes. Gate de 02/09.
    #
    # 12h e não 24h porque a janela de perda aceitável é meio dia. O par desta
    # entrada é GET /health/backup, que denuncia a AUSÊNCIA: agendamento que
    # morre não avisa, só deixa de aparecer arquivo novo.
    # Fila `reports` e NÃO `maintenance`: o worker consome
    # extraction,quality,versioning,inference,training,reports,quality_cep
    # (railway_start.py). `maintenance` não tem consumidor — a tarefa seria
    # agendada e ficaria numa fila que ninguém lê, que é exatamente o silêncio
    # que este backup existe para acabar. Quem pegou isso foi
    # test_beat_schedule.py, cuja regra é: entrada ativa só com worker na fila.
    # ⚠️ `crontab` e NÃO `43200` (12h). MEDIDO em 05/09: com intervalo, o beat
    # só dispara `intervalo` depois do ÚLTIMO BOOT — o estado do
    # PersistentScheduler vive em /tmp (efêmero, ver railway_start.py) e nasce
    # zerado a cada deploy. O DEV redeploya sozinho a cada merge na develop;
    # num dia de mutirão isso é mais de uma vez a cada 12h, então a entrada
    # com intervalo NUNCA venceria. Cron é hora de parede: reiniciar não adia
    # o próximo disparo. 03:00 e 15:00 UTC = 00:00 e 12:00 em Brasília.
    "backup-postgres": {
        "task": "tasks.backup.backup_database",
        "schedule": crontab(minute=0, hour="3,15"),  # 2x/dia, hora de parede
        "options": {"queue": "reports"},
    },
    # Compliance EPI — relatório diário arquivado no R2 (task-043 lacuna 2)
    "compliance-daily-report": {
        "task": "app.infrastructure.queue.tasks.compliance.generate_daily_compliance_reports",
        "schedule": 86400,  # diário
        "options": {"queue": "reports"},
    },
    "quality-cep-baseline": {
        "task": "app.infrastructure.queue.tasks.quality_cep.update_quality_cep_baseline",
        "schedule": 84600,  # diário (23.5h para evitar drift)
        "options": {"queue": "quality_cep"},
    },
    "quality-shift-reports": {
        "task": "app.infrastructure.queue.tasks.quality_cep.generate_shift_reports",
        "schedule": 28800,  # a cada 8h (cobre 06:15, 14:15, 22:15 com margem)
        "options": {"queue": "quality_cep"},
    },
    "model-drift-check": {
        # deve casar com o name= explícito em tasks/model_drift.py
        "task": "tasks.model_drift.compute_drift_metrics",
        "schedule": 86400,  # diário
        "options": {"queue": "training"},
    },
    "runpod-reconcile-pods": {
        # deve casar com o name= explícito em tasks/gpu_reconciler.py
        "task": "tasks.gpu_reconciler.reconcile_runpod_pods",
        "schedule": 300,  # a cada 5 minutos
        "options": {"queue": "training"},
    },
    "edge-propagation-reconcile-timeouts": {
        # deve casar com o name= explícito em tasks/gpu_reconciler.py
        "task": "tasks.gpu_reconciler.reconcile_edge_propagation_timeouts",
        "schedule": 300,  # a cada 5 minutos
        "options": {"queue": "training"},
    },
}

# DELIBERADAMENTE não agendadas — ver o bloco de comentário acima.
DEFERRED_BEAT_SCHEDULE = {
    "quality-cleanup-recordings": {
        "task": "app.infrastructure.queue.tasks.quality_cep.cleanup_quality_recordings",
        "schedule": 3600,  # horário
        "options": {"queue": "quality_cep"},
    },
    "quality-cleanup-clips": {
        "task": "app.infrastructure.queue.tasks.quality_cep.cleanup_quality_clips",
        "schedule": 86400,  # diário
        "options": {"queue": "quality_cep"},
    },
    "quality-wiser-retry": {
        "task": "app.infrastructure.queue.tasks.quality_inference.retry_failed_wiser_exports",
        "schedule": 300,  # a cada 5 minutos
        "options": {"queue": "quality_inference"},
    },
    "auto-retraining-check": {
        # X-5: deve casar com o name= explícito em tasks/auto_training.py
        "task": "tasks.auto_training.check_auto_retraining",
        "schedule": 3600,  # horário
        "options": {"queue": "training"},
    },
}


def make_celery(app: object | None = None) -> Celery:
    """Cria instância Celery configurada.

    Se app Flask fornecido, integra com app context.
    Caso contrário, cria standalone (para worker).
    """
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    celery = Celery(
        "epi_monitor",
        broker=redis_url,
        backend=redis_url,
        include=[
            "app.infrastructure.queue.tasks.backup",
            "app.infrastructure.queue.tasks.extraction",
            "app.infrastructure.queue.tasks.quality",
            "app.infrastructure.queue.tasks.versioning",
            "app.infrastructure.queue.tasks.versioning_v2",
            "app.infrastructure.queue.tasks.inference",
            "app.infrastructure.queue.tasks.training",
            "app.infrastructure.queue.tasks.propagation",
            "app.infrastructure.queue.tasks.verification",
            "app.infrastructure.queue.tasks.auto_training",
            "app.infrastructure.queue.tasks.nvr_extraction",
            "app.infrastructure.queue.tasks.model_evaluation",
            "app.infrastructure.queue.tasks.model_validation",
            "app.infrastructure.queue.tasks.model_drift",
            "app.infrastructure.queue.tasks.gpu_reconciler",
            # Módulo de Qualidade Industrial — filas dedicadas e isoladas
            "app.infrastructure.queue.tasks.quality_recording",
            "app.infrastructure.queue.tasks.quality_clips",
            "app.infrastructure.queue.tasks.quality_annotation",
            "app.infrastructure.queue.tasks.quality_training",
            "app.infrastructure.queue.tasks.quality_inference",
            "app.infrastructure.queue.tasks.quality_cep",
            # Relatórios agendados
            "app.infrastructure.queue.tasks.compliance",
        ],
    )

    celery.conf.update(
        # Serialização
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Confiabilidade
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        # Cleanup
        worker_max_tasks_per_child=100,
        # Timezone
        timezone="UTC",
        enable_utc=True,
        # Rotas por fila
        task_routes={
            "app.infrastructure.queue.tasks.extraction.*": {"queue": "extraction"},
            "app.infrastructure.queue.tasks.nvr_extraction.*": {"queue": "extraction"},
            "app.infrastructure.queue.tasks.quality.*": {"queue": "extraction"},
            "app.infrastructure.queue.tasks.versioning.*": {"queue": "versioning"},
            "app.infrastructure.queue.tasks.training.*": {"queue": "training"},
            "app.infrastructure.queue.tasks.propagation.*": {"queue": "training"},
            "app.infrastructure.queue.tasks.model_evaluation.*": {"queue": "training"},
            "app.infrastructure.queue.tasks.model_drift.*": {"queue": "training"},
            "app.infrastructure.queue.tasks.gpu_reconciler.*": {"queue": "training"},
            "app.infrastructure.queue.tasks.inference.*": {"queue": "inference"},
            "app.infrastructure.queue.tasks.verification.*": {"queue": "inference"},
            # Módulo de Qualidade Industrial — filas isoladas
            "app.infrastructure.queue.tasks.quality_recording.*": {"queue": "quality_recording"},
            "app.infrastructure.queue.tasks.quality_clips.*":     {"queue": "quality_clips"},
            "app.infrastructure.queue.tasks.quality_annotation.*": {"queue": "quality_annotation"},
            "app.infrastructure.queue.tasks.quality_training.*":  {"queue": "quality_training"},
            "app.infrastructure.queue.tasks.quality_inference.*": {"queue": "quality_inference"},
            "app.infrastructure.queue.tasks.quality_cep.*":       {"queue": "quality_cep"},
            # Relatórios agendados
            "app.infrastructure.queue.tasks.compliance.*": {"queue": "reports"},
        },
        # Celery Beat — apenas o schedule CURADO seguro. Os cleanups destrutivos,
        # o wiser-retry e o auto-retraining ficam em DEFERRED_BEAT_SCHEDULE (topo
        # do módulo) e NÃO são agendados.
        beat_schedule=SAFE_BEAT_SCHEDULE,
    )

    # Integrar com Flask app context se disponível
    if app is not None:
        class ContextTask(celery.Task):  # type: ignore[name-defined]
            abstract = True

            def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                with app.app_context():  # type: ignore[union-attr]
                    return self.run(*args, **kwargs)

        celery.Task = ContextTask

    # ⛔ `redis_url[:30]` VAZAVA a senha: `redis://default:` são 16 caracteres,
    # então os 30 primeiros entregam ~14 caracteres do segredo — a cada boot,
    # em todo log de worker e de API. Truncar NÃO é mascarar.
    # Mesmo defeito já corrigido em tasks/quality_inference.py (era rtsp_url[:40]);
    # os outros dois sites ficaram. Ver app/core/redact.py.
    from app.core.redact import redact_url_credentials  # noqa: PLC0415

    logger.info("celery_configured: broker=%s", redact_url_credentials(redis_url))
    return celery


# Standalone celery instance para o worker
celery = make_celery()


# Inicializa DatabasePool em cada worker process (prefork model)
from celery.signals import worker_process_init  # noqa: E402


@worker_process_init.connect
def _init_worker_db(**kwargs):  # type: ignore[no-untyped-def]
    """Chamado em cada forked worker — garante sys.path e inicializa o pool DB."""
    import os as _os  # noqa: PLC0415
    import sys  # noqa: PLC0415

    # celery_app.py está em backend/app/infrastructure/queue/ — sobe 4 níveis para backend/
    _backend = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__)
    ))))
    if _backend not in sys.path:
        sys.path.insert(0, _backend)
        logger.info("worker_syspath_fixed: %s", _backend)

    from app.infrastructure.database.connection import DatabasePool, get_database_url
    db_url = get_database_url()
    if db_url:
        DatabasePool.initialize(db_url, min_conn=1, max_conn=3)
        logger.info("worker_db_pool_initialized")


# ---------------------------------------------------------------------------
# Storage preflight no boot do worker (mutirão 2.1, D-03).
#
# O worker Celery roda em processo separado (railway_start.py:start_celery_worker
# -> celery.worker_main(...)) que NÃO passa por app.create_app() — sem isto,
# um R2 mal configurado só apareceria na primeira task de upload (quality_*,
# extraction, versioning, ...), tarde e sem matar o boot.
#
# `worker_init` (não `worker_process_init`) de propósito: dispara 1x no
# processo principal do worker, ANTES do fork dos filhos do pool prefork —
# se a config for inválida, o worker nunca chega a consumir fila nenhuma.
# Só roda de fato quando `celery.worker_main(...)`/`Worker(...).start()` é
# chamado (start_celery_worker) — nunca ao meramente importar este módulo
# (ex.: API despachando tasks, ou os testes importando `celery`).
# ---------------------------------------------------------------------------
from celery.signals import worker_init  # noqa: E402


@worker_init.connect
def _preflight_storage_on_worker_boot(**kwargs):  # type: ignore[no-untyped-def]
    """Mesma checagem de storage do boot da API (app.create_app) — o worker
    roda em processo separado que não passa por lá. Mata o processo
    (SystemExit(78)) se R2 não estiver configurado nem ALLOW_EPHEMERAL_
    STORAGE=1 explícito, se o efêmero estiver ligado em produção, ou se a
    credencial R2 não passar no head_bucket."""
    from app.infrastructure.storage.local_storage import ensure_storage_ready
    ensure_storage_ready()


# ---------------------------------------------------------------------------
# Contadores de sucesso/falha/retry por fila (WS11/E2-5 — Observability)
#
# Hash Redis `celery:stats:{queue}:{YYYYMMDD}` (fields ok/fail/retry, TTL 8d).
# Viabiliza "falhas por fila" no dashboard sem consumir task events.
# Handlers 100% defensivos — métrica NUNCA quebra a task.
# ---------------------------------------------------------------------------
from celery.signals import task_failure, task_retry, task_success  # noqa: E402

_STATS_TTL_SECONDS = 8 * 86400  # 8 dias


def _resolve_queue(sender: object) -> str:
    """Fila da task via routing_key do delivery_info; fallback 'unknown'."""
    try:
        req = getattr(sender, "request", None)
        delivery_info = getattr(req, "delivery_info", None) or {}
        return delivery_info.get("routing_key") or "unknown"
    except Exception:
        return "unknown"


def _incr_task_stat(sender: object, field: str) -> None:
    try:
        from datetime import datetime, timezone  # noqa: PLC0415

        import redis as _redis  # noqa: PLC0415

        queue = _resolve_queue(sender)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        key = f"celery:stats:{queue}:{day}"
        r = _redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        pipe = r.pipeline(transaction=False)
        pipe.hincrby(key, field, 1)
        pipe.expire(key, _STATS_TTL_SECONDS)
        pipe.execute()
        r.close()
    except Exception as exc:
        logger.debug("celery_stats_incr_failed: field=%s err=%s", field, exc)


@task_success.connect
def _on_task_success(sender=None, **_kwargs):  # type: ignore[no-untyped-def]
    _incr_task_stat(sender, "ok")


@task_failure.connect
def _on_task_failure(sender=None, **_kwargs):  # type: ignore[no-untyped-def]
    _incr_task_stat(sender, "fail")


@task_retry.connect
def _on_task_retry(sender=None, **_kwargs):  # type: ignore[no-untyped-def]
    _incr_task_stat(sender, "retry")


def get_inference_queue(tenant_schema: str) -> str:
    """
    Retorna a fila de inferência correta para o tenant.

    Se um worker on-premise estiver ativo (heartbeat Redis presente),
    retorna `inference_{tenant_schema}`.
    Caso contrário, retorna `inference` (fila padrão Railway).

    Args:
        tenant_schema: schema do tenant (ex: "rvb")

    Returns:
        Nome da fila Celery a usar para enviar tasks de inferência.
    """
    try:
        from app.infrastructure.queue.worker_registry import get_worker_status
        status = get_worker_status(tenant_schema)
        if status == "onpremise":
            return f"inference_{tenant_schema}"
    except Exception as exc:
        logger.debug("get_inference_queue_error: schema=%s err=%s", tenant_schema, exc)
    return "inference"

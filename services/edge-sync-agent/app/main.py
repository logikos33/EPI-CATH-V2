"""Entrypoint(s) for the edge-sync-agent (task-090/091/096, PR-A/PR-B/PR-C).

Two entrypoints live here:
  - `main()` — the evidence + discovery API alone: the evidence mini-API with
    a real (non-stub) RecorderClient (`evidence_api.create_app`,
    task-090/091) plus the ONVIF discovery API (`discovery_api.bp`,
    task-096), registered onto the SAME app (not a second `create_app`/
    second port) so both share one TRUST_ANCHOR and one WireGuard-restricted
    bind-host validation (see evidence_api.py's `validate_bind_host` —
    ADR-0020: this process must never listen on 0.0.0.0/::). Kept as its own
    function/tests unchanged since PR-C.
  - `run_daemon()` — the full supervised daemon (PR-C): the evidence API
    above (in its own thread) PLUS config_poller/command_poller/uploader/
    heartbeat (PR-B), each in its own thread sharing one `stop_event`,
    restarted with backoff if one crashes, shut down gracefully on
    SIGTERM/SIGINT. This is what `python -m app.main` runs. It reuses the
    loops as-is (task-028/task-034/PR-B) — no loop logic lives in this file,
    only orchestration (identity via PR-A's token_manager, thread lifecycle).

Run: `python -m app.main` (env-configured, see AGENT.md's "Variáveis de
Ambiente" table for the RECORDER_*/EVIDENCE_*/ONVIF_DISCOVERY_*/DEVICE_*/
EDGE_*/SQLITE_*/UPLOAD_* entries).
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from typing import Any

import httpx

from . import edge_config_cache
from .auth.enrollment import EnrollmentError, build_enrollment_config_from_env, ensure_enrolled
from .auth.token_manager import TokenManager, build_token_manager_from_env
from .command_poller import CommandPoller
from .config_poller import ConfigPoller
from .detection_relay import build_detection_relay_from_env
from .discovery_api import bp as discovery_bp
from .evidence_api import create_app, run_server
from .evidence_auth import TrustAnchor
from .heartbeat import build_heartbeat_loop_from_env
from .logging_setup import install_redacted_logging
from .monitoring.handlers import build_monitoring_handler_from_env
from .recorder_client import RecorderError
from .recorder_factory import (
    build_recorder_client_from_env,
    resolve_channel_map,
    validate_onvif_boot_or_raise,
)
from .snapshot_executor import SnapshotExecutor
from .sqlite_buffer import SQLiteBuffer
from .uploader import Uploader

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8443
_DEFAULT_DISCOVERY_TIMEOUT_S = 3.0
_DEFAULT_DISCOVERY_MAX_RESPONSES = 50
_DEFAULT_SQLITE_BUFFER_PATH = "/var/edge-sync/buffer.db"
_DEFAULT_UPLOAD_BATCH_SIZE = 500
_RESTART_BACKOFF_STEPS: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0)
_THREAD_JOIN_TIMEOUT_S = 35.0


def _read_public_key_pem(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        raise RecorderError(
            f"não foi possível ler EVIDENCE_TRUST_PUBLIC_KEY_PATH={path!r}: {exc}"
        ) from exc


def build_trust_anchor(env: dict[str, str] | None = None) -> TrustAnchor:
    source = env if env is not None else os.environ
    key_path = source.get(
        "EVIDENCE_TRUST_PUBLIC_KEY_PATH", "/run/secrets/evidence_trust_public_key.pem"
    )
    tenant_id = source.get("TENANT_ID", "")
    site_id = source.get("SITE_ID", "")
    public_key_pem = _read_public_key_pem(key_path)
    return TrustAnchor(public_key_pem=public_key_pem, tenant_id=tenant_id, site_id=site_id)


def build_evidence_app_and_bind() -> tuple[Any, str, int, Any]:
    """Builds the evidence+discovery Flask app. Factored out of `main()` so
    `run_daemon()` (PR-C) can start it on its own thread instead of blocking
    the calling thread on it — `main()`'s own behavior is unchanged.

    Also returns the `recorder_client` instance built here: `run_daemon()`
    (PR-C, snapshot capture) reuses this SAME object for the snapshot
    executor rather than constructing a second RecorderClient — building it
    twice would run `validate_onvif_boot_or_raise`'s one-shot auth check
    twice at boot, doubling the auth attempts against the NVR (exactly what
    the anti-lockout discipline exists to avoid).
    """
    bind_host = os.environ.get("EVIDENCE_API_BIND_HOST", "")
    port = int(os.environ.get("EVIDENCE_API_PORT", str(_DEFAULT_PORT)))

    recorder_client = build_recorder_client_from_env()
    # RECORDER_PROTOCOL=onvif: one auth-liveness check now, at boot — not a
    # retry loop (anti-lockout, see validate_onvif_boot_or_raise docstring).
    # No-op for any other protocol.
    validate_onvif_boot_or_raise(recorder_client)
    trust_anchor = build_trust_anchor()

    app = create_app(recorder_client=recorder_client, trust_anchor=trust_anchor)
    app.config["DISCOVERY_TIMEOUT_S"] = float(
        os.environ.get("ONVIF_DISCOVERY_TIMEOUT_S", str(_DEFAULT_DISCOVERY_TIMEOUT_S))
    )
    app.config["DISCOVERY_MAX_RESPONSES"] = int(
        os.environ.get("ONVIF_DISCOVERY_MAX_RESPONSES", str(_DEFAULT_DISCOVERY_MAX_RESPONSES))
    )
    app.config["DISCOVERY_ENRICH_DEVICE_INFO"] = (
        os.environ.get("ONVIF_DISCOVERY_ENRICH_DEVICE_INFO", "true").strip().lower() != "false"
    )
    app.register_blueprint(discovery_bp, url_prefix="/api/v1/edge/discovery")
    return app, bind_host, port, recorder_client


def main() -> None:
    """Evidence + discovery API only (task-090/091/096). See `run_daemon()`
    for the full supervised daemon (PR-C) that also runs the sync loops."""
    install_redacted_logging()
    try:
        app, bind_host, port, _recorder_client = build_evidence_app_and_bind()
    except RecorderError as exc:
        logger.error("evidence_api_startup_config_error %s", exc)
        sys.exit(1)
    run_server(app, host=bind_host, port=port)


# ─────────────────────────────────────────────────────────────────────────────
# PR-C: supervised daemon. Starts config_poller/command_poller/uploader/
# heartbeat (PR-B) each in its own thread sharing one stop_event, plus the
# evidence/discovery API above in a fourth thread. Restarts a crashed loop
# with backoff instead of taking the whole daemon down; shuts down gracefully
# on SIGTERM/SIGINT. No loop logic lives here — only orchestration.
# ─────────────────────────────────────────────────────────────────────────────


class _AutoAuthHttpClient:
    """Wraps an http client so every request carries a FRESH bearer minted by
    `TokenManager.get_bearer()`.

    `config_poller`/`command_poller`/`uploader` each build a static
    `Authorization` header from the `token` string given at construction —
    fine for a short call, wrong for a loop that outlives a ~5min JWT. This
    overwrites that header per-request instead of touching those loops'
    source (closing the `TokenProvider` contour PR-A left open for the three
    loops that don't already take a live token source — `HeartbeatLoop` does,
    via `token_source`, so it isn't wrapped here).
    """

    def __init__(self, inner: Any, token_manager: TokenManager) -> None:
        self._inner = inner
        self._token_manager = token_manager

    def _authed_kwargs(self, kwargs: dict) -> dict:
        headers = dict(kwargs.get("headers") or {})
        headers["Authorization"] = f"Bearer {self._token_manager.get_bearer()}"
        kwargs["headers"] = headers
        return kwargs

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._inner.get(url, **self._authed_kwargs(kwargs))

    def post(self, url: str, **kwargs: Any) -> Any:
        return self._inner.post(url, **self._authed_kwargs(kwargs))

    def patch(self, url: str, **kwargs: Any) -> Any:
        return self._inner.patch(url, **self._authed_kwargs(kwargs))


def _supervise(
    name: str,
    loop: Any,
    stop_event: threading.Event,
    backoff_steps: tuple[float, ...] = _RESTART_BACKOFF_STEPS,
) -> None:
    """Runs `loop.run(stop_event)`; restarts it with backoff on ANY exit that
    isn't the daemon itself stopping — an uncaught exception (crash) or a
    clean return while `stop_event` is still unset (e.g. a loop that gives up
    on its own, like `HeartbeatLoop` after a 403). Never raises: one
    misbehaving loop must not take the daemon down, and a loop that keeps
    failing should keep failing LOUDLY (backoff-capped retries + logs), not
    go silently idle.
    """
    backoff_idx = 0
    while not stop_event.is_set():
        try:
            loop.run(stop_event)
        except Exception:
            logger.exception("loop_crashed name=%s", name)
        else:
            logger.info("loop_returned name=%s", name)

        if stop_event.is_set():
            return

        delay = backoff_steps[min(backoff_idx, len(backoff_steps) - 1)]
        backoff_idx = min(backoff_idx + 1, len(backoff_steps) - 1)
        logger.warning("loop_restarting name=%s delay_s=%s", name, delay)
        stop_event.wait(timeout=delay)


def _start_loop_threads(
    loops: dict[str, Any], stop_event: threading.Event
) -> list[threading.Thread]:
    """Starts one supervised daemon thread per loop. Callers `join()` the
    returned threads for graceful shutdown."""
    threads = []
    for name, loop in loops.items():
        t = threading.Thread(
            target=_supervise, args=(name, loop, stop_event), name=name, daemon=True
        )
        t.start()
        threads.append(t)
        logger.info("loop_started name=%s", name)
    return threads


def build_sync_loops_from_env(
    http_client: Any,
    token_manager: TokenManager,
    device_id: str,
    cloud_url: str,
    config_version_applied: str = "",
    recorder_client: Any = None,
) -> dict[str, Any]:
    """Constructs config_poller/command_poller/uploader/heartbeat wired to the
    device's enrolled identity. The loop classes are reused unmodified — only
    the http_client/token plumbing (this file's job) is new.

    `SQLITE_BUFFER_PATH`/`UPLOAD_BATCH_SIZE` env vars — no new secrets, same
    names already documented in AGENT.md. `EDGE_REDIS_URL` (optional) turns on
    the detection_relay loop (det:* → buffer) — see detection_relay.py.
    `EDGE_CONFIG_CACHE_PATH` (ADR-0058,
    optional, defaults to edge_config_cache.DEFAULT_CACHE_PATH) is where
    config_poller persists the recorder channel_map for
    recorder_factory.py/collector_loop.py to pick up on their next restart.

    *config_version_applied* — snapshot (taken once in `run_daemon()`, via
    `resolve_channel_map()`, at the SAME moment the evidence API's
    RecorderClient was built) of which config_version this PROCESS actually
    has baked into its recorder client, forwarded to the heartbeat so the
    cloud can compare it against the CURRENT live config_version and log a
    divergence if the box needs a restart to pick up newer cameras.

    *recorder_client* — the SAME RecorderClient instance `run_daemon()` built
    for the evidence API (see build_evidence_app_and_bind's docstring for why
    it must be the same object, not a second one) — wired into a
    SnapshotExecutor for command_poller's capture_snapshot handling.
    `None` (the default, and every existing caller/test) means no snapshot
    capability: capture_snapshot commands ack failed/unsupported, same as any
    other unknown command type.
    """
    authed_http = _AutoAuthHttpClient(http_client, token_manager)

    buffer_path = os.environ.get("SQLITE_BUFFER_PATH", _DEFAULT_SQLITE_BUFFER_PATH)
    batch_size = int(os.environ.get("UPLOAD_BATCH_SIZE", str(_DEFAULT_UPLOAD_BATCH_SIZE)))
    buffer = SQLiteBuffer(buffer_path)
    cache_path = os.environ.get("EDGE_CONFIG_CACHE_PATH") or edge_config_cache.DEFAULT_CACHE_PATH

    config_poller = ConfigPoller(
        authed_http, cloud_url, device_id, token="", cache_path=cache_path
    )
    # Handler dos comandos monitoring.* (/monitoring): lê o ring buffer do
    # coletor em read-only e responde pela mesma artéria outbound (ADR-0020).
    snapshot_executor = (
        SnapshotExecutor(recorder_client, authed_http, cloud_url, token="")
        if recorder_client is not None
        else None
    )
    command_poller = CommandPoller(
        authed_http,
        cloud_url,
        token="",
        config_poller=config_poller,
        monitoring_handler=build_monitoring_handler_from_env(),
        snapshot_executor=snapshot_executor,
        # task "propagação no edge": PROPAGATION_PYTHON/PROPAGATION_EXECUTOR_PATH/
        # PROPAGATION_RUN_DIR são opt-in por env — sem elas, CommandPoller cai
        # nos defaults (~/envs/propagation/bin/python, training/propagate_seeded.py
        # relativo ao checkout do repo, ~/run) documentados em command_poller.py.
        propagation_python=os.environ.get("PROPAGATION_PYTHON") or None,
        propagation_executor_path=os.environ.get("PROPAGATION_EXECUTOR_PATH") or None,
        propagation_run_dir=os.environ.get("PROPAGATION_RUN_DIR") or None,
    )
    uploader = Uploader(
        buffer, authed_http, cloud_url, device_id, token="", batch_size=batch_size
    )
    # HeartbeatLoop mints its own fresh bearer per send via token_manager
    # (token_source) directly — it doesn't need the header-rewriting wrapper.
    heartbeat = build_heartbeat_loop_from_env(
        http_client,
        token_manager,
        device_id=device_id,
        config_version_applied=config_version_applied or None,
    )

    loops: dict[str, Any] = {
        "config_poller": config_poller,
        "command_poller": command_poller,
        "uploader": uploader,
        "heartbeat": heartbeat,
    }
    # Produtor do caminho detecções→cloud (det:* local → o MESMO buffer que o
    # uploader drena). Opt-in por EDGE_REDIS_URL — sem ela, os 4 loops de sempre.
    # A evidência sai do MESMO SnapshotExecutor do comando de snapshot: mesmo
    # gravador, mesmo upload multipart e — o que importa — o MESMO circuit
    # breaker anti-lockout. Uma credencial rejeitada em qualquer um dos dois
    # caminhos para os dois (CLAUDE.md: o gravador pune tentativa repetida).
    relay = build_detection_relay_from_env(
        buffer,
        evidence_capture=(
            snapshot_executor.capture_evidence if snapshot_executor is not None else None
        ),
    )
    if relay is not None:
        loops["detection_relay"] = relay
    return loops


def _make_signal_handler(stop_event: threading.Event) -> Any:
    def _handle(signum: int, _frame: Any) -> None:
        logger.info("signal_received signum=%s — shutting down", signum)
        stop_event.set()

    return _handle


def run_daemon() -> None:
    """Full supervised daemon (PR-C): evidence/discovery API + the four sync
    loops, one process, shared `stop_event`, graceful SIGTERM/SIGINT shutdown.
    """
    install_redacted_logging()

    try:
        evidence_app, bind_host, evidence_port, recorder_client = build_evidence_app_and_bind()
    except RecorderError as exc:
        logger.error("evidence_api_startup_config_error %s", exc)
        sys.exit(1)

    # ADR-0058: snapshot, taken at the SAME startup moment the evidence API's
    # RecorderClient was just built above, of which config_version its
    # channel_map came from ("" when sourced from RECORDER_CHANNEL_MAP in
    # .env, or when the cloud hasn't ever answered — see resolve_channel_map).
    # Forwarded to the heartbeat as-is (a FIXED value, not re-read live) —
    # this is deliberately the process's baked-in state, not ConfigPoller's
    # current in-memory state, so a stale evidence-api/collector process
    # (structural change pending a restart, ADR-0054 D2) is what shows up as
    # a divergence against the cloud's current config_version.
    _, _, config_version_applied = resolve_channel_map(os.environ)

    evidence_thread = threading.Thread(
        target=run_server,
        args=(evidence_app, bind_host, evidence_port),
        name="evidence-api",
        daemon=True,
    )
    evidence_thread.start()
    logger.info("loop_started name=evidence-api")

    token_manager = build_token_manager_from_env()
    enrollment_config = build_enrollment_config_from_env()
    http_client = httpx.Client()
    try:
        identity = ensure_enrolled(token_manager, enrollment_config, http_client)
    except EnrollmentError as exc:
        logger.error("enrollment_failed %s", exc)
        sys.exit(1)

    loops = build_sync_loops_from_env(
        http_client,
        token_manager,
        device_id=identity.device_id,
        cloud_url=enrollment_config.api_url,
        config_version_applied=config_version_applied,
        recorder_client=recorder_client,
    )

    stop_event = threading.Event()
    threads = _start_loop_threads(loops, stop_event)

    handler = _make_signal_handler(stop_event)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)

    stop_event.wait()
    for t in threads:
        t.join(timeout=_THREAD_JOIN_TIMEOUT_S)
        if t.is_alive():
            logger.warning("thread_join_timeout name=%s", t.name)
    logger.info("daemon_stopped")


if __name__ == "__main__":
    run_daemon()

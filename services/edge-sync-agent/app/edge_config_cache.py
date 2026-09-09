"""Local cache of the recorder channel_map delivered by GET /api/v1/edge/config/poll
(ADR-0058, fatia mínima).

Why a file, not in-process state: `ConfigPoller` lives inside `run_daemon()`'s
"config_poller" thread, but the recorder channel_map (camera_id -> canal do
gravador) is also needed by TWO other things that build their own
`RecorderClient`/camera list independently, at their OWN process/thread
startup, before (or without) any `ConfigPoller` of their own:
  - `build_evidence_app_and_bind()` in main.py — builds the evidence API's
    RecorderClient BEFORE `run_daemon()`'s loops (incl. config_poller) exist.
  - `python -m app.collector` — its OWN systemd unit, a SEPARATE OS process
    from the daemon that runs ConfigPoller (see collector_loop.py's docstring
    for why it's a separate process).
A small JSON file, written by ConfigPoller on every successful poll and read
by both consumers at their own startup, is the simplest way to hand the
value across that thread/process boundary without inventing IPC. This is the
same "structural change needs a controlled reload (restart), not a live
hot-swap" trade-off ADR-0054 (D2) already accepted for other config that
changes the camera set — a new/changed channel takes effect on next
restart of the process that consumes it, not instantly.

Never carries a secret: only camera_id -> channel (int) and the config_version
hash — RECORDER_HOST/PORT/PROTOCOL/USERNAME/PASSWORD stay in the device's own
.env (ADR-0057: segredo não entra na config/poll).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Sibling of SQLITE_BUFFER_PATH's default (/var/edge-sync/buffer.db) — same
# directory convention for edge-local runtime state.
DEFAULT_CACHE_PATH = "/var/edge-sync/config_cache.json"


@dataclass(frozen=True)
class ChannelMapCache:
    channel_map: dict[str, int]
    config_version: str
    # Eixo COLETA (migration 114): camera_id -> collection_subtype (0=principal,
    # 1=substream). Independente do channel_map (canal do gravador) acima —
    # convive no MESMO arquivo/cache por ser escrito no mesmo poll. Ausente em
    # arquivos gravados antes desta mudança -> {} (default_factory), nunca None.
    collection_subtype_map: dict[str, int] = field(default_factory=dict)
    # Eixo OPERAÇÃO: camera_id -> fps_target (quadros por segundo que a
    # INFERÊNCIA deve ver nessa câmera). Distinto do collection_subtype acima,
    # que é o eixo COLETA. Sem este mapa o gerador do DeepStream só sabia um
    # `interval` GLOBAL, e o valor que o dono escolhe na tela por câmera não
    # chegava ao box — o elo estava rompido desde a ADR-0055.
    fps_target_map: dict[str, int] = field(default_factory=dict)


def write_channel_map(
    path: str,
    channel_map: dict[str, int],
    config_version: str,
    collection_subtype_map: dict[str, int] | None = None,
    fps_target_map: dict[str, int] | None = None,
) -> None:
    """Atomically persists *channel_map* (best-effort — NEVER raises).

    A write failure (read-only filesystem, missing dir permissions, full
    disk) must not crash the config-poll loop — it just means this poll's
    channel map won't be picked up by a future process restart, same class
    of "config ruim não derruba o site" discipline as the rest of ADR-0054/56.

    collection_subtype_map (eixo COLETA, migration 114): opcional — None vira
    {} no arquivo, mesma convenção do channel_map vazio.
    """
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "channel_map": channel_map,
            "config_version": config_version,
            "collection_subtype_map": collection_subtype_map or {},
            "fps_target_map": fps_target_map or {},
        })
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), prefix=".config_cache-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_path, target)
        finally:
            # tmp_path already gone if os.replace succeeded; ignore if so.
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    except OSError as exc:
        logger.warning("edge_config_cache_write_failed path=%s err=%s", path, exc)


def read_channel_map(path: str) -> ChannelMapCache | None:
    """Reads the cached channel map. Returns None if the file is missing,
    unreadable, or malformed — callers treat None as "cloud config not
    available yet", never as "cloud says zero cameras" (that case returns a
    real, empty-but-valid ChannelMapCache instead — see module docstring:
    the FILE existing and parsing is what makes the cloud authoritative, not
    whether the map inside it happens to be non-empty).

    collection_subtype_map (eixo COLETA) is read tolerantly: absent (older
    cache file, pre-114) or malformed -> {}, never None and never fails the
    whole read — an old cache file must stay valid for the channel_map it
    already carries.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    raw_map = data.get("channel_map")
    if not isinstance(raw_map, dict):
        return None
    try:
        channel_map = {str(k): int(v) for k, v in raw_map.items()}
    except (TypeError, ValueError):
        logger.warning("edge_config_cache_corrupt path=%s (canal não-inteiro)", path)
        return None

    config_version = str(data.get("config_version") or "")

    collection_subtype_map: dict[str, int] = {}
    raw_collection_map = data.get("collection_subtype_map")
    if isinstance(raw_collection_map, dict):
        for k, v in raw_collection_map.items():
            try:
                collection_subtype_map[str(k)] = int(v)
            except (TypeError, ValueError):
                logger.warning(
                    "edge_config_cache_collection_subtype_corrupt path=%s camera=%s value=%r",
                    path, k, v,
                )

    fps_target_map: dict[str, int] = {}
    raw_fps_map = data.get("fps_target_map")
    if isinstance(raw_fps_map, dict):
        for k, v in raw_fps_map.items():
            try:
                fps_target_map[str(k)] = int(v)
            except (TypeError, ValueError):
                logger.warning(
                    "edge_config_cache_fps_target_corrupt path=%s camera=%s value=%r",
                    path, k, v,
                )

    return ChannelMapCache(
        channel_map=channel_map,
        config_version=config_version,
        collection_subtype_map=collection_subtype_map,
        fps_target_map=fps_target_map,
    )

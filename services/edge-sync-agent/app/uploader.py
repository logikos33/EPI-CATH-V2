"""Batch uploader: drains SQLite buffer → cloud API.

Target: POST /api/v1/edge/events/ingest (services/api/app/api/v1/edge_events/
routes.py) — the ONE canonical ingest (device auth RS256, scope events:write;
decision recorded in docs/edge/INTEGRACAO_EDGE_F0F2_2026-07-19.md §1 —
`/api/v1/edge/detections` never existed in the API). Body is
{"events": [{event_type, camera_id, payload, occurred_at}, ...]}, at most
_MAX_BATCH items (the route answers 422 above that).

Idempotency: every batch carries a deterministic X-Batch-Id derived from the
sorted event IDs.  Resending the same events produces the identical header so
the cloud can deduplicate without storing extra state on the edge. The server
dedup key is X-Batch-Id + sha256(event JSON), so the serialized event must be
byte-stable across retries — `_to_ingest_event` therefore never includes
buffer bookkeeping (id/attempts) in what goes over the wire.

Backoff: 30 s → 60 s → 120 s → 300 s (capped), resets on first success.
"""

import hashlib
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from .sqlite_buffer import SQLiteBuffer

logger = logging.getLogger(__name__)

_BACKOFF_STEPS: tuple[float, ...] = (30.0, 60.0, 120.0, 300.0)
_DEFAULT_INTERVAL = 30.0
# Contract limit of POST /api/v1/edge/events/ingest ("events deve ser lista
# com máximo 500 itens" → 422). A bigger batch would be rejected forever.
_MAX_BATCH = 500


def _batch_id(ids: list[int]) -> str:
    """SHA-256 of sorted IDs → deterministic, collision-resistant batch key."""
    key = ",".join(str(i) for i in sorted(ids))
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _to_ingest_event(item: dict) -> dict:
    """Buffer row → one item of the /edge/events/ingest `events` list.

    Only the fields the route reads; `created_at` (epoch) becomes
    `occurred_at` ISO-8601 UTC (column is TIMESTAMPTZ). Nothing that changes
    between retries (attempts) goes in — see module docstring on dedup.
    """
    occurred = datetime.fromtimestamp(item["created_at"], tz=timezone.utc)
    payload = item.get("payload") or {}
    evento = {
        "event_type": item["event_type"],
        "camera_id": item["camera_id"],
        "payload": payload,
        "occurred_at": occurred.isoformat(),
    }
    # `evidence_r2_key` é campo de PRIMEIRO NÍVEL do evento na rota de ingest
    # (`evt.get("evidence_r2_key")` em edge_events/routes.py), mas o buffer só
    # tem colunas para type/camera/payload — o DetectionRelay a grava dentro
    # do payload e ela é promovida aqui. Sem esta promoção a chave chegava à
    # nuvem enterrada no JSONB e `alerts.evidence_r2_key` nascia NULL: o
    # alerta aparece na tela sem frame para o operador julgar.
    evidencia = payload.get("evidence_r2_key") if isinstance(payload, dict) else None
    if evidencia:
        evento["evidence_r2_key"] = evidencia
    return evento


class Uploader:
    """Uploads buffered events to the cloud in idempotent batches with backoff."""

    def __init__(
        self,
        buffer: SQLiteBuffer,
        http_client: Any,
        cloud_url: str,
        device_id: str,
        token: str,
        batch_size: int = 500,
        upload_interval_s: float = _DEFAULT_INTERVAL,
        backoff_steps: tuple[float, ...] = _BACKOFF_STEPS,
    ) -> None:
        self._buffer = buffer
        self._http = http_client
        self._url = f"{cloud_url.rstrip('/')}/api/v1/edge/events/ingest"
        self._device_id = device_id
        self._token = token
        self._batch_size = min(batch_size, _MAX_BATCH)
        self._interval = upload_interval_s
        self._backoff_steps = backoff_steps
        self._backoff_idx = 0

    # ── backoff helpers ──────────────────────────────────────────────────────

    def current_backoff(self) -> float:
        """Current wait duration (seconds) before the next retry."""
        return self._backoff_steps[min(self._backoff_idx, len(self._backoff_steps) - 1)]

    def _advance_backoff(self) -> None:
        self._backoff_idx = min(self._backoff_idx + 1, len(self._backoff_steps) - 1)

    def _reset_backoff(self) -> None:
        self._backoff_idx = 0

    # ── upload logic ─────────────────────────────────────────────────────────

    def _try_upload(self, batch: list[dict]) -> bool:
        """Attempt one batch upload. Returns True on success."""
        ids = [e["id"] for e in batch]
        bid = _batch_id(ids)
        try:
            resp = self._http.post(
                self._url,
                json={"events": [_to_ingest_event(e) for e in batch]},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "X-Batch-Id": bid,
                },
                timeout=30.0,
            )
            if resp.status_code == 200:
                self._buffer.mark_sent(ids)
                self._reset_backoff()
                logger.info("batch_uploaded batch_id=%s count=%d", bid, len(ids))
                return True
            logger.warning(
                "upload_rejected status=%d batch_id=%s", resp.status_code, bid
            )
        except Exception as exc:  # network / timeout
            logger.warning("upload_error %s batch_id=%s", exc, bid)

        self._buffer.mark_failed(ids)
        self._advance_backoff()
        return False

    # ── main loop ────────────────────────────────────────────────────────────

    def run(self, stop_event: threading.Event) -> None:
        """Drain buffer continuously until *stop_event* is set."""
        while not stop_event.is_set():
            batch = self._buffer.dequeue_batch(limit=self._batch_size)
            if not batch:
                stop_event.wait(timeout=self._interval)
                continue
            success = self._try_upload(batch)
            if not success:
                stop_event.wait(timeout=self.current_backoff())

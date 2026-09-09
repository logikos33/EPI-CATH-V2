"""Tests for Uploader: backoff, idempotency, event safety, stop_event."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from app.sqlite_buffer import SQLiteBuffer
from app.uploader import Uploader, _batch_id

# ── helpers ──────────────────────────────────────────────────────────────────

def _ok_response():
    r = MagicMock()
    r.status_code = 200
    return r


def _err_response(status: int = 503):
    r = MagicMock()
    r.status_code = status
    return r


def _make_uploader(buf, http, *, backoff_steps=(1.0, 2.0, 4.0, 8.0)):
    return Uploader(
        buffer=buf,
        http_client=http,
        cloud_url="http://cloud.test",
        device_id="dev-001",
        token="tok",
        batch_size=500,
        upload_interval_s=0.0,
        backoff_steps=backoff_steps,
    )


@pytest.fixture()
def buf(tmp_path):
    b = SQLiteBuffer(str(tmp_path / "up.db"))
    yield b
    b.close()


# ── batch_id determinism ─────────────────────────────────────────────────────

def test_batch_id_is_deterministic():
    assert _batch_id([3, 1, 2]) == _batch_id([1, 2, 3])


def test_batch_id_differs_for_different_ids():
    assert _batch_id([1, 2]) != _batch_id([1, 3])


def test_batch_id_is_32_hex_chars():
    bid = _batch_id([10, 20, 30])
    assert len(bid) == 32
    assert all(c in "0123456789abcdef" for c in bid)


# ── success path ─────────────────────────────────────────────────────────────

def test_successful_upload_marks_sent(buf):
    buf.enqueue("detection", "cam1", {"x": 1})
    http = MagicMock()
    http.post.return_value = _ok_response()
    up = _make_uploader(buf, http)

    result = up._try_upload(buf.dequeue_batch())

    assert result is True
    assert buf.count_unsent() == 0


def test_successful_upload_resets_backoff(buf):
    buf.enqueue("detection", "cam1", {})
    http = MagicMock()
    http.post.return_value = _err_response()
    up = _make_uploader(buf, http)
    up._try_upload(buf.dequeue_batch())  # fail → advance backoff
    assert up.current_backoff() == 2.0

    # make next call succeed
    buf.enqueue("detection", "cam1", {})
    http.post.return_value = _ok_response()
    up._try_upload(buf.dequeue_batch())
    assert up.current_backoff() == 1.0  # reset


def test_x_batch_id_header_is_sent(buf):
    buf.enqueue("detection", "cam1", {})
    http = MagicMock()
    http.post.return_value = _ok_response()
    up = _make_uploader(buf, http)

    batch = buf.dequeue_batch()
    up._try_upload(batch)

    _, kwargs = http.post.call_args
    assert "X-Batch-Id" in kwargs["headers"]
    expected = _batch_id([e["id"] for e in batch])
    assert kwargs["headers"]["X-Batch-Id"] == expected


# ── failure / backoff ────────────────────────────────────────────────────────

def test_failed_upload_keeps_events_in_buffer(buf):
    buf.enqueue("detection", "cam1", {})
    http = MagicMock()
    http.post.return_value = _err_response()
    up = _make_uploader(buf, http)

    result = up._try_upload(buf.dequeue_batch())

    assert result is False
    assert buf.count_unsent() == 1


def test_backoff_grows_on_consecutive_failures(buf):
    http = MagicMock()
    http.post.return_value = _err_response()
    up = _make_uploader(buf, http, backoff_steps=(1.0, 2.0, 4.0, 8.0))

    assert up.current_backoff() == 1.0

    for expected in (2.0, 4.0, 8.0, 8.0):  # capped at last step
        buf.enqueue("detection", "cam1", {})
        up._try_upload(buf.dequeue_batch())
        assert up.current_backoff() == expected


def test_network_error_treated_as_failure(buf):
    buf.enqueue("detection", "cam1", {})
    http = MagicMock()
    http.post.side_effect = OSError("connection refused")
    up = _make_uploader(buf, http)

    result = up._try_upload(buf.dequeue_batch())

    assert result is False
    assert buf.count_unsent() == 1
    assert up.current_backoff() == 2.0  # advanced


# ── idempotency on retry ─────────────────────────────────────────────────────

def test_retry_sends_identical_x_batch_id(buf):
    """Same events in buffer → same X-Batch-Id on every retry attempt."""
    buf.enqueue("detection", "cam1", {"n": 1})
    buf.enqueue("detection", "cam1", {"n": 2})

    http = MagicMock()
    http.post.side_effect = [_err_response(), _err_response(), _ok_response()]
    up = _make_uploader(buf, http)

    sent_ids: list[str] = []
    for _ in range(3):
        batch = buf.dequeue_batch()
        up._try_upload(batch)
        _, kwargs = http.post.call_args
        sent_ids.append(kwargs["headers"]["X-Batch-Id"])

    # All three attempts must carry the same batch id
    assert sent_ids[0] == sent_ids[1] == sent_ids[2]


def test_no_event_lost_after_multiple_failures(buf):
    n = 10
    for i in range(n):
        buf.enqueue("detection", "cam1", {"i": i})

    http = MagicMock()
    http.post.return_value = _err_response()
    up = _make_uploader(buf, http)

    for _ in range(3):
        up._try_upload(buf.dequeue_batch())

    assert buf.count_unsent() == n


# ── run() loop ───────────────────────────────────────────────────────────────

def test_run_exits_when_stop_event_set(buf):
    http = MagicMock()
    http.post.return_value = _ok_response()
    up = _make_uploader(buf, http)
    stop = threading.Event()
    stop.set()  # already set — loop must exit immediately

    up.run(stop)  # should return without blocking


def test_run_uploads_all_events_then_stops(buf):
    for i in range(3):
        buf.enqueue("detection", "cam1", {"i": i})

    http = MagicMock()
    http.post.return_value = _ok_response()
    up = _make_uploader(buf, http)
    stop = threading.Event()

    def _run():
        up.run(stop)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    # Wait until buffer is drained, then stop
    deadline = time.time() + 5.0
    while buf.count_unsent() > 0 and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(timeout=2.0)

    assert buf.count_unsent() == 0
    assert not t.is_alive()


# ── contrato POST /api/v1/edge/events/ingest ─────────────────────────────────
# A rota canônica da API (services/api/app/api/v1/edge_events/routes.py) é
# /api/v1/edge/events/ingest, device auth + escopo events:write, corpo
# {"events": [{event_type, camera_id, payload, occurred_at, ...}]} com no
# máximo 500 itens, idempotência por X-Batch-Id. /api/v1/edge/detections
# NUNCA existiu na API (decisão registrada em
# docs/edge/INTEGRACAO_EDGE_F0F2_2026-07-19.md §1).


def test_posts_to_events_ingest_route(buf):
    buf.enqueue("detection", "cam1", {})
    http = MagicMock()
    http.post.return_value = _ok_response()
    up = _make_uploader(buf, http)

    up._try_upload(buf.dequeue_batch())

    url = http.post.call_args.args[0]
    assert url == "http://cloud.test/api/v1/edge/events/ingest"


def test_payload_follows_ingest_contract(buf):
    """Corpo = {"events": [...]}, cada item com os campos que a rota lê
    (event_type/camera_id/payload/occurred_at) — e NÃO vaza colunas internas
    do buffer (id/attempts/created_at epoch)."""
    buf.enqueue("detection", "cam1", {"classes": ["no_helmet"]})
    http = MagicMock()
    http.post.return_value = _ok_response()
    up = _make_uploader(buf, http)

    batch = buf.dequeue_batch()
    up._try_upload(batch)

    body = http.post.call_args.kwargs["json"]
    assert set(body) == {"events"}, body  # rota lê body["events"]; resto é ignorado
    assert len(body["events"]) == 1
    evt = body["events"][0]
    assert evt["event_type"] == "detection"
    assert evt["camera_id"] == "cam1"
    assert evt["payload"] == {"classes": ["no_helmet"]}
    # occurred_at = created_at do buffer em ISO-8601 UTC (coluna TIMESTAMPTZ)
    assert evt["occurred_at"].endswith("+00:00")
    assert evt["occurred_at"].startswith(
        time.strftime("%Y-%m-%d", time.gmtime(batch[0]["created_at"]))
    )
    for interno in ("id", "attempts", "created_at"):
        assert interno not in evt


def test_payload_is_stable_across_retries(buf):
    """Dedup no servidor = X-Batch-Id + sha256(evento): o MESMO evento tem
    que serializar idêntico em toda tentativa, senão o reenvio duplica."""
    buf.enqueue("detection", "cam1", {"n": 1})
    http = MagicMock()
    http.post.side_effect = [_err_response(), _ok_response()]
    up = _make_uploader(buf, http)

    up._try_upload(buf.dequeue_batch())
    up._try_upload(buf.dequeue_batch())

    first, second = (c.kwargs["json"] for c in http.post.call_args_list)
    assert first == second


def test_batch_size_clamped_to_ingest_max(buf):
    """A rota recusa com 422 qualquer lote > 500 — um UPLOAD_BATCH_SIZE maior
    viraria lote-veneno permanente (422 eterno, buffer nunca drena)."""
    for i in range(501):
        buf.enqueue("detection", "cam1", {"i": i})
    http = MagicMock()
    http.post.return_value = _ok_response()
    up = Uploader(
        buffer=buf, http_client=http, cloud_url="http://cloud.test",
        device_id="dev-001", token="tok", batch_size=1000, upload_interval_s=0.0,
    )
    stop = threading.Event()
    t = threading.Thread(target=up.run, args=(stop,), daemon=True)
    t.start()
    deadline = time.time() + 5.0
    while buf.count_unsent() > 0 and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(timeout=2.0)

    assert buf.count_unsent() == 0
    sizes = [len(c.kwargs["json"]["events"]) for c in http.post.call_args_list]
    assert max(sizes) <= 500
    assert sum(sizes) == 501


@pytest.mark.parametrize("status", [401, 403, 422, 500, 503])
def test_non_200_keeps_batch_and_backs_off(buf, status):
    buf.enqueue("detection", "cam1", {})
    http = MagicMock()
    http.post.return_value = _err_response(status)
    up = _make_uploader(buf, http)

    assert up._try_upload(buf.dequeue_batch()) is False
    assert buf.count_unsent() == 1
    assert up.current_backoff() == 2.0


# ── evidência: chave promovida do payload para o nível do evento ───────────
#
# `/edge/events/ingest` lê `evt["evidence_r2_key"]`, mas o buffer só tem
# colunas type/camera/payload — o relay grava a chave DENTRO do payload. Sem a
# promoção ela chega enterrada no JSONB e `alerts.evidence_r2_key` nasce NULL.

def test_evidence_r2_key_sobe_no_nivel_do_evento(buf):
    from app.uploader import _to_ingest_event

    buf.enqueue("detection", "cam-1", {
        "camera_id": "cam-1",
        "detections": [{"class": "Sem óculos", "confidence": 0.9}],
        "has_violation": True,
        "evidence_r2_key": "evidence/cam-1/20260909T041800000000.jpg",
    })
    (linha,) = buf.dequeue_batch()

    evento = _to_ingest_event(linha)

    assert evento["evidence_r2_key"] == "evidence/cam-1/20260909T041800000000.jpg"


def test_evento_sem_evidencia_nao_ganha_o_campo(buf):
    """Ausente ≠ null explícito: o campo só existe quando há chave, e o
    evento serializado tem de continuar byte-estável entre reenvios (o dedup
    da nuvem é sha256 do JSON do evento)."""
    from app.uploader import _to_ingest_event

    buf.enqueue("detection", "cam-1", {"camera_id": "cam-1", "has_violation": True})
    (linha,) = buf.dequeue_batch()

    assert "evidence_r2_key" not in _to_ingest_event(linha)

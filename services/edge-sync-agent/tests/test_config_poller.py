"""Tests for ConfigPoller: apply config in memory, model manifest, stop_event."""

import json
import threading
import time
from unittest.mock import MagicMock

from app.config_poller import ConfigPoller, ModelManifest
from app.edge_config_cache import read_channel_map

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_poller(http, *, poll_interval_s=0.0):
    return ConfigPoller(
        http_client=http,
        cloud_url="http://cloud.test",
        device_id="dev-001",
        token="tok",
        poll_interval_s=poll_interval_s,
    )


def _http_ok(body: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp


def _http_err(status: int = 503):
    resp = MagicMock()
    resp.status_code = status
    return resp


# ── cameras / rules / scenario ───────────────────────────────────────────────

def test_applies_cameras_from_response():
    http = MagicMock()
    cameras = [{"id": "c1", "name": "Entry"}]
    http.get.return_value = _http_ok({"cameras": cameras})
    p = _make_poller(http)

    p._poll_once()

    assert p.get_cameras() == cameras


def test_applies_rules_from_response():
    http = MagicMock()
    rules = [{"id": "r1", "type": "epi"}]
    http.get.return_value = _http_ok({"rules": rules})
    p = _make_poller(http)

    p._poll_once()

    assert p.get_rules() == rules


def test_applies_scenario_from_response():
    http = MagicMock()
    scenario = {"module": "epi", "zones": []}
    http.get.return_value = _http_ok({"scenario": scenario})
    p = _make_poller(http)

    p._poll_once()

    assert p.get_scenario() == scenario


def test_partial_update_preserves_existing_cameras():
    """Second poll with only rules must not wipe previously received cameras."""
    http = MagicMock()
    cameras = [{"id": "c1"}]
    http.get.side_effect = [
        _http_ok({"cameras": cameras}),
        _http_ok({"rules": [{"id": "r99"}]}),
    ]
    p = _make_poller(http)

    p._poll_once()
    p._poll_once()

    assert p.get_cameras() == cameras  # cameras must survive second poll
    assert p.get_rules() == [{"id": "r99"}]


# ── model manifest ───────────────────────────────────────────────────────────

def test_sets_pending_model_when_sha256_differs():
    http = MagicMock()
    http.get.return_value = _http_ok(
        {"model": {"sha256": "abc123", "url": "https://r2.example/m.pt", "engine_type": "pt"}}
    )
    p = _make_poller(http)

    p._poll_once()

    m = p.get_model_manifest()
    assert isinstance(m, ModelManifest)
    assert m.sha256 == "abc123"
    assert m.engine_type == "pt"


def test_no_pending_model_when_sha256_is_same():
    http = MagicMock()
    sha = "deadbeef"
    http.get.return_value = _http_ok(
        {"model": {"sha256": sha, "url": "https://r2.example/m.pt", "engine_type": "pt"}}
    )
    p = _make_poller(http)
    p._state.current_model_sha256 = sha  # already on this model

    p._poll_once()

    assert p.get_model_manifest() is None


def test_no_pending_model_when_model_key_absent():
    http = MagicMock()
    http.get.return_value = _http_ok({"cameras": []})
    p = _make_poller(http)

    p._poll_once()

    assert p.get_model_manifest() is None


def test_no_pending_model_when_model_is_null():
    http = MagicMock()
    http.get.return_value = _http_ok({"model": None})
    p = _make_poller(http)

    p._poll_once()

    assert p.get_model_manifest() is None


# ── ack_model_applied ────────────────────────────────────────────────────────

def test_ack_model_applied_clears_pending_and_updates_sha256():
    http = MagicMock()
    http.get.return_value = _http_ok(
        {"model": {"sha256": "newsha", "url": "https://r2/m.pt", "engine_type": "pt"}}
    )
    p = _make_poller(http)
    p._poll_once()
    assert p.get_model_manifest() is not None

    p.ack_model_applied("newsha")

    assert p.get_model_manifest() is None
    assert p.get_current_model_sha256() == "newsha"


def test_ack_prevents_redundant_re_download_on_next_poll():
    """After ack, next poll with same sha256 should not set pending model again."""
    sha = "stablesha"
    http = MagicMock()
    http.get.return_value = _http_ok(
        {"model": {"sha256": sha, "url": "https://r2/m.pt", "engine_type": "pt"}}
    )
    p = _make_poller(http)

    p._poll_once()
    p.ack_model_applied(sha)

    # same sha in next poll
    p._poll_once()

    assert p.get_model_manifest() is None


# ── error resilience ─────────────────────────────────────────────────────────

def test_poll_error_preserves_existing_state():
    http = MagicMock()
    cameras = [{"id": "c1"}]
    http.get.side_effect = [
        _http_ok({"cameras": cameras}),
        _http_err(503),
    ]
    p = _make_poller(http)

    p._poll_once()  # success
    p._poll_once()  # failure — state must not be wiped

    assert p.get_cameras() == cameras


def test_network_exception_preserves_state():
    http = MagicMock()
    cameras = [{"id": "c2"}]
    http.get.side_effect = [
        _http_ok({"cameras": cameras}),
        OSError("timeout"),
    ]
    p = _make_poller(http)

    p._poll_once()
    result = p._poll_once()

    assert result is False
    assert p.get_cameras() == cameras


# ── request params ───────────────────────────────────────────────────────────

def test_poll_sends_device_id_and_current_sha256():
    http = MagicMock()
    http.get.return_value = _http_ok({})
    p = _make_poller(http)
    p._state.current_model_sha256 = "currentsha"

    p._poll_once()

    _, kwargs = http.get.call_args
    assert kwargs["params"]["device_id"] == "dev-001"
    assert kwargs["params"]["current_model_sha256"] == "currentsha"


# ── run() loop ───────────────────────────────────────────────────────────────

def test_run_exits_when_stop_event_set():
    http = MagicMock()
    http.get.return_value = _http_ok({})
    p = _make_poller(http)
    stop = threading.Event()
    stop.set()  # already set

    p.run(stop)  # must return without blocking


def test_run_applies_config_before_stopping():
    cameras = [{"id": "c3"}]
    http = MagicMock()
    http.get.return_value = _http_ok({"cameras": cameras})
    p = _make_poller(http)
    stop = threading.Event()

    def _run():
        p.run(stop)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    deadline = time.time() + 3.0
    while not p.get_cameras() and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(timeout=2.0)

    assert p.get_cameras() == cameras
    assert not t.is_alive()


# ── F1: versionamento por ETag / 304 ─────────────────────────────────────────

def _http_resp(status: int, body: dict | None = None, etag: str = ""):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"ETag": etag} if etag else {}
    resp.json.return_value = body or {}
    return resp


def test_stores_and_sends_if_none_match():
    """Após um 200 com ETag, o próximo poll manda If-None-Match com esse ETag."""
    http = MagicMock()
    http.get.return_value = _http_resp(200, {"cameras": [{"id": "c1"}]}, etag='"abc123"')
    p = _make_poller(http)

    p._poll_once()  # primeiro: 200, grava ETag
    p._poll_once()  # segundo: deve enviar If-None-Match

    second_headers = http.get.call_args_list[1].kwargs["headers"]
    assert second_headers.get("If-None-Match") == '"abc123"'


def test_304_preserves_last_good_config():
    """304 = nada mudou: mantém a última config boa e retorna True."""
    http = MagicMock()
    http.get.side_effect = [
        _http_resp(200, {"cameras": [{"id": "c1", "fps_target": 10}]}, etag='"v1"'),
        _http_resp(304, etag='"v1"'),
    ]
    p = _make_poller(http)

    assert p._poll_once() is True
    before = p.get_cameras()
    assert p._poll_once() is True  # 304
    assert p.get_cameras() == before  # inalterado


def test_first_poll_sends_no_if_none_match():
    http = MagicMock()
    http.get.return_value = _http_resp(200, {"cameras": []}, etag='"v1"')
    p = _make_poller(http)

    p._poll_once()

    first_headers = http.get.call_args_list[0].kwargs["headers"]
    assert "If-None-Match" not in first_headers


# ── ADR-0058: cache local do channel_map (camera_id -> canal do gravador) ───
#
# recorder_factory.py/collector_loop.py (processos/threads separados) leem
# esse arquivo no PRÓXIMO restart em vez de RECORDER_CHANNEL_MAP no .env —
# fail-before/pass-after: sem `cache_path`, nada é escrito (comportamento de
# TODOS os testes acima, que não passam cache_path).

def _make_poller_with_cache(http, cache_path, *, poll_interval_s=0.0):
    return ConfigPoller(
        http_client=http,
        cloud_url="http://cloud.test",
        device_id="dev-001",
        token="tok",
        poll_interval_s=poll_interval_s,
        cache_path=cache_path,
    )


def test_no_cache_path_never_writes_a_file(tmp_path):
    """Comportamento anterior a esta ADR: sem cache_path, nenhum arquivo é
    tocado — protege quem constrói um ConfigPoller sem essa opção."""
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200, {"cameras": [{"id": "c1", "channel": 1, "is_active": True}]}, etag='"v1"'
    )
    p = _make_poller(http)  # SEM cache_path

    p._poll_once()

    assert not (tmp_path / "config_cache.json").exists()
    assert read_channel_map(cache_path) is None


def test_successful_poll_writes_channel_map_cache(tmp_path):
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200,
        {
            "cameras": [
                {"id": "c1", "channel": 1, "is_active": True},
                {"id": "c2", "channel": 2, "is_active": True},
            ],
            "config_version": "abc123def456",
        },
        etag='"abc123def456"',
    )
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()

    cached = read_channel_map(cache_path)
    assert cached is not None
    assert cached.channel_map == {"c1": 1, "c2": 2}
    assert cached.config_version == "abc123def456"


def test_inactive_camera_excluded_from_channel_map_cache(tmp_path):
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200,
        {
            "cameras": [
                {"id": "c1", "channel": 1, "is_active": True},
                {"id": "c2", "channel": 2, "is_active": False},
            ],
        },
        etag='"v1"',
    )
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()

    cached = read_channel_map(cache_path)
    assert cached.channel_map == {"c1": 1}


def test_cache_never_carries_credentials(tmp_path):
    """ADR-0057: mesmo se um payload malicioso/errado incluísse username/
    password nas câmeras, o cache só grava id->channel — nunca sobe o
    dict de câmera inteiro pro disco."""
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200,
        {
            "cameras": [
                {
                    "id": "c1",
                    "channel": 1,
                    "is_active": True,
                    "username": "admin",
                    "password_encrypted": "should-never-be-cached",
                }
            ],
        },
        etag='"v1"',
    )
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()

    raw = json.loads((tmp_path / "config_cache.json").read_text())
    dumped = json.dumps(raw)
    assert "admin" not in dumped
    assert "should-never-be-cached" not in dumped
    assert raw["channel_map"] == {"c1": 1}


def test_304_does_not_rewrite_cache(tmp_path):
    """304 significa 'nada mudou' — não há data['cameras'] pra reaplicar,
    então o cache do poll anterior permanece intocado (não é reescrito, mas
    também não é apagado)."""
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.side_effect = [
        _http_resp(200, {"cameras": [{"id": "c1", "channel": 1}]}, etag='"v1"'),
        _http_resp(304, etag='"v1"'),
    ]
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()
    mtime_after_first = (tmp_path / "config_cache.json").stat().st_mtime_ns
    p._poll_once()  # 304
    mtime_after_second = (tmp_path / "config_cache.json").stat().st_mtime_ns

    assert mtime_after_first == mtime_after_second
    assert read_channel_map(cache_path).channel_map == {"c1": 1}


def test_camera_without_channel_key_is_skipped(tmp_path):
    """Payload de agente/cloud antigo sem 'channel' — não quebra, só não
    entra no mapa (ADR-0017: sem valor inventado)."""
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200, {"cameras": [{"id": "c1"}, {"id": "c2", "channel": 2}]}, etag='"v1"'
    )
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()

    assert read_channel_map(cache_path).channel_map == {"c2": 2}


# ── migration 114: eixo COLETA — collection_subtype_map no MESMO cache ──────
#
# camera_id -> collection_subtype (0=principal, 1=substream), gravado no
# MESMO arquivo/poll que já grava o channel_map — sem cache/rota nova.

def test_writes_collection_subtype_map_alongside_channel_map(tmp_path):
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200,
        {
            "cameras": [
                {"id": "c1", "channel": 1, "is_active": True, "collection_subtype": 0},
                {"id": "c2", "channel": 2, "is_active": True, "collection_subtype": 1},
            ],
            "config_version": "v1",
        },
        etag='"v1"',
    )
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()

    cached = read_channel_map(cache_path)
    assert cached.channel_map == {"c1": 1, "c2": 2}
    assert cached.collection_subtype_map == {"c1": 0, "c2": 1}


def test_collection_subtype_map_excludes_inactive_or_channelless_cameras(tmp_path):
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200,
        {
            "cameras": [
                {"id": "c1", "channel": 1, "is_active": True, "collection_subtype": 1},
                {"id": "c2", "channel": 2, "is_active": False, "collection_subtype": 1},
                {"id": "c3", "is_active": True, "collection_subtype": 1},  # sem channel
            ],
        },
        etag='"v1"',
    )
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()

    cached = read_channel_map(cache_path)
    assert cached.collection_subtype_map == {"c1": 1}


def test_camera_without_collection_subtype_key_is_skipped_from_that_map(tmp_path):
    """Câmera sem collection_subtype no payload (default do backend não veio,
    ou é payload antigo) — não entra no mapa, mas o channel_map continua
    intacto (ADR-0017: sem valor inventado)."""
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200,
        {"cameras": [{"id": "c1", "channel": 1, "is_active": True}]},
        etag='"v1"',
    )
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()

    cached = read_channel_map(cache_path)
    assert cached.channel_map == {"c1": 1}
    assert cached.collection_subtype_map == {}


def test_old_cache_file_without_collection_subtype_map_reads_as_empty(tmp_path):
    """Arquivo de cache gravado ANTES desta mudança (sem a chave
    collection_subtype_map) continua válido — {} e channel_map intacto."""
    cache_path = tmp_path / "config_cache.json"
    cache_path.write_text(json.dumps({"channel_map": {"c1": 3}, "config_version": "old"}))

    cached = read_channel_map(str(cache_path))

    assert cached is not None
    assert cached.channel_map == {"c1": 3}
    assert cached.collection_subtype_map == {}


def test_cache_with_malformed_collection_subtype_map_falls_back_to_empty(tmp_path):
    """collection_subtype_map malformado (não é dict) não invalida o arquivo
    inteiro — só aquele campo vira {}, channel_map continua válido."""
    cache_path = tmp_path / "config_cache.json"
    cache_path.write_text(json.dumps({
        "channel_map": {"c1": 1},
        "config_version": "v1",
        "collection_subtype_map": "not-a-dict",
    }))

    cached = read_channel_map(str(cache_path))

    assert cached is not None
    assert cached.channel_map == {"c1": 1}
    assert cached.collection_subtype_map == {}


def test_cache_with_non_integer_collection_subtype_value_skips_only_that_entry(tmp_path):
    cache_path = tmp_path / "config_cache.json"
    cache_path.write_text(json.dumps({
        "channel_map": {"c1": 1, "c2": 2},
        "config_version": "v1",
        "collection_subtype_map": {"c1": 0, "c2": "not-an-int"},
    }))

    cached = read_channel_map(str(cache_path))

    assert cached is not None
    assert cached.collection_subtype_map == {"c1": 0}


# ── eixo OPERAÇÃO: fps_target_map no MESMO cache ────────────────────────────
#
# O elo estava ROMPIDO: `fps_target` viajava no config/poll (a rota já o
# seleciona em `list_for_site_config`) e morria ali — o cache não o persistia e
# o gerador do DeepStream só conhecia um `interval` GLOBAL. Resultado prático:
# o valor que o dono escolhe por câmera na tela não mudava nada no box.

def test_writes_fps_target_map_alongside_channel_map(tmp_path):
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200,
        {
            "cameras": [
                {"id": "c1", "channel": 1, "is_active": True, "fps_target": 5},
                {"id": "c2", "channel": 2, "is_active": True, "fps_target": 1},
            ],
            "config_version": "v1",
        },
        etag='"v1"',
    )
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()

    cached = read_channel_map(cache_path)
    assert cached.channel_map == {"c1": 1, "c2": 2}
    # Sem esta linha, "5 FPS nesta câmera" nunca sai da nuvem.
    assert cached.fps_target_map == {"c1": 5, "c2": 1}


def test_fps_target_map_excludes_inactive_or_channelless_cameras(tmp_path):
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200,
        {
            "cameras": [
                {"id": "c1", "channel": 1, "is_active": True, "fps_target": 5},
                {"id": "c2", "channel": 2, "is_active": False, "fps_target": 5},
                {"id": "c3", "is_active": True, "fps_target": 5},  # sem channel
            ],
        },
        etag='"v1"',
    )
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()

    assert read_channel_map(cache_path).fps_target_map == {"c1": 5}


def test_fps_target_ausente_nao_vira_default_no_cache(tmp_path):
    """Ausência não vira 0 nem taxa cheia AQUI — quem lê decide.

    Importa porque o gerador do DeepStream RECUSA gerar sem valor, em vez de
    assumir taxa cheia: 17 fontes a 30 fps saturam a GPU do Orin e ninguém
    descobre isso olhando o config.
    """
    cache_path = str(tmp_path / "config_cache.json")
    http = MagicMock()
    http.get.return_value = _http_resp(
        200,
        {"cameras": [{"id": "c1", "channel": 1, "is_active": True}]},
        etag='"v1"',
    )
    p = _make_poller_with_cache(http, cache_path)

    p._poll_once()

    cached = read_channel_map(cache_path)
    assert cached.channel_map == {"c1": 1}
    assert cached.fps_target_map == {}


def test_cache_antigo_sem_fps_target_map_continua_valido(tmp_path):
    """Cache gravado antes desta mudança não pode virar 'config indisponível'."""
    from app.edge_config_cache import read_channel_map as ler
    p = tmp_path / "antigo.json"
    p.write_text('{"channel_map": {"c1": 3}, "config_version": "v9"}', encoding="utf-8")

    cached = ler(str(p))

    assert cached is not None and cached.channel_map == {"c1": 3}
    assert cached.fps_target_map == {}

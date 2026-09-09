"""Tests for RtspTimestampRecorderClient: the no-search-API RTSP fallback
used for Intelbras/Dahua/generic gravadores (RVB's actual hardware,
CLAUDE.md). No timeline index exists in this protocol — tests assert the
documented "one synthetic event per window" behavior, not a real search.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.recorder_client import RecorderClient, RecorderError
from app.rtsp_timestamp_recorder_client import RtspTimestampRecorderClient

_NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
_CAMERA_ID = "cam-1"
_CHANNEL_MAP = {_CAMERA_ID: 2}


def _make_client() -> RtspTimestampRecorderClient:
    return RtspTimestampRecorderClient(
        host="10.0.0.9",
        port=554,
        username="admin",
        password="s3cr3t",
        channel_map=_CHANNEL_MAP,
    )


def test_satisfies_recorder_client_protocol():
    assert isinstance(_make_client(), RecorderClient)


def test_list_events_returns_one_synthetic_event_covering_window():
    client = _make_client()
    start = _NOW - timedelta(minutes=10)
    events = client.list_events(_CAMERA_ID, start, _NOW)

    assert len(events) == 1
    assert events[0].camera_id == _CAMERA_ID
    assert events[0].started_at == start
    assert events[0].ended_at == _NOW
    assert events[0].description is not None  # documents the "no real index" limitation


def test_list_events_unmapped_camera_raises():
    client = _make_client()
    with pytest.raises(RecorderError):
        client.list_events("unmapped", _NOW - timedelta(minutes=10), _NOW)


def test_health_reports_reachable_when_port_accepts_connection(monkeypatch):
    class _FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.socket.create_connection",
        lambda addr, timeout: _FakeSocket(),
    )
    client = _make_client()
    health = client.health()
    assert health.reachable is True


def test_health_reports_unreachable_on_connection_error(monkeypatch):
    def _raise(addr, timeout):
        raise OSError("refused")

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.socket.create_connection", _raise
    )
    client = _make_client()
    health = client.health()
    assert health.reachable is False


def test_stream_clip_builds_dahua_style_timestamp_url(monkeypatch):
    client = _make_client()
    start = datetime(2026, 7, 15, 11, 30, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 15, 11, 30, 30, tzinfo=timezone.utc)

    captured = {}

    def _fake_stream_rtsp_clip(url, duration_seconds):
        captured["url"] = url
        captured["duration"] = duration_seconds
        yield b"clip-bytes"

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.stream_rtsp_clip", _fake_stream_rtsp_clip
    )

    chunks = list(client.stream_clip(_CAMERA_ID, start, end))

    assert chunks == [b"clip-bytes"]
    assert captured["duration"] == 30.0
    assert "starttime=2026_07_15_11_30_00" in captured["url"]
    assert "endtime=2026_07_15_11_30_30" in captured["url"]
    assert "channel=2" in captured["url"]


def test_stream_clip_unmapped_camera_raises_before_building_url():
    client = _make_client()
    with pytest.raises(RecorderError):
        list(client.stream_clip("unmapped", _NOW - timedelta(seconds=10), _NOW))


def test_capture_frame_builds_realmonitor_live_url(monkeypatch):
    client = _make_client()
    captured = {}

    def _fake_capture_still_frame(url):
        captured["url"] = url
        return b"jpeg-bytes"

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.capture_still_frame", _fake_capture_still_frame
    )

    result = client.capture_frame(_CAMERA_ID)

    assert result == b"jpeg-bytes"
    assert "realmonitor" in captured["url"]
    assert "channel=2" in captured["url"]
    assert "subtype=0" in captured["url"]


def test_capture_frame_unmapped_camera_raises_before_building_url():
    client = _make_client()
    with pytest.raises(RecorderError):
        client.capture_frame("unmapped")


# ── get_snapshot (Bloco A — no ONVIF equivalent here; live-frame grab with
#    its own channel resolution: channel_map first, hint fallback) ──────────


def test_get_snapshot_delegates_to_capture_frame(monkeypatch):
    client = _make_client()

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.capture_still_frame", lambda url: b"snap-bytes"
    )

    assert client.get_snapshot(_CAMERA_ID) == b"snap-bytes"


def test_get_snapshot_draft_out_of_map_uses_hint_channel(monkeypatch):
    """Bug de campo (RVB, canal 9 draft): draft nunca entra no channel_map
    (config_poller só alimenta câmeras is_active) — o hint do payload do
    comando é o que permite fotografá-la sem ativar."""
    client = _make_client()
    captured = {}

    def _fake_capture_still_frame(url):
        captured["url"] = url
        return b"draft-bytes"

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.capture_still_frame", _fake_capture_still_frame
    )

    result = client.get_snapshot("cam-draft", channel_hint=9)

    assert result == b"draft-bytes"
    assert "channel=9" in captured["url"]


def test_get_snapshot_map_wins_over_divergent_hint(monkeypatch):
    """Câmera ATIVA (no mapa) com hint divergente vindo de um comando antigo
    na fila: o channel_map (fonte viva da box) vence — nunca dessincroniza."""
    client = _make_client()  # _CHANNEL_MAP: {_CAMERA_ID: 2}
    captured = {}

    def _fake_capture_still_frame(url):
        captured["url"] = url
        return b"jpeg"

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.capture_still_frame", _fake_capture_still_frame
    )

    client.get_snapshot(_CAMERA_ID, channel_hint=55)

    assert "channel=2" in captured["url"]
    assert "channel=55" not in captured["url"]


def test_get_snapshot_out_of_map_without_hint_raises_specific_channel_error():
    """Sem mapa E sem hint: erro ESPECÍFICO (RecorderChannelError), nunca o
    genérico que o executor classificaria como 'sem sinal no canal'."""
    from app.recorder_client import RecorderChannelError

    client = _make_client()
    with pytest.raises(RecorderChannelError) as excinfo:
        client.get_snapshot("unmapped")
    assert "fora do channel_map" in str(excinfo.value)


def test_get_snapshot_reclassifies_401_message_as_auth_error(monkeypatch):
    """ffmpeg/RTSP has no structured status code — an auth failure only shows
    up as free text (redacted stderr tail). Reclassified so the snapshot
    anti-lockout breaker still trips on it."""
    from app.recorder_client import RecorderAuthError, RecorderError

    def _raise_401(url):
        raise RecorderError("ffmpeg não produziu bytes para o frame: 401 Unauthorized")

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.capture_still_frame", _raise_401
    )

    with pytest.raises(RecorderAuthError):
        client = _make_client()
        client.get_snapshot(_CAMERA_ID)


def test_get_snapshot_non_auth_failure_stays_generic_recorder_error(monkeypatch):
    from app.recorder_client import RecorderAuthError, RecorderError

    def _raise_timeout(url):
        raise RecorderError("ffmpeg não respondeu em 10.0s ao capturar frame")

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.capture_still_frame", _raise_timeout
    )

    client = _make_client()
    with pytest.raises(RecorderError) as excinfo:
        client.get_snapshot(_CAMERA_ID)
    assert not isinstance(excinfo.value, RecorderAuthError)


def test_get_snapshot_unmapped_camera_raises_before_building_url():
    client = _make_client()
    with pytest.raises(RecorderError):
        client.get_snapshot("unmapped")


def test_capture_frame_defaults_to_main_stream_subtype_0(monkeypatch):
    client = _make_client()
    captured = {}

    def _fake_capture_still_frame(url):
        captured["url"] = url
        return b"jpeg-bytes"

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.capture_still_frame", _fake_capture_still_frame
    )
    client.capture_frame(_CAMERA_ID)
    assert "subtype=0" in captured["url"]


def test_capture_frame_uses_configured_sub_stream(monkeypatch):
    client = RtspTimestampRecorderClient(
        host="10.0.0.9",
        port=554,
        username="admin",
        password="s3cr3t",
        channel_map=_CHANNEL_MAP,
        stream_subtype=1,
    )
    captured = {}

    def _fake_capture_still_frame(url):
        captured["url"] = url
        return b"jpeg-bytes"

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.capture_still_frame", _fake_capture_still_frame
    )
    client.capture_frame(_CAMERA_ID)
    assert "subtype=1" in captured["url"]


# ── migration 114: eixo COLETA — collection_subtype_overrides per camera ────
#
# capture_frame() é o único consumidor do override; live view continua
# global via _build_live_url(channel) (1 argumento, inalterado).

def test_capture_frame_uses_per_camera_collection_override(monkeypatch):
    """Câmera COM override no eixo COLETA usa o subtype dela, mesmo com o
    stream_subtype global (eixo OPERAÇÃO) apontando para outro valor."""
    client = RtspTimestampRecorderClient(
        host="10.0.0.9",
        port=554,
        username="admin",
        password="s3cr3t",
        channel_map=_CHANNEL_MAP,
        stream_subtype=0,
        collection_subtype_overrides={_CAMERA_ID: 1},
    )
    captured = {}

    def _fake_capture_still_frame(url):
        captured["url"] = url
        return b"jpeg-bytes"

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.capture_still_frame", _fake_capture_still_frame
    )
    client.capture_frame(_CAMERA_ID)
    assert "subtype=1" in captured["url"]


def test_capture_frame_without_override_falls_back_to_global_stream_subtype(monkeypatch):
    """Câmera SEM override usa self._stream_subtype (global) — comportamento
    pré-114 preservado."""
    client = RtspTimestampRecorderClient(
        host="10.0.0.9",
        port=554,
        username="admin",
        password="s3cr3t",
        channel_map=_CHANNEL_MAP,
        stream_subtype=1,
        collection_subtype_overrides={"other-camera": 0},
    )
    captured = {}

    def _fake_capture_still_frame(url):
        captured["url"] = url
        return b"jpeg-bytes"

    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.capture_still_frame", _fake_capture_still_frame
    )
    client.capture_frame(_CAMERA_ID)
    assert "subtype=1" in captured["url"]


def test_build_live_url_single_arg_uses_global_stream_subtype(monkeypatch):
    """live_view_loop._resolve_camera_urls chama _build_live_url(channel) com
    UM argumento — deve continuar usando self._stream_subtype (global),
    nunca um override de coleta, mesmo que a câmera tenha um."""
    client = RtspTimestampRecorderClient(
        host="10.0.0.9",
        port=554,
        username="admin",
        password="s3cr3t",
        channel_map=_CHANNEL_MAP,
        stream_subtype=0,
        collection_subtype_overrides={_CAMERA_ID: 1},
    )
    url = client._build_live_url(2)
    assert "subtype=0" in url


def test_build_live_url_explicit_subtype_overrides_global():
    client = RtspTimestampRecorderClient(
        host="10.0.0.9",
        port=554,
        username="admin",
        password="s3cr3t",
        channel_map=_CHANNEL_MAP,
        stream_subtype=0,
    )
    url = client._build_live_url(2, 1)
    assert "subtype=1" in url


def test_collection_subtype_overrides_defaults_to_empty_dict():
    client = _make_client()
    assert client._collection_subtype_overrides == {}


# ── evidência ancorada no INSTANTE (capture_frame_at) ───────────────────────
#
# Contrato confirmado contra o gravador real da RVB (Intelbras iNVD 3032,
# firmware 4.001.00IB000.1.T, 09/09/2026) — não por documentação:
#   · RTSP DESCRIBE de /cam/playback com starttime/endtime -> 200 OK com
#     `a=range:npt=0-5.000000` (a âncora é aceita); canal inexistente -> 403,
#     janela sem gravação -> 404, ou seja o aparelho distingue os dois;
#   · loadfile.cgi da MESMA janela -> HTTP 200 com MP4, e o JPEG extraído
#     saiu byte a byte igual ao do caminho RTSP (778546 B), em 0,79s contra
#     3,44s;
#   · o Δ foi conferido no OSD da própria câmera: pedido -45s -> quadro
#     09:21:53 (exato); pedido -10s -> 09:22:29 (+1s de alinhamento de I-frame).
#
# O RELÓGIO é o detalhe que faz tudo funcionar ou falhar 100% das vezes: o
# gravador estava 333s ATRASADO em relação ao box.

class _RespostaFalsa:
    def __init__(self, corpo: bytes) -> None:
        self._corpo = corpo

    def read(self, _n: int = -1) -> bytes:
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


#: Relógio de parede fixo do box nos testes — o desvio do gravador é medido
#: contra ele, e sem fixá-lo a diferença de sub-segundo entre a construção da
#: resposta falsa e a medição já move a janela em 1s.
_AGORA_BOX = datetime(2026, 9, 9, 9, 30, 0)


class _OpenerFalso:
    """Gravador de mentira: relógio atrasado *desvio_s* e um MP4 por janela."""

    def __init__(self, desvio_s: float = 333.0, corpo: bytes = b"mp4-da-janela") -> None:
        self.desvio_s = desvio_s
        self.corpo = corpo
        self.urls: list[str] = []
        self.erro = None

    def open(self, url, timeout=None):  # noqa: A003 — assinatura do urllib
        self.urls.append(url)
        if self.erro is not None:
            raise self.erro
        if "global.cgi" in url:
            hora = _AGORA_BOX - timedelta(seconds=self.desvio_s)
            return _RespostaFalsa(
                f"result={hora.strftime('%Y-%m-%d %H:%M:%S')}\r\n".encode()
            )
        return _RespostaFalsa(self.corpo)


def _cliente_com_cgi(opener: _OpenerFalso) -> RtspTimestampRecorderClient:
    return RtspTimestampRecorderClient(
        host="10.0.0.9", port=554, username="admin", password="s3cr3t",
        channel_map=_CHANNEL_MAP, url_opener=opener, clock=lambda: 0.0,
        agora=lambda: _AGORA_BOX,
    )


def test_capture_frame_at_corrige_o_desvio_do_relogio_do_gravador(monkeypatch):
    """O TESTE QUE PRENDE O DEFEITO REAL: ancorar pelo relógio do BOX pediria
    um instante 5,5 min no FUTURO do gravador -> 404 em todos os eventos, e a
    evidência cairia sempre para o ao vivo sem ninguém perceber."""
    vistos = []
    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.extract_still_frame",
        lambda b: vistos.append(b) or b"jpeg",
    )
    opener = _OpenerFalso(desvio_s=333.0)
    cliente = _cliente_com_cgi(opener)

    instante = _AGORA_BOX.astimezone() - timedelta(seconds=60)
    assert cliente.capture_frame_at(_CAMERA_ID, instante) == b"jpeg"
    assert vistos == [b"mp4-da-janela"]

    # 09:30:00 (box) - 60s de evento - 333s de desvio = 09:23:27 no gravador
    esperado = (_AGORA_BOX - timedelta(seconds=60 + 333)).strftime(
        "%Y-%m-%d %H%%3A%M%%3A%S"
    )
    assert esperado.startswith("2026-09-09 09%3A23%3A27")
    (url,) = [u for u in opener.urls if "loadfile.cgi" in u]
    assert f"startTime={esperado.replace(' ', '%20')}" in url, url
    assert f"channel={_CHANNEL_MAP[_CAMERA_ID]}" in url


def test_relogio_do_gravador_e_cacheado(monkeypatch):
    """0,1 requisição/min: o relógio não é perguntado a cada evento — o teto de
    tráfego contra o gravador é o que manda neste desenho."""
    monkeypatch.setattr(
        "app.rtsp_timestamp_recorder_client.extract_still_frame", lambda b: b"jpeg"
    )
    opener = _OpenerFalso()
    cliente = _cliente_com_cgi(opener)
    for _ in range(3):
        cliente.capture_frame_at(_CAMERA_ID, datetime.now(timezone.utc))

    assert len([u for u in opener.urls if "global.cgi" in u]) == 1
    assert len([u for u in opener.urls if "loadfile.cgi" in u]) == 3


def test_cgi_401_vira_auth_error_para_fechar_o_breaker():
    """Credencial rejeitada tem de parar TODO acesso ao gravador (anti-lockout,
    CLAUDE.md), não só este caminho."""
    import urllib.error

    from app.recorder_client import RecorderAuthError

    opener = _OpenerFalso()
    opener.erro = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)
    with pytest.raises(RecorderAuthError):
        _cliente_com_cgi(opener).capture_frame_at(_CAMERA_ID, datetime.now(timezone.utc))


def test_cgi_desligado_nao_ancora():
    """RECORDER_CGI_PORT=0 é o rollback sem OTA: volta tudo ao ao vivo."""
    cliente = RtspTimestampRecorderClient(
        host="10.0.0.9", port=554, username="admin", password="s3cr3t",
        channel_map=_CHANNEL_MAP, cgi_port=0,
    )
    with pytest.raises(RecorderError, match="desligado"):
        cliente.capture_frame_at(_CAMERA_ID, datetime.now(timezone.utc))


def test_capture_frame_at_nunca_leva_a_senha_na_url():
    """A senha vive no gerenciador do urllib, não na URL — diferente do caminho
    RTSP, onde ela viaja no argv do ffmpeg e aparece no `ps`."""
    opener = _OpenerFalso()
    cliente = _cliente_com_cgi(opener)
    try:
        cliente.capture_frame_at(_CAMERA_ID, datetime.now(timezone.utc))
    except RecorderError:
        pass
    assert opener.urls, "nenhuma requisição feita"
    assert all("s3cr3t" not in u and "admin" not in u for u in opener.urls)

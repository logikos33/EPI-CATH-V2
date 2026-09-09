"""RtspTimestampRecorderClient — RTSP-with-timestamp fallback RecorderClient.

Ported from services/api/app/infrastructure/nvr/generic_rtsp_client.py
(cascata da ADR-0034: ONVIF → SDK do fabricante → este fallback). Used
whenever the site's gravador has no dedicated search API — this is the real
protocol RVB's Intelbras NVR speaks (CLAUDE.md: RVB usa Intelbras). Same
Dahua-dialect timestamp format the monolith already fixed once
(`YYYY_MM_DD_HH_MM_SS`, NOT ISO 8601 — Intelbras licenses the Dahua
platform on several NVR lines, so this dialect *tends* to work, but is not
guaranteed for every Intelbras firmware).

LIMITATION (do not pretend otherwise): there is no real timeline index
here. `list_events` always returns exactly one synthetic RecorderEvent
covering the requested [start, end) window — the RTSP URL itself seeks to
the timestamp; if there is no actual recording in that window, playback
fails when `stream_clip` is called, not here. This mirrors the monolith's
documented behavior/limitation for this same fallback (ADR-0034 accepted
this trade-off already). If RVB's actual Intelbras firmware doesn't speak
this Dahua-OEM dialect, the real fix is Intelbras's CGI-based HTTP API
(`/cgi-bin/playBack.cgi?action=getStream&channel=...&startTime=...`) — not
implemented here because there is no source to confirm its exact contract
without hardware to validate against (documented as a known gap, not
invented).

LACUNA ACIMA: FECHADA para o caminho de EVIDÊNCIA (09/09/2026). O contrato
do CGI foi confirmado contra o gravador real da RVB (Intelbras iNVD 3032,
firmware 4.001.00IB000.1.T) e vive em `capture_frame_at`, que devolve o
quadro DO INSTANTE da detecção em vez do "agora". O endpoint que este
comentário chutava (`playBack.cgi`) responde 501 Not Implemented neste
firmware; o que funciona é `loadfile.cgi?action=startLoad`. `stream_clip`
(clipe de evidência) segue no caminho RTSP — não foi tocado.
"""

from __future__ import annotations

import logging
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from .recorder_client import (
    RecorderAuthError,
    RecorderError,
    RecorderEvent,
    RecorderHealth,
    is_auth_failure_message,
    resolve_snapshot_channel,
)
from .rtsp_clip_stream import stream_rtsp_clip
from .rtsp_frame_capture import capture_still_frame, extract_still_frame
from .rtsp_validator import RTSPUrlValidator

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 5.0

#: Porta do CGI HTTP do gravador (dialeto Dahua/Intelbras). 0 DESLIGA a
#: evidência ancorada no instante — toda captura volta a ser "o agora".
_CGI_PORT_PADRAO = 80
_CGI_TIMEOUT_S = 12.0
#: Teto do trecho baixado. Medido no iNVD 3032: ~640KB por 3s de H.265
#: 1280x720. O teto existe porque uma janela mal formada faria o gravador
#: despejar o arquivo inteiro (>100MB) dentro do loop do relay.
_CGI_MAX_BYTES = 16 * 1024 * 1024
#: Janela pedida ao gravador. 3s cobre um GOP inteiro com folga; o primeiro
#: quadro decodificável é o que volta. Medido na RVB em 09/09 contra o OSD
#: da própria câmera: pedido -45s -> quadro 09:21:53 (exato); pedido -10s ->
#: quadro 09:22:29 (+1s, alinhamento de I-frame).
_JANELA_S = 3.0
#: O relógio do gravador é medido, não presumido: o iNVD 3032 da RVB estava
#: 333s ATRASADO em relação ao box em 09/09/2026 (medição repetida 5x em 5
#: minutos, desvio estável entre 333 e 334s). Ancorar pelo relógio do BOX
#: pediria um instante 5,5 min no FUTURO do gravador -> 404 em 100% dos
#: casos. Recalculado a cada TTL porque relógio de aparelho anda sozinho.
_TTL_RELOGIO_S = 600.0


def _fmt(dt: datetime) -> str:
    """Dahua/Intelbras timestamp format (e.g. 2012_09_15_12_37_05) — NOT ISO 8601."""
    return dt.strftime("%Y_%m_%d_%H_%M_%S")


class RtspTimestampRecorderClient:
    """RecorderClient fallback: RTSP with starttime/endtime, no search API.

    camera_id → channel resolution and connection details are the same
    channel_map convention used by OnvifRecorderClient (see recorder_factory.py).

    Two independent quality axes live here (migration 114):
      - OPERAÇÃO: `stream_subtype` (RECORDER_STREAM_SUBTYPE, global) — used by
        the LIVE VIEW loop via `_build_live_url(channel)` (no override arg),
        never per-camera.
      - COLETA: `collection_subtype_overrides` (camera_id -> 0/1, from the
        cloud-polled cache) — used ONLY by `capture_frame()` (training-frame
        collection), per camera, falling back to `stream_subtype` when a
        camera has no override.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        channel_map: dict[str, int],
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        stream_subtype: int = 0,
        collection_subtype_overrides: dict[str, int] | None = None,
        cgi_port: int = _CGI_PORT_PADRAO,
        url_opener: "Any | None" = None,
        clock: "Any | None" = None,
        agora: "Any | None" = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._channel_map = dict(channel_map)
        self._timeout = timeout
        self._stream_subtype = stream_subtype
        # Eixo COLETA (migration 114): camera_id -> collection_subtype.
        # Independente de self._stream_subtype (eixo OPERAÇÃO, usado pelo
        # live view). Câmera ausente daqui usa self._stream_subtype.
        self._collection_subtype_overrides = dict(collection_subtype_overrides or {})
        # Eixo EVIDÊNCIA (CGI HTTP): independente dos eixos OPERAÇÃO/COLETA
        # acima, que falam RTSP. cgi_port=0 desliga e todo mundo volta ao vivo.
        self._cgi_port = cgi_port
        self._url_opener = url_opener or self._montar_opener(host, cgi_port, username, password)
        # Dois relógios, de propósito: `clock` é monotônico (TTL do cache, imune
        # a salto de relógio) e `agora` é o de PAREDE do box, que é a régua
        # contra a qual o desvio do gravador é medido.
        self._clock = clock or time.monotonic
        self._agora = agora or datetime.now
        # (medido_em, desvio_s). None = ainda não medido / última medição falhou.
        self._relogio: "tuple[float, float] | None" = None

    # ── CGI HTTP do gravador (evidência ancorada no instante) ────────────────

    @staticmethod
    def _montar_opener(host: str, cgi_port: int, username: str, password: str) -> "Any | None":
        """Opener urllib com digest. A senha fica no gerenciador em memória e
        NUNCA na URL — diferente do caminho RTSP, onde ela viaja no argv do
        ffmpeg e aparece no `ps`. Esse é um motivo concreto de a evidência
        ancorada usar o CGI: ela some do `ps` junto com o Δ."""
        if cgi_port <= 0:
            return None
        base = f"http://{host}:{cgi_port}"
        gerenciador = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        gerenciador.add_password(None, base, username, password)
        return urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(gerenciador),
            urllib.request.HTTPBasicAuthHandler(gerenciador),
        )

    def _cgi(self, caminho: str, max_bytes: int) -> bytes:
        """Um GET no CGI do gravador. 401 vira RecorderAuthError para o
        breaker anti-lockout do SnapshotExecutor fechar TODO acesso ao
        aparelho — não só este caminho (CLAUDE.md: o gravador pune tentativa
        repetida de autenticação)."""
        if self._url_opener is None:
            raise RecorderError("CGI do gravador desligado (cgi_port=0)")
        url = f"http://{self._host}:{self._cgi_port}{caminho}"
        try:
            with self._url_opener.open(url, timeout=_CGI_TIMEOUT_S) as resposta:
                return resposta.read(max_bytes)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise RecorderAuthError(
                    f"gravador rejeitou a credencial no CGI (HTTP {exc.code})"
                ) from exc
            raise RecorderError(f"CGI do gravador respondeu HTTP {exc.code}") from exc
        except Exception as exc:  # noqa: BLE001 — urllib levanta OSError/URLError/socket.*
            raise RecorderError(f"CGI do gravador inacessível: {exc}") from exc

    def _desvio_relogio(self) -> float:
        """Segundos que o relógio do BOX está adiantado em relação ao gravador.

        Medido, nunca presumido, e é o que faz a âncora funcionar: na RVB o
        desvio era de 333s. Absorve de quebra qualquer diferença de fuso —
        é uma subtração entre dois relógios de parede, não uma conversão.

        Cacheado por `_TTL_RELOGIO_S` (0,1 requisição/min): relógio de
        aparelho anda, mas não anda depressa.
        """
        agora = self._clock()
        if self._relogio is not None and agora - self._relogio[0] < _TTL_RELOGIO_S:
            return self._relogio[1]
        bruto = self._cgi("/cgi-bin/global.cgi?action=getCurrentTime", 512)
        texto = bruto.decode("utf-8", "replace").strip()
        # "result=2026-09-09 09:22:38"
        if "result=" not in texto:
            raise RecorderError(f"relógio do gravador em formato inesperado: {texto[:60]!r}")
        try:
            hora_gravador = datetime.strptime(
                texto.split("result=", 1)[1].strip(), "%Y-%m-%d %H:%M:%S"
            )
        except ValueError as exc:
            raise RecorderError(f"relógio do gravador ilegível: {exc}") from exc
        desvio = (self._agora() - hora_gravador).total_seconds()
        self._relogio = (agora, desvio)
        logger.info(
            "gravador_relogio hora=%s desvio_s=%.0f — a evidência é ancorada no "
            "relógio DO GRAVADOR, não no do box",
            hora_gravador.isoformat(), desvio,
        )
        return desvio

    def capture_frame_at(self, camera_id: str, instante: datetime) -> bytes:
        """JPEG do quadro que o gravador tinha NO INSTANTE *instante*.

        É a diferença entre a evidência mostrar o que gerou o alerta e mostrar
        outro momento: a captura ao vivo (`capture_frame`/`get_snapshot`) pede
        "o agora", e o agora é sempre segundos depois da detecção — medido em
        200 alertas da RVB: mediana 4,75s, mínimo 2,90s, máximo 34,3s. Uma
        pessoa a 1,4 m/s anda ~6,6 m na mediana.

        Caminho: `loadfile.cgi` (CGI HTTP do dialeto Dahua/Intelbras) devolve
        um MP4 da janela pedida; `extract_still_frame` tira o primeiro quadro.
        A lacuna que o topo deste módulo registrava — "o conserto real é a API
        CGI da Intelbras, não implementado por não haver hardware para
        confirmar o contrato" — é a que esta função fecha, agora com o
        contrato confirmado contra o iNVD 3032 da RVB (09/09/2026):
          · RTSP DESCRIBE de /cam/playback com starttime/endtime -> 200 OK e
            `a=range:npt=0-5.000000`, provando que a âncora é ACEITA;
          · loadfile.cgi da mesma janela -> HTTP 200, MP4, e o JPEG extraído
            saiu byte a byte IGUAL ao do caminho RTSP (778546 B);
          · 0,79s pelo CGI contra 3,44s pelo RTSP — e a captura roda DENTRO
            do loop que lê o pub/sub, então o tempo de parede aqui é tempo em
            que o relay não lê o barramento;
          · o OSD da própria câmera confere o Δ: pedido -45s -> quadro
            09:21:53 (exato), pedido -10s -> 09:22:29 (+1s de I-frame).

        Levanta RecorderError quando o gravador não tem o trecho (404 — ele
        grava por movimento/agendamento, nem todo instante existe) ou quando
        o CGI está fora. Quem chama DEGRADA para a captura ao vivo; evidência
        ruim é ruim, alerta que não chega é pior.
        """
        canal = self._channel_for(camera_id)
        desvio = self._desvio_relogio()
        # tz-aware (payload manda ISO-8601 UTC) -> hora de parede do box, que é
        # a mesma régua em que o desvio foi medido.
        local = instante.astimezone().replace(tzinfo=None) if instante.tzinfo else instante
        inicio = local - timedelta(seconds=desvio)
        fim = inicio + timedelta(seconds=_JANELA_S)
        fmt = lambda d: quote(d.strftime("%Y-%m-%d %H:%M:%S"), safe="")  # noqa: E731
        trecho = self._cgi(
            f"/cgi-bin/loadfile.cgi?action=startLoad&channel={canal}"
            f"&startTime={fmt(inicio)}&endTime={fmt(fim)}",
            _CGI_MAX_BYTES,
        )
        logger.info(
            "evidencia_ancorada camera=%s canal=%d instante=%s janela_gravador=%s "
            "desvio_s=%.0f bytes=%d",
            camera_id, canal, instante.isoformat(), inicio.isoformat(), desvio, len(trecho),
        )
        return extract_still_frame(trecho)

    def _channel_for(self, camera_id: str) -> int:
        if camera_id in self._channel_map:
            return self._channel_map[camera_id]
        raise RecorderError(
            f"camera_id={camera_id!r} sem canal RTSP mapeado em RECORDER_CHANNEL_MAP "
            "— sem fallback silencioso (ADR-0017)."
        )

    def health(self) -> RecorderHealth:
        """No HTTP API to probe — checks only that the RTSP port accepts TCP."""
        try:
            with socket.create_connection((self._host, self._port), timeout=self._timeout):
                return RecorderHealth(reachable=True, detail="rtsp port open")
        except OSError as exc:
            logger.warning(
                "rtsp_timestamp_health_check_failed host=%s port=%s err=%s",
                self._host,
                self._port,
                exc,
            )
            return RecorderHealth(reachable=False, detail=str(exc))

    def list_events(
        self, camera_id: str, start: datetime, end: datetime
    ) -> list[RecorderEvent]:
        """No real timeline — one synthetic event covering the requested window.

        See module docstring: this fallback has no search API, so "does a
        recording exist here" can only be answered by attempting playback.
        """
        self._channel_for(camera_id)  # validates mapping even though unused below
        return [
            RecorderEvent(
                event_id=f"rtsp-timestamp:{camera_id}:{start.isoformat()}",
                camera_id=camera_id,
                started_at=start,
                ended_at=end,
                event_type="recording",
                description=(
                    "Sem índice de timeline real neste protocolo (fallback RTSP "
                    "com timestamp) — cobertura assumida, não confirmada."
                ),
            )
        ]

    def stream_clip(
        self, camera_id: str, start: datetime, end: datetime
    ) -> Iterator[bytes]:
        channel = self._channel_for(camera_id)
        playback_url = self._build_playback_url(channel, start, end)
        duration_seconds = (end - start).total_seconds()
        yield from stream_rtsp_clip(playback_url, duration_seconds)

    def _build_playback_url(self, channel: int, start: datetime, end: datetime) -> str:
        user = quote(self._username or "", safe="")
        pwd = quote(self._password or "", safe="")
        creds = f"{user}:{pwd}@" if user else ""
        url = (
            f"rtsp://{creds}{self._host}:{self._port}/cam/playback"
            f"?channel={channel}"
            f"&starttime={_fmt(start)}&endtime={_fmt(end)}"
        )
        return RTSPUrlValidator.validate(url)

    def capture_frame(self, camera_id: str) -> bytes:
        """Coleta de frame de treino (eixo COLETA, migration 114).

        Resolve o subtype POR CÂMERA via `_collection_subtype_overrides`
        (cloud-polled, ver recorder_factory.resolve_collection_subtype_overrides)
        — câmera sem override cai para `self._stream_subtype` (global,
        RECORDER_STREAM_SUBTYPE), o comportamento pré-114.
        """
        channel = self._channel_for(camera_id)
        subtype = self._collection_subtype_overrides.get(camera_id, self._stream_subtype)
        live_url = self._build_live_url(channel, subtype)
        return capture_still_frame(live_url)

    def get_snapshot(self, camera_id: str, channel_hint: "int | None" = None) -> bytes:
        """This backend has no ONVIF GetSnapshotUri equivalent — the snapshot
        triage flow (Bloco A) grabs one live frame, same mechanics as
        `capture_frame`, but with its OWN channel resolution:

        Canal via `resolve_snapshot_channel` (channel_map PRIMEIRO, depois o
        *channel_hint* do payload do comando). Câmera DRAFT nunca entra no
        channel_map por desenho (config_poller filtra is_active — draft não
        pode entrar nos pipelines de HLS/coleta), então o hint é o que
        permite fotografar draft sem ativá-la — o caso central do Bloco A
        (achado em campo na RVB: canal 9 draft falhava como "sem sinal",
        quando na verdade nunca resolvia canal nenhum). O mapa vence quando
        presente, para uma câmera ATIVA nunca dessincronizar de um comando
        antigo na fila. NÃO delega a `capture_frame`, que é map-only de
        propósito (ADR-0017 — para coleta, câmera fora do mapa É
        misconfiguração).

        ffmpeg/RTSP has no structured HTTP status code, so an auth failure
        only shows up as free text in the RecorderError message (stderr tail,
        already redacted — see rtsp_frame_capture.py). Reclassified here via
        `is_auth_failure_message` into RecorderAuthError so the snapshot
        anti-lockout circuit breaker (snapshot_executor.py) still trips on
        it, same as the ONVIF path.
        """
        channel = resolve_snapshot_channel(self._channel_map, camera_id, channel_hint)
        subtype = self._collection_subtype_overrides.get(camera_id, self._stream_subtype)
        live_url = self._build_live_url(channel, subtype)
        try:
            return capture_still_frame(live_url)
        except RecorderError as exc:
            if is_auth_failure_message(str(exc)):
                raise RecorderAuthError(str(exc)) from exc
            raise

    def _build_live_url(self, channel: int, subtype: int | None = None) -> str:
        """Dahua/Intelbras-dialect LIVE stream path — same OEM family as
        _build_playback_url's `/cam/playback`. `subtype=0` (default) selects
        the main (high-res) stream, `subtype=1` the sub stream.

        *subtype* defaults to `self._stream_subtype` (RECORDER_STREAM_SUBTYPE,
        eixo OPERAÇÃO, global) when omitted — this is the LIVE VIEW path
        (`live_view_loop._resolve_camera_urls` calls this with a single
        argument, `_build_live_url(channel)`, and MUST keep using the global
        subtype, never a per-camera collection override). `capture_frame()`
        above passes an explicit *subtype* resolved per-camera (eixo COLETA,
        migration 114) — the two axes are independent on purpose."""
        effective_subtype = self._stream_subtype if subtype is None else subtype
        user = quote(self._username or "", safe="")
        pwd = quote(self._password or "", safe="")
        creds = f"{user}:{pwd}@" if user else ""
        url = (
            f"rtsp://{creds}{self._host}:{self._port}/cam/realmonitor"
            f"?channel={channel}&subtype={effective_subtype}"
        )
        return RTSPUrlValidator.validate(url)

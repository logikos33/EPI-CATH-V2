"""SnapshotExecutor — captura no gravador + upload pra nuvem, com breaker.

Dois clientes, um só caminho até o gravador:
  · `capture_and_upload` — comando `capture_snapshot` (miniatura de triagem);
  · `capture_evidence`   — evidência do alerta que o `DetectionRelay` acabou
    de receber do barramento `det:*` (o frame que o operador vai julgar).

Os dois compartilham o MESMO circuit breaker de propósito: uma credencial
rejeitada tem de parar todo acesso ao gravador, não só o caminho que a
descobriu.

Two steps per command: `RecorderClient.get_snapshot(camera_id, channel_hint)`
(ONVIF GetSnapshotUri, D-85, falling back to a live-frame RTSP grab — see
onvif_recorder_client.py/rtsp_timestamp_recorder_client.py; o hint do payload
é o que permite fotografar câmera DRAFT, fora do channel_map por desenho —
ver capture_and_upload) then a multipart POST of the JPEG to the cloud
(`POST /api/v1/edge/cameras/<camera_id>/snapshot`, device auth, mirrors
live_view/segment_pusher.py's push pattern).

ANTI-LOCKOUT CIRCUIT BREAKER (CLAUDE.md: the gravador applies anti-brute-force
lockout to repeated failed-auth attempts): the FIRST RecorderAuthError from
ANY capture_snapshot attempt trips a process-local breaker (`circuit_open`)
that makes every subsequent call to `capture_and_upload` — this poll cycle
AND every future one, until the process restarts — fail immediately with
reason="auth" WITHOUT touching the recorder again. No auto-recovery, no
retry: same one-shot discipline as recorder_factory.validate_onvif_boot_or_raise.
A human fixes the credential and restarts the box.

Never raises out of `capture_and_upload` — every failure (capture, auth,
upload) comes back as a `{"ok": False, "reason": ..., "detail": ...}` dict so
the caller (CommandPoller) can always ack a definite status.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .recorder_client import RecorderAuthError, RecorderChannelError, RecorderError

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 15.0
_MIN_CHANNEL = 1
_MAX_CHANNEL = 64  # mesmo teto do cadastro de câmeras na nuvem (channel 1..64)

# Motivo específico para "sem como resolver o canal" — distinto do genérico
# "sem sinal no canal" (que seria mentira: o canal nem chegou a ser
# contatado). Mapeado para texto de UI em
# services/api/app/api/v1/edge_commands/routes.py::_SNAPSHOT_FAILURE_MESSAGES.
_REASON_NO_CHANNEL = "no_channel"


class SnapshotUploadError(Exception):
    """A nuvem rejeitou, ou está inalcançável para, um upload de snapshot."""


class SnapshotExecutor:
    """Captures + uploads one snapshot per `capture_and_upload` call.

    *http_client* follows the same convention as command_poller.py/
    segment_pusher.py: pass the already-auth-wrapped client (main.py's
    `_AutoAuthHttpClient`) when running inside the daemon, or a bare mock in
    tests — `token` only matters for callers NOT behind that wrapper.
    """

    def __init__(
        self,
        recorder_client: Any,
        http_client: Any,
        cloud_url: str,
        token: str = "",
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._recorder = recorder_client
        self._http = http_client
        self._upload_url_base = f"{cloud_url.rstrip('/')}/api/v1/edge/cameras"
        self._token = token
        self._timeout = timeout
        self._circuit_open = False
        self._circuit_reason: Optional[str] = None

    # ── circuit breaker state (read-only from the outside) ─────────────────

    @property
    def circuit_open(self) -> bool:
        return self._circuit_open

    @property
    def circuit_reason(self) -> Optional[str]:
        return self._circuit_reason

    # ── main entrypoint ──────────────────────────────────────────────────

    def capture_and_upload(self, camera_id: str, channel: Any = None) -> dict:
        """Executes one capture_snapshot command end-to-end.

        *channel* (do payload do comando, vindo de `public.cameras.channel`
        na nuvem) é validado (int 1..64; inválido/ausente -> None) e passado
        adiante como HINT para `RecorderClient.get_snapshot`. A resolução no
        client é channel_map PRIMEIRO (fonte viva da box — uma câmera ATIVA
        nunca dessincroniza de um comando antigo na fila), hint como
        fallback quando a câmera não está no mapa. É o hint que torna câmera
        DRAFT fotografável: draft nunca entra no channel_map por desenho
        (config_poller filtra is_active — achado em campo na RVB, canal 9),
        e fotografá-la sem ativar é exatamente o propósito do Bloco A.

        Sem mapa E sem hint -> RecorderChannelError no client, reportado com
        reason="no_channel" (específico) — nunca o genérico "sem sinal no
        canal", que mentiria pro usuário sobre um canal que nem foi contatado.
        """
        if self._circuit_open:
            return {"ok": False, "reason": "auth", "detail": self._circuit_reason}

        channel_hint = self._validate_channel_hint(camera_id, channel)

        try:
            jpeg_bytes = self._recorder.get_snapshot(camera_id, channel_hint=channel_hint)
        except RecorderAuthError as exc:
            self._trip_circuit(str(exc))
            return {"ok": False, "reason": "auth", "detail": str(exc)}
        except RecorderChannelError as exc:
            logger.warning(
                "snapshot_capture_failed camera_id=%s reason=%s err=%s",
                camera_id, _REASON_NO_CHANNEL, exc,
            )
            return {"ok": False, "reason": _REASON_NO_CHANNEL, "detail": str(exc)}
        except RecorderError as exc:
            reason = self._classify_capture_failure(str(exc))
            logger.warning(
                "snapshot_capture_failed camera_id=%s reason=%s err=%s",
                camera_id, reason, exc,
            )
            return {"ok": False, "reason": reason, "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 — nunca deixa o poller quebrar
            logger.exception("snapshot_capture_unexpected_error camera_id=%s", camera_id)
            return {"ok": False, "reason": "capture_failed", "detail": str(exc)}

        try:
            self._upload(camera_id, jpeg_bytes)
        except SnapshotUploadError as exc:
            logger.warning("snapshot_upload_failed camera_id=%s err=%s", camera_id, exc)
            return {"ok": False, "reason": "upload_failed", "detail": str(exc)}

        return {"ok": True}

    # ── circuit breaker ──────────────────────────────────────────────────

    def _trip_circuit(self, reason: str) -> None:
        self._circuit_open = True
        self._circuit_reason = reason
        logger.error(
            "snapshot_circuit_breaker_open reason=%s — TODAS as capturas de snapshot "
            "suspensas até restart do processo (anti-lockout: o gravador pune "
            "tentativas repetidas de autenticação, CLAUDE.md)",
            reason,
        )

    def reset_circuit(self) -> None:
        """Exposed for tests / an eventual manual-recovery path — production
        code never calls this: the breaker is meant to require a restart."""
        self._circuit_open = False
        self._circuit_reason = None

    @staticmethod
    def _validate_channel_hint(camera_id: str, channel: Any) -> Optional[int]:
        """Valida o `channel` do payload como hint: int (bool não conta) no
        range 1..64 -> o próprio valor; qualquer outra coisa (ausente, tipo
        errado, fora do range) -> None, com log — o client decide se ainda
        consegue resolver pelo channel_map. Nunca levanta: payload malformado
        de um comando antigo não pode derrubar o processamento."""
        if channel is None:
            return None
        if isinstance(channel, bool) or not isinstance(channel, int):
            logger.warning(
                "snapshot_channel_hint_invalid camera_id=%s channel=%r — ignorando hint",
                camera_id, channel,
            )
            return None
        if not (_MIN_CHANNEL <= channel <= _MAX_CHANNEL):
            logger.warning(
                "snapshot_channel_hint_out_of_range camera_id=%s channel=%d — ignorando hint",
                camera_id, channel,
            )
            return None
        return channel

    @staticmethod
    def _classify_capture_failure(message: str) -> str:
        """Best-effort machine-readable reason for a non-auth capture
        failure — "timeout" vs a generic "no signal" bucket. Approximate by
        nature (ffmpeg/ONVIF error text isn't structured); never confused
        with the auth path, which uses the precise RecorderAuthError type,
        not string matching."""
        lowered = message.lower()
        if "timeout" in lowered or "não respondeu" in lowered:
            return "timeout"
        return "sem sinal no canal"

    # ── upload ───────────────────────────────────────────────────────────

    def _upload(self, camera_id: str, jpeg_bytes: bytes, rota: str = "snapshot") -> "str | None":
        """POST do JPEG para `/api/v1/edge/cameras/<id>/<rota>`.

        Devolve a `r2_key` que a nuvem respondeu (None se ela não mandou
        nenhuma — o caminho de snapshot não depende dela). É esse valor que
        vira `alerts.evidence_r2_key` no caminho de evidência: o box NÃO tem
        credencial de R2 e nunca terá, então a chave só pode vir de quem
        gravou o objeto.
        """
        url = f"{self._upload_url_base}/{camera_id}/{rota}"
        try:
            resp = self._http.post(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                files={"file": (f"{rota}.jpg", jpeg_bytes, "image/jpeg")},
                timeout=self._timeout,
            )
        except Exception as exc:
            raise SnapshotUploadError(
                f"upload falhou: camera={camera_id} err={exc}"
            ) from exc

        if resp.status_code != 201:
            raise SnapshotUploadError(
                f"upload rejeitado: camera={camera_id} status={resp.status_code} "
                f"body={getattr(resp, 'text', '')[:200]}"
            )
        try:
            return ((resp.json() or {}).get("data") or {}).get("r2_key")
        except Exception:  # noqa: BLE001 — corpo não-JSON não invalida o upload
            return None

    # ── evidência de alerta ──────────────────────────────────────────────

    def capture_evidence(self, camera_id: str) -> "tuple[str | None, bytes | None]":
        """Captura UM frame e o sobe como evidência; devolve `(chave R2, JPEG)`.

        Mesmo par captura+upload do snapshot, e de propósito o MESMO breaker:
        uma credencial rejeitada tem de parar TODO acesso ao gravador, não só
        o caminho que a descobriu (anti-lockout, CLAUDE.md).

        O JPEG volta junto porque quem chama (`DetectionRelay`) precisa OLHAR o
        quadro antes de decidir se o evento vira alerta — é o `GuardaPessoa`,
        que barra cena sem gente. Devolver só a chave fazia o pixel morrer aqui
        dentro, e a alternativa seria uma segunda captura: outra conexão RTSP no
        mesmo gravador que pune tentativa repetida. Os dois valores são
        independentes — `(None, jpeg)` é upload que falhou sobre um frame bom, e
        o guarda ainda pode julgá-lo.

        Nunca levanta. Evidência é desejável, não pode bloquear o alerta —
        quem chama enfileira o evento com `None` e a nuvem grava o alerta sem
        imagem, que é pior que ter imagem e MUITO melhor que não ter alerta.
        """
        if self._circuit_open:
            logger.warning(
                "evidence_capture_skipped camera_id=%s reason=circuito_aberto", camera_id
            )
            return None, None
        try:
            jpeg_bytes = self._recorder.get_snapshot(camera_id)
        except RecorderAuthError as exc:
            self._trip_circuit(str(exc))
            return None, None
        except Exception as exc:  # noqa: BLE001 — sem sinal/timeout/canal: sem evidência, com alerta
            logger.warning(
                "evidence_capture_failed camera_id=%s err=%s", camera_id, exc
            )
            return None, None
        try:
            r2_key = self._upload(camera_id, jpeg_bytes, rota="evidence")
        except SnapshotUploadError as exc:
            logger.warning("evidence_upload_failed camera_id=%s err=%s", camera_id, exc)
            return None, jpeg_bytes
        logger.info(
            "evidence_uploaded camera_id=%s r2_key=%s bytes=%d",
            camera_id, r2_key, len(jpeg_bytes),
        )
        return r2_key, jpeg_bytes

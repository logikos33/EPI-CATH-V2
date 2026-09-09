"""DetectionRelay: Redis det:* (pipeline local) → SQLiteBuffer → Uploader.

The producer side of the detections→cloud path. Until this loop existed
nothing in the agent called `SQLiteBuffer.enqueue` outside tests: the Uploader
drained a buffer nobody filled, so `/api/v1/edge/events/ingest` never saw a
single event from a box.

Bus: Redis pub/sub, one channel per camera (ADR-0002). The in-repo publishers
(`services/inference/inference/inference_engine.py`, the Celery task in
`services/api/app/infrastructure/queue/tasks/inference.py`) and the API's
`socket_bridge` use `det:{camera_id}`; ADR-0002 / deployments/edge/
edge.env.example spell it `detections:{camera_id}`. Both patterns are
subscribed so the relay works whichever name the DeepStream probe on the box
ends up publishing — that probe is NOT in this repo (it lives in the
`jetson-experiments/mm` runners the systemd unit calls) and, per
docs/edge/DIAGNOSTICO_OBSERVABILIDADE_2026-07-21.md, was not publishing yet.

Message → event rule: only frames flagged `has_violation` become a
`detection` EVENT in the buffer. det:* is live-overlay traffic (5 FPS × N
cameras, ephemeral by ADR-0002); relaying every frame would grow a buffer
that never discards (sqlite_buffer.py) on a box where a full disk is a
device interlock (CLAUDE.md "Evidência"). The whole published payload is kept
as the event payload — the cloud stores it as JSONB.

EVIDÊNCIA (ADR-0070): antes de enfileirar, o relay captura UM frame ao vivo e
o sobe pela nuvem (`SnapshotExecutor.capture_evidence`), gravando a chave R2
no payload — `alerta_de_evento_do_edge` não sobe imagem, ela espera a chave
pronta. Sem isso todo alerta do box nascia com `evidence_r2_key` NULL e o
operador abria a tela sem frame para julgar. A captura é best-effort com teto
e pausa (ver abaixo): evidência é desejável, o alerta é obrigatório.

Opt-in: `EDGE_REDIS_URL` unset → loop not built (see
build_detection_relay_from_env). Transport errors propagate out of `run()`:
main._supervise restarts the loop with backoff and a fresh pubsub — no
bespoke reconnect logic here.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable

from .sqlite_buffer import SQLiteBuffer

logger = logging.getLogger(__name__)

# ponytail: two names for one bus (code says det:*, ADR-0002 says detections:*);
# collapse to one once the box's DeepStream probe fixes which it publishes.
_PATTERNS: tuple[str, ...] = ("det:*", "detections:*")
_EVENT_TYPE = "detection"
_DEFAULT_POLL_S = 1.0
#: Teto GLOBAL de capturas de evidência por minuto (todas as câmeras somadas).
#: Anti-lockout: cada captura é UMA conexão RTSP nova no gravador
#: (192.168.35.18 na RVB, 29 canais no mesmo aparelho). O publicador já
#: espaça por câmera (--cooldown-s 30), mas ele não enxerga o total: com 29
#: câmeras o pior caso seria ~58/min. Este teto não depende de quantas
#: câmeras o site tem — estourou, o evento sobe SEM evidência.
_DEFAULT_MAX_EVIDENCE_PER_MIN = 20

#: Piso de confiança para GASTAR uma captura de evidência.
#:
#: O teto de 20/min é cota escassa, e sem piso ela é gasta por ordem de
#: chegada. Medido no box da RVB em 09/09: 548 eventos em 15 min consumiram a
#: cota inteira, e só **2 de 75 alertas** nasceram com imagem — porque a nuvem
#: descarta abaixo de `DETECTION_CONFIDENCE_THRESHOLD` (0,50) e a cota já
#: tinha ido embora em eventos que morreriam ali.
#:
#: Este piso NÃO filtra evento nenhum: tudo continua subindo. Ele só decide
#: ONDE gastar a captura — no evento que tem chance de virar alerta.
_DEFAULT_PISO_EVIDENCIA = 0.5
#: Falhou a evidência → nem tenta pelos próximos 60s. Existe pelo custo de
#: BLOQUEIO, não pela falha em si: `capture_evidence` roda DENTRO do loop
#: que lê o pub/sub, e uma nuvem fora do ar leva o timeout inteiro (15s) em
#: cada evento. Sem esta pausa, R2 indisponível deixaria de custar 'alerta
#: sem imagem' e passaria a custar 'alerta que não chega' — exatamente o
#: que a degradação tenta evitar.
_PAUSA_APOS_FALHA_S = 60.0


def _text(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


class DetectionRelay:
    """Subscribes to the local detection bus and enqueues violation frames."""

    def __init__(
        self,
        buffer: SQLiteBuffer,
        pubsub_factory: Callable[[], Any],
        poll_s: float = _DEFAULT_POLL_S,
        evidence_capture: "Callable[[str], str | None] | None" = None,
        max_evidence_per_min: int = _DEFAULT_MAX_EVIDENCE_PER_MIN,
        piso_evidencia: float = _DEFAULT_PISO_EVIDENCIA,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._buffer = buffer
        self._pubsub_factory = pubsub_factory
        self._poll_s = poll_s
        # None = relay sem evidência (comportamento anterior): o evento chega,
        # o alerta nasce sem imagem. É o que acontecia com TODO alerta do box.
        self._evidence_capture = evidence_capture
        self._max_evidence_per_min = max_evidence_per_min
        self._piso_evidencia = piso_evidencia
        self._clock = clock
        self._janela_inicio = clock()
        self._na_janela = 0
        self._pausa_ate = 0.0

    # ── evidência ────────────────────────────────────────────────────────────

    def _dentro_do_teto(self) -> bool:
        """Janela fixa de 60s. Conta TENTATIVAS, não sucessos: é a tentativa
        que bate no gravador, e é ela que precisa ser limitada."""
        agora = self._clock()
        if agora - self._janela_inicio >= 60.0:
            self._janela_inicio = agora
            self._na_janela = 0
        if self._na_janela >= self._max_evidence_per_min:
            return False
        self._na_janela += 1
        return True

    @staticmethod
    def _confianca_maxima(payload: dict) -> float:
        """Maior confiança entre as detecções do evento. 0.0 se não houver."""
        return max(
            (float(d.get("confidence") or 0.0) for d in (payload.get("detections") or [])),
            default=0.0,
        )

    def _evidencia(self, camera_id: str) -> "str | None":
        """Chave R2 do frame do evento, ou None. NUNCA levanta.

        Degradação para o lado seguro (R2 fora, gravador mudo, teto estourado):
        o evento segue para o buffer sem evidência. Alerta sem imagem é ruim;
        alerta que não chega é pior.
        """
        if self._evidence_capture is None:
            return None
        if self._clock() < self._pausa_ate:
            return None
        if not self._dentro_do_teto():
            logger.warning(
                "detection_relay_evidencia_no_teto camera=%s max=%d/min — evento "
                "segue sem imagem",
                camera_id, self._max_evidence_per_min,
            )
            return None
        try:
            chave = self._evidence_capture(camera_id)
        except Exception:  # noqa: BLE001 — evidência nunca bloqueia o alerta
            logger.warning(
                "detection_relay_evidencia_falhou camera=%s", camera_id, exc_info=True
            )
            chave = None
        if not chave:
            self._pausa_ate = self._clock() + _PAUSA_APOS_FALHA_S
            logger.warning(
                "detection_relay_evidencia_pausada por %.0fs após falha (camera=%s) — "
                "os eventos continuam subindo, sem imagem",
                _PAUSA_APOS_FALHA_S, camera_id,
            )
            return None
        return chave

    def handle(self, channel: Any, data: Any) -> int | None:
        """One bus message → buffer row id, or None when dropped."""
        try:
            payload = json.loads(_text(data))
        except ValueError:
            logger.warning("detection_relay_bad_json channel=%s", _text(channel))
            return None
        if not isinstance(payload, dict) or not payload.get("has_violation"):
            return None
        camera_id = str(payload.get("camera_id") or _text(channel).rsplit(":", 1)[-1])
        # A chave entra no PAYLOAD (não numa coluna nova do buffer): o schema
        # do SQLite é `CREATE TABLE IF NOT EXISTS`, então uma coluna nova
        # simplesmente não apareceria num buffer já existente no box. O
        # Uploader promove o campo para o nível do evento, que é onde
        # /edge/events/ingest o lê. Gravada ANTES do enqueue, ela é estável
        # entre reenvios — o dedup da nuvem é sha256 do evento serializado.
        # Só gasta a cota onde o evento tem chance de virar alerta. A nuvem
        # descarta abaixo do próprio limiar, e imagem de evento descartado é
        # cota jogada fora — foi o que deixou 73 de 75 alertas sem frame.
        # ⛔ Não filtra o EVENTO: ele sobe de qualquer jeito, só sem imagem.
        confianca = self._confianca_maxima(payload)
        evidencia = self._evidencia(camera_id) if confianca >= self._piso_evidencia else None
        if evidencia:
            payload["evidence_r2_key"] = evidencia
        return self._buffer.enqueue(_EVENT_TYPE, camera_id, payload)

    def run(self, stop_event: threading.Event) -> None:
        """Drain the bus until *stop_event* is set. Raises on transport error."""
        pubsub = self._pubsub_factory()
        try:
            pubsub.psubscribe(*_PATTERNS)
            logger.info("detection_relay_subscribed patterns=%s", ",".join(_PATTERNS))
            while not stop_event.is_set():
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=self._poll_s)
                if not msg or msg.get("type") != "pmessage":
                    continue
                self.handle(msg.get("channel"), msg.get("data"))
        finally:
            pubsub.close()


def build_detection_relay_from_env(
    buffer: SQLiteBuffer,
    env: dict[str, str] | None = None,
    evidence_capture: "Callable[[str], str | None] | None" = None,
) -> DetectionRelay | None:
    """`EDGE_REDIS_URL` set → relay on the local Redis; unset → None (off).

    *evidence_capture* — normalmente `SnapshotExecutor.capture_evidence`
    (main.py). Ausente, o relay volta ao comportamento anterior: evento sem
    imagem. Teto por `EDGE_MAX_EVIDENCE_PER_MIN` (0 desliga a captura).
    """
    source = env if env is not None else os.environ
    url = source.get("EDGE_REDIS_URL", "").strip()
    if not url:
        logger.info("detection_relay_disabled EDGE_REDIS_URL unset")
        return None

    teto = int(source.get("EDGE_MAX_EVIDENCE_PER_MIN", str(_DEFAULT_MAX_EVIDENCE_PER_MIN)))
    piso = float(source.get("EDGE_PISO_EVIDENCIA", str(_DEFAULT_PISO_EVIDENCIA)))
    if teto <= 0:
        evidence_capture = None
    logger.info(
        "detection_relay_evidencia=%s teto=%d/min",
        "ligada" if evidence_capture is not None else "desligada", teto,
    )

    def _factory() -> Any:
        import redis  # lazy: only needed when the relay is on

        return redis.Redis.from_url(url).pubsub()

    return DetectionRelay(
        buffer,
        _factory,
        evidence_capture=evidence_capture,
        piso_evidencia=piso,
        max_evidence_per_min=teto,
    )

"""DetectionRelay: Redis det:* (pipeline local, ADR-0002) → SQLiteBuffer.

É o PRODUTOR do caminho detecções→cloud: antes dele nada no agente chamava
SQLiteBuffer.enqueue fora dos testes — o Uploader drenava um buffer que
ninguém enchia.
"""

import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.detection_relay import (
    DetectionRelay,
    build_detection_relay_from_env,
)
from app.sqlite_buffer import SQLiteBuffer


class _FakePubSub:
    """pubsub mínimo (psubscribe/get_message/close) alimentado por uma lista."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.patterns: list[str] = []
        self.closed = False

    def psubscribe(self, *patterns):
        self.patterns.extend(patterns)

    def get_message(self, ignore_subscribe_messages=False, timeout=0.0):
        if self._messages:
            return self._messages.pop(0)
        return None

    def close(self):
        self.closed = True


def _pmessage(channel: str, payload, pattern: str = "det:*"):
    data = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    return {"type": "pmessage", "pattern": pattern, "channel": channel, "data": data}


@pytest.fixture()
def buf(tmp_path):
    b = SQLiteBuffer(str(tmp_path / "relay.db"))
    yield b
    b.close()


# ── handle(): uma mensagem → (ou não) uma linha no buffer ───────────────────

def test_violation_frame_becomes_detection_event(buf):
    relay = DetectionRelay(buf, lambda: _FakePubSub([]))
    payload = {
        "camera_id": "cam-uuid-1",
        "timestamp": "2026-08-23T12:00:00Z",
        "detections": [{"class": "no_helmet", "confidence": 0.91, "bbox": [1, 2, 3, 4]}],
        "has_violation": True,
    }

    row_id = relay.handle("det:cam-uuid-1", json.dumps(payload))

    assert row_id is not None
    [item] = buf.dequeue_batch()
    assert item["event_type"] == "detection"
    assert item["camera_id"] == "cam-uuid-1"
    assert item["payload"] == payload


def test_frame_without_violation_is_dropped(buf):
    """det:* é tráfego de overlay ao vivo (5 FPS × N câmeras, efêmero por
    ADR-0002). Só violação vira EVENTO — relayar todo frame encheria um
    buffer que nunca descarta (disco cheio = intertravamento do Orin)."""
    relay = DetectionRelay(buf, lambda: _FakePubSub([]))

    assert relay.handle("det:c1", json.dumps({"camera_id": "c1", "detections": [],
                                               "has_violation": False})) is None
    assert relay.handle("det:c1", json.dumps({"camera_id": "c1", "detections": []})) is None
    assert buf.count_unsent() == 0


def test_camera_id_falls_back_to_channel_suffix(buf):
    relay = DetectionRelay(buf, lambda: _FakePubSub([]))

    relay.handle("det:cam-from-channel", json.dumps({"has_violation": True}))
    relay.handle("detections:tenant-x:cam-deep", json.dumps({"has_violation": True}))

    cams = [e["camera_id"] for e in buf.dequeue_batch()]
    assert cams == ["cam-from-channel", "cam-deep"]


def test_bytes_channel_and_data_are_decoded(buf):
    relay = DetectionRelay(buf, lambda: _FakePubSub([]))

    relay.handle(b"det:cam-b", json.dumps({"has_violation": True}).encode())

    [item] = buf.dequeue_batch()
    assert item["camera_id"] == "cam-b"


def test_malformed_message_is_dropped_not_raised(buf):
    relay = DetectionRelay(buf, lambda: _FakePubSub([]))

    assert relay.handle("det:c1", b"\xff\xfenot json") is None
    assert relay.handle("det:c1", "[1, 2, 3]") is None  # JSON, mas não objeto
    assert buf.count_unsent() == 0


# ── run(): assina, drena mensagens, para no stop_event, fecha o pubsub ───────

def test_run_subscribes_both_channel_names_and_drains(buf):
    """Assina det:* (nome usado por services/inference e pelo worker Celery —
    o que o socket_bridge da API consome) E detections:* (nome do ADR-0002 /
    edge.env.example) — os dois nomes coexistem na documentação."""
    ps = _FakePubSub([
        _pmessage("det:c1", {"camera_id": "c1", "has_violation": True}),
        {"type": "psubscribe", "pattern": "det:*", "channel": "det:*", "data": 1},
        _pmessage("det:c2", {"camera_id": "c2", "has_violation": False}),
        _pmessage("detections:c3", {"camera_id": "c3", "has_violation": True},
                  pattern="detections:*"),
    ])
    relay = DetectionRelay(buf, lambda: ps, poll_s=0.0)
    stop = threading.Event()

    def _stop_when_drained():
        while ps._messages:
            pass
        stop.set()

    t = threading.Thread(target=_stop_when_drained, daemon=True)
    t.start()
    relay.run(stop)
    t.join(timeout=2.0)

    assert set(ps.patterns) == {"det:*", "detections:*"}
    assert [e["camera_id"] for e in buf.dequeue_batch()] == ["c1", "c3"]
    assert ps.closed is True


def test_run_exits_immediately_when_stop_already_set(buf):
    ps = _FakePubSub([_pmessage("det:c1", {"has_violation": True})])
    relay = DetectionRelay(buf, lambda: ps, poll_s=0.0)
    stop = threading.Event()
    stop.set()

    relay.run(stop)

    assert buf.count_unsent() == 0
    assert ps.closed is True


def test_run_propagates_transport_error_for_supervisor_restart(buf):
    """Queda do Redis → exceção sobe; main._supervise reinicia com backoff e
    o pubsub é refeito do zero (nada de reconexão caseira aqui)."""
    ps = _FakePubSub([])
    ps.get_message = MagicMock(side_effect=ConnectionError("redis down"))
    relay = DetectionRelay(buf, lambda: ps, poll_s=0.0)

    with pytest.raises(ConnectionError):
        relay.run(threading.Event())
    assert ps.closed is True


# ── build_detection_relay_from_env ───────────────────────────────────────────

def test_disabled_without_edge_redis_url(buf):
    assert build_detection_relay_from_env(buf, env={}) is None
    assert build_detection_relay_from_env(buf, env={"EDGE_REDIS_URL": ""}) is None


def test_enabled_with_edge_redis_url_uses_redis_pubsub(buf, monkeypatch):
    fake_redis_mod = MagicMock()
    client = MagicMock()
    fake_redis_mod.Redis.from_url.return_value = client
    monkeypatch.setitem(__import__("sys").modules, "redis", fake_redis_mod)

    relay = build_detection_relay_from_env(
        buf, env={"EDGE_REDIS_URL": "redis://127.0.0.1:6379/0"}
    )

    assert isinstance(relay, DetectionRelay)
    relay._pubsub_factory()
    fake_redis_mod.Redis.from_url.assert_called_once_with("redis://127.0.0.1:6379/0")
    client.pubsub.assert_called_once_with()


# ── evidência: a chave R2 entra no payload antes do enqueue ────────────────
#
# Sem isto o alerta nasce com `evidence_r2_key` NULL e o operador abre a tela
# de Eventos sem frame para julgar — medido em 09/09: 9 linhas em
# public.edge_events, todas sem evidência.

def _payload_com_violacao(camera_id="cam-uuid-1"):
    return {
        "camera_id": camera_id,
        "timestamp": "2026-09-09T04:09:01Z",
        "detections": [{"class": "Sem protetor de ouvido", "confidence": 0.8}],
        "has_violation": True,
    }


def test_evidencia_entra_no_payload_do_evento(buf):
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, _ts=None: (
            f"evidence/{cam}/20260909T040901000000.jpg", b"jpg", "ao_vivo",
        ),
    )

    relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao()))

    (linha,) = buf.dequeue_batch()
    assert linha["payload"]["evidence_r2_key"] == (
        "evidence/cam-uuid-1/20260909T040901000000.jpg"
    )


def test_falha_na_captura_nao_impede_o_evento(buf):
    """Degradação para o lado seguro: alerta sem imagem > alerta que não chega."""
    def _explode(_cam, _ts=None):
        raise RuntimeError("gravador mudo")

    relay = DetectionRelay(buf, lambda: _FakePubSub([]), evidence_capture=_explode)

    assert relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao())) is not None
    (linha,) = buf.dequeue_batch()
    assert "evidence_r2_key" not in linha["payload"]


def test_sem_capturador_o_evento_segue_como_antes(buf):
    relay = DetectionRelay(buf, lambda: _FakePubSub([]))

    assert relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao())) is not None
    (linha,) = buf.dequeue_batch()
    assert "evidence_r2_key" not in linha["payload"]


def test_teto_por_minuto_para_de_capturar_mas_nao_de_enfileirar(buf):
    """Anti-lockout: cada captura é uma conexão RTSP nova no MESMO gravador
    (29 canais na RVB). Estourado o teto, o evento sobe sem imagem."""
    chamadas: list[str] = []

    def _captura(cam, _ts=None):
        chamadas.append(cam)
        return f"evidence/{cam}/x.jpg", b"jpg", "ao_vivo"

    agora = [0.0]
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=_captura,
        max_evidence_per_min=2,
        clock=lambda: agora[0],
    )

    for _ in range(5):
        relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao()))

    assert len(chamadas) == 2, chamadas
    assert len(buf.dequeue_batch()) == 5  # os 5 eventos chegaram

    agora[0] = 61.0  # janela virou
    relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao()))
    assert len(chamadas) == 3


def test_frame_sem_violacao_nao_gasta_captura(buf):
    """O gate de violação vem ANTES da captura: quadro limpo não toca o gravador."""
    chamadas: list[str] = []
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, _ts=None: chamadas.append(cam) or ("k", b"jpg", "ao_vivo"),
    )

    limpo = {**_payload_com_violacao(), "has_violation": False}
    assert relay.handle("det:cam-uuid-1", json.dumps(limpo)) is None
    assert chamadas == []


def test_env_desliga_evidencia_com_teto_zero(buf, monkeypatch):
    monkeypatch.setenv("EDGE_REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("EDGE_MAX_EVIDENCE_PER_MIN", "0")
    relay = build_detection_relay_from_env(
        buf, evidence_capture=lambda cam, _ts=None: ("k", b"jpg", "ao_vivo")
    )
    assert relay is not None
    assert relay._evidence_capture is None


def test_falha_pausa_a_captura_mas_os_eventos_continuam(buf):
    """R2/nuvem fora: `capture_evidence` só devolve None depois do timeout
    (15s) e isso roda DENTRO do loop que lê o pub/sub. Sem pausa, o custo
    deixaria de ser 'alerta sem imagem' e viraria 'alerta que não chega'."""
    chamadas: list[str] = []
    agora = [0.0]

    def _falha(cam, _ts=None):
        chamadas.append(cam)
        return None, None, None  # nuvem fora: upload rejeitado

    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=_falha,
        clock=lambda: agora[0],
    )

    for _ in range(4):
        relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao()))

    assert chamadas == ["cam-uuid-1"]         # tentou UMA vez
    assert len(buf.dequeue_batch()) == 4      # e os 4 eventos chegaram

    agora[0] = 61.0                            # pausa expirou
    relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao()))
    assert len(chamadas) == 2


# ── piso de confiança: a cota de evidência vai para quem vira alerta ─────────
#
# Medido no box da RVB em 09/09: 548 eventos em 15 min gastaram a cota de
# 20/min por ordem de chegada, e só 2 de 75 ALERTAS nasceram com imagem — a
# nuvem descarta abaixo de 0,50 e a cota já tinha ido embora em evento que
# morreria ali.

def _payload(conf: float) -> str:
    return json.dumps({
        "camera_id": "cam-1",
        "has_violation": True,
        "detections": [{"class": "Sem Luvas", "confidence": conf}],
    })


def _relay_com_captura(buffer, chamadas):
    return DetectionRelay(
        buffer,
        lambda: _FakePubSub([]),
        evidence_capture=lambda cid, _ts=None: (
            chamadas.append(cid) or ("evidence/k.jpg", b"jpg", "ao_vivo")
        ),
        piso_evidencia=0.5,
    )


def test_evidencia_so_gasta_cota_acima_do_piso(buf):
    chamadas = []
    relay = _relay_com_captura(buf, chamadas)

    relay.handle("det:cam-1", _payload(0.30))

    # O EVENTO sobe — o piso não filtra evento, só decide onde gastar imagem.
    assert buf.count_unsent() == 1
    assert chamadas == [], "não pode bater no gravador por evento que a nuvem descarta"


def test_evidencia_gasta_cota_quando_passa_do_piso(buf):
    chamadas = []
    relay = _relay_com_captura(buf, chamadas)

    relay.handle("det:cam-1", _payload(0.66))

    assert chamadas == ["cam-1"]
    assert buf.count_unsent() == 1


def test_evento_abaixo_do_piso_nao_consome_a_janela(buf):
    """O que quebrava antes: 100 eventos fracos zeravam a cota do minuto e o
    evento forte que vinha depois ficava sem imagem."""
    chamadas = []
    relay = _relay_com_captura(buf, chamadas)

    for _ in range(100):
        relay.handle("det:cam-1", _payload(0.30))
    relay.handle("det:cam-1", _payload(0.90))

    assert chamadas == ["cam-1"], "o forte tem de achar cota livre"


def test_confianca_maxima_usa_a_maior_deteccao():
    p = {"detections": [{"confidence": 0.2}, {"confidence": 0.7}, {"confidence": 0.4}]}
    assert DetectionRelay._confianca_maxima(p) == 0.7
    assert DetectionRelay._confianca_maxima({}) == 0.0
    assert DetectionRelay._confianca_maxima({"detections": [{}]}) == 0.0


# ── guarda de pessoa: cena vazia não vira alerta ────────────────────────────
#
# Medido em 2026-09-09 sobre 60 frames de evidência REAIS da RVB, com árbitro
# independente: 9 (15%) eram cena vazia. O relay é o ÚNICO ponto no repo entre
# o publicador do box (que não está aqui) e o alerta na nuvem.

class _GuardaFake:
    """Guarda de mentira com o mesmo contrato: True publica, False barra."""

    def __init__(self, decisao=True):
        self.decisao = decisao
        self.frames: list[bytes] = []

    def julgar(self, _camera_id, payload, frame):
        self.frames.append(frame)
        payload["guarda_pessoa"] = {"veredito": "sem_pessoa" if not self.decisao else "com_pessoa"}
        return self.decisao


def test_guarda_barra_o_evento_de_cena_vazia(buf):
    guarda = _GuardaFake(decisao=False)
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, _ts=None: ("evidence/k.jpg", b"frame-do-evento", "ao_vivo"),
        guarda=guarda,
    )

    assert relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao())) is None
    assert buf.count_unsent() == 0, "o alerta não pode nascer"
    assert guarda.frames == [b"frame-do-evento"], "julgou o MESMO frame da evidência"


def test_guarda_deixa_passar_quando_ve_gente(buf):
    guarda = _GuardaFake(decisao=True)
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, _ts=None: ("evidence/k.jpg", b"frame", "ao_vivo"),
        guarda=guarda,
    )

    assert relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao())) is not None
    (linha,) = buf.dequeue_batch()
    assert linha["payload"]["guarda_pessoa"]["veredito"] == "com_pessoa"


def test_sem_frame_o_guarda_nao_opina_e_o_evento_sobe(buf):
    """Abaixo do piso de evidência não há captura — e sem pixel o guarda não
    pode barrar. Degradação para o lado seguro."""
    guarda = _GuardaFake(decisao=False)
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, _ts=None: ("evidence/k.jpg", b"frame", "ao_vivo"),
        piso_evidencia=0.5,
        guarda=guarda,
    )

    assert relay.handle("det:cam-1", _payload(0.30)) is not None
    assert guarda.frames == []
    assert buf.count_unsent() == 1


def test_captura_que_falha_nao_deixa_o_guarda_barrar(buf):
    """Gravador mudo devolve (None, None): sem frame, publica."""
    guarda = _GuardaFake(decisao=False)
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, _ts=None: (None, None, None),
        guarda=guarda,
    )

    assert relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao())) is not None
    assert guarda.frames == []


def test_upload_falho_ainda_entrega_o_frame_ao_guarda(buf):
    """R2 fora: o alerta nasce sem imagem, mas o pixel existe e vale julgar."""
    guarda = _GuardaFake(decisao=False)
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, _ts=None: (None, b"frame-bom", "ao_vivo"),
        guarda=guarda,
    )

    assert relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao())) is None
    assert guarda.frames == [b"frame-bom"]


# ── o INSTANTE da evidência (o defeito medido em 09/09/2026) ────────────────
#
# A evidência era o quadro ao vivo capturado no momento de enfileirar — 2,90s
# (mínimo) a 34,3s depois do quadro que gerou a caixa, mediana 4,75s em 200
# alertas da RVB. O dono julgava a foto de outro momento. O que estes testes
# prendem: o relay ENTREGA o instante do evento para a captura, e o payload
# passa a dizer de que instante o quadro é.

def test_captura_recebe_o_instante_da_deteccao(buf):
    """Falha antes do conserto: a captura era chamada só com o camera_id, e o
    instante do evento morria no payload sem ninguém usar."""
    recebidos: list = []

    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, ts: (
            recebidos.append(ts) or ("evidence/k.jpg", b"jpg", "gravador_no_instante")
        ),
    )
    relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao()))

    assert len(recebidos) == 1
    assert recebidos[0] is not None, "instante do evento não chegou na captura"
    assert recebidos[0].isoformat() == "2026-09-09T04:09:01+00:00"


def test_payload_diz_de_que_instante_o_quadro_e(buf):
    """Sem isto não há como provar depois que o Δ melhorou — nem como o
    operador saber que está olhando um momento diferente da caixa."""
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, ts: ("evidence/k.jpg", b"jpg", "gravador_no_instante"),
    )
    relay.handle("det:cam-uuid-1", json.dumps(_payload_com_violacao()))

    (linha,) = buf.dequeue_batch()
    assert linha["payload"]["evidence_origem"] == "gravador_no_instante"
    assert linha["payload"]["evidence_delta_s"] == 0.0


def test_quadro_ao_vivo_registra_o_atraso_real_no_payload(buf):
    """Degradou para o ao vivo -> o payload CONTA isso, com o Δ de verdade.
    Evidência ruim marcada é auditável; evidência ruim disfarçada não é."""
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, ts: ("evidence/k.jpg", b"jpg", "ao_vivo"),
    )
    payload = _payload_com_violacao()
    payload["timestamp"] = (
        datetime.now(timezone.utc) - timedelta(seconds=5)
    ).isoformat()
    relay.handle("det:cam-uuid-1", json.dumps(payload))

    (linha,) = buf.dequeue_batch()
    assert linha["payload"]["evidence_origem"] == "ao_vivo"
    assert 4.0 <= linha["payload"]["evidence_delta_s"] <= 8.0


def test_timestamp_ilegivel_nao_derruba_o_evento(buf):
    """Payload sem hora legível: cai para o ao vivo, mas o alerta sobe."""
    relay = DetectionRelay(
        buf, lambda: _FakePubSub([]),
        evidence_capture=lambda cam, ts: (ts, b"jpg", "ao_vivo"),
    )
    payload = _payload_com_violacao()
    payload["timestamp"] = "ontem de manhã"
    assert relay.handle("det:cam-uuid-1", json.dumps(payload)) is not None
    assert len(buf.dequeue_batch()) == 1

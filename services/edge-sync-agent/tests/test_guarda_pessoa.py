"""GuardaPessoa: cena sem gente não vira alerta — e o erro do guarda fica visível.

O que estes testes protegem, em ordem de importância:
  1. NUNCA silenciar por falha de infra (detector mudo, exceção, frame ausente);
  2. sombra não barra NADA — só marca o payload, que é a prova auditável;
  3. barrar registra o pixel do que silenciou, senão ninguém pode julgar.
"""

import json
from dataclasses import dataclass

import pytest

from app.guarda_pessoa import (
    MODO_BARRAR,
    MODO_OFF,
    MODO_SOMBRA,
    GuardaPessoa,
    build_guarda_pessoa_from_env,
)


@dataclass
class _Resultado:
    found: bool
    undetermined: bool = False
    max_confidence: float = 0.0


class _Detector:
    """Detector de mentira: devolve o que o teste mandar."""

    def __init__(self, resultado):
        self._r = resultado
        self.chamadas = 0

    def detect(self, _frame):
        self.chamadas += 1
        if isinstance(self._r, Exception):
            raise self._r
        return self._r


def _payload(conf=0.9, classe="Sem protetor de ouvido"):
    return {
        "camera_id": "cam-1",
        "has_violation": True,
        "detections": [{"class": classe, "confidence": conf}],
    }


def _guarda(resultado, modo=MODO_BARRAR, **kw):
    return GuardaPessoa(_Detector(resultado), modo=modo, **kw)


# ── o que o dono pediu: cena vazia não vira alerta ──────────────────────────

def test_cena_sem_pessoa_e_barrada_em_modo_barrar(tmp_path):
    g = _guarda(_Resultado(found=False), ring_dir=str(tmp_path))
    assert g.julgar("cam-1", _payload(), b"jpg") is False
    assert g.resumo()["barrados"] == 1


def test_cena_com_pessoa_publica(tmp_path):
    g = _guarda(_Resultado(found=True, max_confidence=0.8), ring_dir=str(tmp_path))
    p = _payload()
    assert g.julgar("cam-1", p, b"jpg") is True
    assert p["guarda_pessoa"]["veredito"] == "com_pessoa"


# ── degradação para o lado seguro: a violação muda é o pior desfecho ────────

def test_detector_indeterminado_publica(tmp_path):
    """ONNX não carregou / erro de inferência: `undetermined` NUNCA é 'não tem
    gente'. Silenciar por falha de infra é o desfecho proibido."""
    g = _guarda(_Resultado(found=False, undetermined=True), ring_dir=str(tmp_path))
    p = _payload()
    assert g.julgar("cam-1", p, b"jpg") is True
    assert p["guarda_pessoa"]["veredito"] == "indeterminado"
    assert g.resumo()["barrados"] == 0


def test_excecao_no_detector_publica(tmp_path):
    g = _guarda(RuntimeError("onnx explodiu"), ring_dir=str(tmp_path))
    assert g.julgar("cam-1", _payload(), b"jpg") is True


def test_anel_ilegivel_nao_impede_a_decisao(tmp_path):
    """Falha ao gravar auditoria não pode virar exceção no caminho do alerta."""
    g = _guarda(_Resultado(found=False), ring_dir="/proc/nao/da/pra/escrever")
    assert g.julgar("cam-1", _payload(), b"jpg") is False


# ── sombra: mede sem barrar; é o padrão até o dono aprovar ──────────────────

def test_sombra_nao_barra_nada_mas_marca_o_payload(tmp_path):
    g = _guarda(_Resultado(found=False, max_confidence=0.02), modo=MODO_SOMBRA,
                ring_dir=str(tmp_path))
    p = _payload()

    assert g.julgar("cam-1", p, b"jpg") is True, "sombra JAMAIS barra"
    assert p["guarda_pessoa"] == {
        "veredito": "sem_pessoa", "confianca": 0.02, "modo": "sombra",
        "ms": p["guarda_pessoa"]["ms"],
    }
    assert g.resumo()["barrados"] == 0
    assert list(tmp_path.iterdir()) == [], "em sombra o frame já está no R2"


# ── como o falso negativo fica VISÍVEL ──────────────────────────────────────

def test_barrado_com_epi_muito_confiante_entra_no_contador_de_desacordo(tmp_path):
    """Contador que sobe quando o guarda ERRA, não quando acerta: fábrica vazia
    produz palpite fraco; 0,93 de certeza numa orelha desprotegida sem ninguém
    na cena é o desacordo com mais chance de ser violação silenciada."""
    g = _guarda(_Resultado(found=False), ring_dir=str(tmp_path))

    g.julgar("cam-1", _payload(conf=0.93), b"jpg")
    g.julgar("cam-1", _payload(conf=0.51), b"jpg")

    r = g.resumo()
    assert r["barrados"] == 2
    assert r["barrados_alta_confianca"] == 1
    assert r["classes_barradas"] == {"Sem protetor de ouvido": 2}


def test_resumo_separa_por_camera_para_denunciar_cegueira(tmp_path):
    """O total global some com o sinal: uma câmera em 100% de `sem_pessoa`
    enquanto outra vê gente é candidata a cegueira do guarda, não a corredor
    vazio."""
    cega = _guarda(_Resultado(found=False), ring_dir=str(tmp_path))
    cega.julgar("cam-cega", _payload(), b"jpg")
    cega.julgar("cam-cega", _payload(), b"jpg")
    cega._detector = _Detector(_Resultado(found=True, max_confidence=0.7))
    cega.julgar("cam-ok", _payload(), b"jpg")

    assert cega.resumo()["por_camera"] == {"cam-cega": [2, 0], "cam-ok": [0, 1]}


def test_frame_barrado_vai_pro_anel_com_o_contexto(tmp_path):
    """Em `barrar` o alerta deixa de existir na nuvem — sem este anel o pixel
    morre dentro da chamada e ninguém pode julgar o que foi silenciado."""
    g = _guarda(_Resultado(found=False, max_confidence=0.04), ring_dir=str(tmp_path))
    p = _payload(conf=0.88)
    p["evidence_r2_key"] = "evidence/cam-1/x.jpg"

    g.julgar("cam-1", p, b"os-bytes-do-frame")

    assert (tmp_path / "000.jpg").read_bytes() == b"os-bytes-do-frame"
    meta = json.loads((tmp_path / "000.json").read_text(encoding="utf-8"))
    assert meta["camera_id"] == "cam-1"
    assert meta["classes_epi"] == ["Sem protetor de ouvido"]
    assert meta["confianca_epi"] == 0.88
    assert meta["score_pessoa"] == 0.04
    assert meta["evidence_r2_key"] == "evidence/cam-1/x.jpg"


def test_anel_e_circular_e_nao_enche_a_ram(tmp_path):
    """/dev/shm é RAM: o anel tem teto e sobrescreve o mais antigo."""
    g = _guarda(_Resultado(found=False), ring_dir=str(tmp_path), ring_max=2)
    for i in range(5):
        g.julgar("cam-1", _payload(), b"frame-%d" % i)

    assert sorted(f.name for f in tmp_path.glob("*.jpg")) == ["000.jpg", "001.jpg"]
    assert (tmp_path / "000.jpg").read_bytes() == b"frame-4"


# ── ligar/desligar sem redeploy ────────────────────────────────────────────

def test_env_off_devolve_none():
    assert build_guarda_pessoa_from_env({"EDGE_GUARDA_PESSOA": "off"}) is None


def test_env_sem_modelo_devolve_none_em_vez_de_guarda_cego():
    """Guarda que nunca opina é só custo — melhor não existir."""
    assert build_guarda_pessoa_from_env(
        {"COLLECTOR_PERSON_MODEL_PATH": "/nao/existe/yolox.onnx"}
    ) is None


@pytest.mark.parametrize("bruto,esperado", [("", (4, 4)), ("2x2", (2, 2)), ("lixo", (4, 4))])
def test_parse_tiles(bruto, esperado):
    from app.guarda_pessoa import _parse_tiles

    assert _parse_tiles(bruto) == esperado


def test_modo_invalido_cai_em_sombra():
    """Erro de digitação no .env não pode ligar `barrar` por acidente — nem
    desligar a medição."""
    assert GuardaPessoa(_Detector(_Resultado(True)), modo="barra").modo == MODO_SOMBRA
    assert MODO_OFF not in (MODO_SOMBRA, MODO_BARRAR)

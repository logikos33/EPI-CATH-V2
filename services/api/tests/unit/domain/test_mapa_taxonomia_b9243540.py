"""Mapa de taxonomia do modelo b9243540 (embarque RVB).

Os dois testes que importam estão marcados com REGRA — eles existem para
quebrar quando alguém "simplificar" o mapa, não para cobrir linha.
"""
import json

import pytest

from app.domain.taxonomia import (
    MODELO_RVB_EPI,
    MapaIncompletoError,
    MapaModelo,
    carregar_mapa,
)

# Taxonomia REAL do modelo, lida do export COCO em
# dataset-exports/.../v17c-partes/train/_annotations.coco.json (medido 08/09):
# 13 categorias, ids 0..12 contíguos. É esta ordem que a cabeça do RF-DETR
# indexa — 13 saídas, confirmado no ONNX (labels [B, Q, 13]).
TAXONOMIA_DO_ONNX = [
    "recognition", "botas", "luva", "mao", "mascara", "mascara_incorreta",
    "oculos", "orelha", "pessoa", "protetor_auricular", "regiao_boca_nariz",
    "regiao_olhos", "rosto",
]


@pytest.fixture
def mapa():
    return carregar_mapa(MODELO_RVB_EPI)


def _det(classe: str, conf: float = 0.9) -> dict:
    return {"class": classe, "confidence": conf, "bbox": [0, 0, 10, 10], "track_id": None}


# ── REGRA 1 — classe interna NUNCA vira evento ────────────────────────────

def test_regra_classe_interna_nunca_vira_evento(mapa):
    """`orelha` e `regiao_boca_nariz` alimentam o pareamento região↔EPI.

    Se um dia virarem evento, o operador do RVB recebe 2.259 caixas de orelha
    por turno (contagem real do split de treino) e a fila de verificação morre
    afogada. Este teste é o que impede isso.
    """
    entrada = [
        _det("orelha"),
        _det("regiao_boca_nariz"),
        _det("mao"),
        _det("regiao_olhos"),
        _det("recognition"),
        _det("pessoa"),
        _det("rosto"),
        _det("mascara_incorreta"),  # esta SIM é evento — a âncora do teste
    ]

    saida = mapa.traduzir(entrada)

    assert [d["class"] for d in saida] == ["Uso incorreto de mascara"]
    # Nenhum nome interno sobreviveu, nem traduzido nem cru.
    assert not ({"orelha", "regiao_boca_nariz", "mao", "regiao_olhos"}
                & {d["class"] for d in saida})


def test_internas_do_mapa_nao_apontam_para_catalogo(mapa):
    """Segunda trava da REGRA 1: interna com destino é evento disfarçado.

    Descartar na tradução não basta — bastaria alguém apontar `orelha` para
    uma classe do catálogo e mudar o papel depois. O construtor recusa a
    combinação, então o mapa não chega a carregar num estado ambíguo.
    """
    internas = [linha for linha in mapa.por_nome.values() if linha["papel"] == "interna"]
    assert len(internas) == 7  # recognition, mao, orelha, pessoa, regiao_*2, rosto
    assert all(linha["catalogo"] is None for linha in internas)


def test_construtor_recusa_interna_com_destino_no_catalogo():
    bruto = {
        "modelo_id": "teste", "onnx_sha256": "x", "onnx_r2_key": "k",
        "entrada_hw": [560, 560], "saidas_do_modelo": 1,
        "classes": [
            {"indice": 0, "modelo": "orelha", "papel": "interna",
             "catalogo": "Protetor auditivo"},
        ],
    }
    with pytest.raises(MapaIncompletoError, match="interna"):
        MapaModelo(bruto)


# ── REGRA 2 — classe sem correspondência falha ALTO ───────────────────────

def test_regra_classe_sem_correspondencia_levanta(mapa):
    """Modelo emitindo classe fora do mapa não pode virar rótulo inventado.

    O modo de falha que isto substitui é silencioso: o nome cru chega ao
    catálogo, não casa com nada, vira "classe indecidida" e o produto lê zero
    alertas como turno limpo (#542).
    """
    with pytest.raises(MapaIncompletoError, match="capacete_novo"):
        mapa.traduzir([_det("capacete_novo")])


def test_regra_evento_sem_destino_no_catalogo_levanta():
    """Mesma regra do outro lado: linha de evento sem `catalogo` é incompleta."""
    bruto = {
        "modelo_id": "teste", "onnx_sha256": "x", "onnx_r2_key": "k",
        "entrada_hw": [560, 560], "saidas_do_modelo": 1,
        "classes": [
            {"indice": 0, "modelo": "mascara_incorreta", "papel": "evento",
             "catalogo": None},
        ],
    }
    with pytest.raises(MapaIncompletoError, match="catálogo"):
        MapaModelo(bruto)


def test_taxonomia_divergente_do_modelo_levanta(mapa):
    """Re-treino com classe nova e mapa velho não pode ser servido."""
    with pytest.raises(MapaIncompletoError, match="não bate"):
        mapa.conferir_contra_o_modelo([*TAXONOMIA_DO_ONNX, "capacete"])


def test_mapa_sem_arquivo_levanta():
    with pytest.raises(FileNotFoundError, match="sem mapa de taxonomia"):
        carregar_mapa("00000000-0000-0000-0000-000000000000")


# ── Coerência com o artefato real ─────────────────────────────────────────

def test_mapa_cobre_exatamente_a_taxonomia_do_onnx(mapa):
    """A ordem é o contrato: índice do mapa == id da categoria COCO do treino."""
    assert mapa.nomes_do_modelo == TAXONOMIA_DO_ONNX
    mapa.conferir_contra_o_modelo(TAXONOMIA_DO_ONNX)


def test_destinos_de_evento_sao_os_esperados(mapa):
    """Os 6 destinos no catálogo, na ordem dos índices do modelo.

    `Luvas` e `Óculos` NÃO existem em yolo_classes do RVB — vêm do catálogo
    GLOBAL (module_classes). A polaridade servida é a união dos dois
    (AlertRepository._nomes_por_polaridade), então casa; criar homônima em
    yolo_classes é que quebraria (ADR-0071).
    """
    assert mapa.nomes_de_evento == [
        "Botas", "Luvas", "mascara", "Uso incorreto de mascara",
        "Óculos", "Protetor auditivo",
    ]


def test_sha256_e_entrada_batem_com_o_onnx_publicado(mapa):
    """Fixa o artefato: o runbook do edge confere este sha antes de converter."""
    assert mapa.onnx_sha256 == (
        "5f54c0e709dda097a0d4def9a613391cf630758b8aacaf6fba25c036fb8e0218"
    )
    assert mapa.entrada_hw == (560, 560)


def test_json_e_valido_e_documenta_o_porque():
    """Todo destino de evento carrega o 'porquê' — o mapa é lido por humano."""
    from app.domain.taxonomia import _DIR

    bruto = json.loads(
        (_DIR / f"mapa_modelo_{MODELO_RVB_EPI[:8]}.json").read_text(encoding="utf-8")
    )
    assert all(linha.get("porque") for linha in bruto["classes"])

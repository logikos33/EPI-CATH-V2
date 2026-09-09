"""A caixa caiu na parte do corpo que a classe exige?

O caso que originou o módulo está no primeiro teste: `Uso incorreto de mascara`
com a caixa na PERNA, que a contenção aprova com 90% e que nenhum cliente aceita
ver num PDF de contrato.

A ordem dos testes é a ordem do risco: primeiro o que esta régua NUNCA pode
fazer (reprovar por dúvida), depois o que ela existe para pegar.
"""

import pytest

from app.anatomia import FAIXAS, plausivel, posicao_na_pessoa

#: Pessoa em pé, inteira no quadro: 15% de largura por 60% de altura.
PESSOA = (0.40, 0.20, 0.55, 0.80)
CABECA = (0.45, 0.22, 0.50, 0.28)      # centro a ~8% da altura
TRONCO = (0.44, 0.44, 0.51, 0.52)      # ~50% — barriga, longe da cabeça
PERNA = (0.45, 0.62, 0.50, 0.70)       # ~76%
PES = (0.44, 0.74, 0.51, 0.79)         # ~92%


# ── o que a régua NUNCA pode fazer: reprovar por dúvida ─────────────────────

def test_classe_fora_do_mapa_e_indeterminada_nunca_reprovada():
    """Classe nova no modelo não pode virar silenciamento automático."""
    veredito, pos = plausivel("classe_que_nao_existe_ainda", CABECA, [PESSOA])
    assert veredito is None
    assert pos is not None, "a posição ainda é medida — só o julgamento falta"


def test_pessoa_achatada_e_indeterminada():
    """Sentada, agachada ou cortada pela borda: a fração vertical perde sentido."""
    sentada = (0.10, 0.50, 0.60, 0.62)          # larga e baixa
    assert plausivel("mascara", (0.30, 0.55, 0.34, 0.58), [sentada])[0] is None


def test_caixa_sem_pessoa_por_perto_e_indeterminada():
    """Quem responde nesse caso é a contenção, não esta régua."""
    assert plausivel("mascara", (0.90, 0.90, 0.95, 0.95), [PESSOA]) == (None, None)


def test_sem_pessoa_nenhuma_e_indeterminada():
    assert plausivel("mascara", CABECA, []) == (None, None)


def test_caixa_ausente_e_indeterminada():
    assert plausivel("mascara", None, [PESSOA]) == (None, None)


# ── o que ela existe para pegar ──────────────────────────────────────────────

def test_mascara_na_perna_e_reprovada():
    """O caso concreto: contenção 90%, e não é violação de máscara nenhuma."""
    veredito, pos = plausivel("Uso incorreto de mascara", PERNA, [PESSOA])
    assert veredito is False
    assert pos > 0.6


def test_mascara_no_rosto_passa():
    veredito, pos = plausivel("Uso incorreto de mascara", CABECA, [PESSOA])
    assert veredito is True
    assert pos < 0.2


def test_protetor_de_ouvido_no_tronco_e_reprovado():
    """Medido: 132 das caixas desta classe caíam fora da cabeça."""
    assert plausivel("Sem protetor de ouvido", TRONCO, [PESSOA])[0] is False


def test_botas_nos_pes_passam_e_na_cabeca_nao():
    assert plausivel("Botas", PES, [PESSOA])[0] is True
    assert plausivel("Botas", CABECA, [PESSOA])[0] is False


def test_luvas_tem_faixa_larga_porque_a_mao_anda():
    """Braço levantado ou mão na bancada: reprovar aqui seria arrogância."""
    assert plausivel("Sem Luvas", TRONCO, [PESSOA])[0] is True
    assert plausivel("Sem Luvas", PERNA, [PESSOA])[0] is True
    assert plausivel("Sem Luvas", CABECA, [PESSOA])[0] is False


# ── escolha da pessoa de referência ─────────────────────────────────────────

def test_usa_a_pessoa_mais_alta_entre_as_que_tocam():
    """Duas pessoas sobrepostas: a de corpo inteiro dá a referência melhor."""
    baixa = (0.42, 0.20, 0.52, 0.34)            # só cabeça/ombros visíveis
    # A caixa toca as duas; pela BAIXA a cabeça estaria a ~40% (reprovaria),
    # pela ALTA está a ~8%.
    veredito, pos = plausivel("Sem protetor de ouvido", CABECA, [baixa, PESSOA])
    assert veredito is True
    assert pos < 0.2


def test_posicao_e_fracao_da_altura_da_pessoa():
    assert posicao_na_pessoa(CABECA, [PESSOA]) == pytest.approx(0.0833, abs=0.01)
    assert posicao_na_pessoa(PES, [PESSOA]) == pytest.approx(0.9417, abs=0.01)


# ── higiene do mapa ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("classe", sorted(FAIXAS))
def test_toda_faixa_e_valida(classe):
    topo, base = FAIXAS[classe]
    assert 0.0 <= topo < base <= 1.0, classe
    assert classe == classe.lower(), "a busca é feita em minúsculas"


def test_as_dez_classes_servidas_estao_no_mapa():
    """Classe servida fora do mapa vira indeterminado silencioso — e aí a régua
    não mede nada justamente onde o volume está."""
    servidas = [
        "Sem protetor de ouvido", "Protetor auditivo", "Uso incorreto de mascara",
        "mascara", "Sem mascara", "Óculos", "Sem Óculos", "Sem Luvas",
        "Botas", "Sem Capacete",
    ]
    faltando = [c for c in servidas if c.strip().lower() not in FAIXAS]
    assert not faltando, faltando

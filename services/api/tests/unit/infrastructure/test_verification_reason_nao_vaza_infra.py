"""`verification_reason` é CAMPO DE TELA — falha de infraestrutura não entra nele.

O bug (medido em 09/09, com o edge da RVB em operação): a triagem por IA,
quando não consegue rodar, gravava o motivo TÉCNICO em
`alerts.verification_reason`. A tela de Verificação mostra essa coluna como
**"Motivo da IA"**, então o operador lia:

    Motivo da IA    API key não configurada

**592 alertas** ficaram com esse texto. Um problema nosso apareceu na cara de
quem opera como se fosse análise do modelo.

Pior: o caminho de exceção genérica montava `f"Erro IA: {exc}"` — mensagem de
exceção pode carregar host, caminho de arquivo ou trecho de payload, e ia
direto para uma coluna que a tela renderiza.

O veredito `needs_human` já diz ao operador tudo que ele precisa (alguém tem de
olhar). O PORQUÊ técnico vive no log.
"""
import json

import pytest

from app.infrastructure.queue.tasks import verification


@pytest.fixture()
def sem_chave(monkeypatch):
    monkeypatch.setattr(verification, "_ANTHROPIC_KEY", "")


def test_sem_api_key_nao_escreve_motivo_de_infra(sem_chave):
    r = verification._call_claude("cam-1", "Sem protetor de ouvido", 0.5, "epi")

    assert r["verdict"] == "needs_human", "sem IA, quem decide é gente"
    # A asserção que mata o bug: nada de infraestrutura no campo de tela.
    assert r["reason"] == ""
    assert "API key" not in r["reason"]


def test_erro_de_parse_nao_escreve_motivo_de_infra(monkeypatch):
    monkeypatch.setattr(verification, "_ANTHROPIC_KEY", "chave-de-teste")

    class _Cliente:
        class messages:  # noqa: N801
            @staticmethod
            def create(**_):
                class _M:
                    content = [type("T", (), {"text": "isto não é json"})()]
                return _M()

    monkeypatch.setitem(
        __import__("sys").modules, "anthropic",
        type("M", (), {"Anthropic": lambda **_: _Cliente()}),
    )
    r = verification._call_claude("cam-1", "Sem Luvas", 0.6, "epi")

    assert r["verdict"] == "needs_human"
    assert r["reason"] == ""
    assert "parsear" not in r["reason"].lower()


def test_excecao_nao_vaza_a_mensagem_do_erro_para_a_tela(monkeypatch):
    """`f"Erro IA: {exc}"` podia carregar host, caminho ou payload."""
    monkeypatch.setattr(verification, "_ANTHROPIC_KEY", "chave-de-teste")
    segredo = "postgres://usuario:senha@host-interno:5432/base"

    def _explode(**_):
        raise RuntimeError(f"falha conectando em {segredo}")

    monkeypatch.setitem(
        __import__("sys").modules, "anthropic",
        type("M", (), {"Anthropic": lambda **_: type("C", (), {
            "messages": type("Ms", (), {"create": staticmethod(_explode)})
        })()}),
    )
    r = verification._call_claude("cam-1", "Sem mascara", 0.7, "epi")

    assert r["verdict"] == "needs_human"
    assert r["reason"] == ""
    assert segredo not in json.dumps(r), "erro cru NUNCA vai para campo de tela"


def test_resposta_valida_da_ia_continua_passando_o_motivo(monkeypatch):
    """O conserto não pode emudecer a IA quando ela REALMENTE respondeu."""
    monkeypatch.setattr(verification, "_ANTHROPIC_KEY", "chave-de-teste")
    corpo = json.dumps({
        "verdict": "reject", "reason": "pessoa de costas, EPI não visível",
        "adjusted_confidence": 0.3,
    })

    monkeypatch.setitem(
        __import__("sys").modules, "anthropic",
        type("M", (), {"Anthropic": lambda **_: type("C", (), {
            "messages": type("Ms", (), {"create": staticmethod(
                lambda **_: type("M2", (), {"content": [type("T", (), {"text": corpo})()]})()
            )})
        })()}),
    )
    r = verification._call_claude("cam-1", "Sem Óculos", 0.3, "epi")

    assert r["verdict"] == "reject"
    assert r["reason"] == "pessoa de costas, EPI não visível"

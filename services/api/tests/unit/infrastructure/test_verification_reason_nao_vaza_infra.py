"""Falha de infraestrutura da triagem não vira conteúdo de tela.

Defeito relatado pelo dono da RVB em 09/09/2026, olhando `/epi/eventos` em
operação: "esse aqui está aparecendo API KEY NÃO CONFIGURADA", em várias
linhas, como texto solto embaixo do selo de veredito.

CADEIA COMPLETA (por que um teste aqui e outro no front):

  1. `_call_claude` devolvia `reason="API key não configurada"` quando faltava
     ANTHROPIC_API_KEY (mesma família: "Erro ao parsear resposta IA",
     "Erro IA: <exc>", "Erro: <exc>").
  2. `_update_alert_verification` grava esse `reason` em
     `alerts.verification_reason`.
  3. `GET /api/alerts` devolve `a.*` — a coluna viaja inteira.
  4. `Eventos.tsx` imprimia o campo embaixo do selo.

Este arquivo trava o passo 1 (a RAIZ: o dado nem chega a ser gravado).
`Eventos.test.tsx` → "erro de infraestrutura não é conteúdo de tela" trava o
passo 4, que é o que salva as centenas de linhas JÁ gravadas no banco da RVB
sem UPDATE em massa.

FALHA ANTES / PASSA DEPOIS: com as três strings de erro de volta em
`_call_claude`, os dois primeiros casos ficam VERMELHOS.
"""
from __future__ import annotations

import json
import sys

_MOD = "app.infrastructure.queue.tasks.verification"

# ⚠️ COLETA COMPARTILHADA — este bloco é uma pinça, não um `import`.
#
# `test_inference_alert_verification.py` planta um MagicMock neste caminho em
# `sys.modules` (na IMPORTAÇÃO do arquivo, não num fixture) para conseguir
# importar `inference` sem celery instalado. Na ordem alfabética da coleção ele
# vem ANTES deste arquivo, então um `from ... import` simples aqui pegaria o
# mock e esta suíte inteira viraria verde-vazia — testaria um MagicMock.
#
# Mas descartar o mock e deixar o módulo real no lugar dele quebra o OUTRO
# arquivo: `_queue_verification_if_low_confidence` faz o import de `verify_alert`
# LAZY, dentro da função, e passaria a resolver a task de verdade em vez do
# `_mock_verify_task` que as asserções de lá espionam (medido: 4 falhas na
# suíte completa, zero rodando os arquivos isolados — a pior forma de quebrar).
#
# Então: tira o mock, carrega o módulo REAL, guarda a referência e DEVOLVE o
# mock a `sys.modules`. Cada arquivo fica com o objeto que precisa e nenhum
# depende da ordem da coleção.
_plantado = sys.modules.get(_MOD)
_e_mock = _plantado is not None and getattr(_plantado, "__file__", None) is None
if _e_mock:
    sys.modules.pop(_MOD, None)

from app.infrastructure.queue.tasks import verification as verification_mod  # noqa: E402

if _e_mock:
    sys.modules[_MOD] = _plantado


def test_sem_api_key_o_motivo_e_nulo_nunca_o_erro(monkeypatch) -> None:
    monkeypatch.setattr(verification_mod, "_ANTHROPIC_KEY", "")

    resultado = verification_mod._call_claude("cam-1", "Sem capacete", 0.62, "epi")

    # O estado continua sendo registrado — nada de informação se perde.
    assert resultado["verdict"] == "needs_human"
    # O texto do erro, não. Ele é assunto de log.
    assert resultado["reason"] is None


def test_qualquer_falha_da_chamada_tambem_cala_o_motivo(monkeypatch) -> None:
    """Não é uma exceção à regra da chave ausente: é a REGRA.

    Corrigir só a frase que o dono viu deixaria "Erro IA: Connection reset by
    peer" pronto para aparecer na tela no próximo soluço de rede.
    """
    monkeypatch.setattr(verification_mod, "_ANTHROPIC_KEY", "sk-de-mentira")

    class _ClienteQuebrado:
        def __init__(self, *_a, **_kw) -> None:
            raise RuntimeError("Connection reset by peer")

    modulo_falso = type(sys)("anthropic")
    modulo_falso.Anthropic = _ClienteQuebrado  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", modulo_falso)

    resultado = verification_mod._call_claude("cam-1", "Sem capacete", 0.62, "epi")

    assert resultado["verdict"] == "needs_human"
    assert resultado["reason"] is None


def test_resposta_ilegivel_da_ia_tambem(monkeypatch) -> None:
    monkeypatch.setattr(verification_mod, "_ANTHROPIC_KEY", "sk-de-mentira")

    class _Bloco:
        text = "isto não é JSON"

    class _Mensagem:
        content = [_Bloco()]

    class _Cliente:
        def __init__(self, *_a, **_kw) -> None:
            self.messages = self

        def create(self, **_kw):  # noqa: ANN003
            return _Mensagem()

    modulo_falso = type(sys)("anthropic")
    modulo_falso.Anthropic = _Cliente  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", modulo_falso)

    resultado = verification_mod._call_claude("cam-1", "Sem capacete", 0.62, "epi")

    assert resultado["reason"] is None


def test_motivo_de_verdade_da_ia_continua_passando(monkeypatch) -> None:
    """O gate é contra ERRO DE INFRAESTRUTURA, não contra a IA ter opinião.

    Se a triagem concluir e explicar por quê, a explicação continua sendo
    gravada — a fila de verificação a mostra sob o rótulo "Motivo da IA", que
    é honesto porque NOMEIA quem escreveu.
    """
    monkeypatch.setattr(verification_mod, "_ANTHROPIC_KEY", "sk-de-mentira")

    class _Bloco:
        text = json.dumps(
            {
                "verdict": "needs_human",
                "reason": "Confiança na faixa ambígua para esta classe",
                "adjusted_confidence": 0.62,
            }
        )

    class _Mensagem:
        content = [_Bloco()]

    class _Cliente:
        def __init__(self, *_a, **_kw) -> None:
            self.messages = self

        def create(self, **_kw):  # noqa: ANN003
            return _Mensagem()

    modulo_falso = type(sys)("anthropic")
    modulo_falso.Anthropic = _Cliente  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", modulo_falso)

    resultado = verification_mod._call_claude("cam-1", "Sem capacete", 0.62, "epi")

    assert resultado["reason"] == "Confiança na faixa ambígua para esta classe"


def test_excecao_nao_leva_o_conteudo_do_erro_para_a_tela(monkeypatch) -> None:
    """O teste acima prova que o motivo fica NULO; este prova o que estava em
    jogo — o CONTEÚDO do erro.

    `f"Erro IA: {exc}"` carrega o que estiver no caminho: host, caminho de
    arquivo, DSN com senha. Ia direto para uma coluna que a tela renderiza.
    A asserção é sobre a propriedade, não sobre a frase.
    """
    monkeypatch.setattr(verification_mod, "_ANTHROPIC_KEY", "sk-de-mentira")
    segredo = "postgres://usuario:senha-secreta@host-interno:5432/base"

    class _ClienteVazado:
        def __init__(self, *_a, **_kw) -> None:
            raise RuntimeError(f"falha conectando em {segredo}")

    modulo_falso = type(sys)("anthropic")
    modulo_falso.Anthropic = _ClienteVazado  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", modulo_falso)

    resultado = verification_mod._call_claude("cam-1", "Sem mascara", 0.7, "epi")

    assert resultado["reason"] is None
    assert segredo not in json.dumps(resultado)
    assert "senha-secreta" not in json.dumps(resultado)

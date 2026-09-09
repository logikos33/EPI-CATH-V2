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
# Outro teste desta suíte (`test_inference_alert_verification.py`) planta um
# MagicMock neste caminho em `sys.modules` para poder importar `inference` sem
# celery. Se ele rodar antes, o `from ... import` abaixo pegaria o mock e este
# arquivo viraria verde-vazio. Módulo sem `__file__` = mock; descarta e recarrega.
_carregado = sys.modules.get(_MOD)
if _carregado is not None and getattr(_carregado, "__file__", None) is None:
    sys.modules.pop(_MOD, None)

from app.infrastructure.queue.tasks import verification as verification_mod  # noqa: E402


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

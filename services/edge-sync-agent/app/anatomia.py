"""A caixa caiu na PARTE DO CORPO que a classe exige?

O caso concreto que originou este módulo (09/09/2026, montando o PDF de evidência
que acompanha o contrato da RVB): uma página mostrava `Uso incorreto de mascara`
com a caixa na **perna** da pessoa. A contenção (ADR-0072 / PR #922) dava **90%** —
a caixa cai sobre o corpo e, nesse sentido, não é alucinação. Mas máscara no
joelho não é violação de máscara; é caixa no lugar errado que a contenção aprova.

QUANTO ISSO PESA, medido sobre os 679 alertas da fila com o quadro reancorado no
instante da detecção — só entre os que **passaram** na contenção (420 caixas):

    plausível        204   (49%)
    lugar errado     175   (42%)   ← o que só esta régua enxerga
    indeterminado     41   ( 9%)

Por classe, `Sem protetor de ouvido` é o caso extremo: **132 caixas fora da
cabeça contra 131 dentro**. Metade das "orelhas desprotegidas" não estava perto
de uma cabeça.

E há uma convergência que dá confiança ao conjunto: contenção + anatomia deixam
**204 de 679** caixas de pé (30%), enquanto a precisão medida contra veredito
humano nos alertas do edge é **33%** (n=51). Dois caminhos independentes — um
automático, outro humano — chegam ao mesmo número.

────────────────────────────────────────────────────────────────────────────────
A RÉGUA, e por que ela é grosseira de propósito
────────────────────────────────────────────────────────────────────────────────
Fração vertical do centro da caixa DENTRO da caixa da pessoa: 0 no topo da
cabeça, 1 nos pés. Não há pose nem keypoints aqui — só a caixa da pessoa que o
`PersonDetector` já devolve. Por isso as faixas são largas: elas existem para
reprovar máscara no joelho, não para arbitrar centímetros.

⚠️ VALE PARA PESSOA EM PÉ, inteira dentro da caixa. Sentada, agachada ou cortada
pela borda do quadro, a fração perde sentido — nesse caso a resposta é
`None` (indeterminado), NUNCA `False`. Indeterminado publica; num produto de
segurança, a violação que ninguém viu é o pior desfecho.

⚠️ CLASSE FORA DO MAPA também é `None`, não `False`. Classe nova aparecendo no
modelo não pode virar silenciamento automático.
"""

from __future__ import annotations

#: classe (minúscula) -> (topo, base) da faixa válida, em fração da altura da
#: pessoa. Os limites são generosos porque a caixa da pessoa costuma incluir
#: cabelo/capacete acima do crânio e sombra abaixo dos pés.
FAIXAS: dict[str, tuple[float, float]] = {
    # ── cabeça: orelha, olhos, boca/nariz ────────────────────────────────────
    "sem protetor de ouvido": (0.00, 0.40),
    "protetor auditivo": (0.00, 0.40),
    "protetor auricular": (0.00, 0.40),
    "orelha": (0.00, 0.40),
    "sem mascara": (0.00, 0.45),
    "sem máscara": (0.00, 0.45),
    "mascara": (0.00, 0.45),
    "máscara": (0.00, 0.45),
    "uso incorreto de mascara": (0.00, 0.45),
    "uso incorreto de máscara": (0.00, 0.45),
    "mascara_incorreta": (0.00, 0.45),
    "regiao_boca_nariz": (0.00, 0.45),
    "sem oculos": (0.00, 0.40),
    "sem óculos": (0.00, 0.40),
    "oculos": (0.00, 0.40),
    "óculos": (0.00, 0.40),
    "regiao_olhos": (0.00, 0.40),
    "rosto": (0.00, 0.45),
    "sem capacete": (0.00, 0.35),
    "capacete": (0.00, 0.35),
    "hardhat": (0.00, 0.35),
    "no-hardhat": (0.00, 0.35),
    # ── mãos: variam muito (braço levantado, mão na bancada) ─────────────────
    "sem luvas": (0.20, 0.95),
    "luvas": (0.20, 0.95),
    "luva": (0.20, 0.95),
    "mao": (0.20, 0.95),
    "mão": (0.20, 0.95),
    # ── pés ──────────────────────────────────────────────────────────────────
    "botas": (0.70, 1.00),
    "sem botas": (0.70, 1.00),
}

#: Abaixo desta razão altura/largura a pessoa está achatada demais (sentada,
#: agachada, cortada pela borda) para a fração vertical significar algo.
_RAZAO_MINIMA = 1.2

_Ret = tuple[float, float, float, float]


def _sobrepoe(a: _Ret, b: _Ret) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def posicao_na_pessoa(caixa: _Ret, pessoas: list[_Ret]) -> float | None:
    """Fração vertical do centro da *caixa* dentro da pessoa que a contém.

    `None` quando nenhuma pessoa toca a caixa (aí quem responde é a contenção,
    não esta régua) ou quando a pessoa está achatada demais.
    """
    candidatas = [p for p in pessoas if _sobrepoe(caixa, p)]
    if not candidatas:
        return None
    # A pessoa MAIS ALTA entre as que tocam: se duas se sobrepõem, a de corpo
    # inteiro é a que dá a referência anatômica melhor.
    p = max(candidatas, key=lambda q: q[3] - q[1])
    altura = p[3] - p[1]
    largura = p[2] - p[0]
    if altura <= 0 or largura <= 0 or (altura / largura) < _RAZAO_MINIMA:
        return None
    return ((caixa[1] + caixa[3]) / 2.0 - p[1]) / altura


def plausivel(
    classe: str, caixa: _Ret | None, pessoas: list[_Ret]
) -> tuple[bool | None, float | None]:
    """(veredito, posição). Veredito `None` = **indeterminado**, não reprovado."""
    if not caixa:
        return None, None
    faixa = FAIXAS.get(str(classe).strip().lower())
    pos = posicao_na_pessoa(caixa, pessoas)
    if faixa is None or pos is None:
        return None, pos
    return faixa[0] <= pos <= faixa[1], pos

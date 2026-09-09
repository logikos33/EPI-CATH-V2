"""Tradução índice/nome do modelo → nome do catálogo do tenant.

POR QUE ISTO EXISTE
-------------------
O ONNX devolve um ÍNDICE. Quem traduz índice→nome é uma lista; quem traduz
nome-do-modelo→nome-do-catálogo não existia. O resultado medido no RVB, com
o modelo `b9243540` e o catálogo de hoje:

    das 13 saídas do modelo, exatamente UMA ('botas') casa por acaso com um
    nome do catálogo. Todo o resto cai em "classe indecidida" — não alerta,
    não conta como conformidade, e some do produto sem uma linha de erro
    (inference.py `_has_violation` só avisa uma vez, em WARNING).

Ou seja: embarcar o modelo sem este mapa produz ZERO alertas e a tela lê isso
como "turno limpo". O mapa é o que faz o embarque significar alguma coisa.

O MAPA É DADO, NÃO CÓDIGO
-------------------------
Fica em `mapa_modelo_<id>.json`, versionado ao lado deste arquivo. Espalhar a
regra em `if nome == "mascara_incorreta"` pelo pipeline é a forma garantida de
ela divergir entre o caminho da nuvem e o do edge.

DUAS COISAS QUE ALGUÉM VAI QUEBRAR PRIMEIRO
-------------------------------------------
1. **Classe interna não é evento.** `mao`, `orelha`, `regiao_boca_nariz`,
   `regiao_olhos` (+ os placeholders `recognition`/`pessoa`/`rosto`) são
   sinais intermediários do pareamento região↔EPI. Elas existem para dizer
   "havia uma boca aqui e a máscara não a cobria". Promovê-las a evento
   entrega ao operador 2.259 caixas de orelha por turno.
2. **Classe sem correspondência não passa em silêncio.** Um modelo re-treinado
   com uma classe nova e o mapa velho falha ALTO no carregamento
   (`MapaIncompletoError`), nunca com um `.get(nome, nome)` que deixa o rótulo
   cru vazar até o alerta do cliente.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Modelo escolhido pelo dono para o embarque RVB (RF-DETR, mAP50 0,677).
MODELO_RVB_EPI = "b9243540-ea14-4949-a870-6b8d6470faab"

PAPEL_EVENTO = "evento"
PAPEL_INTERNA = "interna"
_PAPEIS_VALIDOS = frozenset({PAPEL_EVENTO, PAPEL_INTERNA})

_DIR = Path(__file__).parent


class MapaTaxonomiaError(Exception):
    """Base — qualquer incoerência entre modelo, mapa e catálogo."""


class MapaIncompletoError(MapaTaxonomiaError):
    """Classe do modelo sem linha no mapa (ou vice-versa).

    Deliberadamente uma EXCEÇÃO, não um warning: servir metade da taxonomia é
    pior do que não servir. Metade some do produto sem ninguém perceber; a
    exceção derruba o carregamento e alguém conserta o mapa.
    """


class MapaModelo:
    """Mapa carregado e validado de UM modelo."""

    def __init__(self, bruto: dict[str, Any]) -> None:
        self.modelo_id: str = bruto["modelo_id"]
        self.onnx_sha256: str = bruto["onnx_sha256"]
        self.onnx_r2_key: str = bruto["onnx_r2_key"]
        self.entrada_hw: tuple[int, int] = tuple(bruto["entrada_hw"])  # type: ignore[assignment]
        self._bruto = bruto

        linhas = bruto["classes"]
        esperado = int(bruto["saidas_do_modelo"])
        indices = [int(linha["indice"]) for linha in linhas]
        if indices != list(range(esperado)):
            raise MapaIncompletoError(
                f"{self.modelo_id}: o modelo tem {esperado} saídas, mas o mapa "
                f"cobre os índices {indices}. Índice é posição na cabeça do "
                "modelo — buraco ou ordem trocada renomeia detecção sem erro."
            )

        # nome-do-modelo → linha. É por NOME que os detectores deste repo
        # entregam a detecção (Detector.predict devolve {"class": str}), então
        # é por nome que a tradução tem de funcionar; o índice fica como a
        # verificação de que o dicionário é o do modelo certo.
        self.por_nome: dict[str, dict[str, Any]] = {}
        for linha in linhas:
            papel = linha["papel"]
            if papel not in _PAPEIS_VALIDOS:
                raise MapaIncompletoError(
                    f"{self.modelo_id}: papel {papel!r} inválido em "
                    f"{linha['modelo']!r} — use {sorted(_PAPEIS_VALIDOS)}"
                )
            if papel == PAPEL_EVENTO and not linha.get("catalogo"):
                raise MapaIncompletoError(
                    f"{self.modelo_id}: {linha['modelo']!r} é papel 'evento' "
                    "mas não aponta para classe nenhuma do catálogo. Evento sem "
                    "destino vira 'classe indecidida' e some do produto."
                )
            if papel == PAPEL_INTERNA and linha.get("catalogo"):
                raise MapaIncompletoError(
                    f"{self.modelo_id}: {linha['modelo']!r} é 'interna' mas "
                    f"aponta para {linha['catalogo']!r}. Sinal intermediário "
                    "com destino no catálogo é um alerta esperando para nascer."
                )
            self.por_nome[linha["modelo"]] = linha

    @property
    def nomes_do_modelo(self) -> list[str]:
        """Lista índice→nome-do-modelo — o `class_names` do detector."""
        return [linha["modelo"] for linha in self._bruto["classes"]]

    @property
    def nomes_de_evento(self) -> list[str]:
        """Nomes do CATÁLOGO que este modelo pode produzir (sem internas)."""
        return [
            linha["catalogo"]
            for linha in self._bruto["classes"]
            if linha["papel"] == PAPEL_EVENTO
        ]

    def conferir_contra_o_modelo(self, class_names: list[str]) -> None:
        """Falha ALTO se a taxonomia real do modelo não é a do mapa.

        `class_names` é o que `_taxonomia_do_modelo` extrai do export COCO —
        a fonte de verdade da ordem gravada nos pesos. Se ela e o mapa
        discordam, alguma das duas está velha, e continuar significa traduzir
        índice de um modelo com o dicionário de outro.
        """
        if class_names == self.nomes_do_modelo:
            return
        raise MapaIncompletoError(
            f"{self.modelo_id}: taxonomia do modelo {class_names!r} não bate "
            f"com o mapa {self.nomes_do_modelo!r} — atualize "
            f"mapa_modelo_{self.modelo_id[:8]}.json antes de servir."
        )

    def traduzir(self, deteccoes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Detecções do detector → detecções em nomes do catálogo.

        · papel 'interna' é DESCARTADO — é aqui, e só aqui, que se garante que
          uma orelha nunca chega ao alerta do cliente;
        · papel 'evento' tem o campo "class" reescrito para o nome do catálogo,
          que é o que `AlertRepository._nomes_por_polaridade` sabe casar;
        · nome desconhecido levanta — inalcançável depois de
          `conferir_contra_o_modelo`, e é exatamente por isso que é exceção:
          se acontecer, as detecções vieram de outro modelo.
        """
        saida: list[dict[str, Any]] = []
        for det in deteccoes:
            nome = str(det.get("class", ""))
            linha = self.por_nome.get(nome)
            if linha is None:
                raise MapaIncompletoError(
                    f"{self.modelo_id}: detecção da classe {nome!r}, que não "
                    "está no mapa. Ou o modelo servido não é este, ou o mapa "
                    "está desatualizado — nos dois casos o rótulo entregue ao "
                    "cliente seria inventado."
                )
            if linha["papel"] == PAPEL_INTERNA:
                continue
            saida.append({**det, "class": linha["catalogo"], "class_modelo": nome})
        return saida


@lru_cache(maxsize=8)
def carregar_mapa(modelo_id: str = MODELO_RVB_EPI) -> MapaModelo:
    """Carrega e VALIDA o mapa do modelo. Sem mapa → FileNotFoundError.

    Cacheado por modelo: o arquivo é imutável em runtime (muda por deploy),
    e o carregamento roda no caminho de inferência.
    """
    caminho = _DIR / f"mapa_modelo_{modelo_id[:8]}.json"
    if not caminho.is_file():
        raise FileNotFoundError(
            f"sem mapa de taxonomia para o modelo {modelo_id} em {caminho} — "
            "servir sem mapa entrega nome cru do modelo ao catálogo, que não "
            "casa com nada e vira 'classe indecidida' (zero alertas, em "
            "silêncio)"
        )
    mapa = MapaModelo(json.loads(caminho.read_text(encoding="utf-8")))
    logger.info(
        "mapa_taxonomia_carregado: modelo=%s saidas=%d eventos=%d internas=%d",
        mapa.modelo_id,
        len(mapa.nomes_do_modelo),
        len(mapa.nomes_de_evento),
        len(mapa.nomes_do_modelo) - len(mapa.nomes_de_evento),
    )
    return mapa

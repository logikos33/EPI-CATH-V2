#!/usr/bin/env python3
"""Dump KITTI do DeepStream → `det:{camera_id}` no Redis local do box.

É a peça que faltava entre "a GPU está inferindo" e "o produto vê o evento".
O `DetectionRelay` do edge-sync-agent (services/edge-sync-agent/app/
detection_relay.py) assina `det:*`/`detections:*` e enfileira no buffer
SQLite; o Uploader manda para POST /api/v1/edge/events/ingest; a nuvem
transforma em `alerts`. Nada disso saía do lugar porque ninguém publicava.

POR QUE ARQUIVO E NÃO METADADO DIRETO
--------------------------------------
Medido no box (pandora, 08/09):
  · `pyds` NÃO está instalado (sem probe Python sobre o metadado);
  · o adaptador Redis do DeepStream fala Redis Streams (`XADD`) e o relay
    fala pub/sub (`PSUBSCRIBE`) — já descartado, e `libhiredis` nem existe;
  · `gie-kitti-output-dir` JÁ foi usado no box (dirs `mm/kitti_park`,
    `mm/kitti_qmain`, 13.314 arquivos) e o formato traz classe, caixa e
    CONFIANÇA (última coluna).
Então: sem código nativo novo, sem sudo, sem dependência nova.

# ponytail: IPC por arquivo. Teto = taxa de escrita/leitura de arquivinhos
# (mitigado com --kitti-dir em /dev/shm e apagando após ler). Caminho de
# upgrade quando algum destes existir: probe pyds, ou msgbroker com
# libhiredis + relay lendo Streams.

CONTRATO DA MENSAGEM (extraído do detection_relay.py + do escritor único
`alerta_de_evento_do_edge` em services/api/.../tasks/inference.py)
------------------------------------------------------------------------
  {"camera_id": "<uuid de public.cameras>",   # relay usa isto, ou o sufixo
   "timestamp": "<ISO-8601 UTC>",             # instante de captura; é a
                                              #   chave de idempotência do
                                              #   alerta na nuvem
   "detections": [{"class": "<NOME DO CATÁLOGO>",
                   "confidence": 0.87,
                   "bbox": [x, y, w, h],
                   "bbox_unidade": "pixels_xywh_streammux",
                   "frame_wh": [1280, 720],
                   "class_modelo": "<nome cru do modelo>"}],
   "has_violation": true}                     # FALSO = o relay DESCARTA

Três detalhes que fazem o evento sumir em silêncio se errados:
  1. `camera_id` tem de ser o UUID de `public.cameras` DO TENANT do device —
     a nuvem faz `get_by_id_and_tenant` e descarta o resto (C-01).
  2. `class` tem de ser o nome do CATÁLOGO. O filtro de escopo da nuvem
     (`_filtrar_por_escopo`) compara string exata; o nome cru do modelo
     ("protetor_auricular") não casa com nada e o turno lê como limpo. Por
     isso a tradução usa o MESMO mapa JSON do caminho da nuvem
     (services/api/app/domain/taxonomia/mapa_modelo_*.json) — e classes de
     papel "interna" (orelha, mão, região) são DESCARTADAS aqui, nunca viram
     evento.
  3. `has_violation` falso = o relay nem enfileira. Ver `--classes-violacao`.

O MODELO É PARÂMETRO: troque `--mapa-taxonomia` quando o duelo terminar.
"""
from __future__ import annotations

from collections import Counter
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("publicar_deteccoes")

# <gie-id>_<source-id>_<frame>.txt — conferido nos dumps do box.
_NOME_KITTI = re.compile(r"^\d+_(\d+)_(\d+)\.txt$")

_UNIDADE_BBOX = "pixels_xywh_streammux"


def parse_kitti(texto: str) -> list[dict]:
    """Linhas KITTI do DeepStream → detecções.

    Formato: `<classe> 0.0 0 0.0 <x1> <y1> <x2> <y2> 0.0×7 <confiança>`.
    Linha curta/ruim é ignorada: o dump pode ser lido no meio da escrita, e
    derrubar o loop por causa de uma linha é pior do que perder um frame.
    """
    saida: list[dict] = []
    for linha in texto.splitlines():
        campos = linha.split()
        if len(campos) < 16:
            continue
        try:
            x1, y1, x2, y2 = (float(v) for v in campos[4:8])
            confianca = float(campos[15])
        except ValueError:
            continue
        saida.append({
            "class": campos[0],
            "confidence": round(confianca, 4),
            "bbox": [round(x1, 1), round(y1, 1), round(x2 - x1, 1), round(y2 - y1, 1)],
            "bbox_unidade": _UNIDADE_BBOX,
        })
    return saida


class Taxonomia:
    """Nome do modelo → nome do catálogo, a partir do mesmo JSON da nuvem.

    Diferença deliberada de `app.domain.taxonomia.MapaModelo`: lá, classe
    fora do mapa LEVANTA (servir metade da taxonomia é pior que não servir).
    Aqui, no box, levantar mataria o loop de publicação inteiro por causa de
    um rótulo — descarta, avisa UMA vez por nome, e segue.
    """

    def __init__(self, bruto: dict | None) -> None:
        self._por_nome: dict[str, dict] = {}
        self.violacao_padrao: set[str] = set()
        self.modelo_id = ""
        if not bruto:
            return
        self.modelo_id = str(bruto.get("modelo_id") or "")
        for linha in bruto.get("classes", []):
            self._por_nome[str(linha["modelo"])] = linha
            if linha.get("papel") == "evento" and linha.get("polaridade_hoje") is True:
                self.violacao_padrao.add(str(linha["catalogo"]).lower())
        self._avisadas: set[str] = set()

    @property
    def ativa(self) -> bool:
        return bool(self._por_nome)

    def traduzir(self, deteccoes: list[dict]) -> list[dict]:
        if not self.ativa:
            return deteccoes
        saida = []
        for det in deteccoes:
            nome = str(det.get("class", ""))
            linha = self._por_nome.get(nome)
            if linha is None:
                if nome not in self._avisadas:
                    self._avisadas.add(nome)
                    logger.warning(
                        "classe_fora_do_mapa: %r não está em %s — descartada. Ou o "
                        "engine servido não é o do mapa, ou o labelfile divergiu.",
                        nome, self.modelo_id or "(mapa)",
                    )
                continue
            if linha.get("papel") == "interna":
                continue  # orelha/mão/região: sinal intermediário, nunca evento
            saida.append({**det, "class": linha["catalogo"], "class_modelo": nome})
        return saida


class Publicador:
    """Varre o dump, publica e apaga. Sem estado em disco além do dump."""

    def __init__(
        self,
        fontes: dict[str, str],
        taxonomia: Taxonomia,
        violacao: set[str] | None,
        cooldown_s: float,
        confianca_min: float,
        mux_wh: tuple[int, int],
        piso_entre_s: float = 10.0,
    ) -> None:
        self._fontes = fontes
        self._taxonomia = taxonomia
        # None = "*" (qualquer detecção conta) — modo de prova de ponta a ponta.
        self._violacao = violacao
        self._cooldown_s = cooldown_s
        self._confianca_min = confianca_min
        self._mux_wh = list(mux_wh)
        # Janela de cena por câmera: (instante em que a janela abriu, MÁXIMO já
        # publicado por classe dentro dela). Ver `_deve_publicar`.
        self._janela: dict[str, tuple[float, Counter]] = {}
        # Cooldown específico da câmera (o front governa); cai no global quando
        # a câmera não tem valor próprio.
        self._cooldown_por_camera: dict[str, float] = {}
        #: Instante da última publicação por câmera — o piso que nem o
        #: crescimento fura. Distinto da janela: a janela decide "é cena nova?",
        #: o piso decide "já não faz pouquíssimo tempo?".
        self._ultima_publicacao: dict[str, float] = {}
        self._piso_entre_s = piso_entre_s
        self.contagem: Counter = Counter()

    def cooldown_de(self, camera_id: str) -> float:
        """Cooldown desta câmera. O front governa quantos alertas ela gera."""
        return self._cooldown_por_camera.get(camera_id, self._cooldown_s)

    def _deve_publicar(self, camera_id: str, deteccoes: list[dict], instante: float) -> bool:
        """Cena estável não republica; cena que CRESCE republica na hora.

        O pedido do dono: "a pessoa vai entrar vai ficar no quadro por um bom
        tempo e se foi gerado um reconhecimento com aquele cenário ela só
        precisa validar depois de um tempo novamente se aquele cenário não
        alterou, óbvio que se entrou mais pessoas na cena ela vai precisar
        funcionar".

        Por que MÁXIMO por classe, e não "a assinatura mudou": o recall
        agregado do detector servido é 0,57 (ADR-0067). Com dois tipos de
        violação em cena, a sequência real é
        `{sem luvas} → {sem luvas, sem óculos} → {sem luvas}` por **falha de
        detecção**, não por mudança de cena. Comparar com a última assinatura
        faz cada oscilação virar evento — o dedup ficaria PIOR que o cooldown
        cego. Guardando o máximo já publicado na janela, encolher nunca
        publica e crescer publica na hora, que é exatamente a assimetria
        pedida: mais gente é notícia, menos gente não é.
        """
        # ⚠️ SÓ CLASSE DE VIOLAÇÃO ENTRA NA ASSINATURA.
        #
        # Medido em produção na RVB (09/09): contando TODAS as detecções, uma
        # pessoa a mais USANDO EPI corretamente furava o cooldown e republicava
        # a violação antiga. Log real da câmera 841ceaef:
        #
        #   08:49:56  ['Sem protetor de ouvido', 'Protetor auditivo']       n=2
        #   08:49:58  ['Sem protetor de ouvido', 'Protetor auditivo' x2]    n=3  ← publicou
        #
        # A violação não mudou — cresceu a CONFORMIDADE. E `Protetor auditivo`
        # é 88% de tudo que o modelo emite, com contagem oscilando a cada
        # quadro: sozinho, isso explicava a enxurrada que o dono viu.
        #
        # Conformidade não vira alerta (a nuvem a descarta na polaridade), logo
        # não pode decidir se um alerta nasce.
        contagem = Counter(
            str(d.get("class", "")) for d in deteccoes
            if self._violacao is None or str(d.get("class", "")).lower() in self._violacao
        )
        abertura, maximo = self._janela.get(camera_id, (0.0, Counter()))
        if instante - abertura >= self.cooldown_de(camera_id):
            self._janela[camera_id] = (instante, contagem)   # janela nova
            self._ultima_publicacao[camera_id] = instante
            return True
        # ⚠️ PISO ENTRE PUBLICAÇÕES, mesmo quando cresce.
        #
        # "Entrou mais gente" é notícia — mas o detector oscila, e três
        # crescimentos no MESMO segundo são o detector se estabilizando, não
        # gente chegando. Sem piso, uma situação só virava três alertas
        # (medido: 11:47:30 três vezes na mesma câmera e classe).
        if instante - self._ultima_publicacao.get(camera_id, 0.0) < self._piso_entre_s:
            return False
        if any(n > maximo.get(c, 0) for c, n in contagem.items()):
            # Monotônico: o máximo nunca encolhe dentro da janela.
            self._janela[camera_id] = (abertura, Counter(
                {c: max(n, maximo.get(c, 0)) for c in set(maximo) | set(contagem)
                 for n in [contagem.get(c, 0)]}))
            self._ultima_publicacao[camera_id] = instante
            return True
        return False

    def tem_violacao(self, deteccoes: list[dict]) -> bool:
        if not deteccoes:
            return False
        if self._violacao is None:
            return True
        return any(str(d.get("class", "")).lower() in self._violacao for d in deteccoes)

    def montar(self, camera_id: str, deteccoes: list[dict], instante: float) -> dict:
        return {
            "camera_id": camera_id,
            "timestamp": datetime.fromtimestamp(instante, tz=timezone.utc).isoformat(),
            "detections": [{**d, "frame_wh": self._mux_wh} for d in deteccoes],
            "has_violation": True,
            "source": "deepstream-kitti",
        }

    def processar_arquivo(self, caminho: Path) -> dict | None:
        """Um arquivo do dump → payload a publicar, ou None (nada a dizer).

        A ORDEM MUDOU e é o conserto principal: antes o cooldown era checado
        **antes** de ler o arquivo, então "entrou mais gente na cena" não
        disparava nada — a câmera ficava muda pela janela inteira, por mais que
        a cena mudasse. Agora lê, traduz e só então decide.

        SOBRE APAGAR: o dump é buffer, não acervo, e deixá-lo crescer é o
        intertravamento por disco cheio. Então continua apagando sempre — mas
        o que antes sumia em silêncio agora é CONTADO e o nome anômalo vai
        para o log. A evidência que faltava para diagnosticar divergência
        entre config e mapa era o contador e o nome, não o arquivo.
        """
        anomalia = None
        try:
            m = _NOME_KITTI.match(caminho.name)
            if not m:
                self.contagem["nome_invalido"] += 1
                anomalia = f"nome fora do padrão: {caminho.name}"
                return None
            camera_id = self._fontes.get(str(int(m.group(1))))
            if camera_id is None:
                # config do DeepStream e mapa de fontes divergiram: a fonte
                # existe no pipeline e não existe no mapa.
                self.contagem["fonte_desconhecida"] += 1
                anomalia = f"fonte {m.group(1)} não está no mapa de fontes"
                return None
            instante = caminho.stat().st_mtime
            self.contagem["lidas"] += 1

            brutas = parse_kitti(caminho.read_text(encoding="utf-8", errors="replace"))
            self.contagem["deteccoes_brutas"] += len(brutas)
            deteccoes = [d for d in brutas if d["confidence"] >= self._confianca_min]
            self.contagem["barradas_confianca"] += len(brutas) - len(deteccoes)
            deteccoes = self._taxonomia.traduzir(deteccoes)
            for d in deteccoes:
                self.contagem[f"classe:{d.get('class')}"] += 1

            if not self.tem_violacao(deteccoes):
                self.contagem["barradas_polaridade"] += 1
                return None
            if not self._deve_publicar(camera_id, deteccoes, instante):
                self.contagem["barradas_cooldown"] += 1
                return None
            self.contagem["publicadas"] += 1
            return self.montar(camera_id, deteccoes, instante)
        except OSError:
            self.contagem["erro_io"] += 1
            return None
        finally:
            if anomalia:
                logger.warning("dump_anomalo %s", anomalia)
            try:
                caminho.unlink()
            except OSError:
                pass

    def tick(self, kitti_dir: Path, idade_minima_s: float) -> list[dict]:
        """Um ciclo. Só toca arquivo já 'assentado' (mtime velho o bastante),
        senão leríamos um dump no meio da escrita do DeepStream."""
        limite = time.time() - idade_minima_s
        payloads = []
        for caminho in sorted(kitti_dir.glob("*.txt")):
            try:
                if caminho.stat().st_mtime > limite:
                    continue
            except OSError:
                continue
            payload = self.processar_arquivo(caminho)
            if payload:
                payloads.append(payload)
        return payloads


def autoteste() -> int:
    """Checagem mínima do que pode quebrar em silêncio: parse, tradução e
    polaridade. Roda sem Redis, sem GPU e sem box."""
    linha = (
        "protetor_auricular 0.0 0 0.0 661.99 757.03 744.88 972.03 "
        "0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.570810"
    )
    dets = parse_kitti(linha + "\nlinha ruim\n")
    assert len(dets) == 1, dets
    assert dets[0]["class"] == "protetor_auricular"
    assert abs(dets[0]["confidence"] - 0.5708) < 1e-6
    assert dets[0]["bbox"] == [662.0, 757.0, 82.9, 215.0], dets[0]["bbox"]

    mapa = {
        "modelo_id": "teste",
        "classes": [
            {"indice": 0, "modelo": "orelha", "papel": "interna", "catalogo": None},
            {"indice": 1, "modelo": "protetor_auricular", "papel": "evento",
             "catalogo": "Protetor auditivo", "polaridade_hoje": False},
            {"indice": 2, "modelo": "sem_protetor", "papel": "evento",
             "catalogo": "Sem protetor de ouvido", "polaridade_hoje": True},
        ],
    }
    tax = Taxonomia(mapa)
    assert tax.violacao_padrao == {"sem protetor de ouvido"}
    traduzidas = tax.traduzir(
        dets + [{"class": "orelha", "confidence": 0.9}, {"class": "inventada", "confidence": 0.9}]
    )
    assert [d["class"] for d in traduzidas] == ["Protetor auditivo"], traduzidas
    assert traduzidas[0]["class_modelo"] == "protetor_auricular"

    # Polaridade: conformidade NÃO alerta; a classe de ausência alerta.
    pub = Publicador({"0": "cam-uuid"}, tax, tax.violacao_padrao, 0.0, 0.0, (1280, 720))
    assert pub.tem_violacao(traduzidas) is False
    assert pub.tem_violacao([{"class": "Sem protetor de ouvido"}]) is True
    # "*" (violacao=None): qualquer detecção passa — é o modo de prova do cano.
    assert Publicador({}, tax, None, 0.0, 0.0, (1280, 720)).tem_violacao(traduzidas) is True

    payload = pub.montar("cam-uuid", traduzidas, 1757370000.0)
    assert payload["has_violation"] is True
    assert payload["timestamp"].endswith("+00:00")
    assert payload["detections"][0]["frame_wh"] == [1280, 720]
    assert payload["detections"][0]["bbox_unidade"] == _UNIDADE_BBOX

    # ---- dedup de cena: o que o dono pediu, e os dois defeitos medidos ----
    d = lambda *cs: [{"class": c} for c in cs]
    VIOL = {"sem protetor de ouvido", "sem luvas"}
    SP, SL, PA = "Sem protetor de ouvido", "Sem Luvas", "Protetor auditivo"
    novo_pub = lambda: Publicador({"0": "cam"}, tax, set(VIOL), 30.0, 0.0, (1280, 720), piso_entre_s=10.0)

    # 1) Cena PARADA não republica. Pessoa 10 min no quadro = 1 evento.
    p1 = novo_pub()
    assert p1._deve_publicar("cam", d(SP), 1000.0) is True
    for t in range(1001, 1030):
        assert p1._deve_publicar("cam", d(SP), float(t)) is False, t

    # 2) DEFEITO MEDIDO NA RVB: crescer a CONFORMIDADE não pode republicar.
    #    Log real da câmera 841ceaef: a violação não mudou, cresceu
    #    `Protetor auditivo` — e o cooldown foi furado. Conformidade não vira
    #    alerta, logo não decide se um alerta nasce.
    p2 = novo_pub()
    assert p2._deve_publicar("cam", d(SP, PA), 2000.0) is True
    assert p2._deve_publicar("cam", d(SP, PA, PA), 2015.0) is False, "conformidade não é notícia"
    assert p2._deve_publicar("cam", d(SP, PA, PA, PA), 2025.0) is False

    # 3) Violação que CRESCE ainda é notícia — "entrou mais gente".
    p3 = novo_pub()
    assert p3._deve_publicar("cam", d(SP), 3000.0) is True
    assert p3._deve_publicar("cam", d(SP, SP), 3015.0) is True

    # 4) DEFEITO MEDIDO: três crescimentos no MESMO segundo eram três alertas.
    #    Isso é o detector se estabilizando, não gente chegando. O piso segura.
    p4 = novo_pub()
    assert p4._deve_publicar("cam", d(SP), 4000.0) is True
    assert p4._deve_publicar("cam", d(SP, SP), 4000.5) is False, "piso segura a oscilação"
    assert p4._deve_publicar("cam", d(SP, SP, SP), 4001.0) is False
    # passado o piso, crescimento real volta a valer
    assert p4._deve_publicar("cam", d(SP, SP, SP), 4011.0) is True

    # 5) ENCOLHER nunca republica.
    p5 = novo_pub()
    assert p5._deve_publicar("cam", d(SP, SP), 5000.0) is True
    assert p5._deve_publicar("cam", d(SP), 5015.0) is False

    # 6) A oscilação por FALHA de detecção (recall 0,57) não vira evento:
    #    {SL} → {SL,SP} → {SL} → {SL,SP} tem UM crescimento, não três.
    p6 = novo_pub()
    assert p6._deve_publicar("cam", d(SL), 6000.0) is True
    assert p6._deve_publicar("cam", d(SL, SP), 6015.0) is True    # cresceu: notícia
    assert p6._deve_publicar("cam", d(SL), 6026.0) is False       # oscilou
    assert p6._deve_publicar("cam", d(SL, SP), 6029.0) is False   # já foi contada
    # (aos 6031 a janela de 30s expira e abrir uma nova É o certo — não é bug)

    # 7) Passado o cooldown, janela nova: volta a publicar mesmo igual.
    p7 = novo_pub()
    assert p7._deve_publicar("cam", d(SP), 7000.0) is True
    assert p7._deve_publicar("cam", d(SP), 7031.0) is True

    # 8) Cooldown por câmera — é como o front governa a cadência.
    p8 = novo_pub()
    p8._cooldown_por_camera["cam"] = 5.0
    assert p8.cooldown_de("cam") == 5.0
    assert p8.cooldown_de("outra") == 30.0

    print("autoteste OK")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--autoteste", action="store_true", help="roda as asserções e sai")
    p.add_argument("--kitti-dir", default="/dev/shm/recognition-kitti")
    p.add_argument("--fontes", default="~/.config/recognition/deepstream/fontes_epi.json",
                   help="mapa indice-da-fonte -> camera_id (gerado por gerar_config_deepstream.py)")
    p.add_argument("--mapa-taxonomia", default="",
                   help="mapa_modelo_<id>.json. Vazio = publica o nome cru do modelo "
                        "(quase sempre errado: a nuvem compara com o nome do catálogo)")
    p.add_argument("--redis-url", default=os.environ.get("EDGE_REDIS_URL", "redis://127.0.0.1:6379/0"))
    # Default por env para que o override caiba numa linha do
    # edge-sync-agent.env (que a unit já lê) em vez de exigir um drop-in do
    # systemd — é a alavanca que se liga para provar o cano e se desliga depois.
    p.add_argument("--classes-violacao", default=os.environ.get("EDGE_CLASSES_VIOLACAO", ""),
                   help="csv de nomes do CATÁLOGO que contam como violação; '*' = qualquer "
                        "detecção (só para provar o cano). Vazio = as de polaridade true no mapa")
    p.add_argument("--confianca-min", type=float, default=0.5)
    p.add_argument("--cooldown-s", type=float, default=30.0,
                   help="mínimo entre dois eventos da MESMA câmera (default 30s)")
    p.add_argument("--cooldowns", default="~/.config/recognition/cooldown_por_camera.json",
                   help="JSON {camera_id: segundos} — é por aqui que o FRONT governa quantos "
                        "alertas cada câmera gera. Relido por mtime, sem reiniciar o processo. "
                        "Ausente = todas usam --cooldown-s")
    p.add_argument("--resumo-s", type=float, default=300.0,
                   help="cadência do log de censo (contadores). 0 desliga")
    p.add_argument("--intervalo-s", type=float, default=2.0, help="cadência da varredura")
    p.add_argument("--idade-minima-s", type=float, default=1.0,
                   help="só lê dump com mtime mais velho que isto (evita leitura parcial)")
    p.add_argument("--mux", default="1280x720", help="resolução do streammux (vai no payload)")
    p.add_argument("--seco", action="store_true", help="não publica; só loga (validação)")
    args = p.parse_args()

    if args.autoteste:
        return autoteste()

    fontes_path = Path(os.path.expanduser(args.fontes))
    if not fontes_path.is_file():
        logger.error("mapa de fontes ausente: %s — rode gerar_config_deepstream.py antes", fontes_path)
        return 2
    fontes = {str(k): str(v) for k, v in json.loads(fontes_path.read_text(encoding="utf-8")).items()}

    bruto = None
    if args.mapa_taxonomia:
        caminho = Path(os.path.expanduser(args.mapa_taxonomia))
        if not caminho.is_file():
            logger.error("mapa de taxonomia ausente: %s", caminho)
            return 2
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    else:
        logger.warning(
            "sem --mapa-taxonomia: o nome CRU do modelo vai para a nuvem e o filtro "
            "de escopo compara string exata — o esperado é zero alerta."
        )
    taxonomia = Taxonomia(bruto)

    bruta = args.classes_violacao.strip()
    if bruta == "*":
        violacao: set[str] | None = None
    elif bruta:
        violacao = {c.strip().lower() for c in bruta.split(",") if c.strip()}
    else:
        violacao = set(taxonomia.violacao_padrao)
    if violacao is not None and not violacao:
        logger.warning(
            "NENHUMA classe conta como violação (mapa sem polaridade_hoje=true e "
            "--classes-violacao vazio). O relay descarta tudo que não tem "
            "has_violation, então este processo vai rodar SEM publicar nada. "
            "Isso é decisão de catálogo (yolo_classes.is_violation), não bug."
        )

    kitti_dir = Path(os.path.expanduser(args.kitti_dir))
    kitti_dir.mkdir(parents=True, exist_ok=True)
    largura, altura = (int(x) for x in args.mux.lower().split("x"))

    cliente = None
    if not args.seco:
        import redis  # noqa: PLC0415 — só é necessário quando publica de verdade

        cliente = redis.Redis.from_url(args.redis_url)
        cliente.ping()  # falha ALTO no boot: sem Redis não há relay, e o
        #                 systemd reinicia até o Redis subir.

    pub = Publicador(fontes, taxonomia, violacao, args.cooldown_s, args.confianca_min, (largura, altura))
    logger.info(
        "publicador_iniciado fontes=%d kitti=%s violacao=%s taxonomia=%s seco=%s",
        len(fontes), kitti_dir, "*" if violacao is None else sorted(violacao),
        taxonomia.modelo_id or "(nenhuma)", args.seco,
    )

    cooldowns_path = Path(os.path.expanduser(args.cooldowns))
    mtime_cooldowns = 0.0

    def recarregar_cooldowns() -> None:
        """Relê o mapa quando o arquivo muda — mudar na tela muda a cadência
        SEM reiniciar o processo (reiniciar o DeepStream reconecta 17 fontes
        RTSP contra um gravador com anti-brute-force; aqui não custa nada)."""
        nonlocal mtime_cooldowns
        try:
            m = cooldowns_path.stat().st_mtime
        except OSError:
            return
        if m == mtime_cooldowns:
            return
        try:
            dados = json.loads(cooldowns_path.read_text(encoding="utf-8"))
            pub._cooldown_por_camera = {str(k): float(v) for k, v in dados.items()}
            mtime_cooldowns = m
            logger.info("cooldowns_recarregados n=%d", len(pub._cooldown_por_camera))
        except (OSError, ValueError, TypeError) as e:
            # Arquivo pela metade (escrita concorrente) ou valor inválido: fica
            # com o anterior. Nunca derruba o publicador por causa de config.
            logger.warning("cooldowns_invalidos %s: %s", cooldowns_path, e)

    publicados = 0
    proximo_resumo = time.time() + args.resumo_s
    while True:
        recarregar_cooldowns()
        for payload in pub.tick(kitti_dir, args.idade_minima_s):
            canal = f"det:{payload['camera_id']}"
            if cliente is not None:
                cliente.publish(canal, json.dumps(payload))
            publicados += 1
            logger.info(
                "deteccao_publicada canal=%s classes=%s n=%d total=%d",
                canal, [d["class"] for d in payload["detections"]],
                len(payload["detections"]), publicados,
            )
        if args.resumo_s and time.time() >= proximo_resumo:
            # CENSO, não amostra dos sobreviventes. Sem isto não dá para
            # distinguir "o modelo está mudo" de "o modelo fala e o filtro
            # barra tudo" — que foi exatamente a dúvida de 09/09, quando 4.829
            # detecções noturnas não chegaram à nuvem e ninguém sabia por quê.
            logger.info("publicador_resumo %s", dict(sorted(pub.contagem.items())))
            pub.contagem.clear()
            proximo_resumo = time.time() + args.resumo_s
        time.sleep(args.intervalo_s)


if __name__ == "__main__":
    sys.exit(main())

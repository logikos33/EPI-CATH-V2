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
    ) -> None:
        self._fontes = fontes
        self._taxonomia = taxonomia
        # None = "*" (qualquer detecção conta) — modo de prova de ponta a ponta.
        self._violacao = violacao
        self._cooldown_s = cooldown_s
        self._confianca_min = confianca_min
        self._mux_wh = list(mux_wh)
        self._ultimo_por_camera: dict[str, float] = {}

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

        SEMPRE apaga o arquivo: o dump é buffer, não acervo. Deixar crescer é
        o intertravamento por disco cheio que o CLAUDE.md descreve.
        """
        m = _NOME_KITTI.match(caminho.name)
        try:
            if not m:
                return None
            camera_id = self._fontes.get(str(int(m.group(1))))
            if camera_id is None:
                return None  # fonte que não está no mapa: config e mapa divergiram
            instante = caminho.stat().st_mtime
            anterior = self._ultimo_por_camera.get(camera_id, 0.0)
            if instante - anterior < self._cooldown_s:
                return None
            deteccoes = [
                d for d in parse_kitti(caminho.read_text(encoding="utf-8", errors="replace"))
                if d["confidence"] >= self._confianca_min
            ]
            deteccoes = self._taxonomia.traduzir(deteccoes)
            if not self.tem_violacao(deteccoes):
                return None
            self._ultimo_por_camera[camera_id] = instante
            return self.montar(camera_id, deteccoes, instante)
        except OSError:
            return None
        finally:
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

    publicados = 0
    while True:
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
        time.sleep(args.intervalo_s)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Pacote de embarque do modelo b9243540 no Jetson Orin NX da RVB.

⛔ NÃO roda sozinho em lugar nenhum. É script de operação, executado à mão por
   um humano no box, passo a passo, com o runbook `embarque_b9243540.md` ao
   lado. Nada aqui habilita serviço, escreve em systemd ou mexe em câmera.

Por que um script e não uma lista de comandos no runbook: três dos artefatos
(labelfile, config do nvinfer, referência da fumaça) TÊM de sair do MESMO mapa
de taxonomia que o caminho da nuvem usa. Labelfile digitado à mão é como o
rótulo do índice 7 vira "orelha" num lado e "Person" no outro — e o box já tem
um `rfdetr_labels.txt` de um dataset Roboflow ANTIGO, com 10 nomes em inglês,
esperando exatamente esse acidente.

Subcomandos:
  baixar     — traz o ONNX do R2 e CONFERE o sha256 do mapa (falha se divergir)
  artefatos  — gera labelfile + config do nvinfer a partir do mapa
  engine     — converte ONNX → TensorRT FP16 com o trtexec da plataforma
  fumaca     — roda o engine sobre frames conhecidos e grava a saída em JSON
  comparar   — confronta a saída do box com a da nuvem no MESMO frame

Uso típico (ver runbook para o passo a passo com as autorizações):
  python3 embarque_b9243540.py baixar     --destino ~/recognition/models
  python3 embarque_b9243540.py artefatos  --destino ~/recognition/models
  python3 embarque_b9243540.py engine     --destino ~/recognition/models
  python3 embarque_b9243540.py fumaca     --destino ~/recognition/models \
                                          --frames ~/embarque/frames --saida edge.json
  python3 embarque_b9243540.py comparar   --edge edge.json --nuvem nuvem.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# O mapa é a fonte única. Caminho relativo ao repo — o script vive em
# deployments/edge/, o mapa em services/api/app/domain/taxonomia/.
MAPA = (
    Path(__file__).resolve().parents[2]
    / "services/api/app/domain/taxonomia/mapa_modelo_b9243540.json"
)

# Plataforma CONGELADA (docs/edge/REGRAS_PLATAFORMA_JETSON.md §8):
# JP6.2 / L4T r36.4.3 / CUDA 12.6 / TRT 10.3 / DeepStream 7.1.
TRTEXEC = "/usr/src/tensorrt/bin/trtexec"

# Parser custom de RF-DETR já compilado no box (campanha task-105).
# ⚠️ Ver o runbook §Bloqueio P-1: este parser faz argmax por query.
PARSER_SO = "/home/pandora/jetson-experiments/rfdetr-parser/libnvdsparsebbox_rfdetr.so"

# Pré-processamento ImageNet do RF-DETR, na forma que o nvinfer aceita:
#   saida = net-scale-factor * (pixel - offset)
# A nuvem faz (pixel/255 - mean) / std, com std POR CANAL
# [0.229, 0.224, 0.225] × 255 = [58.395, 57.12, 57.375]. O nvinfer só aceita
# UM escalar, então usa-se 1/57.63 (a média) — erro de até ~1,3% por canal.
# Isso é conhecido e limitado; é o motivo de a fumaça comparar CLASSE e CAIXA,
# nunca float bit a bit.
NET_SCALE_FACTOR = "0.0173520736"
OFFSETS = "123.675;116.28;103.53"


def carregar_mapa() -> dict:
    if not MAPA.is_file():
        logger.error("mapa de taxonomia ausente em %s", MAPA)
        sys.exit(2)
    return json.loads(MAPA.read_text(encoding="utf-8"))


def _nomes_em_ordem(mapa: dict) -> list[str]:
    """Índice → nome do MODELO (não do catálogo).

    O labelfile do nvinfer é indexado pela saída do modelo; traduzir para o
    nome do catálogo aqui esconderia a origem do índice na hora de depurar.
    A tradução para o catálogo é do lado que gera o evento (app.domain.
    taxonomia.MapaModelo.traduzir), não do rótulo do OSD.
    """
    linhas = sorted(mapa["classes"], key=lambda c: c["indice"])
    return [c["modelo"] for c in linhas]


# ── baixar ────────────────────────────────────────────────────────────────

def cmd_baixar(args: argparse.Namespace) -> int:
    """Traz o ONNX do R2 e confere o sha256 ANTES de qualquer conversão.

    Integridade primeiro: converter um download truncado produz um engine que
    carrega, roda e detecta errado — o pior modo de falha possível num produto
    de segurança.
    """
    mapa = carregar_mapa()
    destino = Path(os.path.expanduser(args.destino))
    destino.mkdir(parents=True, exist_ok=True)
    alvo = destino / "b9243540.onnx"

    if not alvo.is_file():
        faltando = [k for k in ("R2_ENDPOINT", "R2_KEY", "R2_SECRET", "R2_BUCKET")
                    if not os.environ.get(k)]
        if faltando:
            logger.error(
                "%s não existe e faltam variáveis do R2: %s. Baixe o objeto "
                "%s e coloque em %s, ou exporte as credenciais.",
                alvo, ", ".join(faltando), mapa["onnx_r2_key"], alvo,
            )
            return 2
        import boto3  # noqa: PLC0415 — só necessário quando de fato baixa
        from botocore.config import Config  # noqa: PLC0415

        cliente = boto3.client(
            "s3", endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_KEY"],
            aws_secret_access_key=os.environ["R2_SECRET"],
            config=Config(signature_version="s3v4"), region_name="auto",
        )
        logger.info("baixando %s (%d bytes)", mapa["onnx_r2_key"], mapa["onnx_bytes"])
        cliente.download_file(os.environ["R2_BUCKET"], mapa["onnx_r2_key"], str(alvo))

    tamanho = alvo.stat().st_size
    digest = hashlib.sha256()
    with alvo.open("rb") as fh:
        for bloco in iter(lambda: fh.read(1 << 20), b""):
            digest.update(bloco)
    obtido = digest.hexdigest()

    if tamanho != mapa["onnx_bytes"] or obtido != mapa["onnx_sha256"]:
        logger.error(
            "INTEGRIDADE FALHOU — esperado %d bytes / sha %s; obtido %d / %s. "
            "NÃO converta este arquivo.",
            mapa["onnx_bytes"], mapa["onnx_sha256"], tamanho, obtido,
        )
        return 1
    logger.info("integridade OK: %s (%d bytes, sha256 %s)", alvo, tamanho, obtido)
    return 0


# ── artefatos ─────────────────────────────────────────────────────────────

_CONFIG_NVINFER = """\
# GERADO por deployments/edge/embarque_b9243540.py — NÃO editar à mão.
# Fonte: services/api/app/domain/taxonomia/mapa_modelo_b9243540.json
# Modelo {modelo_id} · {modelo_nome}
[property]
gpu-id=0
# Pré-proc ImageNet RGB do RF-DETR. Ver NET_SCALE_FACTOR no script: o nvinfer
# não expressa std por canal, então há erro conhecido de ~1,3% por canal.
net-scale-factor={escala}
offsets={offsets}
model-color-format=0
model-engine-file={engine}
labelfile-path={labels}
# O ONNX exporta entrada ESTÁTICA [1,3,{h},{w}] — batch 1 é o teto sem
# re-exportar o modelo com eixo de batch dinâmico.
batch-size=1
# 0=FP32 1=INT8 2=FP16. FP16 por decisão medida (runbook §Precisão):
# INT8 em DETR não compensa (REGRAS_PLATAFORMA_JETSON §3.2).
network-mode=2
num-detected-classes={n_classes}
interval=0
gie-unique-id=1
network-type=0
# DETR não usa NMS.
cluster-mode=4
# ⚠️ 0, NÃO 1. A nuvem (domain/detectors/onnx_rfdetr.py::_preprocess_rfdetr)
# faz resize BILINEAR direto para {h}x{w}, sem preservar proporção e sem
# padding. Ligar maintain-aspect-ratio aqui faria o box ver uma imagem
# geometricamente diferente da que a nuvem viu, e a fumaça acusaria uma
# divergência que é só de config.
maintain-aspect-ratio=0
symmetric-padding=0
parse-bbox-func-name=NvDsInferParseCustomRFDETR
custom-lib-path={parser}

[class-attrs-all]
pre-cluster-threshold=0.4
"""


def cmd_artefatos(args: argparse.Namespace) -> int:
    """Gera labelfile e config do nvinfer A PARTIR DO MAPA."""
    mapa = carregar_mapa()
    destino = Path(os.path.expanduser(args.destino))
    destino.mkdir(parents=True, exist_ok=True)

    nomes = _nomes_em_ordem(mapa)
    if len(nomes) != mapa["saidas_do_modelo"]:
        logger.error(
            "mapa incoerente: %d linhas para %d saídas do modelo",
            len(nomes), mapa["saidas_do_modelo"],
        )
        return 1

    labels = destino / "b9243540_labels.txt"
    labels.write_text("\n".join(nomes) + "\n", encoding="utf-8")
    logger.info("labelfile: %s (%d classes)", labels, len(nomes))

    h, w = mapa["entrada_hw"]
    config = destino / "config_infer_b9243540.txt"
    config.write_text(
        _CONFIG_NVINFER.format(
            modelo_id=mapa["modelo_id"], modelo_nome=mapa["modelo_display_name"],
            escala=NET_SCALE_FACTOR, offsets=OFFSETS,
            engine=destino / "b9243540_fp16.engine", labels=labels,
            h=h, w=w, n_classes=len(nomes), parser=PARSER_SO,
        ),
        encoding="utf-8",
    )
    logger.info("config nvinfer: %s", config)

    if not Path(PARSER_SO).is_file():
        logger.warning(
            "parser %s não encontrado — o config aponta para ele; compilar "
            "antes de subir pipeline (ver runbook §Bloqueio P-1)", PARSER_SO,
        )
    return 0


# ── engine ────────────────────────────────────────────────────────────────

def cmd_engine(args: argparse.Namespace) -> int:
    """ONNX → TensorRT FP16 com o trtexec da plataforma congelada (TRT 10.3).

    Sem `--int8`: ver runbook §Precisão. Sem `--shapes`: a entrada do ONNX é
    estática [1,3,560,560].
    """
    destino = Path(os.path.expanduser(args.destino))
    onnx = destino / "b9243540.onnx"
    engine = destino / "b9243540_fp16.engine"
    if not onnx.is_file():
        logger.error("%s ausente — rode `baixar` antes", onnx)
        return 2
    if not Path(TRTEXEC).is_file():
        logger.error("%s ausente — este passo roda NO BOX, não no laptop", TRTEXEC)
        return 2

    cmd = [
        TRTEXEC,
        f"--onnx={onnx}",
        f"--saveEngine={engine}",
        "--fp16",
        "--memPoolSize=workspace:4096",
        # Só constrói. Medir throughput é outro passo, com o pipeline montado.
        "--skipInference",
        f"--exportLayerInfo={destino / 'b9243540_fp16.layers.json'}",
    ]
    logger.info("build (esperar dezenas de minutos — usar tmux): %s", " ".join(cmd))
    if args.mostrar:
        return 0
    return subprocess.call(cmd)


# ── fumaça ────────────────────────────────────────────────────────────────

def _pos_processar(dets, logits, larg, alt, limiar: float) -> list[dict]:
    """MESMO pós-processamento do caminho servido da nuvem.

    Cópia deliberada de `domain/detectors/onnx_rfdetr.py::_postprocess_raw`:
    sigmoid (focal loss, não softmax) + top-k sobre query×classe (não argmax
    por query). Se este script usasse argmax, a fumaça compararia o box com
    uma nuvem imaginária e daria "bateu" para um pipeline errado.
    """
    import numpy as np  # noqa: PLC0415

    probs = 1.0 / (1.0 + np.exp(-logits))
    plano = probs.ravel()
    n_classes = probs.shape[1]
    k = min(300, plano.size)
    idx = np.argpartition(plano, -k)[-k:]
    idx = idx[np.argsort(-plano[idx])]
    queries, classes = np.divmod(idx, n_classes)
    scores = plano[idx]
    caixas = dets[queries]

    saida = []
    for caixa, score, classe in zip(caixas, scores, classes):
        if score < limiar:
            continue
        cx, cy, bw, bh = caixa * np.array([larg, alt, larg, alt])
        saida.append({
            "classe_idx": int(classe),
            "score": round(float(score), 4),
            "bbox": [round(float(cx - bw / 2), 1), round(float(cy - bh / 2), 1),
                     round(float(bw), 1), round(float(bh), 1)],
        })
    return saida


def cmd_fumaca(args: argparse.Namespace) -> int:
    """Roda o engine sobre frames conhecidos e grava a saída em JSON.

    O JSON é a evidência: sem ele, "o embarque funcionou" é opinião.
    """
    import numpy as np  # noqa: PLC0415
    import tensorrt as trt  # noqa: PLC0415
    from cuda import cudart  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    mapa = carregar_mapa()
    nomes = _nomes_em_ordem(mapa)
    h, w = mapa["entrada_hw"]
    destino = Path(os.path.expanduser(args.destino))
    engine_path = destino / "b9243540_fp16.engine"

    runtime = trt.Runtime(trt.Logger(trt.Logger.WARNING))
    engine = runtime.deserialize_cuda_engine(engine_path.read_bytes())
    contexto = engine.create_execution_context()

    resultados = {}
    for frame in sorted(Path(os.path.expanduser(args.frames)).glob("*.jpg")):
        img = Image.open(frame).convert("RGB")
        larg_orig, alt_orig = img.size
        # Resize direto, sem preservar proporção — idêntico à nuvem.
        arr = np.asarray(img.resize((w, h), Image.BILINEAR), dtype=np.float32) / 255.0
        arr = (arr - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
            [0.229, 0.224, 0.225], dtype=np.float32
        )
        blob = np.ascontiguousarray(arr.transpose(2, 0, 1)[None], dtype=np.float32)

        saidas = _executar(engine, contexto, blob, cudart, trt)
        dets = next(v for v in saidas.values() if v.shape[-1] == 4)[0]
        logits = next(v for v in saidas.values() if v.shape[-1] != 4)[0]
        brutos = _pos_processar(dets, logits, larg_orig, alt_orig, args.limiar)
        resultados[frame.name] = [
            {**d, "classe": nomes[d["classe_idx"]]} for d in brutos
        ]
        logger.info("%s: %d detecções", frame.name, len(brutos))

    Path(args.saida).write_text(json.dumps(resultados, indent=2, ensure_ascii=False))
    logger.info("saída do box gravada em %s", args.saida)
    return 0


def _executar(engine, contexto, blob, cudart, trt) -> dict:
    """Uma inferência TensorRT. Isolado para manter cmd_fumaca legível."""
    import numpy as np  # noqa: PLC0415

    buffers, saidas = {}, {}
    for i in range(engine.num_io_tensors):
        nome = engine.get_tensor_name(i)
        if engine.get_tensor_mode(nome) == trt.TensorIOMode.INPUT:
            contexto.set_input_shape(nome, blob.shape)
            _, ptr = cudart.cudaMalloc(blob.nbytes)
            cudart.cudaMemcpy(ptr, blob.ctypes.data, blob.nbytes,
                              cudart.cudaMemcpyKind.cudaMemcpyHostToDevice)
        else:
            forma = tuple(contexto.get_tensor_shape(nome))
            host = np.empty(forma, dtype=np.float32)
            _, ptr = cudart.cudaMalloc(host.nbytes)
            saidas[nome] = host
        buffers[nome] = ptr
        contexto.set_tensor_address(nome, int(ptr))

    _, stream = cudart.cudaStreamCreate()
    contexto.execute_async_v3(stream)
    cudart.cudaStreamSynchronize(stream)
    for nome, host in saidas.items():
        cudart.cudaMemcpy(host.ctypes.data, buffers[nome], host.nbytes,
                          cudart.cudaMemcpyKind.cudaMemcpyDeviceToHost)
    for ptr in buffers.values():
        cudart.cudaFree(ptr)
    return saidas


# ── comparar ──────────────────────────────────────────────────────────────

def _iou(a: list[float], b: list[float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    uniao = aw * ah + bw * bh - inter
    return inter / uniao if uniao > 0 else 0.0


def cmd_comparar(args: argparse.Namespace) -> int:
    """Confronta box × nuvem no MESMO frame. Divergiu = embarque FALHOU.

    Critério (deliberadamente frouxo em score, apertado em geometria e
    classe): para cada detecção da nuvem tem de existir uma do box com a
    MESMA classe e IoU ≥ --iou; o score pode diferir até --tol, porque FP16 e
    o net-scale-factor escalar do nvinfer introduzem erro conhecido.

    Falta de par nos DOIS sentidos é reportada: sobra no box é falso positivo
    novo, falta é detecção que sumiu — e no RVB uma detecção que some é uma
    violação que ninguém vê.
    """
    edge = json.loads(Path(args.edge).read_text())
    nuvem = json.loads(Path(args.nuvem).read_text())

    faltando = perdidas = sobrando = pareadas = 0
    for frame, esperadas in nuvem.items():
        obtidas = edge.get(frame)
        if obtidas is None:
            logger.error("frame %s não está na saída do box", frame)
            faltando += 1
            continue
        livres = list(obtidas)
        for det in esperadas:
            par = None
            for cand in livres:
                if cand["classe"] == det["classe"] and _iou(cand["bbox"], det["bbox"]) >= args.iou:
                    par = cand
                    break
            if par is None:
                logger.error(
                    "%s: nuvem viu %s (score %.3f) e o box NÃO — detecção perdida",
                    frame, det["classe"], det["score"],
                )
                perdidas += 1
                continue
            livres.remove(par)
            pareadas += 1
            if abs(par["score"] - det["score"]) > args.tol:
                logger.warning(
                    "%s: %s score nuvem=%.3f box=%.3f (Δ %.3f > tolerância)",
                    frame, det["classe"], det["score"], par["score"],
                    abs(par["score"] - det["score"]),
                )
        for extra in livres:
            logger.error(
                "%s: box viu %s (score %.3f) e a nuvem NÃO — falso positivo novo",
                frame, extra["classe"], extra["score"],
            )
            sobrando += 1

    logger.info(
        "pareadas=%d perdidas=%d sobrando=%d frames_ausentes=%d",
        pareadas, perdidas, sobrando, faltando,
    )
    if perdidas or sobrando or faltando:
        logger.error("EMBARQUE REPROVADO — o box não reproduz a nuvem")
        return 1
    logger.info("EMBARQUE APROVADO na fumaça (%d detecções pareadas)", pareadas)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("baixar"); p.add_argument("--destino", default="~/recognition/models")
    p.set_defaults(func=cmd_baixar)
    p = sub.add_parser("artefatos"); p.add_argument("--destino", default="~/recognition/models")
    p.set_defaults(func=cmd_artefatos)
    p = sub.add_parser("engine"); p.add_argument("--destino", default="~/recognition/models")
    p.add_argument("--mostrar", action="store_true", help="só imprime o comando")
    p.set_defaults(func=cmd_engine)
    p = sub.add_parser("fumaca"); p.add_argument("--destino", default="~/recognition/models")
    p.add_argument("--frames", required=True); p.add_argument("--saida", required=True)
    p.add_argument("--limiar", type=float, default=0.4)
    p.set_defaults(func=cmd_fumaca)
    p = sub.add_parser("comparar")
    p.add_argument("--edge", required=True); p.add_argument("--nuvem", required=True)
    p.add_argument("--iou", type=float, default=0.85)
    p.add_argument("--tol", type=float, default=0.05)
    p.set_defaults(func=cmd_comparar)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

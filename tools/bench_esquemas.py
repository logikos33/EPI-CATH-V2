"""Bancada: esquema de PARTES DO CORPO contra esquema de AUSENCIA.

O que esta bancada existe para responder, e por que ela e honesta:

· Os dois modelos sairam dos MESMOS 4.983 quadros, com o MESMO split. O teste
  sao 248 quadros que NENHUM dos dois viu. Entao a diferenca medida e do esquema
  de classes, nao de sorte de amostra nem de tamanho de dataset.
· Nao uso o mAP que o proprio treino reportou: cada um mediu no seu val, e "mAP
  no val do proprio dataset nao ordena modelos" ja mordeu nesta casa.
· A pergunta e a do PRODUTO, nao a do paper: dado um quadro, o modelo aponta a
  violacao no lugar certo? Para o esquema de ausencia isso e uma classe direta.
  Para o de partes, a violacao e DERIVADA: orelha sem protetor sobreposto.

⚠️ SUPOSICAO DE PRE-PROCESSAMENTO, explicita porque ja houve duvida (D-66):
RF-DETR = RGB, /255, normalizacao ImageNet. Se estiver errado, as deteccoes saem
poucas e fracas nos DOIS modelos — o script imprime a taxa de deteccao por
modelo justamente para essa suspeita nao passar despercebida.
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np
import onnxruntime as ort
from PIL import Image

BASE = "/Users/vitoremanuel/Logikos-mutirao/bench-esquemas"
MODELOS = "/Users/vitoremanuel/Logikos-mutirao/_modelos_runpod"
MEDIA = np.array([0.485, 0.456, 0.406], dtype=np.float32)
DESVIO = np.array([0.229, 0.224, 0.225], dtype=np.float32)
IOU_MIN = 0.5


def carrega_coco(caminho):
    d = json.load(open(caminho))
    cats = {c["id"]: c["name"] for c in d["categories"]}
    imgs = {i["id"]: i for i in d["images"]}
    gt = defaultdict(list)
    for a in d["annotations"]:
        x, y, w, h = a["bbox"]
        gt[imgs[a["image_id"]]["file_name"]].append((cats[a["category_id"]], [x, y, x + w, y + h]))
    return cats, imgs, gt


def prepara(caminho, lado):
    im = Image.open(caminho).convert("RGB")
    W, H = im.size
    a = np.asarray(im.resize((lado, lado), Image.BILINEAR), dtype=np.float32) / 255.0
    a = (a - MEDIA) / DESVIO
    return np.transpose(a, (2, 0, 1))[None].astype(np.float32), W, H


def infere(sess, nome_in, tensor, W, H, nomes, limiar):
    dets, labels = sess.run(None, {nome_in: tensor})
    dets, labels = dets[0], labels[0]
    escores = 1.0 / (1.0 + np.exp(-labels))          # sigmoide, RF-DETR
    saida = []
    for i in range(dets.shape[0]):
        j = int(np.argmax(escores[i]))
        s = float(escores[i][j])
        if s < limiar or j >= len(nomes):
            continue
        cx, cy, w, h = dets[i]
        saida.append((nomes[j], s, [float((cx - w / 2) * W), float((cy - h / 2) * H),
                                    float((cx + w / 2) * W), float((cy + h / 2) * H)]))
    return saida


def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (aa + bb - inter)


def avalia(preds, gts, classes_de_interesse):
    """Precisao/recall por classe, casamento guloso por IoU>=0.5."""
    tp = defaultdict(int); fp = defaultdict(int); fn = defaultdict(int)
    for arq, ps in preds.items():
        gs = [g for g in gts.get(arq, []) if g[0] in classes_de_interesse]
        ps = [p for p in ps if p[0] in classes_de_interesse]
        usados = set()
        for nome, _s, cx in sorted(ps, key=lambda p: -p[1]):
            achou = -1
            melhor = IOU_MIN
            for k, (gn, gb) in enumerate(gs):
                if k in usados or gn != nome:
                    continue
                v = iou(cx, gb)
                if v >= melhor:
                    melhor, achou = v, k
            if achou >= 0:
                usados.add(achou); tp[nome] += 1
            else:
                fp[nome] += 1
        for k, (gn, _gb) in enumerate(gs):
            if k not in usados:
                fn[gn] += 1
    return tp, fp, fn


def relatorio(nome, tp, fp, fn, classes):
    print(f"\n=== {nome}")
    print(f"{'classe':26} {'TP':>5} {'FP':>5} {'FN':>5} {'prec':>6} {'rec':>6} {'F1':>6}")
    TP = FP = FN = 0
    for c in sorted(classes):
        t, f, n = tp[c], fp[c], fn[c]
        if t + f + n == 0:
            continue
        TP += t; FP += f; FN += n
        p = t / (t + f) if t + f else 0.0
        r = t / (t + n) if t + n else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        print(f"{c[:26]:26} {t:5} {f:5} {n:5} {p:6.2f} {r:6.2f} {f1:6.2f}")
    p = TP / (TP + FP) if TP + FP else 0.0
    r = TP / (TP + FN) if TP + FN else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    print(f"{'TOTAL':26} {TP:5} {FP:5} {FN:5} {p:6.2f} {r:6.2f} {f1:6.2f}")
    return p, r, f1


def main():
    limiar = float(sys.argv[1]) if len(sys.argv) > 1 else 0.30
    resultados = {}
    for tag, coco, lado in (("11f1303c", "v17c-partes", 560), ("9cc62fd6", "v17b-ausencia", 560), ("04508616", "v17a-presenca", 560)):
        cats, _imgs, gt = carrega_coco(f"{BASE}/{coco}.coco.json")
        nomes = [cats[i] for i in sorted(cats)]
        sess = ort.InferenceSession(f"{MODELOS}/{tag}_model.onnx", providers=["CPUExecutionProvider"])
        nome_in = sess.get_inputs()[0].name
        preds = {}
        arquivos = sorted(os.listdir(f"{BASE}/img"))
        for i, arq in enumerate(arquivos):
            t, W, H = prepara(f"{BASE}/img/{arq}", lado)
            preds[arq] = infere(sess, nome_in, t, W, H, nomes, limiar)
            if i % 40 == 0:
                print(f"  {tag} {i}/{len(arquivos)}", flush=True)
        n_pred = sum(len(v) for v in preds.values())
        n_gt = sum(len(v) for v in gt.values())
        print(f"{tag}: {n_pred} predicoes, {n_gt} verdades, {len(arquivos)} quadros "
              f"(limiar {limiar}) — classes: {nomes}")
        classes = set(cats.values())
        tp, fp, fn = avalia(preds, gt, classes)
        resultados[tag] = (relatorio(f"{tag} ({coco})", tp, fp, fn, classes), preds, gt, nomes)
        json.dump({k: v for k, v in preds.items()},
                  open(f"{BASE}/pred_{tag}.json", "w"))
    return resultados


if __name__ == "__main__":
    main()

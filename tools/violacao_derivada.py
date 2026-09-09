"""A violacao DERIVADA bate a violacao DETECTADA?

A pergunta que decide a arquitetura, nos mesmos 248 quadros retidos:

  · caminho de hoje  — o modelo tem a classe `Sem protetor de ouvido` e a
    detecta direto. Medido: precisao 0,16, recall 0,14.
  · caminho proposto — o modelo acha `orelha` e `protetor_auricular` (presencas,
    que ele sabe achar) e a violacao sai da GEOMETRIA: orelha sem protetor
    sobreposto.

O gabarito da violacao vem do export `v17b-ausencia`, anotado por humano — NAO
do proprio esquema de partes. Reu nao avalia a propria prova.

⚠️ O QUE ESTA MEDIDA NAO PROVA: o gabarito de `v17b` marca a violacao onde o
anotador achou que ela estava; a regra derivada a marca na ORELHA. Se as duas
convencoes de enquadramento nao coincidirem, o IoU pune a regra por uma
diferenca de convencao, nao de acerto. Por isso o script reporta tambem o
acerto por QUADRO (tem/nao tem violacao), que independe de enquadramento.
"""
import json
from collections import defaultdict

BASE = "/Users/vitoremanuel/Logikos-mutirao/bench-esquemas"
IOU_MIN = 0.5
#: Fracao da orelha que precisa estar coberta por um protetor para valer como
#: protegida. Baixo de proposito: protetor auricular aparece como um pontinho
#: sobre a orelha, nao a cobre inteira.
COBERTURA_MIN = 0.15


def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (aa + bb - inter)


def cobertura(alvo, outro):
    x0, y0 = max(alvo[0], outro[0]), max(alvo[1], outro[1])
    x1, y1 = min(alvo[2], outro[2]), min(alvo[3], outro[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = (alvo[2] - alvo[0]) * (alvo[3] - alvo[1])
    return ((x1 - x0) * (y1 - y0)) / area if area > 0 else 0.0


def gt_violacao():
    d = json.load(open(f"{BASE}/v17b-ausencia.coco.json"))
    cats = {c["id"]: c["name"] for c in d["categories"]}
    imgs = {i["id"]: i["file_name"] for i in d["images"]}
    gt = defaultdict(list)
    for a in d["annotations"]:
        if cats[a["category_id"]] != "Sem protetor de ouvido":
            continue
        x, y, w, h = a["bbox"]
        gt[imgs[a["image_id"]]].append([x, y, x + w, y + h])
    return gt


def derivada(preds_partes):
    """orelha sem protetor sobreposto -> violacao na caixa da orelha."""
    saida = defaultdict(list)
    for arq, ps in preds_partes.items():
        orelhas = [(s, b) for n, s, b in ps if n == "orelha"]
        protetores = [b for n, _s, b in ps if n == "protetor_auricular"]
        for s, o in orelhas:
            if not any(cobertura(o, p) >= COBERTURA_MIN for p in protetores):
                saida[arq].append((s, o))
    return saida


def pontua(pred_por_arq, gt, arquivos):
    """(caixa: TP/FP/FN por IoU) e (quadro: acerto do sim/nao)."""
    tp = fp = fn = 0
    q_tp = q_fp = q_fn = q_tn = 0
    for arq in arquivos:
        gs = list(gt.get(arq, []))
        ps = sorted(pred_por_arq.get(arq, []), key=lambda x: -x[0])
        usados = set()
        for _s, b in ps:
            achou, melhor = -1, IOU_MIN
            for k, g in enumerate(gs):
                if k in usados:
                    continue
                v = iou(b, g)
                if v >= melhor:
                    melhor, achou = v, k
            if achou >= 0:
                usados.add(achou); tp += 1
            else:
                fp += 1
        fn += len(gs) - len(usados)
        tem_gt, tem_pred = bool(gs), bool(ps)
        q_tp += tem_gt and tem_pred
        q_fp += (not tem_gt) and tem_pred
        q_fn += tem_gt and (not tem_pred)
        q_tn += (not tem_gt) and (not tem_pred)
    return (tp, fp, fn), (q_tp, q_fp, q_fn, q_tn)


def linha(nome, caixa, quadro, n):
    tp, fp, fn = caixa
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    qtp, qfp, qfn, qtn = quadro
    qp = qtp / (qtp + qfp) if qtp + qfp else 0.0
    qr = qtp / (qtp + qfn) if qtp + qfn else 0.0
    qf1 = 2 * qp * qr / (qp + qr) if qp + qr else 0.0
    print(f"{nome:32} caixa: P={p:.2f} R={r:.2f} F1={f1:.2f} (TP{tp} FP{fp} FN{fn})")
    print(f"{'':32} quadro: P={qp:.2f} R={qr:.2f} F1={qf1:.2f} "
          f"(TP{qtp} FP{qfp} FN{qfn} TN{qtn}) de {n}")


def main():
    gt = gt_violacao()
    partes = json.load(open(f"{BASE}/pred_11f1303c.json"))
    ausencia = json.load(open(f"{BASE}/pred_9cc62fd6.json"))
    arquivos = sorted(partes)
    n_gt = sum(len(v) for v in gt.values())
    print(f"gabarito humano: {n_gt} violacoes 'Sem protetor de ouvido' em "
          f"{len(gt)} de {len(arquivos)} quadros\n")

    direto = defaultdict(list)
    for arq, ps in ausencia.items():
        for n, s, b in ps:
            if n == "Sem protetor de ouvido":
                direto[arq].append((s, b))
    linha("DETECTADA (classe de ausencia)", *pontua(direto, gt, arquivos), len(arquivos))
    print()
    der = derivada(partes)
    linha(f"DERIVADA (orelha sem protetor)", *pontua(der, gt, arquivos), len(arquivos))


if __name__ == "__main__":
    main()

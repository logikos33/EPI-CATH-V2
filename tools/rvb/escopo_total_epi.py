#!/usr/bin/env python3
"""Põe TODAS as câmeras ativas da RVB reconhecendo TODAS as classes do modelo.

Por que existe
--------------
Medido em 09/09: 18 câmeras ativas, **14 deployments** e escopos que continham
só classes de PRESENÇA — `["Protetor auditivo"]`, `["Botas", "Protetor
auditivo"]`. Nenhum escopo tinha uma única classe de ausência.

Isso importa porque `alerta_de_evento_do_edge` aplica `_filtrar_por_escopo`
antes de gravar em `alerts`: **classe fora do escopo não vira alerta**. Ou
seja, mesmo com a polaridade decidida e o box publicando, "sem máscara" era
descartado na porta da nuvem. É o que a tela de Cenário já dizia com o selo
FORA DO ESCOPO — a tela estava certa e ninguém tinha ligado os pontos.

O escopo atual nasceu de derivação automática (`>=10 anotações humanas`,
issue #535). Critério defensável para quem não sabia o que o modelo faz; ruim
como estado permanente, porque congela o produto no que já foi anotado.

O que este script faz
---------------------
Garante, para cada câmera ATIVA, um deployment ativo com escopo = tudo que o
modelo servido emite. Idempotente: rodar 2x não duplica nem alterna estado.

⚠️ Escopo é INTENÇÃO, não capacidade. Uma classe no escopo só vira alerta se o
modelo souber emiti-la. Por isso a lista abaixo é o que o modelo REALMENTE
emite, não o catálogo inteiro: `Capacete`, `Sem Capacete`, `Colete` e
`Sem Colete` ficam de fora porque o modelo servido não tem essas saídas, e
oferecê-las seria a tela mentindo que funcionaria.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# As 10 classes do modelo servido, pelo mapa PROVADO por matriz GT x slot
# (88,3% na diagonal) em `mapa_taxonomia_epi.json`. Nomes do CATÁLOGO — é por
# nome que `_filtrar_por_escopo` compara, não por índice.
CLASSES_DO_MODELO = [
    "Luvas", "Sem Luvas",
    "Óculos", "Sem Óculos",
    "Protetor auditivo", "Sem protetor de ouvido",
    "mascara", "Sem mascara", "Uso incorreto de mascara",
    "Botas",
]

ORIGEM = (
    "decisao do dono 2026-09-09: todas as cameras reconhecem tudo que o modelo "
    "emite (ausencia e presenca). O ajuste fino por camera passa a ser feito na "
    "tela de Modelos por camera, nao por derivacao automatica."
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--aplicar", action="store_true",
                   help="sem isto o script só MOSTRA o que faria (seco por padrão)")
    p.add_argument("--modelo-id", default="",
                   help="model_id dos deployments novos (default: o já usado pelos existentes)")
    a = p.parse_args()

    import psycopg2
    from psycopg2.extras import RealDictCursor

    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("faltou DATABASE_PUBLIC_URL — rode sob `railway run --service Postgres`")
    conn = psycopg2.connect(url)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT c.id, c.name, c.channel, c.tenant_id,
               d.id AS deploy_id, d.config, d.model_id, d.deployed_by
          FROM public.cameras c
          LEFT JOIN public.model_deployments d
                 ON d.camera_id = c.id AND d.status = 'active' AND d.module_code = 'epi'
         WHERE c.is_active
         ORDER BY c.channel
    """)
    linhas = cur.fetchall()
    if not linhas:
        sys.exit("nenhuma câmera ativa — nada a fazer")

    # Herda model_id/deployed_by dos deployments que já existem, em vez de
    # inventar: manter uma referência só evita divergir o inventário.
    existentes = [r for r in linhas if r["deploy_id"]]
    modelo_id = a.modelo_id or (str(existentes[0]["model_id"]) if existentes else "")
    autor = str(existentes[0]["deployed_by"]) if existentes else None
    if not modelo_id:
        sys.exit("sem model_id: informe --modelo-id (nenhum deployment ativo para herdar)")

    criar, ampliar, ja_ok = [], [], []
    for r in linhas:
        atual = sorted((r["config"] or {}).get("classes") or []) if r["deploy_id"] else None
        if not r["deploy_id"]:
            criar.append(r)
        elif atual != sorted(CLASSES_DO_MODELO):
            ampliar.append((r, atual))
        else:
            ja_ok.append(r)

    print(f"câmeras ativas: {len(linhas)}  ·  criar: {len(criar)}  "
          f"ampliar: {len(ampliar)}  já corretas: {len(ja_ok)}")
    for r in criar:
        print(f"  CRIAR   ch{str(r['channel']):>3}  {str(r['name'])[:40]}")
    for r, atual in ampliar:
        print(f"  AMPLIAR ch{str(r['channel']):>3}  {str(r['name'])[:32]:32} {len(atual or [])} -> {len(CLASSES_DO_MODELO)}")

    if not a.aplicar:
        print("\n(seco — nada foi gravado. use --aplicar)")
        return 0

    config = {"classes": CLASSES_DO_MODELO, "mode": "shadow", "origem_escopo": ORIGEM}
    for r in ampliar:
        cur.execute(
            "UPDATE public.model_deployments SET config = %s WHERE id = %s",
            (json.dumps({**(r[0]["config"] or {}), **config}), r[0]["deploy_id"]),
        )
    for r in criar:
        cur.execute(
            """INSERT INTO public.model_deployments
                   (tenant_id, model_id, camera_id, module_code, config, status, deployed_by)
               VALUES (%s, %s, %s, 'epi', %s, 'active', %s)""",
            (str(r["tenant_id"]), modelo_id, str(r["id"]), json.dumps(config), autor),
        )
    conn.commit()
    print(f"\naplicado: {len(ampliar)} ampliados, {len(criar)} criados")

    cur.execute("""SELECT count(*) n FROM public.model_deployments
                    WHERE status='active' AND module_code='epi'
                      AND config->'classes' @> %s::jsonb""",
                (json.dumps(CLASSES_DO_MODELO),))
    print("deployments ativos com escopo completo:", cur.fetchone()["n"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Manifesto dos modelos no R2 — quem é cada `model.onnx`, em texto legível.

O PROBLEMA que isto fecha: no R2 o modelo mora em
`models/<tenant>/runpod/<job_uuid>/model.onnx`. O caminho não diz o nome, nem
a data, nem as classes, nem de que split saiu a métrica. Quem abre o bucket
seis meses depois não tem como saber o que está baixando — e já aconteceu de
o NOME mentir: `6ca25ee9` se chama "5 classes", tem mesmo 5, e todas são de
PRESENÇA (Botas, Luvas, mascara, Óculos, Protetor auditivo). Ele é incapaz de
acusar violação, apesar de ter o melhor mAP50 do acervo (0,764).

Por isso este script escreve, AO LADO de cada `model.onnx`, um `manifest.json`
com o que o caminho não conta — e um índice único em
`models/<tenant>/MANIFESTO.json` pra achar tudo sem varrer o bucket.

⛔ REGRA INEGOCIÁVEL: a lista de classes NUNCA é derivada do NOME do modelo.
Ela vem de `metrics…per_class` (o que foi de fato AVALIADO) ou de
`dataset_versions.class_distribution` (o que foi de fato TREINADO). Se
nenhuma das duas existir, o campo sai `null` com `classes_origem:
"nao_registrado"` — não sabemos, e o manifesto diz que não sabemos.

Aditivo por construção: só escreve `manifest.json` e `MANIFESTO.json`. Nunca
sobrescreve, move ou apaga `model.onnx` nem qualquer outro artefato.

Idempotente: relê o manifesto que já está no R2 e só regrava quando o
CONTEÚDO muda (o carimbo `gerado_em` é ignorado na comparação — senão toda
rodada geraria escrita nova sem nenhum fato novo).

Uso:
    DATABASE_URL=... python3 scripts/ops/manifesto_modelos_r2.py             # dry-run (padrão)
    DATABASE_URL=... R2_ENDPOINT=... R2_KEY=... R2_SECRET=... R2_BUCKET=... \\
        python3 scripts/ops/manifesto_modelos_r2.py --aplicar

O dry-run NÃO precisa de credencial de R2 (só lê o banco e imprime o que
subiria). O `--aplicar` precisa, e falha ALTO se ela faltar.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

_RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_RAIZ / "services" / "api"))

logger = logging.getLogger(__name__)

#: Versão do formato do manifesto. Sobe quando o SIGNIFICADO de um campo muda —
#: quem lê o arquivo no bucket precisa saber contra qual contrato está lendo.
MANIFESTO_VERSAO = 1

#: `created_at` é `timestamp without time zone` e é gravado com `NOW()`. O
#: Postgres do Railway roda em `Etc/UTC` (conferido: `SHOW timezone`), então o
#: valor É UTC — mas a coluna não carrega essa informação. O manifesto carimba
#: o fuso explicitamente pra ninguém reinterpretar como horário local.
FUSO_CREATED_AT = (
    "UTC — trained_models.created_at é 'timestamp without time zone' gravado "
    "com NOW() num Postgres em Etc/UTC; o fuso é assumido aqui, não vem da coluna"
)


# ── Resolução de classes (o coração honesto do manifesto) ────────────────────

def _buscar_per_class(metrics: Any) -> tuple[dict[str, Any], str] | None:
    """Primeiro `per_class` não-vazio dentro de `metrics`, com o caminho JSON.

    Busca em LARGURA e com as chaves ordenadas: o acervo tem modelo com DOIS
    `per_class` (`8e8fedf7` tem um no topo, split=test, e outro em
    `_censo_2026_09.eval_val`, split=val). Largura + ordem fixa faz a escolha
    ser sempre a mesma — o mais raso vence — em vez de depender da ordem em
    que o psycopg2 devolveu o JSONB.
    """
    if not isinstance(metrics, dict):
        return None
    fila: deque[tuple[dict[str, Any], str]] = deque([(metrics, "metrics")])
    while fila:
        no, caminho = fila.popleft()
        for chave in sorted(no):
            valor = no[chave]
            if not isinstance(valor, dict) or not valor:
                continue
            if chave == "per_class":
                return valor, f"{caminho}.per_class"
            fila.append((valor, f"{caminho}.{chave}"))
    return None


def _nomes_de_classe(bruto: dict[str, Any]) -> list[str]:
    """Chaves que são classe de verdade, ordenadas.

    Descarta o prefixo reservado `__` (`__sem_suporte_treino__` em
    `class_distribution` registra classes EXCLUÍDAS do treino por falta de
    suporte — é marcador, não classe; mesma regra de
    `tasks/training.py::_classes_treinadas`).
    """
    return sorted(k for k in bruto if not str(k).startswith("__"))


def resolver_classes(
    modelo: dict[str, Any], class_distribution: dict[str, Any] | None
) -> tuple[list[str] | None, str]:
    """(classes, classes_origem) — a lista REAL, e de onde ela veio.

    Precedência, e o porquê de cada degrau:
      1. `metrics…per_class` — foi AVALIADO com essas classes. É a prova mais
         forte que existe: alguém mediu o modelo classe a classe.
      2. `dataset_versions.class_distribution` — foi TREINADO com essas
         classes. Mais fraco (treinar não é medir), mas é fato registrado.
      3. nada — `(None, "nao_registrado")`.

    ⛔ `modelo` chega inteiro (com `name`/`display_name`) DE PROPÓSITO: é aqui
    que a tentação mora. Nome NÃO é contrato — `6ca25ee9` se chama
    "5 classes" e tem 5, todas de presença; `93fa2610` se chama "origem
    desconhecida". Derivar classe do nome inventaria taxonomia. O teste
    `test_manifesto_modelos_r2.py` REPROVA se este parâmetro virar fonte.
    """
    achado = _buscar_per_class(modelo.get("metrics"))
    if achado:
        bruto, caminho = achado
        nomes = _nomes_de_classe(bruto)
        if nomes:
            return nomes, caminho
    if class_distribution:
        nomes = _nomes_de_classe(class_distribution)
        if nomes:
            return nomes, "dataset_versions.class_distribution"
    return None, "nao_registrado"


def classificar_polaridade(
    classes: list[str] | None, polaridade: dict[str, bool | None]
) -> dict[str, Any]:
    """Separa as classes em ausência / presença / sem polaridade.

    Fonte única da polaridade é o catálogo (`module_classes` ∪ `yolo_classes`,
    ADR-0065) — NUNCA o prefixo "Sem " do rótulo. Heurística de nome erraria
    em `Uso incorreto de mascara` (é violação e não começa com "Sem") e
    mentiria sobre `Sem mascara`, que hoje está gravada `is_violation=FALSE`
    no catálogo do DEV.

    Classe fora do catálogo, ou com `is_violation` NULL, cai em
    `classes_sem_polaridade` — é "ninguém decidiu ainda", não é "não é
    violação". A distinção é o ponto: `capaz_de_acusar_violacao` só fica
    `true` com ausência PROVADA no catálogo.
    """
    if classes is None:
        return {
            "classes_de_ausencia": None,
            "classes_de_presenca": None,
            "classes_sem_polaridade": None,
            "capaz_de_acusar_violacao": None,
        }
    ausencia, presenca, indefinidas = [], [], []
    for nome in classes:
        marca = polaridade.get(nome.lower(), None)
        if marca is True:
            ausencia.append(nome)
        elif marca is False:
            presenca.append(nome)
        else:
            indefinidas.append(nome)
    return {
        "classes_de_ausencia": ausencia,
        "classes_de_presenca": presenca,
        "classes_sem_polaridade": indefinidas,
        "capaz_de_acusar_violacao": bool(ausencia),
    }


def resolver_map50(modelo: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
    """(map50, map50_origem) — o número E as condições em que ele foi medido.

    Citar "0,430" sozinho engana: o do modelo ATIVO (`8b3bd146`) saiu de uma
    amostra de `sample_n: 60` imagens do split `val`. Sem o `sample_n` ao
    lado, o número parece uma avaliação completa.

    Precedência: avaliação registrada (`…eval_val`, tem split e sample_n) >
    `metrics.map50` do treino (split não registrado) > coluna `map50`.
    """
    metrics = modelo.get("metrics") or {}
    censo = metrics.get("_censo_2026_09") if isinstance(metrics, dict) else None
    aval = (censo or {}).get("eval_val") if isinstance(censo, dict) else None
    if isinstance(aval, dict) and aval.get("map50") is not None:
        return float(aval["map50"]), {
            "fonte": "metrics._censo_2026_09.eval_val",
            "split": aval.get("split"),
            "sample_n": aval.get("sample_n"),
            "val_count_total": aval.get("val_count_total"),
            "score_threshold": aval.get("score_threshold"),
            "matching": aval.get("matching"),
        }
    if isinstance(metrics, dict) and metrics.get("map50") is not None:
        return float(metrics["map50"]), {
            "fonte": "metrics.map50",
            # Split não registrado: o número veio do log do próprio treino,
            # que não grava em que conjunto mediu. Dizer "val" aqui seria chute.
            "split": None,
            "sample_n": None,
            "metric_source": metrics.get("metric_source"),
            "metric_source_key": metrics.get("metric_source_key"),
            "observacao": "split não registrado pelo treino — não assumir 'val'",
        }
    if modelo.get("map50") is not None:
        return float(modelo["map50"]), {
            "fonte": "trained_models.map50 (coluna)",
            "split": None,
            "sample_n": None,
            "observacao": "só o agregado; nenhuma condição de medição registrada",
        }
    return None, {"fonte": "nao_registrado", "split": None, "sample_n": None}


def montar_manifesto(
    modelo: dict[str, Any],
    class_distribution: dict[str, Any] | None,
    polaridade: dict[str, bool | None],
) -> dict[str, Any]:
    """O manifesto de UM modelo. Função pura — nada de banco nem de rede."""
    classes, classes_origem = resolver_classes(modelo, class_distribution)
    map50, map50_origem = resolver_map50(modelo)
    criado: datetime = modelo["created_at"]
    if criado.tzinfo is None:
        criado = criado.replace(tzinfo=UTC)
    manifesto = {
        "manifesto_versao": MANIFESTO_VERSAO,
        "model_id": str(modelo["id"]),
        "display_name": modelo.get("display_name") or modelo["name"],
        "name_interno": modelo["name"],
        "created_at": criado.isoformat(),
        "created_at_fuso": FUSO_CREATED_AT,
        "classes": classes,
        "classes_origem": classes_origem,
        "n_classes": len(classes) if classes is not None else None,
        "polaridade_origem": "module_classes ∪ yolo_classes (ADR-0065)",
        "map50": map50,
        "map50_origem": map50_origem,
        "framework": modelo.get("framework"),
        "origin": modelo.get("origin"),
        "module_code": modelo.get("module_code"),
        "dataset_version_id": (
            str(modelo["dataset_version_id"]) if modelo.get("dataset_version_id") else None
        ),
        "r2_onnx_key": modelo.get("r2_onnx_key"),
        "tenant_id": str(modelo["tenant_id"]) if modelo.get("tenant_id") else None,
        # Fato do MOMENTO da geração, não permanente — `gerado_em` no mesmo
        # arquivo é o carimbo que diz "ativo quando?".
        "is_active": bool(modelo.get("is_active")),
    }
    manifesto.update(classificar_polaridade(classes, polaridade))
    return manifesto


def chave_do_manifesto(r2_onnx_key: str) -> str:
    """`manifest.json` no MESMO 'diretório' do artefato.

    Guarda-corpo de escrita aditiva: só devolve caminho que TERMINA em
    `/manifest.json`. Um erro de montagem aqui é a única forma de este script
    escrever por cima de um artefato — então o caminho é validado antes de
    virar upload.
    """
    chave = r2_onnx_key.rsplit("/", 1)[0] + "/manifest.json"
    if not chave.endswith("/manifest.json"):
        raise ValueError(f"chave de manifesto inválida: {chave!r}")
    return chave


def _comparavel(manifesto: dict[str, Any]) -> str:
    """Conteúdo do manifesto SEM o carimbo de geração — é o que decide se há
    escrita nova. Sem isto, `gerado_em` mudaria a cada rodada e o script
    reescreveria 16 objetos por nada."""
    return json.dumps(
        {k: v for k, v in manifesto.items() if k != "gerado_em"},
        sort_keys=True, ensure_ascii=False,
    )


# ── Banco ────────────────────────────────────────────────────────────────────

def carregar_modelos(cur) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT id, name, display_name, created_at, is_active, origin, framework, "
        "       module_code, map50, r2_onnx_key, model_path, dataset_version_id, "
        "       tenant_id, metrics "
        "FROM public.trained_models ORDER BY created_at DESC"
    )
    return list(cur.fetchall())


def carregar_class_distributions(cur) -> dict[str, dict[str, Any]]:
    cur.execute("SELECT id, class_distribution FROM public.dataset_versions")
    return {str(r["id"]): (r["class_distribution"] or {}) for r in cur.fetchall()}


def carregar_polaridade(cur, tenant_id: str, module_code: str) -> dict[str, bool | None]:
    """Nome (lower) -> is_violation, do catálogo global ∪ classes do tenant.

    Os DOIS nomes do catálogo global entram (`class_name` E `display_name`):
    o detector emite o rótulo (`Sem Luvas`), não o id técnico (`no_gloves`) —
    casar só por `class_name` faria as classes de violação nunca baterem
    (mesmo achado de `alert_repository._NOMES_DO_CATALOGO`).

    Nome com polaridade CONFLITANTE entre as duas origens vira None
    ("ninguém decidiu"), nunca um dos lados no chute.
    """
    cur.execute(
        "SELECT lower(nome) AS n, is_violation FROM module_classes, "
        "LATERAL unnest(ARRAY[class_name, display_name]) AS nome "
        "WHERE module_code = %s "
        "UNION ALL "
        "SELECT lower(name) AS n, is_violation FROM yolo_classes "
        "WHERE tenant_id = %s AND module_code = %s",
        (module_code, tenant_id, module_code),
    )
    mapa: dict[str, bool | None] = {}
    for row in cur.fetchall():
        nome, marca = row["n"], row["is_violation"]
        if nome in mapa and mapa[nome] is not marca:
            mapa[nome] = None  # conflito entre origens = indefinido
        else:
            mapa.setdefault(nome, marca)
    return mapa


# ── R2 ───────────────────────────────────────────────────────────────────────

def abrir_r2(tenant_id: str):
    """Storage do tenant, ou morre gritando.

    Reusa `get_storage` (precedência integration-store > env, a mesma do resto
    do sistema) e então EXIGE que o resultado seja R2 de verdade: `get_storage`
    pode devolver `LocalStorage` quando `ALLOW_EPHEMERAL_STORAGE=1` está ligado,
    e um manifesto gravado em disco efêmero seria exatamente o "sucesso
    silencioso com zero upload" que este script não pode produzir.
    """
    from app.infrastructure.storage.local_storage import get_storage
    from app.infrastructure.storage.r2_storage import R2Storage

    storage = get_storage(tenant_id)
    if not isinstance(storage, R2Storage):
        raise SystemExit(
            f"ABORTADO: storage resolvido para o tenant {tenant_id} é "
            f"{type(storage).__name__}, não R2Storage. Configure R2_ENDPOINT, "
            "R2_KEY, R2_SECRET (e R2_BUCKET) — gravar manifesto em disco "
            "efêmero seria pior que não gravar."
        )
    return storage


def ler_manifesto_existente(storage, chave: str) -> dict[str, Any] | None:
    """Manifesto já no bucket, ou None. Erro de leitura NÃO vira None: seguir
    como se não existisse regravaria por cima às cegas."""
    from app.core.exceptions import StorageError

    try:
        if not storage.exists(chave):
            return None
        return json.loads(storage.download_bytes(chave).decode("utf-8"))
    except (StorageError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("manifesto_existente_ilegivel: chave=%s err=%s", chave, exc)
        return None


def subir(storage, chave: str, conteudo: dict[str, Any]) -> None:
    storage.upload_bytes(
        chave,
        json.dumps(conteudo, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
        "application/json",
    )


# ── Orquestração ─────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--aplicar", action="store_true",
        help="sobe os manifestos ao R2; sem isto, só imprime o que subiria",
    )
    ap.add_argument("--tenant", help="restringe a um tenant_id (default: todos)")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL não definida.", file=sys.stderr)
        return 2

    agora = datetime.now(UTC).isoformat()
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            modelos = carregar_modelos(cur)
            distribuicoes = carregar_class_distributions(cur)
            polaridades: dict[tuple[str, str], dict[str, bool | None]] = {}
            for m in modelos:
                chave_pol = (str(m["tenant_id"]), m["module_code"])
                if m["tenant_id"] and chave_pol not in polaridades:
                    polaridades[chave_pol] = carregar_polaridade(cur, *chave_pol)
    finally:
        conn.close()

    por_tenant: dict[str, list[dict[str, Any]]] = {}
    sem_artefato: list[str] = []
    for m in modelos:
        tenant = str(m["tenant_id"]) if m["tenant_id"] else None
        if args.tenant and tenant != args.tenant:
            continue
        artefato = m.get("r2_onnx_key") or m.get("model_path")
        if not tenant or not artefato:
            # Sem tenant não há bucket-path; sem artefato não há "ao lado de quê".
            sem_artefato.append(str(m["id"]))
            continue
        dist = distribuicoes.get(str(m.get("dataset_version_id")))
        pol = polaridades.get((tenant, m["module_code"]), {})
        manifesto = montar_manifesto(m, dist, pol)
        manifesto["gerado_em"] = agora
        manifesto["r2_manifest_key"] = chave_do_manifesto(artefato)
        por_tenant.setdefault(tenant, []).append(manifesto)

    escritos = pulados = 0
    for tenant, lista in sorted(por_tenant.items()):
        storage = abrir_r2(tenant) if args.aplicar else None
        indice = {
            "manifesto_versao": MANIFESTO_VERSAO,
            "tenant_id": tenant,
            "gerado_em": agora,
            "total_modelos": len(lista),
            # Resumo, não cópia: o manifesto completo mora ao lado do artefato.
            "modelos": [
                {
                    k: man[k] for k in (
                        "model_id", "display_name", "created_at", "n_classes",
                        "classes_origem", "capaz_de_acusar_violacao", "map50",
                        "is_active", "framework", "r2_onnx_key", "r2_manifest_key",
                    )
                }
                for man in lista
            ],
        }
        alvos = [(man["r2_manifest_key"], man) for man in lista]
        alvos.append((f"models/{tenant}/MANIFESTO.json", indice))

        for chave, conteudo in alvos:
            if not args.aplicar:
                print(f"\n=== [dry-run] {chave} ===")
                print(json.dumps(conteudo, ensure_ascii=False, indent=2, sort_keys=True))
                continue
            atual = ler_manifesto_existente(storage, chave)
            if atual is not None and _comparavel(atual) == _comparavel(conteudo):
                pulados += 1
                logger.info("manifesto_inalterado: %s", chave)
                continue
            subir(storage, chave, conteudo)
            escritos += 1
            logger.info("manifesto_gravado: %s", chave)

    print("\n" + "=" * 72)
    modo = "APLICADO" if args.aplicar else "DRY-RUN (nada gravado; use --aplicar)"
    print(f"modo: {modo}")
    print(f"modelos processados: {sum(len(v) for v in por_tenant.values())} "
          f"em {len(por_tenant)} tenant(s)")
    if args.aplicar:
        print(f"objetos gravados: {escritos} | inalterados (pulados): {pulados}")
    if sem_artefato:
        print(f"⚠️ sem tenant/artefato, fora do manifesto: {', '.join(sem_artefato)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Recognition — Training Dispatch Task.

Cadeia de dispatch (dispatch_training), task "treino não pode mentir"
(ADR-0060) + task "runner genérico RunPod" (substitui Vast.ai — decisão do
dono, `infrastructure/gpu/vast_client.py` deletado, a API console.vast.ai
nunca entregou treino real em produção):
  1. RunPod REST real (RUNPOD_API_KEY resolvível — integration store do
     tenant > env, ver resolve_runpod_api_key em infrastructure/gpu/runpod_client.py)
     — GPU de terceiro: SÓ dispara com opt-in explícito do tenant
     (training_third_party_cloud_enabled, checado em `_dispatch_runpod_train`).
     Sem dataset exportado (coco_r2_key ausente) também nunca simula — erro
     alto (ver `_get_runpod_training_context`). O ciclo de vida do pod
     (preço → teto de custo → criar → acompanhar → matar) vive no runner
     genérico `infrastructure/gpu/runpod_runner.py::run_runpod_job` — as três
     camadas de garantia de morte (trap local, watchdog Celery, reconciler
     celery-beat) estão documentadas lá.
  2. Edge (BLOQUEADO-HARDWARE, ver training_compute.EdgeProvider) — opt-in
     explícito via feature flag training_compute_target='edge' + edge_site
     cadastrado.
  3. Nenhum provedor real disponível (inclusive tenant configurado
     explicitamente com training_compute_target='local'): erro alto, job
     marcado 'failed' com mensagem clara — NUNCA um artefato fake passando
     por treino real. Ultralytics Hub e a simulação local (_simulate_training/
     LocalProvider) foram DELETADOS na task "treino não pode mentir" —
     mentiam sobre o resultado do treino. Ver docs/decisions/adr/ para o
     inventário completo do que foi removido e por quê.

Regra "nunca completed sem artefato verificável" (task "treino não pode
mentir"): todo caminho que persiste um job como 'completed' chama
`app.infrastructure.storage.verify_model_artifact` (HEAD/exists real no
storage) ANTES de gravar — ver o próprio `dispatch_training`,
`runpod_runner.run_runpod_job` (via `verify_completed_fn`) e
`app/api/v1/training/job_handlers.py::training_progress_callback_handler`.

Ver app/domain/services/integration_service.py → resolve_r2_credentials
para a precedência de credenciais R2.
"""
import contextlib
import json
import logging
import os
import secrets
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from uuid import uuid4

import redis as _redis

from app.infrastructure.database.connection import DatabasePool
from app.infrastructure.database.repositories.annotation_repository import (
    AnnotationRepository,
)
from app.infrastructure.database.repositories.training_repository import (
    TrainingRepository,
)
from app.infrastructure.gpu.license_gate import assert_rfdetr_variant_allowed
from app.infrastructure.gpu.runpod_client import RunPodClient, resolve_runpod_api_key
from app.infrastructure.gpu.runpod_runner import JobKind, JobStoppedError, run_runpod_job
from app.infrastructure.gpu.training_compute import get_training_compute
from app.infrastructure.queue.celery_app import celery
from app.infrastructure.storage import verify_model_artifact
from app.infrastructure.storage.local_storage import get_storage

logger = logging.getLogger(__name__)

# Alias preservado (era a classe local `_JobStoppedError`): agora vive em
# `runpod_runner.py` (genérica — reusável por qualquer JobKind), mas o nome
# privado permanece aqui porque é isso que dispatch_training/testes importam.
# Job foi parado explicitamente (stop_job_handler) durante o dispatch.
#
# Achado da revisão adversarial (ainda válido com RunPod): sem essa
# distinção, um stop que chega entre o início do dispatch e a criação do pod
# (ou entre a criação e o próximo poll do watchdog) era tratado como falha
# genérica → update_job("failed", ...) sobrescrevia 'stopped' e o Celery
# reagendava dispatch_training (max_retries=1), provisionando um SEGUNDO
# pod GPU pago pra um job que o usuário já tinha cancelado.
# dispatch_training captura este tipo especificamente e NUNCA chama
# self.retry() para ele.
_JobStoppedError = JobStoppedError


class _DispatchDuplicadoError(RuntimeError):
    """Segunda entrega Celery do MESMO job — já existe pod em voo (#510).

    `task_acks_late=True` (celery_app.py) faz o broker REENTREGAR a task
    quando o worker morre no meio do dispatch (redeploy). Não é retry
    (`max_retries=0` não impede redelivery) e não é falha: o pod nº1 segue
    vivo e reportando. A 2ª entrega tem de sair CALADA — sem regravar o
    callback_token (todo callback do pod nº1 passaria a tomar 403), sem
    reconstruir o dataset.zip e sem criar um segundo pod pago.
    """

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
_PROGRESS_TTL = 86400  # 24h

# ADR-0047 (estendida pela task "treino honesto" — achado C5): QUALQUER
# caminho que manda dados de treino a uma GPU de terceiro — RunPod REST
# real incluído, não só Ultralytics Hub e o fluxo legado Vast+Roboflow
# (deletados) — só dispara com opt-in EXPLÍCITO por tenant (feature flag),
# nunca só por uma env var estar setada no processo (isso valeria pra TODOS
# os tenants sem chave própria, achado da investigação C-04 da task-086).
_FEATURE_FLAG_THIRD_PARTY_CLOUD = "training_third_party_cloud_enabled"


def _third_party_cloud_training_enabled(tenant_id: str | None) -> bool:
    """Opt-in explícito por tenant pra qualquer GPU de terceiro (RunPod REST
    real, Ultralytics Hub e Vast+Roboflow legado — ambos deletados).

    Fail-safe: qualquer erro de leitura (DB indisponível, tenant sem flags,
    tenant_id ausente) => False. Um erro aqui deve BLOQUEAR o caminho de
    risco (envio a terceiro), nunca liberá-lo — mesmo espírito do ADR-0017,
    aplicado ao inverso de onde ele normalmente se aplica.
    """
    if not tenant_id:
        return False
    try:
        from uuid import UUID  # noqa: PLC0415

        from app.infrastructure.database.repositories.tenant_settings_repository import (  # noqa: PLC0415,E501
            TenantSettingsRepository,
        )

        pool = DatabasePool.get_instance()
        if pool is None:
            return False
        flags = TenantSettingsRepository(pool).get_feature_flags(UUID(str(tenant_id)))
        return flags.get(_FEATURE_FLAG_THIRD_PARTY_CLOUD) is True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "third_party_cloud_flag_read_failed: tenant=%s err=%s", tenant_id, exc
        )
        return False


def _publish_progress(job_id: str, payload: dict[str, Any]) -> None:
    """Publica progresso no Redis (SET para polling + PUBLISH para WebSocket bridge)."""
    try:
        r = _redis.from_url(_REDIS_URL, decode_responses=True)
        serialized = json.dumps(payload)
        r.setex(f"training_progress:{job_id}", _PROGRESS_TTL, serialized)
        r.publish(f"training_progress:{job_id}", serialized)
        r.close()
    except Exception as exc:
        logger.debug("publish_progress_failed: job=%s err=%s", job_id, exc)


_FRAMEWORK_LABELS = {"rfdetr": "RF-DETR", "yolo26": "YOLO26", "yolox": "YOLOX"}


def _primeira_metrica(metrics: dict, *chaves: str) -> float:
    """Primeira das `chaves` presente e numérica em `metrics`, senão 0.0.

    Existe porque a coluna `trained_models.map50` era preenchida só com
    `metrics.get("mAP50")` — chave que NENHUM runner deste repositório jamais
    emitiu. O RF-DETR (via `remote_train._metricas_do_log`) reporta `map50`, com
    'm' minúsculo; a grafia procurada aqui era herança do fluxo Ultralytics Hub,
    deletado. Resultado medido em 02/09: TODOS os modelos do tenant com
    map50/precision/recall = 0, e o ranking campeão×desafiante comparando zeros
    com zeros — inclusive um censo anterior que precisou recalcular tudo rodando
    inferência de novo, sem saber que a causa era esta linha.

    Aceitar as duas grafias (em vez de renomear no runner) mantém compatível o
    que já está gravado e o que qualquer runner futuro venha a emitir.
    """
    for chave in chaves:
        if chave in metrics:
            try:
                return float(metrics[chave])
            except (TypeError, ValueError):
                continue
    return 0.0


def _build_model_name(framework: str, model_size: str, job_id: str) -> str:
    """Nome honesto do trained_model: reflete o framework REAL do artefato.

    `model_size` (ex.: 'yolo26n') é um campo legado do TrainingService que só
    valida variantes YOLO26 (yolo26n/s/m/l/x) — pra jobs rfdetr/yolox ele
    chega aqui só com o default 'yolo26n', sem relação com o artefato
    treinado. Incluí-lo no nome pra esses frameworks batizaria RF-DETR de
    "YOLO26 yolo26n": só entra no nome quando o framework É yolo26.
    """
    label = _FRAMEWORK_LABELS.get(framework, framework.upper())
    short_job = job_id[:8]
    if framework == "yolo26":
        return f"{label} {model_size} - Job {short_job}"
    return f"{label} - Job {short_job}"


# Regra de nomenclatura do modelo no nascimento (dono do produto, requisito):
# "Logikos <MÓDULO> <escopo> · DD/MM HHhMM" — substitui o alias legado
# "Logikos V<n> · DD/MM" (task D3). <MÓDULO> é o module_code traduzido/
# maiúsculo; <escopo> é "Completo" quando o treino cobre todas as classes
# ATIVAS do módulo, ou os nomes das classes treinadas quando é subconjunto.
_MODULE_LABELS_PT: dict[str, str] = {
    "epi": "EPI",
    "quality": "QUALIDADE",
    "counting": "CONTAGEM",
    "fueling": "ABASTECIMENTO",
    "basic": "BÁSICO",
    "analytics": "ANALYTICS",
}

# Teto de classes nomeadas no escopo ("Luvas+Máscara"). Acima disso, o nome
# comercial vira ilegível — troca para "N classes" (ex.: "7 classes").
_ESCOPO_TETO_CLASSES = 3

# Fallback honesto quando a informação de classes treinadas genuinamente não
# existe (lookup falhou, dataset_version sem class_distribution) — NUNCA
# "Completo" (mentira de cobertura) nem um subconjunto inventado.
_ESCOPO_PERSONALIZADO = "Personalizado"


def _module_label(module_code: str) -> str:
    """<MÓDULO> do nome comercial: module_code em PT-BR maiúsculo.

    Fallback: o próprio module_code.upper() — um módulo futuro sem entrada
    no mapa ainda produz um nome válido (ex.: "NOVOMODULO"), nunca quebra o
    dispatch nem vira 'None' no nome do cliente.
    """
    return _MODULE_LABELS_PT.get(module_code, module_code.upper())


def _prettify_class_name(class_name: str) -> str:
    """class_name interno (snake_case, ex. 'no_helmet') -> rótulo apresentável
    quando a classe treinada não tem display_name cadastrado em
    module_classes (classe custom do tenant, fora do catálogo global)."""
    return class_name.replace("_", " ").strip().title()


def _classes_treinadas(class_distribution: dict[str, Any] | None) -> list[str]:
    """class_name das classes REALMENTE treinadas, lidas de
    dataset_versions.class_distribution (chaves são class_name — task-077/
    versioning_v2.py `_fetch_annotations`/`build_dataset_version_v2`).

    Filtra chaves reservadas (prefixo "__", ex. "__sem_suporte_treino__" —
    versioning_v2.py: classe excluída do treino por falta de suporte,
    registrada na versão mas NÃO é uma classe treinada).
    """
    if not class_distribution:
        return []
    return [k for k in class_distribution if not str(k).startswith("__")]


def _module_class_catalog(repo, module_code: str) -> dict[str, str]:
    """class_name -> display_name das classes ATIVAS do módulo (module_classes,
    catálogo global — mesma tabela de ModuleRepository.get_classes).

    Catálogo vazio (módulo sem linhas cadastradas, ou a query falhou) nunca
    deixa `_derive_scope` inventar "Completo": sem saber o total de classes
    do módulo, não há como provar cobertura — cai direto no ramo de
    subconjunto nomeado (comportamento correto e mais seguro).
    """
    try:
        rows = repo._execute(
            "SELECT class_name, display_name FROM module_classes "
            "WHERE module_code = %s AND is_active = TRUE",
            (module_code,),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "module_class_catalog_lookup_failed: module_code=%s err=%s",
            module_code, exc,
        )
        return {}
    return {
        row["class_name"]: (
            row.get("display_name") or _prettify_class_name(row["class_name"])
        )
        for row in rows
    }


def _derive_scope(trained_class_names: list[str], catalog: dict[str, str]) -> str:
    """<escopo> do nome comercial: "Completo" quando as classes treinadas
    cobrem TODO o catálogo ativo do módulo; senão, os nomes das classes
    treinadas juntados com "+" (ex.: "Luvas+Máscara"); acima do teto de
    classes, "N classes" em vez de um nome quilométrico.

    trained_class_names vazio (nenhuma classe real no class_distribution) ->
    _ESCOPO_PERSONALIZADO — mesmo fallback honesto de quando a informação
    simplesmente não existe (ver _build_display_name).
    """
    if not trained_class_names:
        return _ESCOPO_PERSONALIZADO
    trained = set(trained_class_names)
    if catalog and trained >= set(catalog):
        return "Completo"
    labels = sorted(catalog.get(name, _prettify_class_name(name)) for name in trained)
    if len(labels) > _ESCOPO_TETO_CLASSES:
        return f"{len(labels)} classes"
    return "+".join(labels)


def _resolve_display_name(existing_display_name: str | None, auto_generated: str) -> str:
    """Precedência humano > auto-gerado (regra do dono do produto): um
    display_name já definido por um humano NUNCA é sobrescrito pelo
    auto-gerado.

    Não existe hoje neste repositório nenhuma rota PATCH/PUT de rename de
    modelo nem coluna/marcador de proveniência dedicado (conferido: grep por
    "rename"/"display_name" em services/api/app não encontrou nenhum —
    trained_models.display_name só é escrito uma vez, no INSERT). Por isso a
    única fonte de verdade possível hoje é "já tem valor" == "alguém (humano,
    ou um dispatch anterior) já decidiu o nome". Esta função é o ponto único
    onde essa precedência é aplicada — pronta para qualquer INSERT/UPDATE
    futuro (inclusive uma futura rota de rename) chamar sem risco de
    sobrescrever um nome humano.
    """
    return existing_display_name if existing_display_name else auto_generated


def _classes_para_metrics(repo, dataset_version_id: str | None) -> dict[str, Any]:
    """Classes treinadas, prontas pra entrar em `trained_models.metrics`.

    POR QUE existe: até aqui o registro nascia SEM classe nenhuma. Os 4
    modelos mais recentes do DEV (2ea8eb62, 1deadfb0, b9243540, 6ca25ee9) só
    têm `map50` agregado — descobrir o que cada um detecta exigiu caçar o
    `dataset_version_id` e ler `class_distribution` à mão. Pior: o NOME não
    serve de atalho. `6ca25ee9` se chama "5 classes", tem mesmo 5, e todas
    são de PRESENÇA — ele é incapaz de acusar violação, apesar do melhor
    mAP50 do acervo. Sem a lista gravada no nascimento, quase ligamos em
    produção um modelo que não geraria um único alerta.

    Aditivo: devolve `{}` (nada a acrescentar) sempre que não houver fato
    registrado — nunca inventa lista, nunca deriva do nome, nunca substitui
    chave que o provider já tenha mandado.
    """
    if not dataset_version_id:
        return {}
    try:
        dsv = repo._execute_one(
            "SELECT class_distribution FROM dataset_versions WHERE id = %s",
            (str(dataset_version_id),),
        )
        classes = _classes_treinadas((dsv or {}).get("class_distribution"))
    except Exception as exc:  # noqa: BLE001 — nunca derruba o registro do treino
        logger.warning(
            "classes_para_metrics_falhou: dataset_version=%s err=%s",
            dataset_version_id, exc,
        )
        return {}
    if not classes:
        return {}
    return {
        "classes": sorted(classes),
        "classes_origem": "dataset_versions.class_distribution",
    }


def _fetch_scope_info(repo, dataset_version_id: str) -> tuple[str, str | None]:
    """(module_code, escopo) do nome comercial, lidos do dataset_version que
    originou o treino (`dataset_versions.module_code`/`class_distribution`).

    Fail-safe total: qualquer erro de leitura (dataset_version sem linha,
    module_classes indisponível, tipo inesperado) devolve module_code
    default + escopo None — o chamador (_build_display_name) já sabe cair
    num rótulo honesto sem inventar cobertura. Uma falha aqui nunca derruba
    o display_name inteiro (comparado ao guard mais amplo do call site, que
    cobre só a contagem de versão).
    """
    module_code = "epi"
    escopo: str | None = None
    try:
        dsv = repo._execute_one(
            "SELECT module_code, class_distribution FROM dataset_versions "
            "WHERE id = %s",
            (str(dataset_version_id),),
        )
        if dsv:
            module_code = dsv.get("module_code") or module_code
            trained = _classes_treinadas(dsv.get("class_distribution"))
            catalog = _module_class_catalog(repo, module_code)
            escopo = _derive_scope(trained, catalog)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "display_name_scope_lookup_failed: dataset_version=%s err=%s",
            dataset_version_id, exc,
        )
    return module_code, escopo


def _build_display_name(
    version_n: int,
    when: datetime | None = None,
    module_code: str = "epi",
    escopo: str | None = None,
) -> str:
    """Nome comercial pro cliente (regra do dono do produto, REQUISITO — não
    é o legado "Logikos V<n>" da task D3): "Logikos <MÓDULO> <escopo> ·
    DD/MM HHhMM". NUNCA framework/".py"/UUID/job-id no nome.

    `version_n` preservado na assinatura por compatibilidade com o único
    call site existente (que ainda precisa contar "modelo nº quantos" pra
    outros fins) — não entra mais no texto: o formato exigido não tem slot
    de versão (identificação passou a ser módulo+escopo+quando).

    escopo=None: quando a informação de classes treinadas genuinamente não
    existe neste ponto (lookup falhou, dataset_version sem
    class_distribution) — usa o rótulo honesto _ESCOPO_PERSONALIZADO, que
    não reivindica cobertura total nem nomeia um subconjunto não confirmado.

    Fuso: grep no repo (ZoneInfo/America/Sao_Paulo) não encontrou nenhuma
    conversão de timezone pro backend — todo `datetime.now()` do módulo já
    usa UTC (era o padrão do próprio `_build_display_name` legado). Mantido
    aqui pela mesma razão do resto do dispatch: sem uma conversão já
    estabelecida em algum lugar do sistema, inventar uma aqui seria a
    "invenção" que a regra proíbe. DÍVIDA CONHECIDA: como display_name é uma
    string gravada uma vez (não um timestamp reformatado no cliente como o
    resto da UI faz), a hora mostrada ao cliente é UTC crua — pode divergir
    do horário local do site (ex. RVB/Blumenau, UTC-3) em até algumas horas.
    """
    quando = when or datetime.now(timezone.utc)
    modulo = _module_label(module_code)
    escopo_final = escopo if escopo else _ESCOPO_PERSONALIZADO
    return f"Logikos {modulo} {escopo_final} · {quando.strftime('%d/%m')} {quando.strftime('%Hh%M')}"


@celery.task(
    # D1: max_retries=0. Retry automático de job GPU é estritamente nocivo:
    # mesmos inputs dão a mesma falha, dobra o custo, e — o pior — APAGA a
    # evidência da tentativa informativa. Foi o retry que sobrescreveu o
    # `ep 29` e o "Module onnx is not installed!" do job f183719a, deixando
    # só uma mensagem genérica. Job GPU que falha fica `failed`, VISÍVEL;
    # re-tentar é decisão de quem diagnosticou, nunca do Celery.
    bind=True, max_retries=0, queue="training",
    name="tasks.training.dispatch_training",
)
def dispatch_training(
    self,
    job_id: str,
    dataset_version_id: str,
    model_size: str = "yolo26n",
    epochs: int = 50,
    imgsz: int = 640,
    batch: int = 16,
) -> dict:
    """Dispara treinamento real via RunPod (framework do job: rfdetr/yolox/yolo26)."""
    logger.info(
        "dispatch_training_start: job_id=%s model=%s epochs=%d",
        job_id, model_size, epochs,
    )

    pool = DatabasePool.get_instance()
    repo = AnnotationRepository(pool)

    def update_job(
        status: str,
        progress: int = 0,
        epoch: int = 0,
        metrics: dict | None = None,
        error_msg: str | None = None,
    ) -> None:
        _gravar_progresso_do_job(
            repo, job_id, status, progress, epoch, metrics, error_msg,
        )
        _publish_progress(job_id, {
            "job_id": job_id,
            "stage": status,
            "progress": progress,
            "epoch": epoch,
            "metrics": metrics or {},
            "error": error_msg,
        })

    try:
        update_job("running", progress=0)

        # WS-D1/ADR-0039: gate do RunPod é tenant-aware (resolve_runpod_api_key:
        # integration store do tenant > env), igual ao que o docstring do
        # módulo sempre alegou pro Vast.ai — mesmo comportamento preservado
        # na troca de provedor (tenant com chave SÓ no integration store,
        # sem RUNPOD_API_KEY no env, ainda dispara o dispatch real).
        tenant_id = _get_job_tenant_id(job_id)
        compute = get_training_compute(tenant_id)
        logger.info(
            "dispatch_training_compute: job_id=%s provider=%s",
            job_id, type(compute).__name__,
        )
        result = compute.dispatch(
            job_id, dataset_version_id, model_size, epochs, imgsz, batch,
            update_job, tenant_id=tenant_id,
        )

        if result.get("status") == "running":
            # Dispatch assíncrono (hoje só EdgeProvider) — job fica
            # 'running' (já setado acima); sem trained_models ainda, a
            # finalização real depende de um callback que não existe
            # (BLOQUEADO-HARDWARE, ver ADR-0039).
            logger.info(
                "dispatch_training_async_pending: job_id=%s source=%s",
                job_id, result.get("source"),
            )
            return {"job_id": job_id, "status": "running", "source": result.get("source")}

        model_path = result.get("model_path", f"models/{job_id}/best.pt")
        metrics = result.get("metrics", {})
        # Origem do treino (migration 090): cada branch de dispatch informa
        # 'source' no resultado ('runpod' hoje — Hub, simulação e Vast.ai
        # foram deletados/substituídos).
        origin = result.get("source", "unknown")

        # "Treino não pode mentir": nunca marcar completed/gravar trained_models
        # sem confirmar que o artefato existe DE FATO no storage (HEAD/exists
        # real — nunca confiar só no que o provider *diz* que produziu).
        # Achado desta task: antes, um provider podia relatar sucesso sem
        # nenhum artefato real (Hub com export falho, callback adulterado)
        # e o registry ganhava uma linha apontando pra um objeto inexistente.
        if not verify_model_artifact(tenant_id, model_path):
            raise RuntimeError(
                "Treino reportou sucesso mas o artefato do modelo não foi "
                f"confirmado no storage (r2_key={model_path!r}) — job={job_id} "
                "nunca registrado como completed sem artefato real."
            )

        # Guarda anti-duplicação (ajuste vinculante #2): job_id não tem UNIQUE
        # e o bridge (socket_bridge._register_trained_model) também registra.
        existing = repo._execute_one(
            "SELECT id FROM trained_models WHERE job_id = %s LIMIT 1", (job_id,)
        )
        if existing:
            logger.info("dispatch_training_model_exists: job_id=%s — skip INSERT", job_id)
        else:
            new_model_id = str(uuid4())
            # Linhagem (migration 098): framework vem do job (training_jobs.framework,
            # NOT NULL DEFAULT 'rfdetr' desde a 097); r2_onnx_key só é preenchido
            # quando o artefato É de fato um objeto R2 (fluxo runpod real —
            # model_path == r2_onnx_key, ver runpod_runner.run_runpod_job) —
            # nunca para hub/simulado, cujo model_path não aponta pra nenhum
            # artefato real.
            r2_onnx_key = model_path if origin == "runpod" else None
            # Nome honesto (fix "nome interno honesto"): o nome tinha
            # "YOLO26" hardcoded (resto da era Ultralytics Hub) mesmo quando
            # o artefato real é rfdetr/yolox — framework é NOT NULL DEFAULT
            # 'rfdetr' desde a 097, buscado aqui pra bater com o mesmo
            # tj.framework que a coluna FRAMEWORK abaixo já lê corretamente.
            job_row = repo._execute_one(
                "SELECT framework FROM training_jobs WHERE id = %s", (job_id,)
            )
            job_framework = str((job_row or {}).get("framework") or "rfdetr")
            model_name = _build_model_name(job_framework, model_size, job_id)
            # display_name (migration 129, regra de nomenclatura no nascimento
            # — dono do produto, requisito): "Logikos <MÓDULO> <escopo> ·
            # DD/MM HHhMM", alias comercial voltado ao cliente, NUNCA o `name`
            # interno acima (framework/job-id são jargão de stack). Só
            # calculado quando o tenant é resolvível — sem tenant não há como
            # contar "modelo nº quantos" e o front já cai em "Logikos"
            # (NOME_PADRAO_CLIENTE) quando display_name vem NULL.
            #
            # Precedência humano > auto (_resolve_display_name): esta é a
            # única linha que grava trained_models.display_name (INSERT,
            # nunca UPDATE — o guard `existing` acima já garante que este
            # branch só roda quando NENHUMA linha existe pro job ainda), então
            # não há nome humano prévio pra preservar AQUI. A chamada
            # existe mesmo assim porque é o ponto único onde a precedência é
            # aplicada — pronta pra um futuro caller (ex. rota de rename, ou
            # um job de resync) que já tenha uma linha existente reusar sem
            # risco de sobrescrever.
            display_name = None
            if tenant_id:
                try:
                    contagem = repo._execute_one(
                        "SELECT COUNT(*) AS cnt FROM trained_models tm "
                        "LEFT JOIN users u ON u.id = tm.user_id "
                        "WHERE COALESCE(tm.tenant_id, u.tenant_id) = %s",
                        (tenant_id,),
                    )
                    version_n = int((contagem or {}).get("cnt") or 0) + 1
                    modulo_do_job, escopo_do_job = _fetch_scope_info(
                        repo, dataset_version_id
                    )
                    auto_display_name = _build_display_name(
                        version_n, module_code=modulo_do_job, escopo=escopo_do_job,
                    )
                    display_name = _resolve_display_name(None, auto_display_name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "display_name_version_lookup_failed: job=%s tenant=%s err=%s",
                        job_id, tenant_id, exc,
                    )
            # C2 (task "treino honesto"): metrics (JSONB, migration 098 —
            # campo JSON já existente, sem migration nova) carrega o marcador
            # {'simulated': true, ...} pra artefatos simulados — indelével,
            # sobrevive independente de qualquer futura mudança em `origin`.
            # Registro nasce COMPLETO: sem isto o modelo entra no acervo sem
            # nenhuma classe registrada (ver _classes_para_metrics). Só
            # ACRESCENTA — chave que o provider já mandou vence, e o dict do
            # provider não é mutado (`metrics` ainda é o que vai pro
            # update_job no fim da função).
            metrics_registro = {
                **_classes_para_metrics(repo, dataset_version_id),
                **metrics,
            }
            repo._execute_mutation_no_return(
                """INSERT INTO trained_models
                   (id, user_id, job_id, name, display_name, model_path,
                    map50, precision, recall, is_active, created_at,
                    created_by, origin, tenant_id, framework,
                    r2_onnx_key, dataset_version_id, metrics)
                   SELECT %s, tj.user_id, %s, %s, %s, %s, %s, %s, %s, FALSE, NOW(),
                          tj.user_id, %s, u.tenant_id, tj.framework, %s, %s, %s
                   FROM training_jobs tj
                   JOIN users u ON u.id = tj.user_id
                   WHERE tj.id = %s""",
                (
                    new_model_id, job_id,
                    model_name,
                    display_name,
                    model_path,
                    _primeira_metrica(metrics, "mAP50", "map50"),
                    metrics.get("precision", 0.0),
                    metrics.get("recall", 0.0),
                    origin,
                    r2_onnx_key,
                    dataset_version_id,
                    json.dumps(metrics_registro),
                    job_id,
                ),
            )
            # WS-C1 (best-effort): dispara avaliação campeão×desafiante do
            # modelo recém-criado. Nunca falha o job de treino — o modelo
            # criado aqui tipicamente não tem r2_onnx_key (coluna legada
            # model_path é reusada pro path do artefato nesta branch), a
            # task retorna status="error"/missing_onnx_key graciosamente
            # nesse caso (mesmo padrão de tasks/model_validation.py).
            #
            # Não há mais branch "simulated" a pular (task "treino não pode
            # mentir" — _simulate_training foi deletado): o artefato já foi
            # confirmado real no storage acima (verify_model_artifact), então
            # todo modelo que chega aqui é candidato legítimo a campeão.
            try:
                from app.infrastructure.queue.tasks.model_evaluation import (  # noqa: PLC0415,E501
                    evaluate_challenger_model,
                )
                evaluate_challenger_model.delay(new_model_id)
            except Exception as eval_exc:  # noqa: BLE001
                logger.warning(
                    "dispatch_training_eval_trigger_failed: model=%s err=%s",
                    new_model_id, eval_exc,
                )

        # ⛔ NUNCA `epoch=epochs`: `epochs` é o total PEDIDO, e este UPDATE roda
        # DEPOIS do callback final do pod — gravava o orçamento por cima da
        # contagem real e desfazia o guard do #420/#536 sem deixar rastro.
        # Medido no A/B do #536: job 28dc8844 rodou 17 épocas (early stop) e a
        # linha ficou com 50. `epochs_ran` é a contagem do próprio pod; sem ela,
        # 0 preserva o que o pod já reportou (NULLIF(...,0) no UPDATE).
        try:
            rodadas = int(float((metrics or {}).get("epochs_ran") or 0))
        except (TypeError, ValueError):
            rodadas = 0
        update_job("completed", progress=100, epoch=rodadas, metrics=metrics)
        logger.info("dispatch_training_completed: job_id=%s", job_id)
        return {"job_id": job_id, "status": "completed", "metrics": metrics}

    except _JobStoppedError as exc:
        # Job parado explicitamente via stop_job_handler — status e
        # callback_token já foram tratados por lá. NUNCA marcar 'failed'
        # (sobrescreveria 'stopped') nem reagendar (self.retry provisionaria
        # uma segunda instância GPU paga para um job já cancelado).
        logger.info("dispatch_training_stopped: job_id=%s msg=%s", job_id, exc)
        return {"job_id": job_id, "status": "stopped"}

    except _DispatchDuplicadoError as exc:
        # ANTES do `except Exception`: marcar 'failed' aqui sobrescreveria o
        # progresso do pod que está treinando AGORA, e reagendar criaria o
        # terceiro pod. Sai calado (#510).
        logger.warning("dispatch_training_duplicado: job_id=%s msg=%s", job_id, exc)
        return {"job_id": job_id, "status": "already_dispatched"}

    except Exception as exc:
        logger.error(
            "dispatch_training_failed: job_id=%s err=%s", job_id, exc, exc_info=True
        )
        # Falha custa GPU igual: o runner anexa `gpu_cost` à exceção (ver
        # runpod_runner.run_runpod_job) justamente para a conta fechar mesmo
        # quando o treino morre na época 0. Antes disto, `actual_usd` ficava
        # NULL em toda falha — lacuna D-155, a mesma janela do órfão.
        custo = getattr(exc, "gpu_cost", None)

        # D2: o log do pod (capturado ANTES do terminate) vai para o R2 e as
        # últimas linhas entram no error_message. Sem isto, toda falha de pod
        # era cega por construção — o log morria com o pod.
        log_pod = getattr(exc, "pod_log", "") or ""
        log_key = None
        if log_pod:
            try:
                get_storage(tenant_id=_get_job_tenant_id(job_id)).upload_bytes(
                    f"jobs/{job_id}/pod.log", log_pod.encode("utf-8"), "text/plain",
                )
                log_key = f"jobs/{job_id}/pod.log"
            except Exception as up_exc:  # noqa: BLE001
                logger.warning("pod_log_upload_falhou: job=%s err=%s", job_id, up_exc)

        cauda = "\n".join(log_pod.splitlines()[-50:])[:2000] if log_pod else ""
        msg = str(exc)
        if cauda:
            msg = f"{msg}\n--- log do pod (últimas linhas) ---\n{cauda}"

        metrics_falha: dict = {}
        if custo:
            metrics_falha["gpu_cost"] = custo
        if log_key:
            metrics_falha["pod_log_r2_key"] = log_key

        with contextlib.suppress(Exception):
            update_job(
                "failed",
                metrics=metrics_falha or None,
                error_msg=msg[:2500],
            )
        # Sem retry (D1): a exceção sobe e o job fica `failed` com a causa.
        raise


def _gravar_progresso_do_job(
    repo,
    job_id: str,
    status: str,
    progress: int = 0,
    epoch: int = 0,
    metrics: dict | None = None,
    error_msg: str | None = None,
) -> None:
    """UPDATE de progresso do job — no módulo, não na closure, para ter teste.

    ⚠️ NUNCA escreva `%s` num comentário SQL desta função (nem em nenhuma).
    O psycopg2 interpola por % na string CRUA, antes de qualquer parser — ele
    não sabe o que é comentário. Em 2026-08-20 um comentário explicando o
    conserto do "dois escritores" continha `metrics = <placeholder>` escrito com
    a sintaxe literal, virou o 11º placeholder para 10 parâmetros, e o dispatch
    morreu com `IndexError` por três disparos seguidos. Há teste contando
    placeholders contra parâmetros (test_sql_placeholders.py).

    Estava embutido no dispatch e por isso o defeito do #459 (dispatch
    escrevendo por cima do que o pod reportou) não tinha como ser fixado por
    teste nenhum. Extraído SEM mudança de comportamento além da correção.
    """
    # AND status != 'stopped': o dispatch NUNCA escreve 'stopped' aqui
    # (só stop_job_handler o faz) — este guard impede que um stop
    # concorrente seja revertido de volta para 'running'/'failed'
    # (achado da revisão: race com update_fn("running", ...) antes do
    # provisioning do pod GPU).
    repo._execute_mutation_no_return(
        """UPDATE training_jobs
           SET status = %s,
               -- Os três campos abaixo tinham o MESMO defeito: o dispatch
               -- escrevia por cima do que o pod já havia reportado, com os
               -- DEFAULTS da assinatura (progress=0, epoch=0, metrics=None).
               -- update_fn("running", progress=5) roda depois do pod criado,
               -- e update_job("failed", metrics=...) roda no fim — os dois
               -- podem chegar depois de um callback do pod (issue #459).
               --
               -- GREATEST: progresso não anda para trás. Um "failed" sem
               -- progress (default 0) não zera 90 por cento de treino já feito.
               progress = GREATEST(COALESCE(progress, 0), %s),
               -- NULLIF(...,0): o 0 default do dispatch nunca apaga a época
               -- real que o pod reportou (mesma família do #420).
               current_epoch = COALESCE(NULLIF(%s, 0), current_epoch),
               -- FUNDE, não substitui — igual ao repository. A atribuição crua
               -- (metrics recebendo o valor direto, sem o `||`)
               -- era o outro lado do "dois escritores" que o repository já
               -- tinha resolvido do lado dele. A fusão é do BANCO, atômica
               -- no mesmo UPDATE: SELECT -> merge em Python -> UPDATE
               -- reabriria a corrida, só que maior.
               metrics = COALESCE(metrics, '{}'::jsonb) || %s::jsonb,
               -- Mensagem ESPECÍFICA nunca é rebaixada por genérica.
               -- O callback do pod grava a causa real ("Could not find
               -- class names in /root/dataset_coco...") e o `except`
               -- deste dispatch gravava "Job runpod failed" por cima,
               -- apagando a única pista. Só preenche se estiver vazia,
               -- ou se a nova for MAIOR (mais informativa).
               error_message = CASE
                   WHEN %s IS NULL THEN error_message
                   WHEN error_message IS NULL OR error_message = '' THEN %s
                   WHEN length(%s) > length(error_message) THEN %s
                   ELSE error_message
               END,
               started_at = CASE
                   WHEN started_at IS NULL THEN NOW()
                   ELSE started_at
               END,
               completed_at = CASE
                   WHEN %s IN ('completed', 'failed') THEN NOW()
                   ELSE completed_at
               END
           WHERE id = %s AND status != 'stopped'
             -- Reentrega do broker (visibility_timeout ~1h, task_acks_late)
             -- roda update_job("running", 0) de novo DEPOIS de o callback do
             -- pod já ter fechado o job. Terminal não ressuscita: 'running'
             -- nunca sobrescreve 'completed'/'failed' (verificação do #510).
             -- A direção contrária (running -> failed/completed) segue livre.
             AND NOT (%s = 'running' AND status IN ('completed', 'failed'))""",
        (
            status, progress, epoch,
            json.dumps(metrics or {}),
            error_msg, error_msg, error_msg, error_msg,
            status,
            job_id,
            status,  # o %s do guard anti-ressurreicao (NOT running-sobre-terminal)
        ),
    )


def _get_job_tenant_id(job_id: str) -> str | None:
    """tenant_id REAL do job (training_jobs.tenant_id, fallback users.tenant_id)."""
    try:
        pool = DatabasePool.get_instance()
        repo = AnnotationRepository(pool)
        row = repo._execute_one(
            """SELECT COALESCE(tj.tenant_id, u.tenant_id) AS tenant_id
               FROM training_jobs tj
               LEFT JOIN users u ON u.id = tj.user_id
               WHERE tj.id = %s""",
            (job_id,),
        )
        return str(row["tenant_id"]) if row and row.get("tenant_id") else None
    except Exception as exc:
        logger.warning("job_tenant_lookup_failed: job=%s err=%s", job_id, exc)
        return None


_PRESIGNED_GET_TTL = 21600   # 6h — download do dataset pode esperar provisioning
_PRESIGNED_PUT_TTL = 28800   # 8h — uploads acontecem no fim do treino
_DEFAULT_PUBLIC_API_URL = "https://api-v3-production-2b22.up.railway.app"

# RF-DETR/YOLOX esperam pastas "train/valid/test" (padrão Roboflow) — o
# export da pipeline usa "train/val/test" (ver versioning_v2._SPLIT_NAMES).
_TRAIN_ZIP_SPLIT_NAMES = ("train", "val", "test")
_TRAIN_ZIP_FOLDER_ALIAS = {"val": "valid"}


def _build_training_dataset_zip(storage: Any, coco_prefix: str) -> bytes:
    """Empacota o COCO exportado (prefixo com train/val/test) num zip único.

    remote_train.py roda num pod efêmero sem credenciais R2 (só recebe
    presigned URLs, por desenho de segurança do ADR-0038) e baixa UM arquivo
    via GET + `zipfile.ZipFile(...).extractall(...)` — mas `coco_r2_key` é um
    PREFIXO (múltiplos objetos soltos), não um zip; um presigned GET nele não
    resolve pra nada baixável. Sem este empacotamento, todo dispatch real
    pro RunPod falharia no primeiro passo (download do dataset). Renomeia
    "val" → "valid" no zip (RFDETR/Roboflow-padrão) sem alterar o layout
    per-split já consumido por dataset_service.get_version_detail.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for split_name in _TRAIN_ZIP_SPLIT_NAMES:
            zip_folder = _TRAIN_ZIP_FOLDER_ALIAS.get(split_name, split_name)
            prefix = f"{coco_prefix}/{split_name}/"
            empacotadas: set[str] = set()
            for key in storage.list_keys(prefix):
                data = storage.download_bytes(key)
                nome = key[len(prefix):]
                zf.writestr(f"{zip_folder}/{nome}", data)
                if nome != _JSON_SPLIT:
                    empacotadas.add(nome)
            _conferir_zip_contra_coco(storage, prefix, split_name, empacotadas)
    return buf.getvalue()


# Splits que o dispatch empacota, e o JSON que o runner procura em cada um.
# Gabarito: o layout do v3-treino1, único que completou 12 épocas.
_SPLITS_FONTE = ("train", "val", "test")
_JSON_SPLIT = "_annotations.coco.json"


def _conferir_zip_contra_coco(
    storage: Any, prefix: str, split_name: str, empacotadas: set[str]
) -> None:
    """Régua INDEPENDENTE: o zip tem que bater com o que o COCO DECLARA.

    Fonte-de-verdade = `images[].file_name` do `_annotations.coco.json` do
    próprio split, baixado pela CHAVE DETERMINÍSTICA (`download_bytes`) —
    nunca pela lista que montou o zip. Régua que reusa `list_keys` concorda
    com o truncamento que deveria detectar: foi assim que o pré-flight deixou
    passar as 293 imagens que faltavam no `v8-propositor` (#509) — ele contava
    as MESMAS chaves truncadas.

    O nome bate por construção: `versioning_v2._build_coco_split` grava
    `file_name = f"{frame['id']}.{ext}"` e a cópia pro R2 usa
    `{base_key}/{split}/{frame['id']}.{ext}` — mesmo basename.

    Pega os dois modos de perda:
      - chave que não entrou na listagem (truncamento, consistência eventual);
      - `copy_object` que falhou no export — `versioning_v2` só loga o erro e
        segue (`copy_errors` não barra ninguém), então o COCO declara imagem
        que nunca chegou ao R2.

    Roda antes de qualquer pod existir (o zip é montado antes de
    `run_runpod_job`), então falhar aqui custa zero de GPU. A exceção sobe até
    `dispatch_training`, que grava o job como `failed` com esta mensagem.

    Não reclama de SOBRA (`zip ⊃ COCO`): arquivo extra é inerte pro runner,
    que lê o json. Vira erro no dia em que algum runner varrer o diretório.
    """
    chave_json = f"{prefix}{_JSON_SPLIT}"
    try:
        declaradas = {
            str(img["file_name"])
            for img in json.loads(storage.download_bytes(chave_json))["images"]
        }
    except Exception as exc:  # noqa: BLE001 — ausente/ilegível/sem "images"
        raise ValueError(
            f"dataset inconsistente: não deu para ler {chave_json} para "
            f"conferir o split '{split_name}' ({exc}). Nenhum pod foi criado."
        ) from exc

    faltando = declaradas - empacotadas
    if faltando:
        amostra = ", ".join(sorted(faltando)[:3])
        raise ValueError(
            f"dataset incompleto no split '{split_name}': o COCO declara "
            f"{len(declaradas)} imagens e o zip levou {len(empacotadas)} — "
            f"faltam {len(faltando)} (ex.: {amostra}). Nenhum pod foi criado; "
            f"reexporte a versão do dataset ({prefix.rstrip('/')})."
        )


def _preflight_artefato(tenant_id: str | None, coco_r2_key: str) -> str | None:
    """Confere a FONTE do dataset antes de criar pod. Motivo se estiver ruim.

    Valida os OBJETOS SOLTOS sob `{coco_r2_key}/{split}/`, não o `dataset.zip`:
    o zip é cache DERIVADO — `_build_training_dataset_zip` o reconstrói e
    sobrescreve a cada dispatch, a partir justamente desses objetos. Validar o
    zip era validar a coisa certa no momento errado: ele passava e o dispatch
    o substituía por um vazio logo depois.

    Foi essa a causa das 4 falhas de época 0 em 18/08 — `v5-relabel` tinha só
    o zip, os prefixos estavam vazios, e o zip remontado saía com 22 bytes.
    """
    try:
        storage = get_storage(tenant_id=tenant_id)
    except Exception as exc:  # noqa: BLE001
        return f"storage indisponível para conferir {coco_r2_key}: {exc}"

    faltando: list[str] = []
    for split in _SPLITS_FONTE:
        prefix = f"{coco_r2_key}/{split}/"
        try:
            keys = list(storage.list_keys(prefix))
        except Exception as exc:  # noqa: BLE001
            return f"não foi possível listar {prefix}: {exc}"
        if not keys:
            faltando.append(f"{split}/ vazio")
        elif not any(k.endswith(_JSON_SPLIT) for k in keys):
            faltando.append(f"{split}/ sem {_JSON_SPLIT} ({len(keys)} objetos)")

    if faltando:
        return (
            f"fonte do dataset incompleta em {coco_r2_key}: {'; '.join(faltando)}. "
            "O dispatch monta o zip a partir destes prefixos — sem eles o pod "
            "recebe um zip vazio."
        )
    return None


def _get_runpod_training_context(job_id: str) -> dict[str, Any] | None:
    """Resolve o contexto do dispatch REST real (WS-A4, RunPod).

    Retorna None quando o fluxo real não é possível (sem API key resolvível
    ou job sem dataset_version com COCO exportado) — o caller levanta erro
    claro (nunca cai num fluxo alternativo silencioso, ADR-0017).

    Inclui `base_model`/`hyperparams` (migration 097) pro license gate
    (`assert_rfdetr_variant_allowed`, ADR-0044) — nenhuma variante RF-DETR
    fora do allowlist Apache 2.0 pode chegar até o pod.
    """
    try:
        pool = DatabasePool.get_instance()
        if pool is None:
            return None
        repo = TrainingRepository(pool)
        job = repo._execute_one(
            """SELECT tj.id, tj.dataset_version_id, tj.framework, tj.total_epochs,
                      tj.base_model, tj.hyperparams,
                      COALESCE(tj.tenant_id, u.tenant_id) AS tenant_id,
                      dv.coco_r2_key
               FROM training_jobs tj
               LEFT JOIN users u ON u.id = tj.user_id
               LEFT JOIN dataset_versions dv ON dv.id = tj.dataset_version_id
               WHERE tj.id = %s""",
            (job_id,),
        )
        if not job or not job.get("coco_r2_key"):
            return None
        tenant_id = str(job["tenant_id"]) if job.get("tenant_id") else None
        api_key = resolve_runpod_api_key(tenant_id)
        if not api_key:
            return None
        hyperparams = job.get("hyperparams")
        if isinstance(hyperparams, str):
            with contextlib.suppress(ValueError):
                hyperparams = json.loads(hyperparams)
        motivo = _preflight_artefato(tenant_id, str(job["coco_r2_key"]))
        if motivo:
            raise ValueError(f"Pré-flight do dataset falhou — nenhum pod criado. {motivo}")

        return {
            "api_key": api_key,
            "tenant_id": tenant_id,
            "coco_r2_key": str(job["coco_r2_key"]),
            "framework": str(job.get("framework") or "rfdetr"),
            "base_model": job.get("base_model"),
            "hyperparams": hyperparams if isinstance(hyperparams, dict) else {},
            # O SELECT acima já buscava tj.total_epochs e o consumidor já fazia
            # `ctx.get("total_epochs") or epochs` — mas o campo NUNCA entrava
            # aqui, então todo treino caía no default 50 do parâmetro Celery,
            # silenciosamente. Pedir 12 épocas e receber 50 quebra qualquer
            # comparação entre treinos (o TREINO 2 rodou 50 contra 12 do
            # TREINO 1 e a diferença passou despercebida no `current_epoch`).
            "total_epochs": job.get("total_epochs"),
        }
    except ValueError:
        # Pré-flight: erro alto e legível, nunca degradar para "sem contexto"
        # (que viraria simulação silenciosa ou pod criado às cegas).
        raise
    except Exception as exc:
        logger.warning("runpod_context_lookup_failed: job=%s err=%s", job_id, exc)
        return None


def worker_provenance(runner_source: str) -> dict[str, str]:
    """Proveniência do WORKER que despachou, gravada no próprio job.

    O worker não fala HTTP, então não tem `/livez` — mas todo dispatch escreve
    no job, e é aí que a prova cabe. Grava:
      - `worker_commit`: RAILWAY_GIT_COMMIT_SHA, ou "unknown" quando o deploy
        veio de `railway up` (que não carrega proveniência);
      - `runner_sha256`: hash do remote_train.py REALMENTE enviado ao pod.

    Por que existe: o `celery-worker` do DEV nunca teve source git e ficou 5
    dias atrás da develop. Conserto nenhum chegava ao pod, e a verificação
    olhava o `/livez` da API — o sensor certo, no serviço errado. O hash do
    runner responde a pergunta que importa: o pod rodou QUAL código?
    """
    import hashlib  # noqa: PLC0415

    sha = (
        os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or os.environ.get("GIT_COMMIT_SHA")
        or ""
    ).strip() or "unknown"
    return {
        "worker_commit": sha,
        "runner_sha256": hashlib.sha256(runner_source.encode("utf-8")).hexdigest()[:16],
    }


def _read_remote_train_source() -> str:
    """Lê o runner self-contained embarcado no onstart (heredoc).

    O pod RunPod NÃO tem acesso ao repositório — o script inteiro é
    embutido no onstart em vez de baixado. Caminho preservado
    (`training/vast/remote_train.py`) — o executor é agnóstico de provedor
    GPU, só o nome do diretório é histórico (decisão do dono: não mover
    pra reduzir escopo/risco da troca Vast→RunPod).
    """
    from app.infrastructure.queue.tasks.repo_files import find_repo_file  # noqa: PLC0415

    return find_repo_file("training", "vast", "remote_train.py").read_text(
        encoding="utf-8"
    )


def _run_runpod_train_job(
    ctx: dict[str, Any],
    job_id: str,
    model_size: str,
    epochs: int,
    imgsz: int,
    batch: int,
    update_fn,
) -> dict:
    """Fluxo REST real RunPod: presigned URLs + `run_runpod_job` (runner
    genérico — preço/teto/onstart/create/watch/terminate/billing, ver
    `infrastructure/gpu/runpod_runner.py`) com a carga 'train'.

    O pod é SEMPRE terminado (garantido dentro de `run_runpod_job`, camada 2
    de garantia de morte) — nunca vaza GPU paga. callback_token é revogado
    (NULL) ao final, sucesso ou erro.
    """
    pool = DatabasePool.get_instance()
    repo = TrainingRepository(pool)

    tenant_id = ctx.get("tenant_id") or "unknown"
    framework = ctx.get("framework") or (
        "yolox" if "yolox" in model_size.lower() else "rfdetr"
    )
    # O job é a fonte de verdade de total_epochs: o fallback Celery
    # (dispatch_training.delay(job_id, dataset_version_id)) não repassa o
    # que o usuário pediu no create e caía sempre no default 50 do task —
    # com timeout fixo de pod, época demais = timeout, não um treino maior.
    epochs = int(ctx.get("total_epochs") or epochs)

    # Token por-job (ajuste C-1 da crítica original Vast — ainda válido):
    # aleatório, revogável no stop.
    #
    # #510: o WHERE aqui é a REIVINDICAÇÃO do job, não um detalhe. Um
    # redeploy do worker fez o Celery reentregar esta task; a 2ª entrega
    # regravou o callback_token e TODO callback do pod nº1 passou a tomar
    # 403 (job_handlers._progress_callback, 'callback_token_invalido').
    # `gpu_instance_ref IS NOT NULL` == existe pod em voo. Condicionar em
    # `callback_token IS NULL` NÃO funciona: o job já nasce com token
    # (job_handlers._issue_callback_token, escrito pro training-service que
    # não existe mais) — travaria todo dispatch legítimo.
    # rowcount 0 = outra entrega já reivindicou.
    # `_execute_mutation_no_return` devolve cur.rowcount e commita.
    #
    # ATENÇÃO a quem for criar um "re-disparar o MESMO job_id": hoje
    # gpu_instance_ref nunca é limpo, então esse caminho precisaria limpá-lo
    # antes (ou criar uma linha nova, que é o modelo atual de re-treino).
    callback_token = secrets.token_urlsafe(48)
    if not repo._execute_mutation_no_return(
        "UPDATE training_jobs SET callback_token = %s, gpu_provider = 'runpod' "
        "WHERE id = %s AND gpu_instance_ref IS NULL",
        (callback_token, job_id),
    ):
        raise _DispatchDuplicadoError(
            f"Job {job_id} já tem pod em voo (gpu_instance_ref preenchido) — "
            "entrega duplicada do Celery. Token do pod nº1 preservado, "
            "nenhum pod novo criado."
        )

    storage = get_storage(tenant_id)
    # Fine-tune (v10 a partir do weights.pth do v9): hyperparams.init_weights_r2_key.
    # Só artefato do PRÓPRIO tenant — o bucket de plataforma é compartilhado
    # entre tenants sem credencial própria; sem o prefixo, a chave de outro
    # tenant sairia presigned (C-01). Inexistente e fora-do-tenant dão a MESMA
    # mensagem (não vaza existência). Falha aqui = nenhum zip, nenhum pod.
    # O gate de licença (ADR-0044) só lê base_model/model_size/variant dos
    # hyperparams — esta chave passa por ele intacta (teste cobre).
    init_key = str((ctx.get("hyperparams") or {}).get("init_weights_r2_key") or "")
    if init_key and framework != "rfdetr":
        # train_yolox ignora INIT_WEIGHTS_URL: o job "diria" fine-tune e
        # treinaria do zero. Erro claro, não silêncio.
        raise ValueError(
            f"fine-tune (init_weights_r2_key) só é suportado para rfdetr — "
            f"framework={framework}. Nenhum pod criado."
        )
    if init_key and (
        ".." in init_key
        or not init_key.startswith(f"models/{tenant_id}/")
        or not storage.exists(init_key)
    ):
        raise ValueError(
            f"Checkpoint inicial não encontrado: {init_key} — nenhum pod criado."
        )

    zip_key = f"{ctx['coco_r2_key']}/dataset.zip"
    storage.upload_bytes(
        zip_key, _build_training_dataset_zip(storage, ctx["coco_r2_key"]),
        "application/zip",
    )
    dataset_url = storage.generate_presigned_download_url(
        zip_key, ttl=_PRESIGNED_GET_TTL
    )
    r2_onnx_key = runpod_onnx_artifact_key(tenant_id, job_id)
    artifact_prefix = f"models/{tenant_id}/runpod/{job_id}"
    r2_weights_key = f"{artifact_prefix}/weights.pth"
    r2_metrics_key = f"{artifact_prefix}/metrics.json"

    base_url = os.environ.get("PUBLIC_API_URL", _DEFAULT_PUBLIC_API_URL).rstrip("/")
    callback_url = f"{base_url}/api/v1/training/jobs/{job_id}/progress-callback"

    remote_env = {
        "DATASET_URL": dataset_url,
        "FRAMEWORK": framework,
        "EPOCHS": str(epochs),
        "BATCH": str(batch),
        # Sem esta linha a trava do experimento é INERTE: `remote_train.py` lê
        # `BATCH_FIXO` do ambiente DO POD, e `remote_env` é um dict fechado —
        # a variável existir no processo que despacha não a leva a lugar nenhum.
        # Vazia (o normal em produção) = adaptação por placa ligada, como antes.
        "BATCH_FIXO": os.environ.get("BATCH_FIXO", ""),
        "IMGSZ": str(imgsz),
        "CALLBACK_URL": callback_url,
        "CALLBACK_TOKEN": callback_token,
        "UPLOAD_URL_ONNX": storage.generate_presigned_upload_url(
            r2_onnx_key, content_type="application/octet-stream",
            ttl=_PRESIGNED_PUT_TTL,
        ),
        "UPLOAD_URL_WEIGHTS": storage.generate_presigned_upload_url(
            r2_weights_key, content_type="application/octet-stream",
            ttl=_PRESIGNED_PUT_TTL,
        ),
        "UPLOAD_URL_METRICS": storage.generate_presigned_upload_url(
            r2_metrics_key, content_type="application/json",
            ttl=_PRESIGNED_PUT_TTL,
        ),
        # D2 — auto-reporte do log: o pod sobe o PRÓPRIO stdout/stderr ao R2.
        # A API REST do RunPod não expõe logs (`/v1/pods/{id}/logs` → HTTP 400),
        # então depender dela era depender de algo que não existe. O runner já
        # tem presigned PUT para artefato; o log usa o mesmo mecanismo.
        "UPLOAD_URL_LOG": storage.generate_presigned_upload_url(
            f"jobs/{job_id}/pod.log", content_type="text/plain",
            ttl=_PRESIGNED_PUT_TTL,
        ),
        "R2_LOG_KEY": f"jobs/{job_id}/pod.log",
        "R2_ONNX_KEY": r2_onnx_key,
    }
    if init_key:
        remote_env["INIT_WEIGHTS_URL"] = storage.generate_presigned_download_url(
            init_key, ttl=_PRESIGNED_GET_TTL
        )

    client = RunPodClient(ctx["api_key"])

    # Recheca status ANTES de provisionar: fecha a janela de corrida em que
    # um stop concorrente (entre o início do dispatch e este ponto) seria
    # ignorado e um pod GPU pago criado para um job já cancelado (achado da
    # revisão adversarial original Vast — ainda válido).
    current = repo._execute_one(
        "SELECT status FROM training_jobs WHERE id = %s", (job_id,)
    )
    if (current or {}).get("status") == "stopped":
        raise JobStoppedError(
            f"Job {job_id} foi parado antes do provisioning — abortando sem criar pod"
        )

    def _poll_status() -> dict[str, Any]:
        job = repo._execute_one(
            "SELECT status, metrics FROM training_jobs WHERE id = %s", (job_id,)
        )
        raw_metrics = (job or {}).get("metrics") or {}
        if isinstance(raw_metrics, str):
            with contextlib.suppress(ValueError):
                raw_metrics = json.loads(raw_metrics)
        return {
            "status": (job or {}).get("status"),
            "metrics": raw_metrics if isinstance(raw_metrics, dict) else {},
        }

    def _persist_instance_ref(pod_id: str) -> None:
        repo._execute_mutation_no_return(
            "UPDATE training_jobs SET gpu_instance_ref = %s WHERE id = %s",
            (pod_id, job_id),
        )
        # Mesmo checkpoint de progresso que o fluxo Vast tinha logo após a
        # instância existir (pod criado, watchdog prestes a começar).
        update_fn("running", progress=5)

    def _verify_completed(_state: dict[str, Any]) -> bool:
        return verify_model_artifact(tenant_id, r2_onnx_key)

    # Proveniência do worker gravada ANTES do pod subir: qualquer falha depois
    # disto já tem, no próprio job, de qual commit e com qual runner ela veio.
    runner_src = _read_remote_train_source()
    proveniencia = worker_provenance(runner_src)
    if init_key:
        # Linhagem: de QUAL checkpoint este treino partiu (vai em metrics.provenance).
        proveniencia["init_weights_r2_key"] = init_key
    logger.info(
        "dispatch_proveniencia: job=%s worker_commit=%s runner_sha256=%s",
        job_id, proveniencia["worker_commit"], proveniencia["runner_sha256"],
    )
    # ⛔ SEM suppress: falha silenciosa num SENSOR é degradar em silêncio — o
    # defeito que este sensor existe para pegar. Falhou → warning alto e marca
    # no próprio job, para quem ler saber que a proveniência não está lá.
    try:
        repo._execute_mutation_no_return(
            "UPDATE training_jobs SET metrics = COALESCE(metrics,'{}'::jsonb) || %s::jsonb "
            "WHERE id = %s",
            (json.dumps({"provenance": proveniencia}), job_id),
        )
    except Exception as exc:  # noqa: BLE001 — não derruba o dispatch, mas GRITA
        logger.error(
            "provenance_write_failed: job=%s err=%s — o job vai rodar SEM prova "
            "de qual código o despachou", job_id, exc, exc_info=True,
        )
        with contextlib.suppress(Exception):
            repo._execute_mutation_no_return(
                "UPDATE training_jobs SET metrics = COALESCE(metrics,'{}'::jsonb) || %s::jsonb "
                "WHERE id = %s",
                (json.dumps({"provenance_error": str(exc)[:300]}), job_id),
            )

    update_fn("running", progress=2)
    try:
        result = run_runpod_job(
            kind=JobKind.TRAIN,
            job_id=job_id,
            client=client,
            executor_source=runner_src,
            executor_filename="remote_train.py",
            env=remote_env,
            poll_status_fn=_poll_status,
            persist_instance_ref_fn=_persist_instance_ref,
            verify_completed_fn=_verify_completed,
        )
        return {
            "model_path": r2_onnx_key,
            "metrics": result["metrics"],
            "source": "runpod",
        }
    finally:
        # SEMPRE: revogar o token (o pod já foi terminado dentro de
        # run_runpod_job — camada 2 de garantia de morte).
        with contextlib.suppress(Exception):
            repo._execute_mutation_no_return(
                "UPDATE training_jobs SET callback_token = NULL WHERE id = %s",
                (job_id,),
            )


def _dispatch_runpod_train(
    job_id: str,
    model_size: str,
    epochs: int,
    imgsz: int,
    batch: int,
    update_fn,
    tenant_id: str | None = None,
) -> dict:
    """Dispara treinamento real no RunPod (WS-A4, substitui `_dispatch_vast_ai`).

    Fluxo REST real quando o contexto completo existe (API key do tenant ou
    env + dataset_version com COCO exportado): presigned GET/PUTs, pod com
    onstart embutindo remote_train.py, watchdog + terminate garantido — tudo
    via `_run_runpod_train_job`/`infrastructure/gpu/runpod_runner.py`.

    C5/ADR-0047 (task "treino honesto"): RunPod É GPU de terceiro — o fluxo
    REST real só dispara com o MESMO opt-in explícito
    (`training_third_party_cloud_enabled`) que já protegia Hub/legado/Vast.

    C1/ADR-0017 (task "treino honesto"): dataset ausente (sem coco_r2_key
    resolvível) é erro alto com mensagem clara — NUNCA desvia para dataset de
    outra origem.

    ADR-0044 (decisão do dono): valida a variante RF-DETR (base_model/
    hyperparams) contra o allowlist Apache 2.0 ANTES de qualquer chamada de
    rede — `assert_rfdetr_variant_allowed` levanta `RfdetrLicenseError`
    (subclasse de `ValueError`, capturada como falha genérica por
    `dispatch_training`) pra qualquer variante XL/2XL (PML, não Apache).
    """
    if not _third_party_cloud_training_enabled(tenant_id):
        raise RuntimeError(
            "Treino em nuvem de terceiro desabilitado para este tenant "
            f"(training_third_party_cloud_enabled=false) — caminho=runpod "
            f"job={job_id}"
        )
    ctx = _get_runpod_training_context(job_id)
    if ctx is None:
        if not resolve_runpod_api_key(tenant_id):
            raise RuntimeError(
                f"Nenhuma chave RunPod resolvível para o tenant — job={job_id}"
            )
        raise RuntimeError(
            f"Job {job_id}: dataset sem exportação COCO (coco_r2_key ausente "
            "no dataset_version vinculado) — não é possível treinar no "
            "RunPod. Exporte o dataset (build_dataset_version) antes de "
            "disparar o treino."
        )
    assert_rfdetr_variant_allowed(
        ctx["framework"], base_model=ctx.get("base_model"), hyperparams=ctx.get("hyperparams"),
    )
    return _run_runpod_train_job(
        ctx, job_id, model_size, epochs, imgsz, batch, update_fn
    )


def runpod_onnx_artifact_key(tenant_id: str | None, job_id: str) -> str:
    """Chave R2 determinística do ONNX de um job RunPod.

    Mesma fórmula usada no dispatch (`_run_runpod_train_job`, que injeta
    esta chave como R2_ONNX_KEY/UPLOAD_URL_ONNX no pod remoto) e na
    reverificação pós-callback (`app/api/v1/training/job_handlers.py::
    training_progress_callback_handler`) — nunca duplicar como f-string solta
    em outro lugar (task "treino não pode mentir").
    """
    return f"models/{tenant_id}/runpod/{job_id}/model.onnx"

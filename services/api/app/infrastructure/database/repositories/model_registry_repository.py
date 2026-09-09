"""Repository: Model Registry — visão MLOps de public.trained_models (WS-A5).

Complementa TrainingRepository (criação/CRUD legado) com as queries do
registry expandido (migration 098): listagem por tenant+módulo+status,
detalhe com colunas de linhagem, ativação atômica por tenant×módulo e
marcação de validação ONNX (task validate_onnx).

NÃO substitui ModelRolloutRepository — {schema}.models (pin/canary)
permanece intocado (decisão do plano / ADR-0037).
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from app.infrastructure.database.repositories.base import BaseRepository

logger = logging.getLogger(__name__)

# Colunas do registry (003 + 052 + 090 + 098 + 129) — constante interna, nunca
# input de usuário (mesmo padrão _COLS de model_rollout_repository.py).
# tm.display_name (129, task D3 "job/treino aparece com nome cru"): sem essa
# coluna aqui, GET /api/v1/models e /api/v1/models/<id> só devolviam o `name`
# interno ("RF-DETR - Job <uuid>") — o front (modelDisplay.ts) já sabia
# preferir display_name, mas o backend nunca o servia nesta query.
_REGISTRY_COLS = (
    "tm.id, tm.user_id, tm.job_id, tm.name, tm.display_name, tm.model_path, "
    "tm.map50, tm.precision, tm.recall, tm.is_active, tm.created_at, tm.created_by, "
    "tm.origin, tm.tenant_id, tm.framework, tm.r2_onnx_key, tm.r2_weights_key, "
    "tm.metrics, tm.dataset_version_id, tm.module_code"
)


class ModelRegistryRepository(BaseRepository):
    """Queries do registry canônico em public.trained_models. Tenant-scoped (C-01).

    Posse validada por COALESCE(tm.tenant_id, u.tenant_id) — cobre linhas
    legadas (pré-090) com tenant_id NULL via tenant do dono do modelo.
    """

    def list_for_tenant(
        self,
        tenant_id: str,
        module_code: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> list[dict[str, Any]]:
        """Lista modelos do tenant com filtros opcionais de módulo e status."""
        query = (
            f"SELECT {_REGISTRY_COLS} FROM trained_models tm "  # noqa: S608
            "LEFT JOIN users u ON u.id = tm.user_id "
            "WHERE COALESCE(tm.tenant_id, u.tenant_id) = %s"
        )
        params: list[Any] = [str(tenant_id)]
        if module_code:
            query += " AND tm.module_code = %s"
            params.append(module_code)
        if is_active is not None:
            query += " AND tm.is_active = %s"
            params.append(is_active)
        query += " ORDER BY tm.created_at DESC"
        return self._execute(query, tuple(params))

    def get_for_tenant(
        self, model_id: UUID, tenant_id: str
    ) -> Optional[dict[str, Any]]:
        """Busca modelo por ID com todas as colunas do registry, validando tenant."""
        return self._execute_one(
            f"SELECT {_REGISTRY_COLS} FROM trained_models tm "  # noqa: S608
            "LEFT JOIN users u ON u.id = tm.user_id "
            "WHERE tm.id = %s AND COALESCE(tm.tenant_id, u.tenant_id) = %s",
            (str(model_id), str(tenant_id)),
        )

    def get_by_id(self, model_id: UUID | str) -> Optional[dict[str, Any]]:
        """Busca por ID SEM escopo de tenant — uso interno do worker
        (task validate_onnx recebe id já validado pelo fluxo de registro)."""
        return self._execute_one(
            "SELECT * FROM trained_models WHERE id = %s",
            (str(model_id),),
        )

    def activate_for_tenant_module(
        self, model_id: UUID, tenant_id: str, module_code: str
    ) -> Optional[dict[str, Any]]:
        """Ativa o modelo desativando os irmãos do mesmo tenant+módulo (atômico).

        O alvo DEVE ter sido validado antes via get_for_tenant (posse) —
        aqui a transação apenas troca o is_active. COALESCE via subselect
        cobre linhas legadas com tenant_id NULL.
        """

        def _txn(conn, cur):  # type: ignore[no-untyped-def]
            cur.execute(
                "UPDATE trained_models SET is_active = FALSE "
                "WHERE module_code = %s AND is_active = TRUE AND id <> %s "
                "AND COALESCE(tenant_id, (SELECT tenant_id FROM users "
                "                          WHERE users.id = trained_models.user_id)) = %s",
                (module_code, str(model_id), str(tenant_id)),
            )
            cur.execute(
                "UPDATE trained_models SET is_active = TRUE "
                "WHERE id = %s RETURNING *",
                (str(model_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._execute_in_transaction(_txn)

    def mark_validation(
        self,
        model_id: UUID | str,
        validated: bool,
        error: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Grava resultado da validação ONNX em trained_models.metrics (JSONB merge).

        metrics.validated=true|false + validated_at; validation_error
        (truncado em 500 chars) apenas quando validated=false.
        """
        payload: dict[str, Any] = {
            "validated": validated,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            payload["validation_error"] = error[:500]
        return self.merge_metrics(model_id, payload)

    def merge_metrics(
        self, model_id: UUID | str, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Funde `payload` em trained_models.metrics (JSONB `||`, top-level).

        Merge e não overwrite: `metrics` acumula fatos de origens diferentes
        (validação do ONNX, custo de GPU, censo, agora os limiares por classe)
        e sobrescrever apagaria o que a outra origem gravou.
        """
        return self._execute_mutation(
            "UPDATE trained_models "
            "SET metrics = COALESCE(metrics, '{}'::jsonb) || %s::jsonb "
            "WHERE id = %s RETURNING id, metrics",
            (json.dumps(payload), str(model_id)),
        )

    def get_declared_classes(
        self, model_id: UUID, tenant_id: str
    ) -> Optional[list[str]]:
        """Classes que o modelo DECLARA prever — de `dataset_versions.class_distribution`.

        Mesma fonte que o front usa para desenhar os chips de escopo
        (`CameraModelScope.classesDoModelo`): chaves da distribuição, sem as
        reservadas `__*` e sem as listadas em `__sem_suporte_treino__` (contadas
        no dataset mas excluídas do treino — `versioning_v2.py`).

        Existe para dar ao POST de model-config como recusar escopo que o
        detector nunca vai emitir. Sem isso o handler aceitava qualquer string:
        gravava "Capacete" no escopo de uma câmera cujo modelo não tem essa
        classe, a tela mostrava o escopo salvo, e nenhuma violação de capacete
        jamais aparecia — zero alertas lido como "não houve violação". O mesmo
        perigo que `_taxonomia_do_modelo` documenta no caminho servido.

        `None` = NÃO deu para provar nada (modelo sem `dataset_version_id`,
        versão de outro tenant, ou distribuição vazia). Quem chama não pode
        ler None como "nenhuma classe": é ausência de medida, não medida de
        ausência — tratar assim recusaria todo escopo de todo modelo sem
        linhagem formal de dataset.
        """
        model = self.get_for_tenant(model_id, tenant_id)
        if model is None:
            return None

        dataset_version_id = model.get("dataset_version_id")
        if not dataset_version_id:
            return None

        row = self._execute_one(
            "SELECT class_distribution FROM dataset_versions "
            "WHERE id = %s AND tenant_id = %s",
            (str(dataset_version_id), str(tenant_id)),
        )
        distribuicao = (row or {}).get("class_distribution")
        if isinstance(distribuicao, str):
            try:
                distribuicao = json.loads(distribuicao)
            except ValueError:
                return None
        if not isinstance(distribuicao, dict) or not distribuicao:
            return None

        sem_suporte = distribuicao.get("__sem_suporte_treino__")
        excluidas = set(sem_suporte) if isinstance(sem_suporte, list) else set()
        nomes = [
            k for k in distribuicao
            if not k.startswith("__") and k not in excluidas
        ]
        return nomes or None

    def get_training_lineage(
        self, model_id: UUID | str, tenant_id: str
    ) -> Optional[dict[str, Any]]:
        """Linhagem completa: modelo → dataset_version → frames → anotações.

        Responde "este modelo foi treinado em quê, anotado por quem e
        quando": model (registry, get_for_tenant já escopa por tenant —
        cross-tenant → None, C-01) → dataset_version (build que gerou o
        COCO) → frames anotados do tenant+módulo → anotações de cada frame
        (created_by/reviewed_by/source, com e-mail resolvido via JOIN
        users para leitura humana).

        APROXIMAÇÃO DOCUMENTADA: build_dataset_version_v2 (versioning_v2.py)
        faz snapshot ad-hoc dos frames no momento do build — não existe
        tabela de junção persistindo QUAIS frames exatos entraram em uma
        dataset_version específica. Os frames aqui são reconstruídos pelo
        mesmo filtro do build (tenant_id + module_code + is_annotated=TRUE)
        mais um corte temporal (tf.created_at <= dataset_version.created_at)
        que aproxima "o que existia até o momento do snapshot". Frames
        anotados DEPOIS do build não aparecem; frames cujo curation_status
        mudou (ex.: excluído da curadoria) DEPOIS do build ainda aparecem
        aqui — reflete o estado NA ÉPOCA do treino, não o estado atual.

        Retorna None se o modelo não existe/não pertence ao tenant. Se o
        modelo existe mas não tem dataset_version_id (ou a versão não
        pertence ao tenant), retorna {"model":..., "dataset_version": None,
        "frames": []} — não é erro, só não há linhagem de dataset pra
        expandir (ex.: modelo registrado sem pipeline de dataset formal).
        """
        model = self.get_for_tenant(model_id, tenant_id)
        if model is None:
            return None

        dataset_version_id = model.get("dataset_version_id")
        if not dataset_version_id:
            return {"model": model, "dataset_version": None, "frames": []}

        dataset_version = self._execute_one(
            "SELECT * FROM dataset_versions WHERE id = %s AND tenant_id = %s",
            (str(dataset_version_id), str(tenant_id)),
        )
        if dataset_version is None:
            return {"model": model, "dataset_version": None, "frames": []}

        frames = self._execute(
            """
            SELECT tf.id AS frame_id, tf.filename, tf.camera_id,
                   tf.captured_at, tf.source AS frame_source,
                   tf.curation_status, tf.created_at AS frame_created_at,
                   a.id AS annotation_id, a.class_id, a.class_name,
                   a.source AS annotation_source, a.created_by, a.reviewed_by,
                   a.created_at AS annotated_at,
                   creator.email AS created_by_email,
                   reviewer.email AS reviewed_by_email
              FROM training_frames tf
              LEFT JOIN frame_annotations a ON a.frame_id = tf.id
              LEFT JOIN users creator ON creator.id = a.created_by
              LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
             WHERE tf.tenant_id = %s
               AND tf.module_code = %s
               AND tf.is_annotated = TRUE
               AND tf.created_at <= %s
             ORDER BY tf.id, a.id
            """,
            (
                str(tenant_id),
                dataset_version["module_code"],
                dataset_version["created_at"],
            ),
        )
        return {
            "model": model,
            "dataset_version": dataset_version,
            "frames": frames,
        }

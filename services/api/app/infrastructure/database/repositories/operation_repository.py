"""
Recognition — OperationRepository.

Acesso a dados de operações configuráveis e seus resultados.
Segue padrão BaseRepository: toda query SQL aqui, nunca fora.
Multi-tenant: todas queries filtram por tenant_id.
"""
import json
import logging
from typing import Any

from app.infrastructure.database.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class OperationRepository(BaseRepository):
    """Repository para operações e seus resultados."""

    # LATERAL join para B10 (último disparo): busca, por operação, o
    # evaluated_at mais recente em operation_results cujo condition_satisfied
    # seja true — sem N+1 (usa o índice idx_results_operation existente,
    # migration 039) e sem confundir com last_evaluated_at (que atualiza a
    # cada avaliação, disparando ou não).
    _LAST_EVENT_JOIN = """
            LEFT JOIN LATERAL (
                SELECT r.evaluated_at
                FROM operation_results r
                WHERE r.operation_id = o.id
                  AND (r.result_json ->> 'condition_satisfied')::boolean IS TRUE
                ORDER BY r.evaluated_at DESC
                LIMIT 1
            ) last_event ON true
    """

    def list_for_site_config(self, site_id: str, tenant_id: str) -> list[dict[str, Any]]:
        """Regras (operações) das câmeras de um site — payload do config/poll do edge.

        É o que a tela de Cenário grava: `config` carrega zona (`zone_points`/
        `line_points`/`roi_points`), escopo de classes (`watch_classes`/
        `target_class`) e o modo de aviso (`persistence_s`, `direction`,
        `iou_threshold`). Sem isto a promessa da tela ("SALVAR PROPAGA AO BOX
        DO SITE EM ~60 S") não se cumpre — o cenário nunca saía da nuvem.

        Junta por `cameras.site_id` e NÃO por `operations.site_id`: a coluna
        existe na tabela, mas está NULL em 100% das linhas (medido no DEV em
        2026-09-07: 17 de 17) — filtrar por ela devolveria lista vazia sempre,
        que é exatamente o modo de falha silenciosa que esta tarefa veio
        consertar.

        Inclui operação `inactive` com o próprio `status`: quem decide ignorar
        é o consumidor. Omitir a linha tornaria "pausada" indistinguível de
        "excluída" para o box — mesmo motivo pelo qual as câmeras arquivadas
        viajam com `is_active` em vez de sumirem do payload.
        """
        return self._execute(
            """
            SELECT o.id, o.camera_id, o.module_id, o.type_id, o.template_id,
                   o.name, o.config, o.status, o.version
            FROM operations o
            JOIN cameras c ON c.id = o.camera_id
            WHERE c.site_id = %s AND o.tenant_id = %s AND c.tenant_id = %s
            ORDER BY o.camera_id, o.id ASC
            """,
            (str(site_id), str(tenant_id), str(tenant_id)),
        )

    def list_by_camera(self, tenant_id: str, camera_id: str) -> list[dict[str, Any]]:
        """Lista operações de uma câmera para o tenant informado."""
        return self._execute(
            f"""
            SELECT o.id, o.tenant_id, o.camera_id, o.module_id, o.type_id, o.name,
                   o.template_id, o.config, o.status, o.version, o.last_value_json,
                   o.last_evaluated_at, o.created_at, last_event.evaluated_at AS last_event_at
            FROM operations o
            {self._LAST_EVENT_JOIN}
            WHERE o.tenant_id = %s AND o.camera_id = %s
            ORDER BY o.id ASC
            """,
            (tenant_id, camera_id),
        )

    def list_by_camera_and_module(
        self, tenant_id: str, camera_id: str, module_id: str
    ) -> list[dict[str, Any]]:
        """Lista operações filtrando por câmera e módulo."""
        return self._execute(
            f"""
            SELECT o.id, o.tenant_id, o.camera_id, o.module_id, o.type_id, o.name,
                   o.template_id, o.config, o.status, o.version, o.last_value_json,
                   o.last_evaluated_at, o.created_at, last_event.evaluated_at AS last_event_at
            FROM operations o
            {self._LAST_EVENT_JOIN}
            WHERE o.tenant_id = %s AND o.camera_id = %s AND o.module_id = %s
            ORDER BY o.id ASC
            """,
            (tenant_id, camera_id, module_id),
        )

    def get_by_id(self, tenant_id: str, operation_id: int) -> dict[str, Any] | None:
        """Busca operação por ID garantindo isolamento multi-tenant."""
        return self._execute_one(
            """
            SELECT id, tenant_id, camera_id, module_id, type_id, name, template_id,
                   config, status, version, last_value_json, last_evaluated_at, created_at
            FROM operations
            WHERE tenant_id = %s AND id = %s
            """,
            (tenant_id, operation_id),
        )

    def create(
        self,
        tenant_id: str,
        camera_id: str,
        module_id: str,
        type_id: str,
        name: str,
        config: dict,
        template_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Cria nova operação. Retorna row criada."""
        return self._execute_mutation(
            """
            INSERT INTO operations (tenant_id, camera_id, module_id, type_id, name, config, template_id)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id, tenant_id, camera_id, module_id, type_id, name, template_id,
                      config, status, version, last_value_json, last_evaluated_at, created_at
            """,
            (tenant_id, camera_id, module_id, type_id, name, json.dumps(config), template_id),
        )

    def update(
        self,
        tenant_id: str,
        operation_id: int,
        name: str,
        config: dict,
    ) -> dict[str, Any] | None:
        """Atualiza nome e config, incrementa version. Retorna row atualizada.

        template_id não é atualizável aqui (B4 — PUT completo — fora de escopo);
        só entra no SELECT/RETURNING para o GET não perder o campo após editar.
        """
        return self._execute_mutation(
            """
            UPDATE operations
            SET name = %s,
                config = %s::jsonb,
                version = version + 1
            WHERE tenant_id = %s AND id = %s
            RETURNING id, tenant_id, camera_id, module_id, type_id, name, template_id,
                      config, status, version, last_value_json, last_evaluated_at, created_at
            """,
            (name, json.dumps(config), tenant_id, operation_id),
        )

    def set_status(
        self, tenant_id: str, operation_id: int, status: str
    ) -> dict[str, Any] | None:
        """Pausa/retoma uma operação (B1) trocando só o status.

        Não incrementa version nem toca config — pausar não é mudança
        estrutural. O worker (OperationsEngine) já ignora status='inactive'
        em list_all_active() e remove do mapa em reload_operation(); esta é a
        rota que faltava para o usuário chegar nesse estado.
        """
        return self._execute_mutation(
            """
            UPDATE operations
            SET status = %s
            WHERE tenant_id = %s AND id = %s
            RETURNING id, tenant_id, camera_id, module_id, type_id, name, template_id,
                      config, status, version, last_value_json, last_evaluated_at, created_at
            """,
            (status, tenant_id, operation_id),
        )

    def delete(self, tenant_id: str, operation_id: int) -> int:
        """Remove operação. Cascata remove operation_results. Retorna rowcount."""
        return self._execute_mutation_no_return(
            "DELETE FROM operations WHERE tenant_id = %s AND id = %s",
            (tenant_id, operation_id),
        )

    def count_results(self, operation_id: int) -> int:
        """Conta resultados históricos de uma operação."""
        row = self._execute_one(
            "SELECT COUNT(*) AS cnt FROM operation_results WHERE operation_id = %s",
            (operation_id,),
        )
        return int(row["cnt"]) if row else 0

    def list_results(
        self,
        operation_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retorna últimos N resultados de uma operação."""
        return self._execute(
            """
            SELECT id, operation_id, result_json, evaluated_at
            FROM operation_results
            WHERE operation_id = %s
            ORDER BY evaluated_at DESC
            LIMIT %s
            """,
            (operation_id, limit),
        )

    # ------------------------------------------------------------------
    # Worker-only (SISTEMA): consultas cross-tenant para o motor de operações.
    # NÃO recebem input de usuário; rodam num processo de background (não numa
    # request). O isolamento é preservado porque cada linha carrega seu tenant_id
    # e as chaves são UUIDs imutáveis (camera_id) — não há caminho de request que
    # atravesse tenant. NÃO reutilizar estes métodos em rotas de usuário.
    # ------------------------------------------------------------------

    def list_all_active(self) -> list[dict[str, Any]]:
        """[worker] Lista TODAS as operações ativas de todos os tenants.

        Ativa = status <> 'inactive' ('active'/'warning'/'error' são estados de
        resultado, não desabilitam a avaliação). Usado pelo motor para montar o
        mapa camera_id → operações no boot e no reload periódico.
        """
        return self._execute(
            """
            SELECT id, tenant_id, camera_id, module_id, type_id, name,
                   config, status, version, last_value_json, last_evaluated_at, created_at
            FROM operations
            WHERE status <> 'inactive'
            ORDER BY camera_id, id ASC
            """,
            (),
        )

    def get_active_by_id(self, operation_id: int) -> dict[str, Any] | None:
        """[worker] Busca uma operação por id sem filtro de tenant (reload pontual)."""
        return self._execute_one(
            """
            SELECT id, tenant_id, camera_id, module_id, type_id, name,
                   config, status, version, last_value_json, last_evaluated_at, created_at
            FROM operations
            WHERE id = %s
            """,
            (operation_id,),
        )

    def update_live_value(
        self,
        operation_id: int,
        last_value_json: dict,
        status: str = "active",
    ) -> None:
        """Atualiza último valor calculado e timestamp. Chamado pelo worker."""
        self._execute_mutation_no_return(
            """
            UPDATE operations
            SET last_value_json = %s::jsonb,
                last_evaluated_at = NOW(),
                status = %s
            WHERE id = %s
            """,
            (json.dumps(last_value_json), status, operation_id),
        )

    def insert_result(self, operation_id: int, result_json: dict) -> None:
        """Insere resultado no histórico."""
        self._execute_mutation_no_return(
            """
            INSERT INTO operation_results (operation_id, result_json)
            VALUES (%s, %s::jsonb)
            """,
            (operation_id, json.dumps(result_json)),
        )

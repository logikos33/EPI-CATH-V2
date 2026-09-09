"""Repository: Model Deployments (migration 100)."""
import json
from typing import Any, Optional
from uuid import UUID

from app.constants import DeploymentStatus
from app.infrastructure.database.repositories.base import BaseRepository


class ModelDeploymentRepository(BaseRepository):
    """Queries SQL para tabela public.model_deployments. Tenant-scoped (C-01).

    Registry-level: histórico completo de deployments modelo↔câmera↔módulo
    com config de geometria (ADR-0032). Pin/canary rápido permanece em
    {schema}.models (ModelRolloutRepository) — semânticas separadas (ADR-0037).
    """

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Registra deployment (status default 'active')."""
        return self._execute_mutation(
            "INSERT INTO model_deployments "
            "(tenant_id, model_id, camera_id, module_code, config, status, deployed_by) "
            "VALUES (%s, %s, %s, COALESCE(%s, 'epi'), %s::jsonb, "
            "        COALESCE(%s, 'active'), %s) RETURNING *",
            (
                str(data["tenant_id"]),
                str(data["model_id"]),
                str(data["camera_id"]),
                data.get("module_code"),
                json.dumps(data.get("config", {})),
                str(data["status"]) if data.get("status") else None,
                str(data["deployed_by"]) if data.get("deployed_by") else None,
            ),
        )  # type: ignore[return-value]

    def get_by_id(self, deployment_id: UUID, tenant_id: str) -> Optional[dict[str, Any]]:
        """Busca deployment por ID validando posse do tenant."""
        return self._execute_one(
            "SELECT * FROM model_deployments WHERE id = %s AND tenant_id = %s",
            (str(deployment_id), str(tenant_id)),
        )

    def get_active_for_camera(
        self, tenant_id: str, camera_id: UUID, module_code: str = "epi"
    ) -> Optional[dict[str, Any]]:
        """Deployment ativo de uma câmera+módulo (inference resolver)."""
        return self._execute_one(
            "SELECT * FROM model_deployments "
            "WHERE tenant_id = %s AND camera_id = %s AND module_code = %s "
            "AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (str(tenant_id), str(camera_id), module_code),
        )

    def list_active_for_tenant(
        self, tenant_id: str, module_code: str = "epi"
    ) -> list[dict[str, Any]]:
        """Deployments ativos de TODAS as câmeras do tenant, numa consulta.

        A aba "Modelos por câmera" pedia um GET por câmera e disparava os 28
        do RVB em `Promise.all`. Cada requisição toma uma conexão do pool da
        API, e o pool estourou: a tela ficou inacessível justamente no tenant
        de tamanho real — medido no DEV em 2026-08-25, com
        "connection pool exhausted" na resposta. O banco estava folgado (5
        conexões de 500); o gargalo era a concorrência da própria tela.

        DISTINCT ON pega o mais recente por câmera, que é o que
        `get_active_for_camera` devolve por câmera (ORDER BY created_at DESC
        LIMIT 1) — mesma semântica, uma ida ao banco.
        """
        return self._execute(
            "SELECT DISTINCT ON (camera_id) * FROM model_deployments "
            "WHERE tenant_id = %s AND module_code = %s AND status = 'active' "
            "ORDER BY camera_id, created_at DESC",
            (str(tenant_id), module_code),
        )

    def list_active_for_site(
        self, site_id: str, tenant_id: str
    ) -> list[dict[str, Any]]:
        """Deployments ATIVOS das câmeras de um site + artefato do modelo.

        Usado pelo config/poll do edge para montar o manifesto de modelo que o
        `ConfigPoller` do agente consome. Devolve, por câmera+módulo, o modelo
        ativo mais recente com a chave R2 do ONNX, o framework e `metrics`
        (de onde sai `onnx_sha256` — o digest REAL do artefato).

        DISTINCT ON (camera_id, module_code) reproduz a semântica de
        `get_active_for_camera` (ORDER BY created_at DESC LIMIT 1) numa ida só
        ao banco — o mesmo motivo de `list_active_for_tenant` existir (o pool
        estourou com um GET por câmera nas 28 do RVB).
        """
        return self._execute(
            "SELECT DISTINCT ON (md.camera_id, md.module_code) "
            "       md.camera_id, md.module_code, md.model_id, "
            "       tm.r2_onnx_key, tm.framework, tm.metrics "
            "FROM model_deployments md "
            "JOIN cameras c ON c.id = md.camera_id "
            "JOIN trained_models tm ON tm.id = md.model_id "
            "WHERE c.site_id = %s AND md.tenant_id = %s AND c.tenant_id = %s "
            "  AND md.status = 'active' "
            "ORDER BY md.camera_id, md.module_code, md.created_at DESC",
            (str(site_id), str(tenant_id), str(tenant_id)),
        )

    def list_for_camera(
        self, tenant_id: str, camera_id: UUID, module_code: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Histórico de deployments de uma câmera (rollback usa o anterior)."""
        if module_code:
            return self._execute(
                "SELECT * FROM model_deployments "
                "WHERE tenant_id = %s AND camera_id = %s AND module_code = %s "
                "ORDER BY created_at DESC",
                (str(tenant_id), str(camera_id), module_code),
            )
        return self._execute(
            "SELECT * FROM model_deployments "
            "WHERE tenant_id = %s AND camera_id = %s ORDER BY created_at DESC",
            (str(tenant_id), str(camera_id)),
        )

    def list_by_model(self, model_id: UUID, tenant_id: str) -> list[dict[str, Any]]:
        """Todos os deployments de um modelo (linhagem — GET /api/v1/models/<id>)."""
        return self._execute(
            "SELECT * FROM model_deployments "
            "WHERE model_id = %s AND tenant_id = %s ORDER BY created_at DESC",
            (str(model_id), str(tenant_id)),
        )

    def deactivate(
        self,
        deployment_id: UUID,
        tenant_id: str,
        status: str = DeploymentStatus.INACTIVE,
    ) -> Optional[dict[str, Any]]:
        """Desativa um deployment (status inactive|rolled_back + deactivated_at)."""
        return self._execute_mutation(
            "UPDATE model_deployments SET status = %s, deactivated_at = NOW() "
            "WHERE id = %s AND tenant_id = %s RETURNING *",
            (str(status), str(deployment_id), str(tenant_id)),
        )

    def deactivate_active_for_camera(
        self, tenant_id: str, camera_id: UUID, module_code: str = "epi"
    ) -> int:
        """Desativa deployments ativos da câmera+módulo (passo do upsert).

        Retorna rowcount — 0 se não havia deployment ativo.
        """
        return self._execute_mutation_no_return(
            "UPDATE model_deployments SET status = 'inactive', deactivated_at = NOW() "
            "WHERE tenant_id = %s AND camera_id = %s AND module_code = %s "
            "AND status = 'active'",
            (str(tenant_id), str(camera_id), module_code),
        )

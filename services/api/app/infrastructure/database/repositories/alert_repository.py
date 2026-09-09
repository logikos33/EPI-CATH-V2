"""Repository: Alerts."""
import json
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from app.core.rajada import DEDUP_WINDOW_SECONDS
from app.infrastructure.database.repositories.base import BaseRepository
from app.infrastructure.database.repositories.camera_module_repository import (
    escopo_params,
    escopo_sql,
)


class AlertRepository(BaseRepository):
    """Queries SQL para tabela alerts."""

    # Única unidade de bbox que o domínio grava (domain/detectors/base.py).
    BBOX_PIXELS = "pixels_xywh_frame_original"

    #: "Uma PESSOA já julgou este alerta?" — MESMA regra de `vereditoHumano`
    #: no front (`components/shared/VereditoHumano.tsx`): não basta ter
    #: `verification_verdict`, porque a task Celery de triagem grava o MESMO
    #: 'approve'/'reject' com `verified_by='claude-haiku'`. A prova de gente é
    #: o prefixo `user:` que `VerificationService.human_review` carimba.
    #:
    #: Serve à ORDENAÇÃO da fila (`list_with_filters`): julgado vai para o FIM.
    #: Precisa ser feito AQUI, junto do `LIMIT/OFFSET` — reordenar a página já
    #: carregada no cliente dá ordem errada na segunda página (o servidor
    #: escolheu QUAIS 20 linhas mandar antes de o cliente opinar).
    #:
    #: `COALESCE(..., false)` não é enfeite: `verified_by` NULL faz o LIKE
    #: devolver NULL, e `ORDER BY ... ASC` põe NULL POR ÚLTIMO no Postgres —
    #: sem o COALESCE, o alerta que ninguém tocou (o mais urgente) afundaria
    #: junto com os já julgados, que é o oposto do pedido. `%%` porque a query
    #: passa por `cur.execute(sql, params)`: `%` solto vira placeholder.
    _JULGADO_POR_HUMANO_SQL = (
        "COALESCE(a.verified_by LIKE 'user:%%' "
        "AND a.verification_verdict IN ('approve', 'reject'), false)"
    )

    @staticmethod
    def ultima_correcao(hist: Optional[list]) -> Optional[dict]:
        """Última entrada do ledger `violations_historico`, só `por`/`por_nome`/`em`.

        Fonte única (ADR-0066): o array de violações anterior não precisa
        trafegar para a tela dizer "caixa corrigida por X em Y" — nem aqui
        nem em `GET /verification/queue`, que projeta o MESMO campo em cima
        do MESMO ledger (achado do cético: a fila reprojetava do zero e a
        autoria sumia da tela ao recarregar). `por_nome` é None em entradas
        gravadas antes dele existir — quem chama mostra travessão nesse caso,
        nunca o UUID cru de `por`.
        """
        if not hist:
            return None
        ultima = hist[-1]
        return {"por": ultima.get("por"), "por_nome": ultima.get("por_nome"), "em": ultima.get("em")}

    # ── Polaridade do evento (ADR-0065) ───────────────────────────────────
    #
    # CONFORMIDADE = o alerta tem ≥1 entrada E TODA classe está explicitamente
    # marcada como PRESENÇA. Tudo o mais é VIOLAÇÃO:
    #   · classe fora do catálogo (o modelo emitiu, ninguém cadastrou);
    #   · entrada sem chave `class` — os alertas `camera_gap` do liveness
    #     ({"type": "camera_gap", ...}) caem aqui e PRECISAM continuar
    #     visíveis;
    #   · is_violation NULL (ninguém decidiu ainda).
    # Sumir da tela é o erro caro; aparecer a mais é barato (ADR-0017).
    #
    # O `%s` é UMA lista de nomes (presence_class_names) — parametrizada,
    # zero f-string de input do usuário. Alias `a` é literal interno.
    _IS_COMPLIANCE_SQL = """(
        jsonb_array_length(a.violations) > 0
        AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(a.violations) v
             WHERE COALESCE(lower(v->>'class'), '') <> ALL(%s::text[])
        )
    )"""

    # ── Contrato A1 — a classe INDECIDIDA não pode ser chamada de violação ──
    #
    # Refina ADR-0065 §4: o predicado acima só sabe dizer "não é conformidade"
    # — e por isso empurrava pra dentro do mesmo balde "violação" três coisas
    # bem diferentes: (a) classe de violação de verdade, (b) entrada sem
    # `class` (`camera_gap` do liveness) e (c) classe INDECIDIDA
    # (`is_violation IS NULL`, ou fora do catálogo — ninguém decidiu).
    # `_nomes_por_polaridade`/`violation_class_names` já tratam (c) como um
    # terceiro estado (nem presença nem violação); esta query que lê o alerta
    # que precisa passar a concordar.
    #
    # ANY, não ALL: espelha `_has_violation` (infrastructure/queue/tasks/
    # inference.py) — lá UMA detecção de classe violação já basta pra
    # `has_violation=True`, mesmo com outras entradas de presença no MESMO
    # alerta. Aqui é o mesmo critério, pro `event_kind` da leitura nunca
    # discordar do que decidiu criar o alerta.
    #
    # Entrada SEM `class` (camera_gap) conta como violação de propósito — ADR-
    # 0065 é explícita que ela "precisa continuar visível", e não é uma
    # detecção de classe (não tem polaridade pra ser indecidida). Por isso o
    # OR com `= ''`: câmera offline nunca vira "observação".
    _IS_VIOLATION_SQL = """(
        EXISTS (
            SELECT 1 FROM jsonb_array_elements(a.violations) v
             WHERE COALESCE(lower(v->>'class'), '') = ''
                OR COALESCE(lower(v->>'class'), '') = ANY(%s::text[])
        )
    )"""

    def presence_class_names(
        self, tenant_id: str, module_code: str | None = None
    ) -> list[str]:
        """Nomes (lower) das classes explicitamente de PRESENÇA do tenant.

        Catálogo global (module_classes, sem tenant_id) ∪ classes custom do
        tenant (yolo_classes). Lista vazia ⇒ nada é conformidade — o lado
        seguro: na dúvida o evento aparece.

        `module_code` ESCOPA o conjunto. Sem ele, o catálogo global devolvia
        junto as classes de `fueling` (truck, plate, pallet… todas
        `is_violation = false`, migration 009): num agregado de EPI, um alerta
        de 'truck' era lido como CONFORMIDADE de EPI. Ambas as tabelas têm
        `module_code` (009 e 093 — NOT NULL DEFAULT 'epi'), então o filtro é
        uma igualdade simples. Omitido = todos os módulos (comportamento
        anterior, mantido para quem não sabe o módulo, como `list_with_filters`).

        ⚠️ Os DOIS nomes do catálogo global entram: `class_name` E
        `display_name`. Ver a nota em `_NOMES_DO_CATALOGO`.
        """
        return self._nomes_por_polaridade(tenant_id, module_code, violacao=False)

    #: No catálogo global as duas colunas são nomes diferentes da MESMA classe:
    #: `class_name` é o id técnico em inglês (`no_gloves`) e `display_name` é o
    #: rótulo (`Sem Luvas`). O que o detector emite é o `display_name`, porque a
    #: taxonomia do modelo vem das categorias do export COCO, que por sua vez
    #: vêm de `frame_annotations.class_name` — e ali o que está gravado é o
    #: rótulo. Medido no DEV: 183 anotações com class_name='Sem Luvas' e
    #: class_id=5, que é `no_gloves` do catálogo global.
    #:
    #: Casar só por `class_name` fazia `Sem Luvas` e `Sem Óculos` — que TÊM
    #: `is_violation = TRUE` no catálogo desde a migration 009 — nunca baterem
    #: com nada. Elas pareciam "sem polaridade" e é por isso que quase foram
    #: recriadas como classe custom, o que teria duplicado a taxonomia e
    #: partido o acervo em dois `class_id`.
    #:
    #: `yolo_classes` não tem esse problema: lá `name` já é o rótulo.
    _NOMES_DO_CATALOGO = (
        "SELECT DISTINCT lower(nome) AS n FROM module_classes, "
        "LATERAL unnest(ARRAY[class_name, display_name]) AS nome "
    )

    def _nomes_por_polaridade(
        self, tenant_id: str, module_code: str | None, violacao: bool
    ) -> list[str]:
        """Catálogo global ∪ classes do tenant, filtrado pela polaridade."""
        polaridade = "IS TRUE" if violacao else "IS FALSE"
        filtro = " AND module_code = %s" if module_code else ""
        # O %s do módulo aparece DUAS vezes no texto (um por lado do UNION) e o
        # primeiro vem antes do tenant_id — a ordem dos params segue o texto.
        params: tuple[Any, ...] = (
            (module_code, str(tenant_id), module_code)
            if module_code
            else (str(tenant_id),)
        )
        rows = self._execute(
            f"{self._NOMES_DO_CATALOGO}"  # noqa: S608
            f" WHERE is_violation {polaridade}{filtro} "
            "UNION "
            "SELECT lower(name) AS n FROM yolo_classes "
            f" WHERE tenant_id = %s AND is_violation {polaridade}{filtro}",
            params,
        )
        return [r["n"] for r in rows]

    def violation_class_names(
        self, tenant_id: str, module_code: str | None = None
    ) -> list[str]:
        """Nomes (lower) das classes explicitamente de VIOLAÇÃO do tenant.

        Espelho de `presence_class_names`, mesma origem dupla (catálogo global
        + classes custom do tenant) e mesmo escopo por módulo.

        Existe porque o laço de inferência decidia violação por um `set`
        montado de UMA VARIÁVEL DE AMBIENTE, com default
        `{no_helmet, no_vest, no_gloves}` — nomes da era COCO que não existem
        na taxonomia de nenhum cliente real. A variável não está setada em
        lugar nenhum, então `has_violation` era SEMPRE falso e nenhum alerta
        chegava à fila de verificação. A polaridade tem uma fonte de verdade
        (ADR-0065) e é esta tabela; o env era uma terceira opinião.

        ⚠️ Assimetria deliberada com `presence_class_names`: lá, lista vazia
        significa "nada é conformidade" e o evento aparece — lado seguro. Aqui,
        lista vazia significaria "nada é violação" e o evento SOME, que é o
        lado caro. Quem chama tem de tratar o vazio como "não sei", nunca como
        "está tudo certo".

        ⚠️ Os DOIS nomes do catálogo global entram: `class_name` E
        `display_name`. Ver a nota em `_NOMES_DO_CATALOGO` — é o que faz
        `Sem Luvas` e `Sem Óculos` finalmente serem reconhecidas como violação
        sem precisar recriá-las como classe custom.
        """
        return self._nomes_por_polaridade(tenant_id, module_code, violacao=True)

    def create(
        self,
        camera_id: UUID,
        violations: list[dict[str, Any]],
        confidence: float,
        evidence_key: str,
        tenant_id: Optional[str] = None,
        module_code: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        site_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Cria alerta de violação.

        tenant_id/module_code são opcionais (retrocompat — ajuste #8): quando
        fornecidos pelo caller (derivados da câmera), o alerta nasce
        tenant-scoped; omitidos, as colunas usam os defaults do schema.

        `site_id` (coluna da migration 067) é OPCIONAL pelo mesmo motivo: os
        callers que rodam no worker (`_save_alert`, ao vivo e retroativo) não
        sabem o site — a câmera pode nem ter um. Quem sabe é o ingest do edge,
        que recebe o site no device token. Foi por não existir NENHUM caller
        que o soubesse que `alerts.site_id` estava NULL em 100% das linhas
        desde que a coluna nasceu.

        `timestamp` (inferência retroativa — inference.py `_save_alert`):
        hora REAL da captura do frame de origem, em vez do DEFAULT NOW() do
        caminho ao vivo. É o par (`timestamp`, `created_at`) que
        `ProcedenciaBadge.classificarLatencia` no front já lê para pintar
        "coleta retroativa" — omitido, o alerta nasce com timestamp≈created_at
        e a tela não tem como distinguir de um evento ao vivo.
        """
        columns = ["camera_id", "violations", "confidence", "evidence_key"]
        placeholders = ["%s", "%s::jsonb", "%s", "%s"]
        values: list[Any] = [str(camera_id), json.dumps(violations), confidence, evidence_key]

        if tenant_id is not None:
            columns.append("tenant_id")
            placeholders.append("%s")
            values.append(str(tenant_id))
        if module_code is not None:
            columns.append("module_code")
            placeholders.append("%s")
            values.append(module_code)
        if timestamp is not None:
            columns.append("timestamp")
            placeholders.append("%s")
            values.append(timestamp)
        if site_id is not None:
            columns.append("site_id")
            placeholders.append("%s")
            values.append(str(site_id))

        return self._execute_mutation(
            f"INSERT INTO alerts ({', '.join(columns)}) "  # noqa: S608
            f"VALUES ({', '.join(placeholders)}) RETURNING *",
            tuple(values),
        )  # type: ignore[return-value]

    def exists_at_capture(self, camera_id: UUID, captured_at: datetime) -> bool:
        """True se já existe alerta desta câmera no MESMO instante de captura.

        Chave natural de idempotência da inferência retroativa (câmera +
        instante de captura, microssegundo — uma colisão real exigiria duas
        capturas simultâneas da mesma câmera). Sem FK para `training_frames`
        de propósito: reaproveita `timestamp` — a mesma coluna que
        `create(timestamp=...)` já grava com a hora real da captura — em vez
        de somar uma coluna nova só para isto.
        """
        row = self._execute_one(
            "SELECT 1 FROM alerts WHERE camera_id = %s AND timestamp = %s LIMIT 1",
            (str(camera_id), captured_at),
        )
        return row is not None

    def get_by_camera(
        self,
        camera_id: UUID,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Lista alertas de uma câmera do TENANT, com paginação.

        `tenant_id` é obrigatório e posicional logo depois da câmera — não tem
        default de propósito: a versão anterior era
        `SELECT * FROM alerts WHERE camera_id = %s`, escopo puro de câmera, e
        alimentava `GET /api/cameras/<id>/alerts` com apenas `@jwt_required()`.
        Qualquer usuário autenticado de QUALQUER tenant lia os alertas de
        qualquer câmera pelo id — mesma forma do achado #14 (fila de
        verificação), que foi corrigido lá e ficou aqui.

        Câmera de outro tenant não bate a condição e a lista sai vazia — o que
        é indistinguível de "câmera sem alerta", como manda o C-01: não vazar
        existência.

        O irmão `get_unacknowledged` já filtrava por tenant desde sempre; era
        só este caminho que não.
        """
        return self._execute(
            "SELECT * FROM alerts WHERE camera_id = %s AND tenant_id = %s "
            "ORDER BY timestamp DESC LIMIT %s OFFSET %s",
            (str(camera_id), str(tenant_id), limit, offset),
        )

    def get_unacknowledged(
        self,
        camera_id: Optional[UUID] = None,
        limit: int = 50,
        tenant_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Lista alertas não reconhecidos, filtrados por tenant_id."""
        if camera_id:
            return self._execute(
                "SELECT * FROM alerts "
                "WHERE camera_id = %s AND acknowledged = FALSE AND tenant_id = %s "
                "ORDER BY timestamp DESC LIMIT %s",
                (str(camera_id), str(tenant_id), limit),
            )
        return self._execute(
            "SELECT * FROM alerts WHERE acknowledged = FALSE AND tenant_id = %s "
            "ORDER BY timestamp DESC LIMIT %s",
            (str(tenant_id), limit),
        )

    def get_evidence_key(self, alert_id: UUID, tenant_id: str) -> Optional[dict[str, Any]]:
        """Busca evidence_key de um alerta, escopado por tenant (task-074 / C-01).

        Retorna None tanto quando o alerta não existe quanto quando pertence a
        outro tenant — o endpoint de snapshot deve responder 404 nos dois
        casos, sem diferenciar (evita enumeração de alert_id de outros
        tenants via diferença de status code).
        """
        return self._execute_one(
            "SELECT evidence_key FROM alerts WHERE id = %s AND tenant_id = %s",
            (str(alert_id), str(tenant_id)),
        )

    def get_by_id(self, alert_id: UUID, tenant_id: str) -> Optional[dict[str, Any]]:
        """Um alerta por id, escopado por tenant (C-01), com o nome da câmera.

        Retorna None tanto para alerta inexistente quanto para alerta de OUTRO
        tenant — a rota responde o mesmo 404 nos dois casos, sem vazar
        existência (mesma regra do `get_evidence_key`).

        Usa o MESMO LEFT JOIN de `list_with_filters` (`cameras` sem qualificar
        schema, resolvido pelo search_path) e seleciona só `name`: a variante
        `{tenant_schema}.cameras` (migration 024) não tem coluna `channel`.
        """
        return self._execute_one(
            "SELECT a.*, COALESCE(c.name, 'Unknown') AS camera_name "
            "FROM alerts a LEFT JOIN cameras c ON a.camera_id = c.id "
            "WHERE a.id = %s AND a.tenant_id = %s",
            (str(alert_id), str(tenant_id)),
        )

    def acknowledge(self, alert_id: UUID, tenant_id: str) -> Optional[dict[str, Any]]:
        """Marca alerta como reconhecido, dentro do tenant de quem pediu.

        `tenant_id` é obrigatório de propósito: sem ele o UPDATE casava por id
        puro e qualquer sessão autenticada reconhecia alerta de OUTRO tenant
        (escrita cross-tenant, C-01). Fora do tenant o rowcount é 0 e o
        chamador devolve 404 — não 403, que confirmaria a existência.
        """
        return self._execute_mutation(
            "UPDATE alerts SET acknowledged = TRUE "
            "WHERE id = %s AND tenant_id = %s RETURNING *",
            (str(alert_id), str(tenant_id)),
        )

    def acknowledge_many(
        self,
        tenant_id: str,
        ids: Optional[list[str]] = None,
        kind: Optional[str] = None,
        camera_id: Optional[str] = None,
    ) -> int:
        """Reconhece em LOTE, dentro do tenant. Devolve quantas linhas mudaram.

        Existe porque o cliente estava fazendo o laço: com 235 pendentes, o
        "marcar todas" do sino disparava 235 POSTs — cada um com round-trip,
        JWT e UPDATE próprios, e um 429 no meio deixava metade marcada sem
        ninguém saber quais. Aqui é UM UPDATE.

        `ids=None` (sem `camera_id`/`kind`) reconhece TUDO que está pendente no
        tenant — por isso a ROTA exige `all: true` explícito para chegar aqui
        sem ids: corpo vazio nunca pode significar "marque tudo".

        `acknowledged = FALSE` no WHERE não é otimização: é o que faz o
        rowcount devolvido significar "quantas MUDARAM", e não "quantas
        casaram com o filtro" — reconhecer duas vezes a mesma lista tem de
        devolver 0 na segunda.

        O recorte de `kind` usa os MESMOS predicados de `list_with_filters`
        (ADR-0065). Se divergissem, "marcar todas as violações" apagaria da
        fila alerta que a tela nunca chamou de violação.

        Cross-tenant: o id de outro tenant simplesmente não casa (rowcount 0),
        igual ao `acknowledge` de um só — nunca 403, que confirmaria existência.
        """
        conditions = ["a.tenant_id = %s", "a.acknowledged = FALSE"]
        params: list = [str(tenant_id)]

        if ids is not None:
            # `::uuid[]` e não texto: id malformado explodiria no cast — a rota
            # valida antes (fronteira de confiança) e nunca deixa chegar aqui.
            conditions.append("a.id = ANY(%s::uuid[])")
            params.append(list(ids))
        if camera_id:
            conditions.append("a.camera_id = %s")
            params.append(camera_id)

        if kind == "compliance":
            conditions.append(self._IS_COMPLIANCE_SQL)
            params.append(self.presence_class_names(tenant_id))
        elif kind == "violation":
            conditions.append(f"{self._IS_VIOLATION_SQL} AND NOT {self._IS_COMPLIANCE_SQL}")
            params.append(self.violation_class_names(tenant_id))
            params.append(self.presence_class_names(tenant_id))
        elif kind == "observacao":
            conditions.append(f"NOT {self._IS_COMPLIANCE_SQL} AND NOT {self._IS_VIOLATION_SQL}")
            params.append(self.presence_class_names(tenant_id))
            params.append(self.violation_class_names(tenant_id))

        return self._execute_mutation_no_return(
            f"UPDATE alerts AS a SET acknowledged = TRUE WHERE {' AND '.join(conditions)}",
            tuple(params),
        )

    def corrigir_bboxes(
        self,
        alert_id: UUID,
        tenant_id: str,
        correcoes: list[dict[str, Any]],
        por: str,
        por_nome: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Reposiciona caixa(s) de um alerta preservando o valor anterior.

        Read-modify-write dentro de UMA transação com FOR UPDATE: duas
        correções simultâneas no mesmo alerta não se sobrescrevem em silêncio.

        Só o `bbox` do cliente é aplicado — `class`, `confidence`, `tipo`,
        `modo` e `ancora_pessoa` ficam como estão: corrigir POSIÇÃO não vira
        porta para reescrever CLASSE. `bbox_unidade` é carimbado pelo servidor
        (caixa gravada em outra unidade é caixa mentirosa na tela).

        None = alerta inexistente OU de outro tenant → a rota devolve o MESMO
        404 (C-01, sem vazar existência). IndexError → 400 na rota.
        """
        def _tx(conn, cur):  # type: ignore[no-untyped-def]
            cur.execute(
                "SELECT violations FROM alerts WHERE id = %s AND tenant_id = %s FOR UPDATE",
                (str(alert_id), str(tenant_id)),
            )
            row = cur.fetchone()
            if row is None:
                return None
            atuais = [dict(v) for v in (row["violations"] or [])]
            for c in correcoes:
                i = c["index"]
                if i >= len(atuais):
                    raise IndexError(i)
                atuais[i]["bbox"] = c["bbox"]
                atuais[i]["bbox_unidade"] = self.BBOX_PIXELS
                atuais[i]["bbox_corrigida"] = True
            cur.execute(
                # `violations` do lado DIREITO é o valor ANTERIOR (Postgres
                # avalia todos os SET contra a linha pré-update) — o histórico
                # sai de graça, sem segundo roundtrip e sem janela entre ler e
                # gravar. Append-only: `||` empurra no fim, nada é removido.
                # `por` continua sendo o id (auditoria); `por_nome` é só para
                # a tela não mostrar UUID cru — None em entradas de usuário
                # não encontrado, e SEMPRE None em entradas gravadas antes
                # desta coluna existir (ledger append-only, nunca reescrito).
                "UPDATE alerts SET violations = %s::jsonb, "
                "violations_historico = violations_historico || jsonb_build_object("
                "'em', to_jsonb(NOW()), 'por', %s::text, 'por_nome', %s::text, "
                "'tipo', 'bbox', 'violations_anteriores', violations) "
                "WHERE id = %s AND tenant_id = %s "
                "RETURNING violations, violations_historico",
                (json.dumps(atuais), por, por_nome, str(alert_id), str(tenant_id)),
            )
            return dict(cur.fetchone())

        return self._execute_in_transaction(_tx)

    def count_by_camera(self, camera_id: UUID, tenant_id: Optional[str] = None) -> int:
        """Conta alertas de uma câmera, filtrados por tenant_id."""
        row = self._execute_one(
            "SELECT COUNT(*) AS count FROM alerts WHERE camera_id = %s AND tenant_id = %s",
            (str(camera_id), str(tenant_id)),
        )
        return row["count"] if row else 0

    def list_with_filters(
        self,
        tenant_id: str,
        limit: int = 20,
        offset: int = 0,
        camera_id: str = None,
        start_date=None,
        end_date=None,
        violation_type: str = None,
        acknowledged: bool = None,
        kind: str = None,
        module_code: str | None = None,
        time_column: str = "created_at",
    ) -> dict:
        """Lista alertas com filtros e paginação, isolado por tenant (P0-03 fix).

        `kind` (ADR-0065, TRÊS estados — contrato A1): 'violation' |
        'compliance' | 'observacao' | None (= todos, default do backend —
        nenhum consumidor existente muda de comportamento). Cada item sai com
        a coluna derivada `event_kind`, calculada pelo MESMO predicado usado
        no filtro — 'observacao' é a classe INDECIDIDA (`is_violation IS
        NULL`, ou fora do catálogo): nem presença, nem violação. Ela CONTINUA
        aparecendo em `kind=None` (não some), só não mente mais como
        'violation'.

        `time_column` (whitelist `_TIME_COLUMNS`, jamais input cru) é o EIXO
        da janela `start_date`/`end_date` E da ordenação: 'created_at' = hora
        da GRAVAÇÃO da linha, 'timestamp' = hora da CAPTURA do frame. Os dois
        divergem por dias em carga em lote (issue #676: 334 de 5.174 alertas
        do DEV). Default 'created_at' aqui preserva os chamadores antigos
        (`compliance_report_service`); a ROTA `/api/alerts` passa 'timestamp',
        porque é a coluna que a tela e o CSV EXIBEM — filtrar por uma e
        mostrar a outra é o que fazia "hoje" listar linha da semana passada.
        A ordenação segue o mesmo eixo (senão a lista sai fora de ordem pela
        data que ela própria imprime), com `a.id` de desempate: `ORDER BY`
        não-determinístico + OFFSET pula linha entre páginas.

        ORDEM DA FILA (pedido do dono, set/2026): o que uma PESSOA já julgou
        vai para o FIM — `_JULGADO_POR_HUMANO_SQL ASC` ANTES do eixo de tempo.
        Dentro de cada grupo a cronologia não se perde: o mais recente
        continua primeiro. Assim quem opera acha o não-julgado no topo sem
        filtrar nada, com centenas de eventos por dia.

        Isto é ordenação de SERVIDOR de propósito, e é a diferença entre
        consertar e fingir: o `LIMIT/OFFSET` roda DEPOIS do `ORDER BY`, então
        é aqui que se decide QUAIS 20 linhas a página 1 traz. Reordenar no
        cliente só embaralha as 20 que o servidor já escolheu — a partir da
        página 2 o operador voltaria a receber julgado primeiro.

        `module_code` aplica o MESMO par de predicados de `/v1/events/*`
        (`_event_filters`): a coluna `alerts.module_code` E o ESCOPO DE CÂMERA
        (`escopo_sql`). Sem os dois, cartão e lista contam conjuntos
        diferentes — 82 linhas de diferença medidas no DEV (issue #676).
        """
        col = time_column if time_column in self._TIME_COLUMNS else "created_at"
        conditions = ["1=1", "a.tenant_id = %s"]
        params: list = [tenant_id]

        if camera_id:
            conditions.append("a.camera_id = %s")
            params.append(camera_id)
        if module_code:
            conditions.append("a.module_code = %s")
            params.append(module_code)
            conditions.append(escopo_sql("a.camera_id"))
            params.extend(escopo_params(str(tenant_id), module_code))
        if start_date:
            conditions.append(f"a.{col} >= %s")
            params.append(start_date)
        if end_date:
            conditions.append(f"a.{col} <= %s")
            params.append(end_date)
        if violation_type:
            conditions.append("a.violations::text LIKE %s")
            params.append(f'%{violation_type}%')
        if acknowledged is not None:
            conditions.append("a.acknowledged = %s")
            params.append(acknowledged)

        # ADR-0065 (contrato A1) — filtro e coluna derivada usam os MESMOS
        # predicados, para o `event_kind` mostrado nunca discordar do recorte
        # paginado. Ordem de prioridade IDÊNTICA nos dois: compliance vence,
        # depois violação de verdade, o resto é observação.
        presence_names = self.presence_class_names(tenant_id)
        violation_names = self.violation_class_names(tenant_id)
        if kind == "compliance":
            conditions.append(self._IS_COMPLIANCE_SQL)
            params.append(presence_names)
        elif kind == "violation":
            conditions.append(f"{self._IS_VIOLATION_SQL} AND NOT {self._IS_COMPLIANCE_SQL}")
            params.append(violation_names)
            params.append(presence_names)
        elif kind == "observacao":
            conditions.append(f"NOT {self._IS_COMPLIANCE_SQL} AND NOT {self._IS_VIOLATION_SQL}")
            params.append(presence_names)
            params.append(violation_names)

        where = " AND ".join(conditions)

        # Count
        count_params = list(params)
        total_row = self._execute_one(
            f"SELECT COUNT(*) as count FROM alerts a WHERE {where}",
            tuple(count_params),
        )
        total = total_row["count"] if total_row else 0

        # `total_situacoes` — quantas RAJADAS (câmera+classe repetida em
        # `DEDUP_WINDOW_SECONDS`) existem no MESMO recorte filtrado, não
        # quantas linhas. Achado ux2/dedup: o filtro de Violações de Eventos
        # mostrava 66 linhas que eram 2 situações reais (mesma câmera, mesmo
        # minuto) — "1/66 reconhecidas" media repetição, não trabalho. Mesma
        # janela/critério de sessão de `VerificationService.get_human_queue`
        # (LAG + gap>janela, partição por camera_id+classe), só que aqui é
        # SÓ contagem — não reordena nem filtra `items` abaixo, e nenhuma
        # tela deve tratar isto como "quantas linhas existem", só como "quantas
        # situações distintas". `classe` usa a MESMA extração
        # (`violations->0->>'class'`) do dedup original — alertas com >1
        # violação por linha continuam contados pela primeira classe, igual
        # ao resto do sistema.
        #
        # EIXO = `timestamp` (CAPTURA), SEMPRE — independente de `time_column`
        # (issue #674). "Situação" é fato de chão de fábrica, e o instante do
        # chão de fábrica é o da CAPTURA; `created_at` é o instante em que a
        # carga rodou. Medido no DEV (RVB, 30 d, 5.122 linhas): 175 linhas
        # foram GRAVADAS a <60s uma da outra mas CAPTURADAS longe — agrupá-las
        # pela gravação FUNDIA 77 situações reais (3.308 no lugar de 3.385), e
        # a tela que as escondesse atrás de "+N repetições" apagaria da vista
        # 77 acontecimentos distintos do chão de fábrica.
        situacoes_params = list(params) + [DEDUP_WINDOW_SECONDS]
        situacoes_row = self._execute_one(
            f"""
            WITH candidatos AS (
                SELECT a.camera_id, COALESCE(a.violations->0->>'class', '') AS classe,
                       a.timestamp AS capturado_em
                FROM alerts a WHERE {where}
            ), com_gap AS (
                SELECT *,
                       LAG(capturado_em) OVER (
                           PARTITION BY camera_id, classe ORDER BY capturado_em
                       ) AS anterior
                FROM candidatos
            )
            SELECT COUNT(*) FILTER (
                WHERE anterior IS NULL
                   OR EXTRACT(EPOCH FROM (capturado_em - anterior)) > %s
            ) AS total_situacoes
            FROM com_gap
            """,
            tuple(situacoes_params),
        )
        # `.get(..., total)`: mock/teste que só devolve `{"count": N}` (formato
        # antigo) cai para `total` em vez de KeyError — nunca menos honesto que
        # antes, só não-diferenciado até o teste real mockar a chave nova.
        total_situacoes = situacoes_row.get("total_situacoes", total) if situacoes_row else total

        # Items with camera name join (best-effort — camera table may vary).
        # Os `%s` do CASE aparecem no SELECT, ANTES do WHERE, no texto da
        # query ⇒ `presence_names`/`violation_names` (NESSA ordem — é a ordem
        # em que os dois WHEN aparecem no texto) vêm ANTES do resto aqui (e
        # NÃO entram no COUNT acima quando `kind` é None).
        page_params = [presence_names, violation_names] + list(params) + [limit, offset]
        items = self._execute(
            f"""SELECT a.*,
               COALESCE(i.name, 'Unknown') as camera_name,
               CASE WHEN {self._IS_COMPLIANCE_SQL} THEN 'compliance'
                    WHEN {self._IS_VIOLATION_SQL} THEN 'violation'
                    ELSE 'observacao' END AS event_kind
            FROM alerts a
            LEFT JOIN cameras i ON a.camera_id = i.id
            WHERE {where}
            ORDER BY {self._JULGADO_POR_HUMANO_SQL} ASC, a.{col} DESC, a.id DESC
            LIMIT %s OFFSET %s""",
            tuple(page_params),
        )

        return {"items": items, "total": total, "total_situacoes": total_situacoes}

    def list_for_camera_scenario(self, tenant_id: str, camera_id: str) -> list[dict[str, Any]]:
        """Lista regras de alerta aplicáveis a uma câmera: específicas + globais do tenant.

        Retorna regras onde camera_id = camera_id (específica) OU camera_id IS NULL (tenant-wide),
        filtrando por tenant_id e enabled=true (C-01).
        """
        return self._execute(
            """
            SELECT id, tenant_id, camera_id, violation_type,
                   min_duration_seconds, min_occurrences, time_window_seconds,
                   create_alert, enabled, created_at, updated_at
            FROM alert_rules
            WHERE tenant_id = %s
              AND enabled = true
              AND (camera_id = %s OR camera_id IS NULL)
            ORDER BY created_at ASC
            """,
            (tenant_id, camera_id),
        )

    def count_since(self, tenant_id: str, module_code: str, since: datetime) -> int:
        """Conta alertas de um tenant/módulo desde uma data.

        Escopo de câmera aplicado (ver `escopo_sql`): "alertas de hoje" do
        módulo EPI não conta o que veio de câmera que o dono não declarou
        como EPI.
        """
        row = self._execute_one(
            "SELECT COUNT(*) AS count FROM alerts a "
            "WHERE a.tenant_id = %s AND a.module_code = %s AND a.created_at >= %s "
            f"AND {escopo_sql('a.camera_id')}",
            tuple(
                [tenant_id, module_code, since]
                + escopo_params(str(tenant_id), module_code)
            ),
        )
        return row["count"] if row else 0

    def count_all_since(self, tenant_id: str, since: datetime) -> int:
        """Conta todos alertas do tenant desde uma data (todos os módulos)."""
        row = self._execute_one(
            "SELECT COUNT(*) AS count FROM alerts WHERE tenant_id = %s AND created_at >= %s",
            (tenant_id, since),
        )
        return row["count"] if row else 0

    def count_by_hour(self, tenant_id: str, start: datetime, end: datetime) -> list:
        """Conta alertas por hora do tenant em um intervalo."""
        return self._execute(
            """
            SELECT
                date_trunc('hour', created_at) AS hour,
                COUNT(*) AS count
            FROM alerts
            WHERE tenant_id = %s AND created_at BETWEEN %s AND %s
            GROUP BY date_trunc('hour', created_at)
            ORDER BY hour
            """,
            (tenant_id, start, end),
        )

    @staticmethod
    def _event_filters(
        alias: str,
        tenant_id: str,
        module_code: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        min_confidence: float | None = None,
        camera_ids: list[str] | None = None,
        class_names: list[str] | None = None,
        time_column: str = "created_at",
    ) -> tuple[str, list[Any]]:
        """Gera WHERE + params para um branch de eventos (alerts ou demo_events).

        `alias` é literal interno ('a'/'d'), nunca input do usuário.
        Todos os VALORES são parametrizados via %s — zero f-string de input.

        `time_column` também é literal interno, validado contra whitelist:
        'created_at' (ingestão) ou 'timestamp' (captura). Só `alerts` tem
        `timestamp` — `demo_events` não, e quem chama passa 'created_at' para
        aquele ramo.
        """
        col = time_column if time_column in AlertRepository._TIME_COLUMNS else "created_at"
        conditions: list[str] = [f"{alias}.tenant_id = %s"]
        params: list[Any] = [tenant_id]

        if module_code:
            conditions.append(f"{alias}.module_code = %s")
            params.append(module_code)
            # ESCOPO DE MÓDULO — só no ramo `alerts` (alias 'a').
            #
            # `alerts.module_code` diz sob que módulo o evento foi PRODUZIDO;
            # o escopo diz se a CÂMERA serve esse módulo. As duas coisas
            # divergem exatamente onde dói: a inferência hoje escolhe o módulo
            # por `cameras.active_module`, que nasce com DEFAULT 'epi' — logo
            # um evento da Guarita chega ao dashboard de EPI carimbado 'epi'
            # sem que ninguém tenha decidido que a Guarita é EPI.
            #
            # `demo_events` (alias 'd') fica de fora: é evento sintético, com
            # `camera_label` em vez de câmera real, e o vínculo não existe para
            # ele. Aplicar o escopo ali apagaria a demonstração inteira no
            # primeiro vínculo que o dono declarasse.
            if alias == "a":
                conditions.append(escopo_sql(f"{alias}.camera_id"))
                params.extend(escopo_params(tenant_id, module_code))
        if from_ts:
            conditions.append(f"{alias}.{col} >= %s")
            params.append(from_ts)
        if to_ts:
            conditions.append(f"{alias}.{col} <= %s")
            params.append(to_ts)
        if min_confidence is not None:
            conditions.append(f"{alias}.confidence >= %s")
            params.append(min_confidence)
        if camera_ids:
            placeholders = ",".join(["%s"] * len(camera_ids))
            conditions.append(f"{alias}.camera_id IN ({placeholders})")
            params.extend(camera_ids)
        if class_names:
            # class_name match: violations JSONB array contains objects with "class" key
            # Use text search over JSONB — still parametrized
            class_conditions = []
            for cn in class_names:
                class_conditions.append(f"{alias}.violations::text LIKE %s")
                params.append(f'%"class": "{cn}"%')
            conditions.append(f"({' OR '.join(class_conditions)})")

        return " AND ".join(conditions), params

    def search_events(
        self,
        tenant_id: str,
        limit: int = 20,
        offset: int = 0,
        camera_ids: list[str] | None = None,
        class_names: list[str] | None = None,
        module_code: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        min_confidence: float | None = None,
        include_demo: bool = True,
    ) -> dict[str, Any]:
        """Busca investigativa de eventos com filtros combinados e tenant isolation.

        include_demo=True (default) une alerts + demo_events via UNION ALL explícita,
        cada branch com o MESMO where tenant-scoped; eventos demo saem com is_demo=TRUE.
        Todos os parâmetros de lista (camera_ids, class_names) são passados como
        params parametrizados — zero f-string de input do usuário.
        """
        where_a, params_a = self._event_filters(
            "a", tenant_id, module_code, from_ts, to_ts,
            min_confidence, camera_ids, class_names,
        )

        alerts_select = f"""SELECT
                a.id,
                a.camera_id,
                a.tenant_id,
                a.module_code,
                a.violations,
                a.confidence,
                a.evidence_key,
                a.acknowledged,
                a.created_at,
                COALESCE(c.name, 'Câmera') AS camera_name,
                FALSE AS is_demo
            FROM alerts a
            LEFT JOIN cameras c ON a.camera_id = c.id AND c.tenant_id = a.tenant_id
            WHERE {where_a}"""

        if include_demo:
            where_d, params_d = self._event_filters(
                "d", tenant_id, module_code, from_ts, to_ts,
                min_confidence, camera_ids, class_names,
            )
            demo_select = f"""SELECT
                d.id,
                d.camera_id,
                d.tenant_id,
                d.module_code,
                d.violations,
                d.confidence,
                d.evidence_key,
                d.acknowledged,
                d.created_at,
                d.camera_label AS camera_name,
                TRUE AS is_demo
            FROM demo_events d
            WHERE {where_d}"""

            count_row = self._execute_one(
                f"SELECT ((SELECT COUNT(*) FROM alerts a WHERE {where_a}) "
                f"+ (SELECT COUNT(*) FROM demo_events d WHERE {where_d})) AS count",
                tuple(params_a + params_d),
            )
            total = count_row["count"] if count_row else 0

            items = self._execute(
                f"""SELECT * FROM (
                {alerts_select}
                UNION ALL
                {demo_select}
                ) ev
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s""",
                tuple(params_a + params_d + [limit, offset]),
            )
            return {"items": items, "total": total}

        count_row = self._execute_one(
            f"SELECT COUNT(*) AS count FROM alerts a WHERE {where_a}",
            tuple(params_a),
        )
        total = count_row["count"] if count_row else 0

        items = self._execute(
            f"""{alerts_select}
            ORDER BY a.created_at DESC
            LIMIT %s OFFSET %s""",
            tuple(params_a + [limit, offset]),
        )

        return {"items": items, "total": total}

    def timeline_by_bucket(
        self,
        tenant_id: str,
        from_ts: datetime,
        to_ts: datetime,
        bucket: str = "hour",
        camera_ids: list[str] | None = None,
        class_names: list[str] | None = None,
        module_code: str | None = None,
        include_demo: bool = True,
        time_column: str = "created_at",
    ) -> list[dict[str, Any]]:
        """Agrega contagem de eventos por bucket de tempo (sem N+1).

        bucket: 'hour' | 'day' | 'week' — passado como literal validado, não f-string de input.
        include_demo=True (default) agrega alerts + demo_events via UNION ALL explícita,
        cada branch com o MESMO where tenant-scoped.

        `time_column`: 'created_at' (default, comportamento histórico) ou
        'timestamp' — o instante da CAPTURA do frame. Ver `capture_profile`
        para por que a diferença importa. `demo_events` não tem `timestamp`
        e continua sempre em `created_at`; o alias da UNION uniformiza as duas
        colunas em `ts`, então os dois ramos continuam somando no mesmo eixo.
        """
        # Validate bucket to prevent SQL injection (only accepted literals)
        valid_buckets = {"hour", "day", "week", "minute"}
        safe_bucket = bucket if bucket in valid_buckets else "hour"
        col_a = time_column if time_column in self._TIME_COLUMNS else "created_at"

        where_a, params_a = self._event_filters(
            "a", tenant_id, module_code, from_ts, to_ts,
            None, camera_ids, class_names, time_column=col_a,
        )

        if include_demo:
            where_d, params_d = self._event_filters(
                "d", tenant_id, module_code, from_ts, to_ts,
                None, camera_ids, class_names,
            )
            return self._execute(
                f"""SELECT
                    date_trunc('{safe_bucket}', ev.ts) AS bucket,
                    COUNT(*) AS count
                FROM (
                    SELECT a.{col_a} AS ts FROM alerts a WHERE {where_a}
                    UNION ALL
                    SELECT d.created_at AS ts FROM demo_events d WHERE {where_d}
                ) ev
                GROUP BY date_trunc('{safe_bucket}', ev.ts)
                ORDER BY bucket""",
                tuple(params_a + params_d),
            )

        return self._execute(
            f"""SELECT
                date_trunc('{safe_bucket}', a.{col_a}) AS bucket,
                COUNT(*) AS count
            FROM alerts a
            WHERE {where_a}
            GROUP BY date_trunc('{safe_bucket}', a.{col_a})
            ORDER BY bucket""",
            tuple(params_a),
        )

    #: Colunas de tempo aceitas numa janela. `created_at` é quando a LINHA
    #: entrou no banco; `timestamp` é quando o FRAME foi capturado. Numa carga
    #: em lote os dois divergem por dias — ver `capture_profile`. Whitelist,
    #: não input: nome de coluna nunca vem do usuário.
    _TIME_COLUMNS = frozenset({"created_at", "timestamp"})

    def _window_conditions(
        self,
        tenant_id: str,
        from_ts: datetime,
        to_ts: datetime,
        module_code: str | None = None,
        camera_ids: list[str] | None = None,
        time_column: str = "created_at",
    ) -> tuple[list[str], list[Any]]:
        """Condições comuns de janela temporal — só literais fixos no SQL, valores via %s."""
        col = time_column if time_column in self._TIME_COLUMNS else "created_at"
        conditions: list[str] = [
            "a.tenant_id = %s",
            f"a.{col} >= %s",
            f"a.{col} <= %s",
        ]
        params: list[Any] = [str(tenant_id), from_ts, to_ts]
        if module_code:
            conditions.append("a.module_code = %s")
            params.append(module_code)
            # Escopo de módulo — mesmo predicado do `_event_filters`. Este é o
            # caminho das JANELAS (alertas na última hora, horas-violação da
            # taxa de conformidade): sem ele o denominador conta câmeras do
            # módulo e o numerador conta alertas de qualquer câmera.
            conditions.append(escopo_sql("a.camera_id"))
            params.extend(escopo_params(str(tenant_id), module_code))
        if camera_ids:
            placeholders = ",".join(["%s"] * len(camera_ids))
            conditions.append(f"a.camera_id IN ({placeholders})")
            params.extend(camera_ids)
        return conditions, params

    def count_in_window(
        self,
        tenant_id: str,
        from_ts: datetime,
        to_ts: datetime,
        module_code: str | None = None,
        camera_ids: list[str] | None = None,
        time_column: str = "created_at",
    ) -> int:
        """Conta alertas do tenant em uma janela temporal (com filtros opcionais)."""
        conditions, params = self._window_conditions(
            tenant_id, from_ts, to_ts, module_code, camera_ids, time_column=time_column
        )
        where = " AND ".join(conditions)
        row = self._execute_one(
            f"SELECT COUNT(*) AS count FROM alerts a WHERE {where}",
            tuple(params),
        )
        return row["count"] if row else 0

    def distinct_cameras_in_window(
        self, tenant_id: str, from_ts: datetime, to_ts: datetime
    ) -> list[str]:
        """Câmeras com >=1 alerta na janela (drift monitor, WS-C3 — escopa o
        trabalho só a câmeras que de fato produziram alerta no período)."""
        rows = self._execute(
            "SELECT DISTINCT camera_id FROM alerts "
            "WHERE tenant_id = %s AND created_at >= %s AND created_at < %s",
            (str(tenant_id), from_ts, to_ts),
        )
        return [str(row["camera_id"]) for row in rows]

    def avg_confidence_in_window(
        self,
        tenant_id: str,
        from_ts: datetime,
        to_ts: datetime,
        module_code: str | None = None,
        camera_ids: list[str] | None = None,
    ) -> float:
        """Confiança média dos alertas do tenant numa janela (drift monitor, WS-C3)."""
        conditions, params = self._window_conditions(
            tenant_id, from_ts, to_ts, module_code, camera_ids
        )
        where = " AND ".join(conditions)
        row = self._execute_one(
            f"SELECT AVG(a.confidence) AS avg_confidence FROM alerts a WHERE {where}",  # noqa: S608
            tuple(params),
        )
        value = row["avg_confidence"] if row else None
        return float(value) if value is not None else 0.0

    def violations_by_class(
        self,
        tenant_id: str,
        from_ts: datetime,
        to_ts: datetime,
        module_code: str | None = None,
        camera_ids: list[str] | None = None,
        class_names: list[str] | None = None,
        time_column: str = "created_at",
    ) -> list[dict[str, Any]]:
        """Distribuição de VIOLAÇÕES por classe no período (server-side).

        ADR-0065: contava toda classe detectada, então "Protetor auditivo"
        (presença) aparecia como linha de violação no `by_class` de
        `/events/summary` e na distribuição do drift monitor — o MESMO defeito
        de polaridade já corrigido em `violation_hours_by_class`. Classe de
        presença não forma grupo aqui.

        Contrato A1: só forma linha quem é VIOLAÇÃO DE VERDADE
        (`violation_class_names`) — classe indecidida (`observacao`, fora do
        catálogo ou `is_violation IS NULL`) não conta mais aqui, mesmo
        predicado de `list_with_filters(kind="violation")`. Antes disto o
        painel "Violações por classe" do Dashboard nomeava como violação o
        MESMO alerta que `/epi/eventos` já mostrava como "Não definida" —
        duas telas se desmentindo sobre o mesmo dado.
        """
        conditions, params = self._window_conditions(
            tenant_id, from_ts, to_ts, module_code, camera_ids, time_column=time_column
        )
        conditions.append("v->>'class' IS NOT NULL")
        conditions.append("lower(v->>'class') = ANY(%s::text[])")
        params.append(self.violation_class_names(tenant_id, module_code))
        if class_names:
            conditions.append("v->>'class' = ANY(%s)")
            params.append(list(class_names))
        where = " AND ".join(conditions)
        return self._execute(
            f"""SELECT v->>'class' AS class, COUNT(*) AS count
            FROM alerts a, jsonb_array_elements(a.violations) v
            WHERE {where}
            GROUP BY v->>'class'
            ORDER BY count DESC""",
            tuple(params),
        )

    def top_cameras_by_alerts(
        self,
        tenant_id: str,
        from_ts: datetime,
        to_ts: datetime,
        module_code: str | None = None,
        limit: int = 10,
        time_column: str = "created_at",
    ) -> list[dict[str, Any]]:
        """Top câmeras por volume de alertas no período (tenant-scoped)."""
        conditions, params = self._window_conditions(
            tenant_id, from_ts, to_ts, module_code, time_column=time_column
        )
        where = " AND ".join(conditions)
        params.append(limit)
        return self._execute(
            f"""SELECT
                a.camera_id,
                COALESCE(c.name, 'Câmera') AS camera_name,
                COUNT(*) AS count
            FROM alerts a
            LEFT JOIN cameras c ON a.camera_id = c.id AND c.tenant_id = a.tenant_id
            WHERE {where}
            GROUP BY a.camera_id, c.name
            ORDER BY count DESC
            LIMIT %s""",
            tuple(params),
        )

    def capture_profile(
        self,
        tenant_id: str,
        from_ts: datetime,
        to_ts: datetime,
        module_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """Volume por HORA DE CAPTURA × polaridade, no período.

        ⚠️ Janela e agrupamento em `alerts.timestamp` (quando o frame foi
        capturado), NÃO em `created_at` (quando a linha entrou no banco). Os
        dois só coincidem quando a ingestão é ao vivo; em qualquer carga em
        lote divergem por dias. Medido no DEV com 423 alertas do RVB: a
        captura se espalha das 10h às 19h — o dia da fábrica — enquanto o
        `created_at` empilha tudo em 3 horários, que são os momentos em que o
        processo de ingestão rodou. Um gráfico de "em que horário a fábrica
        gera violação" montado sobre `created_at` responde "a que horas o
        servidor gravou", que não é pergunta de segurança nenhuma.

        A polaridade sai dos MESMOS predicados de `list_with_filters` (ADR-0065
        + contrato A1), e por isso são TRÊS baldes e não dois:
          conformidade — toda classe do alerta é de PRESENÇA (EPI em uso);
          violacao     — ≥1 classe de violação de verdade, ou entrada sem
                         `class` (o `camera_gap` do liveness);
          indefinido   — ninguém decidiu a polaridade da classe
                         (`is_violation IS NULL` ou fora do catálogo).
        Somar os três num "total de alertas" sem dizer o que ele soma é o
        número sem significado: no acervo do RVB a maioria das linhas é
        'Protetor auditivo', que é CONFORMIDADE, não violação.

        Ordem dos %s segue o texto: presença (CASE), violação (CASE), janela.
        """
        conditions, params = self._window_conditions(
            tenant_id, from_ts, to_ts, module_code, time_column="timestamp"
        )
        where = " AND ".join(conditions)
        # Conformidade primeiro: exige que TODA classe seja de presença, então
        # não disputa linha com o predicado de violação (que é ANY).
        case_params: list[Any] = [
            self.presence_class_names(tenant_id, module_code),
            self.violation_class_names(tenant_id, module_code),
        ]
        return self._execute(
            f"""SELECT
                date_trunc('hour', a.timestamp) AS bucket,
                CASE
                    WHEN {self._IS_COMPLIANCE_SQL} THEN 'conformidade'
                    WHEN {self._IS_VIOLATION_SQL} THEN 'violacao'
                    ELSE 'indefinido'
                END AS kind,
                COUNT(*) AS count
            FROM alerts a
            WHERE {where}
            GROUP BY 1, 2
            ORDER BY 1""",  # noqa: S608 — só literais internos; valores via %s
            tuple(case_params + params),
        )

    def review_situation(
        self,
        tenant_id: str,
        from_ts: datetime,
        to_ts: datetime,
        module_code: str | None = None,
    ) -> dict[str, Any]:
        """Situação de tratamento dos eventos capturados no período.

        Mesma janela de captura do `capture_profile` — os dois cartões falam do
        MESMO conjunto de eventos, e ler um em horário de captura e o outro em
        horário de ingestão faria dois números que não fecham na mesma tela.

        `verification_status` é a coluna que a fila de verificação escreve
        (`human_approved` / `human_rejected` / `pending`); `acknowledged` é o
        outro eixo, independente ("alguém viu"), e é o que sustenta a contagem
        de eventos ainda sem tratativa.
        """
        conditions, params = self._window_conditions(
            tenant_id, from_ts, to_ts, module_code, time_column="timestamp"
        )
        where = " AND ".join(conditions)
        row = self._execute_one(
            f"""SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE NOT a.acknowledged) AS nao_reconhecidos,
                COUNT(*) FILTER (WHERE a.verification_status = 'human_approved')
                    AS procedentes,
                COUNT(*) FILTER (WHERE a.verification_status = 'human_rejected')
                    AS improcedentes,
                COUNT(DISTINCT a.camera_id) AS cameras,
                MIN(a.timestamp) AS primeira_captura,
                MAX(a.timestamp) AS ultima_captura,
                AVG(a.confidence) AS confianca_media
            FROM alerts a
            WHERE {where}""",  # noqa: S608 — só literais internos; valores via %s
            tuple(params),
        )
        return dict(row) if row else {}

    def camera_hours_with_violation(
        self, tenant_id: str, module_code: str, since: datetime, until: datetime | None = None
    ) -> int:
        """Horas-câmera com ≥1 violação DE VERDADE desde `since`.

        `until` (opcional, default `None` = sem limite superior, o
        comportamento que o Dashboard já usava) fecha a janela pelo topo. Existe
        para o relatório de compliance (issue #797), que apura períodos
        FECHADOS ("semana passada", "mês anterior"): sem limite superior ele
        contaria até AGORA e o PDF arquivado somaria violação de fora do
        período que ele afirma cobrir — o mesmo defeito que `count_since` →
        `count_in_window` já corrigiu na contagem de eventos.

        ADR-0065: contava TODO alerta, inclusive EPI PRESENTE — o que invertia
        o `compliance_rate` que se apoia neste número (quanto mais gente usava
        EPI, menor a "conformidade").

        Contrato A1: `NOT compliance` sozinho ainda empurrava classe
        INDECIDIDA (`observacao`) pro balde de violação — o MESMO alerta que
        `/epi/eventos` mostra como "Não definida" derrubava o
        `compliance_rate` do Dashboard como se fosse violação confirmada.
        Mesmo predicado de `list_with_filters(kind="violation")`:
        `_IS_VIOLATION_SQL AND NOT _IS_COMPLIANCE_SQL`.
        """
        teto_sql = " AND a.created_at <= %s" if until is not None else ""
        teto_params: list = [until] if until is not None else []
        row = self._execute_one(
            f"""
            SELECT COUNT(DISTINCT (a.camera_id, date_trunc('hour', a.created_at))) AS count
            FROM alerts a
            WHERE a.tenant_id = %s AND a.module_code = %s AND a.created_at >= %s{teto_sql}
              AND {self._IS_VIOLATION_SQL} AND NOT {self._IS_COMPLIANCE_SQL}
            """,  # noqa: S608 — só literais internos; valores via %s
            tuple(
                [str(tenant_id), module_code, since]
                + teto_params
                + [
                    self.violation_class_names(tenant_id, module_code),
                    self.presence_class_names(tenant_id, module_code),
                ]
            ),
        )
        return row["count"] if row else 0

    def violation_hours_by_class(
        self, tenant_id: str, module_code: str, since: datetime
    ) -> list[dict[str, Any]]:
        """Horas-câmera com violação DE VERDADE por classe desde `since`.

        ADR-0065: agrupava por TODA classe detectada, então "Protetor
        auditivo" (presença) virava uma linha de "violação por classe" no
        `compliance_by_class`.

        Contrato A1: só forma linha quem é violação de verdade
        (`violation_class_names`) — classe indecidida (fora do catálogo,
        `is_violation IS NULL`) não conta mais aqui, mesmo critério de
        `violations_by_class`/`list_with_filters(kind="violation")`.
        """
        return self._execute(
            """
            SELECT
                v->>'class' AS class,
                COUNT(DISTINCT (a.camera_id, date_trunc('hour', a.created_at))) AS hours
            FROM alerts a, jsonb_array_elements(a.violations) v
            WHERE a.tenant_id = %s AND a.module_code = %s AND a.created_at >= %s
              AND v->>'class' IS NOT NULL
              AND lower(v->>'class') = ANY(%s::text[])
            GROUP BY v->>'class'
            ORDER BY hours DESC
            """,
            (
                str(tenant_id),
                module_code,
                since,
                self.violation_class_names(tenant_id, module_code),
            ),
        )

    def usage_rate_by_area(
        self,
        tenant_id: str,
        from_ts: datetime,
        to_ts: datetime,
        module_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """Conformidades × violações por ÁREA (ADR-0065) — painel de taxa de uso.

        Área = `cameras.location`; sem location, o nome da câmera. Não existe
        tabela de áreas — a câmera é a proxy de hoje, e inventar uma seria
        escopo de outra decisão. A DIVISÃO fica na tela: aqui só saem
        contagens, para o painel mostrar "3 de 4" junto do percentual sem uma
        segunda chamada.

        Contrato A1: `violation` usava `NOT compliance` — empurrava classe
        INDECIDIDA pro numerador de violação (`compliance`+`violation` somava
        o total, então "observação" inflava silenciosamente o lado errado).
        Mesmo predicado de `list_with_filters(kind="violation")`:
        `_IS_VIOLATION_SQL AND NOT _IS_COMPLIANCE_SQL`. `compliance` +
        `violation` pode agora somar MENOS que o total do período — a
        diferença é observação (indecidida), que este painel binário não
        reparte; não some, só não é chamada de violação.
        """
        presence_names = self.presence_class_names(tenant_id, module_code)
        violation_names = self.violation_class_names(tenant_id, module_code)
        conditions = ["a.tenant_id = %s", "a.created_at >= %s", "a.created_at <= %s"]
        # Os três %s do predicado aparecem no SELECT, antes do WHERE.
        params: list[Any] = [
            presence_names, violation_names, presence_names,
            str(tenant_id), from_ts, to_ts,
        ]
        if module_code:
            conditions.append("a.module_code = %s")
            params.append(module_code)
        where = " AND ".join(conditions)
        return self._execute(
            f"""SELECT
                COALESCE(NULLIF(c.location, ''), c.name, 'Sem área') AS area,
                COUNT(*) FILTER (WHERE {self._IS_COMPLIANCE_SQL})
                    AS compliance,
                COUNT(*) FILTER (WHERE {self._IS_VIOLATION_SQL} AND NOT {self._IS_COMPLIANCE_SQL})
                    AS violation
            FROM alerts a
            LEFT JOIN cameras c ON a.camera_id = c.id AND c.tenant_id = a.tenant_id
            WHERE {where}
            GROUP BY 1
            ORDER BY 1""",  # noqa: S608 — só literais internos; valores via %s
            tuple(params),
        )

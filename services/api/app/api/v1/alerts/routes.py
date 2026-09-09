"""
Recognition — Alerts Routes.

Lista, filtra, exporta e reconhece alertas de violações de EPI.
"""
import csv
import io
import logging
from datetime import UTC, datetime, timedelta
from math import isfinite
from uuid import UUID

from flask import Blueprint, Response, request
from flask_jwt_extended import jwt_required

from app.core.auth import get_current_user_id, get_tenant_id
from app.core.exceptions import EpiMonitorError
from app.core.responses import success, error
from app.core.tenant import require_permission
from app.infrastructure.database.connection import DatabasePool
from app.infrastructure.database.repositories.alert_repository import AlertRepository
from app.infrastructure.database.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)

alerts_bp = Blueprint("alerts", __name__, url_prefix="/api/alerts")


def _get_repo() -> AlertRepository:
    pool = DatabasePool.get_instance()
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    return AlertRepository(pool)


def _parse_date(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_bool(s: str | None) -> bool | None:
    if s is None:
        return None
    return s.lower() in ("true", "1", "yes")


def _time_column() -> str:
    """Eixo do tempo da janela `start_date`/`end_date` — `?time_field=`.

    Mesmo vocabulário de `/v1/events/*` ('captured' | 'created'), DEFAULT
    OPOSTO e de propósito (issue #676):

      · `/v1/events/*` nasceu com 'created' e tem consumidor antigo;
      · aqui o default é a CAPTURA, porque é a coluna que esta rota EXIBE —
        a lista imprime `timestamp` e o CSV também (ver `export_alerts`).
        Filtrar por `created_at` enquanto se mostra `timestamp` fazia o
        filtro "hoje" devolver linha capturada na semana passada, e fazia
        a barra "Eventos por hora" do Dashboard (que é hora de captura)
        abrir uma janela de hora de GRAVAÇÃO — outro conjunto. Medido no
        DEV: 334 de 5.174 alertas com `timestamp` ≠ `created_at`,
        defasagem máxima de 3,5 dias.

    `?time_field=created` continua acessível para quem quer o eixo da
    ingestão (auditoria de carga), e é o que o nome diz.
    """
    return "created_at" if request.args.get("time_field") == "created" else "timestamp"


def _module_code() -> str | None:
    """`?module_code=` — escopo de módulo, MESMO par de predicados de
    `/v1/events/summary` (coluna `alerts.module_code` + escopo de câmera).
    Vazio/ausente = sem escopo (comportamento anterior, byte a byte)."""
    return (request.args.get("module_code") or "").strip() or None


def _parse_kind(s: str | None) -> str | None:
    """?kind= (ADR-0065, contrato A1: TRÊS valores). Valor inválido → None (=
    todos). Nunca 500 por querystring."""
    return s if s in ("violation", "compliance", "observacao") else None


def _iso_utc(value):  # type: ignore[no-untyped-def]
    """Data de alerta → ISO 8601 UTC com sufixo Z. Não-datetime passa intacto.

    O ÚNICO formato de data que este blueprint emite. Antes cada rota escolhia
    o seu: a lista caía no jsonify do Flask (RFC 822, "…GMT") e o detalhe usava
    `isoformat()` de um TIMESTAMP naive (SEM offset) — o browser lê o segundo
    como hora LOCAL e o MESMO alerta aparecia com 3h de diferença entre lista e
    detalhe. Naive é tratado como UTC: é o que o NOW() do banco grava, e é o
    mesmo pressuposto do http_date do Flask (que carimbava "GMT" na lista).
    """
    if not isinstance(value, datetime):
        return value
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


def _ultima_correcao(hist):  # type: ignore[no-untyped-def]
    """Compat: delega para `AlertRepository.ultima_correcao` (fonte única —
    o mesmo ledger também é projetado em GET /verification/queue)."""
    return AlertRepository.ultima_correcao(hist)


def _nome_usuario_atual() -> str | None:
    """Nome do usuário autenticado, para carimbar `por_nome` no ledger.

    None se o pool não estiver pronto ou o usuário não existir — a rota
    segue gravando a correção mesmo assim (id em `por` já é auditoria
    suficiente; nome é só para a tela).
    """
    pool = DatabasePool.get_instance()
    if pool is None:
        return None
    user = UserRepository(pool).get_by_id(get_current_user_id())
    return user.get("name") if user else None


def _serialize_dates(row: dict) -> dict:
    """Aplica `_iso_utc` em todo valor datetime da linha (created_at, timestamp, …)."""
    return {k: _iso_utc(v) for k, v in row.items()}


@alerts_bp.route("", methods=["GET"])
@jwt_required()
def list_alerts():  # type: ignore[no-untyped-def]
    """Lista alertas com filtros e paginação."""
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(int(request.args.get("per_page", 20)), 100)
        offset = (page - 1) * per_page

        result = _get_repo().list_with_filters(
            tenant_id=get_tenant_id(),
            limit=per_page,
            offset=offset,
            camera_id=request.args.get("camera_id"),
            start_date=_parse_date(request.args.get("start_date")),
            end_date=_parse_date(request.args.get("end_date")),
            violation_type=request.args.get("violation_type"),
            acknowledged=_parse_bool(request.args.get("acknowledged")),
            kind=_parse_kind(request.args.get("kind")),
            # Eixo e escopo do MESMO recorte que produziu o número no cartão
            # que trouxe o usuário até aqui (issue #676). Sem os dois, o card
            # dizia 647 e a lista mostrava 47.
            module_code=_module_code(),
            time_column=_time_column(),
        )

        total = result["total"]
        return success({
            # Datas em ISO 8601 UTC (Z) — o MESMO formato do detalhe. Sem isto o
            # jsonify emitiria RFC 822 aqui e a mesma linha teria dois formatos.
            "alerts": [_serialize_dates(a) for a in result["items"]],
            "count": len(result["items"]),
            "total": total,
            # Rajadas (câmera+classe em <60s) do MESMO recorte, não linhas —
            # ux2/dedup. `.get(..., total)`: repo antigo/mock sem a chave nova
            # ainda responde (degrada para o total de linhas, nunca 500).
            "total_situacoes": result.get("total_situacoes", total),
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        })
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("list_alerts_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@alerts_bp.route("/export", methods=["GET"])
@jwt_required()
def export_alerts():  # type: ignore[no-untyped-def]
    """Exporta alertas para CSV."""
    try:
        result = _get_repo().list_with_filters(
            tenant_id=get_tenant_id(),
            limit=10000,
            offset=0,
            camera_id=request.args.get("camera_id"),
            start_date=_parse_date(request.args.get("start_date")),
            end_date=_parse_date(request.args.get("end_date")),
            violation_type=request.args.get("violation_type"),
            acknowledged=_parse_bool(request.args.get("acknowledged")),
            # O CSV exporta o MESMO recorte que a tela mostra (ADR-0065) —
            # inclusive o EIXO do tempo e o ESCOPO de módulo (issue #676).
            kind=_parse_kind(request.args.get("kind")),
            module_code=_module_code(),
            time_column=_time_column(),
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Data", "Câmera", "Tipo de Violação", "Confiança", "Reconhecido"])

        for alert in result["items"]:
            violations = alert.get("violations") or []
            if not violations:
                violations = [{}]
            # Coluna "Data" = a MESMA que a tela mostra: hora de CAPTURA
            # (`timestamp`), com fallback para `created_at`. Exportar created_at
            # enquanto a tela exibe timestamp faz CSV e tela discordarem da
            # mesma linha. Formato idêntico ao da API (ISO 8601 UTC, Z).
            data = _iso_utc(alert.get("timestamp") or alert.get("created_at") or "")
            for v in violations:
                writer.writerow([
                    data,
                    alert.get("camera_name", ""),
                    v.get("class", ""),
                    f"{v.get('confidence', 0):.0%}" if v.get("confidence") else "",
                    "Sim" if alert.get("acknowledged") else "Não",
                ])

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=alertas.csv"},
        )
    except Exception as exc:
        logger.error("export_alerts_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@alerts_bp.route("/usage-rate", methods=["GET"])
@jwt_required()
def usage_rate():  # type: ignore[no-untyped-def]
    """Taxa de uso de EPI por área (ADR-0065): conformidades × violações.

    EPI PRESENTE é telemetria, não evento alertável — este é o painel que a
    consome. A divisão fica na tela; aqui só saem contagens.

    Rota estática declarada ANTES de `/<alert_id>` de propósito: leitura
    humana. (Werkzeug já prioriza regra sem argumento, mas a ordem no arquivo
    evita a dúvida na próxima leitura.)
    """
    try:
        # tz-aware, igual ao que `_parse_date` devolve — não misturar naive
        # com aware nos dois lados da janela.
        to_ts = _parse_date(request.args.get("end_date")) or datetime.now(UTC)
        from_ts = _parse_date(request.args.get("start_date")) or (to_ts - timedelta(days=7))
        rows = _get_repo().usage_rate_by_area(
            tenant_id=str(get_tenant_id()),
            from_ts=from_ts,
            to_ts=to_ts,
            module_code=request.args.get("module_code"),
        )
        return success({"areas": rows})
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("usage_rate_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


_MAX_IDS_LOTE = 500


def _uuids_validos(valores):  # type: ignore[no-untyped-def]
    """Lista de ids → (lista normalizada, erro). Fronteira de confiança.

    O id vai para um `::uuid[]` no SQL: string malformada estouraria no cast
    e viraria 500. Aqui vira 400, que é a verdade (o cliente mandou lixo).
    """
    if not isinstance(valores, list) or not (0 < len(valores) <= _MAX_IDS_LOTE):
        return None, f"ids deve ser uma lista de 1 a {_MAX_IDS_LOTE} identificadores"
    limpos = []
    for v in valores:
        if not isinstance(v, str):
            return None, "cada id deve ser uma string UUID"
        try:
            limpos.append(str(UUID(v)))
        except ValueError:
            return None, "cada id deve ser um UUID válido"
    return limpos, None


@alerts_bp.route("/acknowledge", methods=["POST"])
@jwt_required()
def acknowledge_alerts_lote():  # type: ignore[no-untyped-def]
    """Reconhece VÁRIOS alertas numa requisição só.

    Body — uma das duas formas, nunca as duas ausentes:

        {"ids": ["<uuid>", ...]}                     → só esses (1..500)
        {"all": true, "kind": "violation"}           → TODOS os pendentes do
                                                       recorte (aceita também
                                                       "camera_id")

    Por que existe: o "marcar todas como lida" era um laço no CLIENTE sobre
    `POST /<id>/acknowledge`. Com 235 pendentes isso é 235 requisições — e uma
    falha no meio deixava o operador sem saber o que ficou marcado.

    `all` tem de vir EXPLÍCITO como `true`: corpo vazio (ou `{}` de um cliente
    com bug) nunca pode significar "reconheça tudo o que existe". Sem `ids` e
    sem `all`, é 400.

    Rota estática declarada ANTES de `/<alert_id>` — mesma leitura humana da
    `usage-rate`.
    """
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return error("Corpo deve ser um objeto JSON", 400)

    ids = None
    if "ids" in body:
        ids, erro = _uuids_validos(body.get("ids"))
        if erro:
            return error(erro, 400)
    elif body.get("all") is not True:
        return error('Informe "ids" ou "all": true', 400)

    camera_id = body.get("camera_id")
    if camera_id is not None:
        if not isinstance(camera_id, str):
            return error("camera_id deve ser uma string UUID", 400)
        try:
            camera_id = str(UUID(camera_id))
        except ValueError:
            return error("camera_id deve ser um UUID válido", 400)

    try:
        total = _get_repo().acknowledge_many(
            tenant_id=str(get_tenant_id()),
            ids=ids,
            # `_parse_kind` descarta valor fora do contrato A1 → None (= sem
            # recorte de polaridade). Nunca 500 por corpo torto.
            kind=_parse_kind(body.get("kind")),
            camera_id=camera_id,
        )
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("acknowledge_alerts_lote_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)

    # `acknowledged` = quantas MUDARAM de estado, não quantas foram pedidas: id
    # de outro tenant, id inexistente e id já reconhecido não entram na conta.
    # Quem chama mostra esse número — inflá-lo com o len(ids) seria afirmar
    # trabalho que não aconteceu.
    return success({"acknowledged": total, "requested": len(ids) if ids is not None else None})


@alerts_bp.route("/<alert_id>", methods=["GET"])
@jwt_required()
def get_alert(alert_id: str):  # type: ignore[no-untyped-def]
    """Detalhe de UM alerta — o que a tela de evidência precisa.

    Devolve `captured_at` (coluna `timestamp` = hora REAL do evento no edge,
    com fallback para `created_at`), o nome da câmera e a URL assinada do
    FRAME INTEIRO. `violations` sai cru, com o `bbox` e o `bbox_unidade` que o
    edge gravou — a tela projeta; o backend não reinterpreta coordenada.

    Cross-tenant, id inexistente e id malformado respondem o MESMO 404 (C-01).
    Projeção explícita (não `dict(row)`): não vaza `tenant_id`/`verified_by`/
    `site_id` e datetime sai em ISO, não no RFC 822 do jsonify do Flask.
    """
    try:
        from app.infrastructure.storage.local_storage import get_storage

        try:
            parsed_id = UUID(alert_id)
        except ValueError:
            return error("Alerta não encontrado", 404)

        alert = _get_repo().get_by_id(parsed_id, tenant_id=get_tenant_id())
        if alert is None:
            return error("Alerta não encontrado", 404)

        captured = alert.get("timestamp") or alert.get("created_at")
        created = alert.get("created_at")
        evidence_url = None
        if alert.get("evidence_key"):
            try:
                evidence_url = get_storage().generate_presigned_download_url(
                    alert["evidence_key"], ttl=3600, response_content_type="image/jpeg"
                )
            except Exception:
                # Evidência indisponível não derruba a tela — câmera, hora,
                # classe e confiança ainda contam o acontecido.
                logger.warning("alert_evidence_url_failed: alert_id=%s", alert_id)

        return success({"alert": {
            "id": str(alert["id"]),
            "camera_id": str(alert["camera_id"]) if alert.get("camera_id") else None,
            "camera_name": alert.get("camera_name"),
            "violations": alert.get("violations") or [],
            "confidence": alert.get("confidence"),
            "acknowledged": bool(alert.get("acknowledged")),
            "class_name": alert.get("class_name"),
            "evidence_key": alert.get("evidence_key"),
            "evidence_url": evidence_url,
            # `_iso_utc`, não `isoformat()` cru: o TIMESTAMP do banco é naive e
            # sairia SEM offset — o browser leria como hora LOCAL (−3h em BRT).
            "captured_at": _iso_utc(captured) if captured else None,
            "created_at": _iso_utc(created) if created else None,
            # Veredito humano — estado SEPARADO de `acknowledged`: reconhecer é
            # ciência do operador, veredito é verdade sobre a detecção.
            # `verified_by` continua FORA da projeção (não vaza id interno).
            "verification_verdict": alert.get("verification_verdict"),
            "verified_at": _iso_utc(alert["verified_at"]) if alert.get("verified_at") else None,
            "correcao_ultima": _ultima_correcao(alert.get("violations_historico")),
        }})
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("get_alert_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


_MAX_CORRECOES = 20


def _validar_correcoes(body: dict):  # type: ignore[no-untyped-def]
    """Fronteira de confiança — bbox chega do browser. Devolve (lista, erro)."""
    correcoes = body.get("correcoes")
    if not isinstance(correcoes, list) or not (0 < len(correcoes) <= _MAX_CORRECOES):
        return None, f"correcoes deve ser uma lista de 1 a {_MAX_CORRECOES} itens"
    limpas = []
    for c in correcoes:
        if not isinstance(c, dict):
            return None, "cada correção deve ser um objeto"
        i = c.get("index")
        if not isinstance(i, int) or isinstance(i, bool) or i < 0:
            return None, "index deve ser inteiro >= 0"
        bbox = c.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return None, "bbox deve ter exatamente 4 números [x, y, w, h]"
        nums = []
        for n in bbox:
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not isfinite(n):
                return None, "bbox deve conter números finitos"
            nums.append(float(n))
        x, y, w, h = nums
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            return None, "bbox exige x,y >= 0 e w,h > 0"
        limpas.append({"index": i, "bbox": [x, y, w, h]})
    return limpas, None
    # ponytail: sem teto superior — o alerta não guarda width/height do frame
    # (a tela deriva de naturalWidth/naturalHeight da <img>). Se um dia
    # `alerts` ganhar as dimensões do frame, clampar aqui também.


@alerts_bp.route("/<alert_id>/violations", methods=["PATCH"])
@jwt_required()
@require_permission("alerts:feedback")
def corrigir_violations(alert_id: str):  # type: ignore[no-untyped-def]
    """Reposiciona a caixa de uma violação — em PIXELS do frame ORIGINAL.

    Body: {"correcoes": [{"index": 0, "bbox": [x, y, w, h]}]}

    Só o `bbox` é aceito do cliente; o resto da violação é preservado e a
    unidade é carimbada pelo servidor. Nada é apagado — o array anterior vai
    INTEIRO para `violations_historico` com quem corrigiu e quando.

    Cross-tenant, id inexistente e id malformado: o MESMO 404 (C-01).
    """
    try:
        parsed_id = UUID(alert_id)
    except ValueError:
        return error("Alerta não encontrado", 404)

    correcoes, erro = _validar_correcoes(request.get_json(silent=True) or {})
    if erro:
        return error(erro, 400)

    try:
        row = _get_repo().corrigir_bboxes(
            parsed_id,
            tenant_id=str(get_tenant_id()),
            correcoes=correcoes,
            por=str(get_current_user_id()),
            por_nome=_nome_usuario_atual(),
        )
    except IndexError:
        return error("index fora do intervalo de violações do alerta", 400)
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("corrigir_violations_error: alert=%s err=%s", alert_id, exc, exc_info=True)
        return error("Erro interno", 500)

    if row is None:
        return error("Alerta não encontrado", 404)
    return success({
        "alert_id": alert_id,
        "violations": row["violations"],
        "correcao_ultima": _ultima_correcao(row["violations_historico"]),
    })


@alerts_bp.route("/<alert_id>/acknowledge", methods=["POST"])
@jwt_required()
def acknowledge_alert(alert_id: str):  # type: ignore[no-untyped-def]
    """Marca alerta como reconhecido."""
    try:
        alert = _get_repo().acknowledge(UUID(alert_id), tenant_id=str(get_tenant_id()))
        if alert is None:
            return error("Alerta não encontrado", 404)
        # Mesmo formato de data das outras rotas do blueprint.
        return success({"alert": _serialize_dates(alert)})
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("acknowledge_alert_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@alerts_bp.route("/<alert_id>/snapshot", methods=["GET"])
@jwt_required()
def alert_snapshot(alert_id: str):  # type: ignore[no-untyped-def]
    """Retorna presigned URL da imagem de evidência do alerta (tenant-scoped — task-074)."""
    try:
        from app.infrastructure.storage.local_storage import get_storage
        from app.infrastructure.storage.r2_storage import R2Storage

        repo = _get_repo()
        # Busca escopada por tenant (C-01) — alerta de outro tenant nunca é
        # encontrado aqui, então cai no mesmo 404 de "alerta inexistente"
        # (evita enumeração cross-tenant via diferença de status/mensagem).
        alert = repo.get_evidence_key(UUID(alert_id), tenant_id=get_tenant_id())
        if not alert or not alert.get("evidence_key"):
            return error("Snapshot não disponível", 404)

        storage = get_storage()
        if isinstance(storage, R2Storage):
            url = storage.generate_presigned_download_url(
                alert["evidence_key"], ttl=3600, response_content_type="image/jpeg"
            )
            return success({"snapshot_url": url})

        return error("Storage local não suporta presigned URLs", 400)
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("alert_snapshot_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)


@alerts_bp.route("/stats", methods=["GET"])
@jwt_required()
def alert_stats():  # type: ignore[no-untyped-def]
    """Estatísticas de alertas (tenant-scoped — BUG-6 fix)."""
    try:
        tenant_id = str(get_tenant_id())
        camera_id = request.args.get("camera_id")
        repo = _get_repo()
        count = repo.count_by_camera(UUID(camera_id), tenant_id=tenant_id) if camera_id else 0
        unack = len(repo.get_unacknowledged(
            camera_id=UUID(camera_id) if camera_id else None,
            limit=1000,
            tenant_id=tenant_id,
        ))
        return success({"total": count, "unacknowledged": unack})
    except EpiMonitorError:
        raise
    except Exception as exc:
        logger.error("alert_stats_error: %s", exc, exc_info=True)
        return error("Erro interno", 500)

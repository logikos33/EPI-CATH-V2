# API_CONTRACT_MAP.md — Mapa Canônico de Contrato Frontend↔Backend

> ⚠️ **SUPERSEDIDO PARA INVENTÁRIO E CLASSIFICAÇÃO (2026-08-23).** A fonte canônica para *quais endpoints existem, quem os consome e o que o novo front precisa cobrir* passou a ser
> **[`docs/migration/MAPA-MIGRACAO-FRONTEND.md`](./migration/MAPA-MIGRACAO-FRONTEND.md)** — gerado por script a partir do `url_map` real (`tools/api_inventory.py`), do consumo real do front resolvido pelo matcher do Flask (`tools/frontend_api_calls.py`) e do banco DEV (`tools/db_schema_snapshot.py`), com rótulo por endpoint (FRONT-ATUAL / BACKEND-ONLY / ÓRFÃO / GAP-DE-PRODUTO) e evidência arquivo:linha. Regenere com `python3 tools/build_migration_map.py build`; não duplique contagens nem rótulos aqui.
> Este arquivo permanece como **registro histórico (2026-07-12)** das notas de *shape* de request/response e dos achados graves daquela data — muitos já invalidados (ver tabela abaixo). Para qualquer divergência entre este arquivo e o mapa novo, **o mapa novo vence** (C-04: código real).

> Gerado em: 2026-07-12
> Escopo: `services/api/app/api/v1/**/routes.py` (32 arquivos de blueprint, família `/api` legado + `/api/v1` novo) × `apps/frontend/src/services/*.ts` + `apps/frontend/src/types/*.ts`
> Como foi gerado: levantamento estático automatizado (9 agentes, branch `develop`, worktree read-only) — 6 agentes leram cada blueprint backend linha a linha (`file, method, path, family, auth, request_shape, response_envelope, error_codes, notes`); 3 agentes leram os consumidores frontend (`services/*.ts`, `types/*.ts`). Saída bruta: `wyx7ujhh7.output` (~4429 linhas JSON, 763k tokens, 89 tool calls). Este documento é a síntese curada dessa saída para a Fase 1 da task-069.
> **Este documento NÃO cobre operabilidade** (se um endpoint tem tela/UI ou não) — isso já está mapeado em **[`docs/quality/CONTRATO_FRONT_BACK.md`](./quality/CONTRATO_FRONT_BACK.md)** (246 endpoints, ~42 com chamada frontend, ~204 "NAO SURFACED"). Aqui referenciamos aquele documento em vez de duplicar a coluna "tem UI?". O foco deste mapa é **contrato de tipos e versionamento**: shape de request/response, envelope, tipo TS correspondente, família de rota (`/api` × `/api/v1`), placeholders e drift.
> Zero mudança de comportamento foi feita para produzir este documento (Fase 1 = auditoria pura).

---

## Resumo Executivo dos Achados Graves

| # | Achado | Severidade | Categoria |
|---|--------|-----------|-----------|
| 1 | **INVALIDADO (2026-07-15)** — ~~`countingService.updateSession()` chama `PATCH /api/counting/sessions/{id}` — endpoint não existe~~. Corrigido pelo commit `5e47f09`, anterior a este levantamento; endpoint existe hoje com o shape esperado (`CountingRepository.UPDATABLE_SESSION_FIELDS`) | ~~P0~~ | ~~(a) FE→endpoint inexistente~~ |
| 2 | **INVALIDADO (2026-07-15)** — ~~`countingService.getValidationReport()` chama `GET /api/counting/sessions/validation-report` — endpoint não existe~~. Idem #1, corrigido pelo commit `5e47f09` | ~~P0~~ | ~~(a) FE→endpoint inexistente~~ |
| 3 | **INVALIDADO (2026-07-15)** — ~~`eventsService.ts` assume envelope `{success, message, data}`; backend usa `{status, data}`~~. Confirmado em runtime/código (`app/core/responses.py`): o envelope REAL é `{success, message, data}` — é o `CLAUDE.md` (agora corrigido) que estava errado, não o frontend. Zero bug de parsing | ~~P0~~ | ~~(b) mismatch de envelope~~ |
| 4 | **INVALIDADO (2026-07-19)** — ~~`POST /api/v1/admin/tenants` gera senha temporária do admin como `f'EpiMonitor@{slug[:4].upper()}2024!'` — padrão previsível~~. Verificado no código real (C-04): `app/api/v1/admin/routes.py:337,857,1106` usa `secrets.token_urlsafe(12)` (aleatório, retornado uma única vez). O placeholder previsível não existe mais. | ~~P0~~ ✅ resolvido | ~~(e) placeholder~~ |
| 5 | **INVALIDADO (2026-07-19)** — ~~`POST /api/v1/quality/demo/seed?force=true` — qualquer usuário autenticado do tenant pode apagar dados reais de produção~~. Verificado no código real (C-04): `app/api/v1/quality/routes.py:1976-1985` restringe `force=true` a **superadmin** E exige body `{"confirm": true}`; sem isso é no-op/403. | ~~P0~~ ✅ resolvido | ~~(e) placeholder~~ |
| 6 | **INVALIDADO (2026-07-19)** — ~~`PATCH /api/modules/<module_code>/classes/<class_id>` — sem checagem de `tenant_id` nem de role/admin~~. Verificado no código real (C-04): `app/api/v1/modules/routes.py:81-95` (task-073) exige `has_permission('modules:write')` (admin/superadmin) e isola por tenant — classe de módulo não habilitado → **404** (C-01, não vaza existência). | ~~P0~~ ✅ resolvido | ~~cross-tenant~~ |
| 7 | **INVALIDADO (2026-07-19)** — ~~`GET /api/alerts/<alert_id>/snapshot` — query direta sem filtro `tenant_id`~~. Verificado no código real (C-04): `app/api/v1/alerts/routes.py:143-158` (task-074) usa `@jwt_required()` + `repo.get_evidence_key(UUID(alert_id), tenant_id=get_tenant_id())`; alerta de outro tenant → **404** (evita enumeração cross-tenant). | ~~P0~~ ✅ resolvido | ~~cross-tenant~~ |
| 8 | ~~`edge_events/routes.py` passa o objeto `request` para `extract_device_id_unverified`/`verify_device_token`~~ **STALE / RESOLVIDO** (verificado no código real 2026-07-18, C-04): `edge_events/routes.py` extrai o Bearer corretamente (`auth_header.removeprefix("Bearer ")`), igual a `/heartbeat`. Este item do map estava desatualizado (o bug foi corrigido no WS10). | **✅ resolvido** | — |
| 9 | Blueprint `frames_bp` (`url_prefix='/api/frames'`) é registrado em `app/__init__.py` mas `services/api/app/api/v1/frames/routes.py` **não define nenhuma rota** — zero endpoints. `AGENTS.md` do diretório e o `CLAUDE.md` raiz documentam `POST /api/frames/{id}/pre-annotate` como existente | **P1** | (c) endpoint morto + (e) doc-vs-código |
| 10 | Duas APIs de branding paralelas: `admin/branding_routes.py` (canônica, flat, `/api/v1/admin/tenants/<id>/branding`) vs `branding/routes.py` (`PUT /api/v1/admin/branding`, formato nested, **docstring marca explicitamente como DEPRECATED** e instrui "NÃO estender este endpoint", mas ainda está viva e roteável) | **P1** | (d) duplicata |
| 11 | `POST /api/alerts/<alert_id>/acknowledge` é registrado **duas vezes** em blueprints diferentes: `alerts/routes.py` (dono natural do domínio) e `training/routes.py` (delegado a `acknowledge_alert_handler`) — mesmo path+método em dois blueprints | **P1** | (d) duplicata de rota |
| 12 | Migração `/api`→`/api/v1` de câmeras está incompleta: de ~26 rotas de `cameras/routes.py`, só 4 têm alias `/api/v1` (`probe`, `effective-model`, `config`, `health-context`); o próprio código comenta que o alias `/api/v1/cameras/probe` foi criado às pressas porque clientes usando esse prefixo caíam num catch-all 405 | **P1** | (d) duplicata/migração incompleta |
| 13 | Três pipelines de upload de imagem/vídeo de treino coexistem sem consolidação: `/api/training/videos` (legado, usado pelo FE via `trainingService.uploadVideo`), `/api/v1/videos/*` (14 rotas, pipeline R2+Celery completo, **zero consumidor frontend**), `/api/training/images/upload` (legado) e `/api/v1/videos/images/upload` (v1) | **P1** | (d) duplicata |
| 14 | `GET /api/v1/verification/queue`, `/queue/count`, `POST /verification/<id>/review` — nenhum parâmetro `tenant_id` visível na assinatura da rota, contradizendo a regra do projeto ("toda query filtra por tenant_id"); precisa checar se o service internamente filtra | **P0 (suspeita, verificar `VerificationService`)** | fora das 5 categorias — cross-tenant |
| 15 | **INVALIDADO (2026-07-19)** — ~~`GET /api/v1/storage/health` é público e executa upload/delete de teste real no storage~~. Verificado no código real (C-04): `app/api/v1/storage/routes.py:23-38` (task-085) agora só resolve config/credenciais, **sem I/O real**; o ciclo write/exists/delete migrou para `POST /api/v1/storage/test-upload` (JWT). Continua público mas sem custo/superfície de R2. | ~~P1~~ ✅ resolvido | ~~segurança~~ |

Ver seção **"Tabela de Divergências"** para a lista completa (25+ itens) e a seção **"Família de rota"** para o inventário completo de endpoints `/api` legado.

---

## Convenções da Tabela

- **Família**: `/api` (legado) ou `/api/v1` (novo) — conforme o path real registrado no Flask, não a pasta do arquivo (há vários casos de arquivo em `api/v1/<dominio>/routes.py` cujo blueprint na verdade serve em `/api/...` sem `/v1` — sinalizado em cada linha).
- **Envelope padrão do projeto**: `success(data)` / `error(msg, status)` de `app.core.responses` → `{status: "success"|"error", data}` (documentado em CLAUDE.md). Qualquer desvio disso é marcado explicitamente.
- **"—" na coluna FE**: nenhum consumidor foi identificado pelos agentes de frontend nesta leva (`services/*.ts` + `types/*.ts`). Isso **não** significa necessariamente "sem UI" — consulte `CONTRATO_FRONT_BACK.md` para status de operabilidade (raw `fetch()` fora de `services/` também conta como consumidor lá).
- **"não determinado"**: dado que o levantamento não conseguiu confirmar (ex.: handler não lido, campo ambíguo). Verificar manualmente antes de agir.

---

## 1. Mapa por Domínio

### 1.1 Admin (`services/api/app/api/v1/admin/*.py` — 10 arquivos)

Blueprint principal `admin_bp` serve `/api/v1/admin/*` com `@require_superadmin`. Cobertura de UI: **quase nula** — ver `CONTRATO_FRONT_BACK.md` §1.11 (49 endpoints, só `audit-log` tem consumidor: `adminService.ts:242`, e via raw `fetch()`, não `api.ts`). Consumidor confirmado nesta leva: `impersonation.ts`.

| Método | Path | Auth | Request | Response | FE | Nota |
|---|---|---|---|---|---|---|
| GET | `/api/v1/admin/dashboard` | `require_superadmin` | — | `success(data)` | — | **Placeholder**: query de tickets abertos tem branch morta `if False else SELECT 0` — `tickets_open` sempre 0 |
| GET | `/api/v1/admin/tenants` | `require_superadmin` | — | `success({tenants})` | — | |
| POST | `/api/v1/admin/tenants` | `require_superadmin` | `{name, slug, plan, modules_enabled}` | `success({tenant, admin_email, temp_password}, 201)` | — | **P0 segurança**: `temp_password = f'EpiMonitor@{slug[:4].upper()}2024!'` — previsível + ano hardcoded |
| GET | `/api/v1/admin/tenants/<id>` | `require_superadmin` | path param | `success({tenant})` | — | |
| PATCH | `/api/v1/admin/tenants/<id>` | `require_superadmin` | whitelist de ~13 campos | `success({updated:true})` | — | UPDATE com f-string de nome de coluna (`# noqa: S608`), seguro só porque vem de allowlist fixa |
| POST | `/api/v1/admin/tenants/<id>/suspend` | `require_superadmin` | `{reason}` | `success({suspended:true})` | — | |
| POST | `/api/v1/admin/tenants/<id>/reactivate` | `require_superadmin` | — | `success({reactivated:true})` | — | |
| GET | `/api/v1/admin/tenants/<id>/overview` | `require_superadmin` | path param | `success({tenant,cameras,recent_alerts,training_jobs})` | — | |
| GET | `/api/v1/admin/tenants/<id>/plan-history` | `require_superadmin` | path param | `success({history})` | — | |
| GET | `/api/v1/admin/users` | `require_superadmin` | query paginação/filtros | `success({items,total})` | — | |
| GET/POST/PATCH | `/api/v1/admin/users[/<id>]` | `require_superadmin` | ver notes | `success(...)` | — | POST retorna `temp_password` + `first_access_token` |
| POST | `.../users/<id>/deactivate\|reactivate\|force-password-reset` | `require_superadmin` | — | `success({...:true})` | — | reactivate pode 409 por seat limit |
| GET/DELETE | `.../users/<id>/sessions` | `require_superadmin` | path param | `success({sessions})`/`success({revoked:true})` | — | |
| GET | `/api/v1/admin/permissions/matrix` | `require_superadmin` | — | `success({matrix})` | — | nenhum erro tratado |
| GET | `/api/v1/admin/modules/catalog` | `require_superadmin` | — | `success({modules})` | — | **Nome quase idêntico** a `modules-registry` abaixo — confirmar se são o mesmo recurso duplicado |
| GET/GET/POST/POST | `.../training-approvals[/<id>][/approve\|reject]` | `require_superadmin` | ver notes | `success(...)` | — | presigned URL do R2 é best-effort (falha vira `sample_urls: []` silenciosamente) |
| GET/GET/POST/GET | `.../workers[/<schema>][/restart\|/metrics]` | `require_superadmin` | — | `success(...)` | — | |
| POST | `/api/v1/admin/workers/heartbeat` | **`X-Worker-Secret` (sem JWT)** | `{tenant_schema, ...}` | `success({command})` | — | único endpoint do arquivo sem `@require_superadmin` — intencional (worker on-prem), mas quebra uniformidade |
| GET | `/api/v1/admin/modules-registry` | `require_superadmin` | — | `success({modules})` | — | ver nota "catalog" acima |
| GET/GET/POST/PATCH/GET | `.../plans[/<id>][/tenants]` | `require_superadmin` | ver notes | `success(...)` | — | |
| GET/PATCH/GET/PATCH | `.../feature-flags[/<key>][/tenant/<id>]` | `require_superadmin` | ver notes | `success(...)` | — | |
| GET/GET/GET/POST/PATCH | `.../tickets[/stats][/<id>][/reply]` | `require_superadmin` | ver notes | `success(...)` | — | |
| GET | `/api/v1/admin/audit-log` | `require_superadmin` | query filtros | `success({items,total})` | — | |
| GET | `/api/v1/admin/audit-log/export` | `require_superadmin` | query filtros | **Response CSV cru (não usa envelope)** | `adminService.ts:242` raw fetch | **Achado (e)**: sucesso retorna `flask.Response` CSV bruto; erro passa a usar `error()` JSON — dois content-types possíveis no mesmo endpoint |
| GET/POST/PATCH/DELETE | `.../announcements[/<id>]` | `require_superadmin` | ver notes | `success(...)` | — | DELETE (soft-delete via `expires_at=NOW()`) não checa se registro existe — `rowcount 0` ainda retorna `deleted:true` |
| GET/GET | `.../health/platform`, `.../health/metrics` | `require_superadmin` | — | `success(...)` | — | |
| GET | `/api/v1/admin/inventory` | `require_superadmin` | query filtros | `success({cameras, edge_devices:[], sites:[], total})` | — | **Placeholder explícito**: `edge_devices`/`sites` sempre `[]`, comentário "reservado para fase futura" |
| POST | `/api/v1/admin/cameras/import` | `require_superadmin` | `{cameras:[...]}` máx 200 | `success({created,errors}, 207\|201)` | — | |
| POST | `/api/v1/admin/cameras/<id>/probe` | `require_superadmin` | path param | `success({...probe_status})` | — | valida anti-SSRF (IP loopback/link-local/multicast) — ponto positivo |
| POST | `/api/v1/admin/cameras/probe-batch` | `require_superadmin` | `{camera_ids:[...]}` máx 50 | `success({results})` | — | threads paralelas sem timeout agregado; resultados de threads que não terminam a tempo (30s) somem do array sem erro |
| GET | `/api/v1/announcements` | `jwt_required` (qualquer role) | — | `success({announcements})` | — | registrado em `client_bp`, não `admin_bp` — vive no mesmo arquivo mas é endpoint de cliente comum |
| POST | `/api/v1/announcements/<id>/read` | `jwt_required` | path param | `success({read:true})` | — | |
| GET/PUT/POST | `/api/v1/admin/tenants/<id>/branding[/logo]` | `require_superadmin` | ver §1.4 Branding | `success(...)` | — | ver achado #10 no resumo executivo — duas APIs de branding |
| GET/POST/DELETE | `/api/v1/admin/demo-events[/seed]` | `require_superadmin` | `{tenant_id, module_code, count}` | `success(result)` | — | |
| POST/GET/DELETE | **`/api/admin/demo-videos[/upload][/<id>]`** | `require_superadmin` | multipart / query / path | `success(...)` | — | **Família `/api` legado** — divergente de todo o resto do diretório admin, que é `/api/v1/admin` |
| POST/POST | `.../users/<id>/impersonate`, `/api/v1/impersonation/stop` | `require_superadmin` (10/min) / `jwt_required` | path param / — | `success({token,user,...})` / `success({stopped:true})` | `impersonation.ts: startImpersonation/stopImpersonation` | **Achado (b)**: FE tipa a resposta como `{success, data}` (`ImpersonateResponse`), diferente do `{status, data}` padrão documentado — inconsistência de envelope entre services |
| GET/PUT/POST/DELETE | `/api/v1/admin/integrations/[<type>][/test]` | `jwt_required()` + checagem manual de role (não usa `@require_superadmin`) | ver notes | `success(...)` | — | padrão de auth estruturalmente diferente do resto do diretório (funcionalmente equivalente) |
| GET×4 / POST | `/api/v1/admin/observability/*` | `require_superadmin` | ver notes | `success(...)` | `types/edge.ts: FleetSite` (via `edge-fleet`) | |
| GET/GET/PUT/GET | `/api/v1/admin/permissions/registry`, `.../users/<id>/permissions`, `/api/v1/permissions/mine` | `require_superadmin` / `jwt_required` (mine) | ver notes | `success(...)` | — | `/permissions/mine` tem fallback silencioso para `permissions_for_role(role)` se banco indisponível |
| GET/POST/POST | `/api/v1/admin/test-console/[status\|start\|stop]` | `require_superadmin` | ver notes | `success(...)` | — | Estado em memória de processo único (`_console_state`) — diverge entre workers gunicorn; **nota**: commit recente do repo (`786f680`) já moveu este status para `/test-console/harness/status` — **este mapa reflete o snapshot lido do worktree `develop`; confirmar path atual antes de usar** |
| GET/POST/GET/POST/GET/POST | `/api/v1/admin/versions[/<id>][/rollback]`, `/api/v1/admin/changelog` | `require_superadmin` | ver notes | `success(...)` | — | rollback restaura `plan/modules_enabled/feature_flags` via JSONB snapshot, nunca schema (regra do projeto respeitada) |

---

### 1.2 Alerts (`services/api/app/api/v1/alerts/routes.py`) — família `/api`

FE: `AlertsHistoryPage.tsx` (`api.get`, `api.post`) + raw fetch para `/export` — ver `CONTRATO_FRONT_BACK.md` §1.3/§2.2.

| Método | Path | Auth | Request | Response | FE | Nota |
|---|---|---|---|---|---|---|
| GET | `/api/alerts` | `jwt_required` + tenant | query paginação/filtros | `success({alerts,count,total,page,per_page,pages})` | `AlertsHistoryPage.tsx` | filtra por `tenant_id` — ok |
| GET | `/api/alerts/export` | `jwt_required` | mesmos filtros, limit 10000 | **CSV cru** (não usa envelope) | `AlertsHistoryPage.tsx:74` raw fetch | erro cai em `error()` JSON — quebra o content-type CSV esperado no path de falha |
| POST | `/api/alerts/<id>/acknowledge` | `jwt_required` | path param | `success({alert})` | `AlertsHistoryPage.tsx` | **Registrado também em `training/routes.py`** (achado #11) — sem checagem explícita de tenant_id no repo visível aqui |
| GET | `/api/alerts/<id>/snapshot` | `jwt_required` | path param | `success({snapshot_url})` | `AlertsHistoryPage.tsx` | **P0 segurança**: `_execute_one` sem filtro `tenant_id` — risco de leak cross-tenant |
| GET | `/api/alerts/stats` | `jwt_required` | query `camera_id?` | `success({total,unacknowledged})` | — | **Suspeito**: sem `camera_id`, `total` fica hardcoded em 0 em vez de somar todo o tenant — comentário do código diz "tenant-scoped BUG-6 fix" mas parece incompleto |

---

### 1.3 Auth (`services/api/app/api/v1/auth/routes.py`) — família `/api`

| Método | Path | Auth | Request | Response | FE | Nota |
|---|---|---|---|---|---|---|
| POST | `/api/auth/register` | público, rate-limit 5/h | `{email,password,name}` | `success({user,message},201)` | — (sem tela de registro, `CONTRATO_FRONT_BACK.md` §1.1) | nenhum token emitido (ADR-0017) |
| POST | `/api/auth/login` | público, rate-limit 10/min | `{email,password}` | `success({token,user})` | hooks de auth (não em `services/`) | claim `perms` best-effort; registro de sessão best-effort |
| GET | `/api/auth/me` | `jwt_required` | — | `success(user + permissions)` | provável `useAuth` | — |

---

### 1.4 Branding — **dois arquivos, dois contratos** (achado #10)

| Arquivo | Método | Path | Auth | Request | Response | Nota |
|---|---|---|---|---|---|---|
| `admin/branding_routes.py` | GET/PUT | `/api/v1/admin/tenants/<id>/branding` | `require_superadmin` | whitelist flat (`product_name, color_*, logo_url, ...`) | `success({branding})`/`success({updated:true,branding})` | **canônico** — comentário do arquivo diz explicitamente ser o formato correto |
| `admin/branding_routes.py` | POST | `.../branding/logo` | `require_superadmin` | multipart `file` + `kind` | `success({<kind>_url,key},201)` | Blueprint `tenant_branding` (`branding_bp`) é declarado no arquivo mas **zero rotas registradas nele** — a promessa de docstring de `GET /api/v1/tenant/branding` público está, na verdade, implementada no outro arquivo (ver linha abaixo) — **não é endpoint morto, é doc mal localizada** |
| `branding/routes.py` | GET | `/api/v1/tenant/branding` | JWT **opcional** | — | `success({branding,is_default})` **sempre**, mesmo em erro interno | catch-all engole toda exceção — pode mascarar bug real de branding |
| `branding/routes.py` | GET | `/api/v1/admin/branding/tenants` | `require_superadmin` | — | `success({tenants})` | |
| `branding/routes.py` | GET | `/api/v1/admin/branding/tenant/<id>` | `require_superadmin` | path param | `success({tenant_id,name,slug,branding})` | |
| `branding/routes.py` | PUT | `/api/v1/admin/branding` | `require_admin` | `{branding:{brand?,colors?}, tenant_id?}` (nested) | `success({branding,tenant_id})` | **DEPRECATED explicitamente no docstring** — formato nested conflita com o canônico flat acima; comentário instrui "NÃO estender este endpoint" mas endpoint segue vivo |
| `branding/routes.py` | POST | `/api/v1/admin/branding/logo` | `require_admin` | multipart `file` | `success({url,key})` | path **diferente** do logo canônico (`/admin/tenants/<id>/branding/logo`, superadmin) — dois endpoints de upload de logo com auth e path distintos |

---

### 1.5 Cameras (`services/api/app/api/v1/cameras/routes.py`) — família `/api` (majoritária) + 4 aliases `/api/v1`

Arquivo é um **roteador fino** (`add_url_rule`) — auth/request/response reais ficam em `crud_handlers.py`, `stream_handlers.py`, `model_handlers.py`, `probe_handler.py`, `test_handler.py`, `config_handler.py`, `health_context_handler.py`, `module_handler.py`, `retention_handler.py`, `tenant_retention_handler.py` (**não lidos neste levantamento** — auth/request/response marcados como "não determinado, ver handler" para a maioria das linhas abaixo).

FE: `cameraService.ts` (`list, get, create, update, delete, test, start, stop, patchConfig, getHealthContext, probe`) + `countingService.ts` (`getCameraModels, setCameraModel` sobre `/cameras/<id>/models`).

| Método | Path | Família | FE | Nota |
|---|---|---|---|---|
| GET/POST | `/api/cameras[/<id>]` | `/api` | `cameraService.list/get/create/update/delete` | auth real não determinado neste levantamento (ver `crud_handlers`) |
| POST/GET | `/api/cameras/<id>/test`, stream `/start`,`/stop`,`/status`,`/info`,`/<filename>` | `/api` | `cameraService.test/start/stop` (só start/stop/test têm FE) | `/status`,`/info`,`/<filename>` sem consumidor identificado aqui — ver `CONTRATO_FRONT_BACK.md` §1.2 |
| POST | `/api/cameras/probe` | `/api` | `cameraService.probe` | alias em `/api/v1/cameras/probe` — **criado às pressas** por catch-all 405 pré-existente (comentário do próprio código confirma migração incompleta) |
| GET/PUT | `/api/cameras/<id>/model` | `/api` | — | rota **legada** GET Redis-only, convive com `/models` (Task 045) — modelo de dados pode divergir entre GET legado e PUT novo |
| GET/PUT | `/api/cameras/<id>/models` | `/api` | `countingService.getCameraModels/setCameraModel` | `setCameraModel` FE tipa `module` como `'epi'\|'quality'\|'counting'` — **CLAUDE.md documenta só `epi`/`fueling`** como módulos Sprint 5; ver achado (b) na tabela de divergências |
| GET/POST | `/api/cameras/<id>/model-config` | `/api` (alias `/api/v1`) | `CameraModelScope.tsx` (aba "Modelos por câmera" em `TrainingPage`) | contrato da aba: **GET `?module=<camera.active_module ∥ epi>` = fonte de verdade** (1 por câmera ativa ao abrir; `deployment: null` = sem deployment → detector padrão do ambiente do worker, `DETECTOR_BACKEND`/`DETECTOR_MODEL_PATH` — **não há rota de desativar**); POST grava `public.model_deployments.config` com `module_code` = o mesmo módulo (o resolver `tasks/inference.py::_resolve_camera_model` lê por `active_module`) — **escopo de classes = `config.classes`** (≥1, validador `geometry_validation.py`); POST = `training:approve`; upsert com histórico (nunca UPDATE/DELETE); `GET /api/cameras` passou a expor `active_module`; lista de modelos via `GET /api/v1/models` (só `r2_onnx_key` não-nulo, filtrados por `module_code` da câmera) e classes via `GET /api/v1/models/<id>` → `lineage.dataset_version.class_distribution` **menos `__sem_suporte_treino__`**; `created_at` sempre com offset. Dívida: coexiste com `cameras.model_{module}_id` (CameraModelAssignment) — o resolver do worker prioriza `model_deployments`; o edge RVB ainda não consome modelo/escopo por câmera |
| PATCH | `/api/cameras/<id>/config` | `/api` (alias `/api/v1`) | `cameraService.patchConfig` | resposta inclui `propagation?` — sem validação client-side de "pelo menos um campo" |
| GET | `/api/cameras/<id>/health-context` | `/api` (alias `/api/v1`) | `cameraService.getHealthContext` | tipo FE `CameraHealthContext` (WS10) |
| GET | `/api/cameras/<id>/available-models` | `/api` **sem alias v1** | — | inconsistente com `effective-model`, que tem alias |
| GET | `/api/cameras/<id>/effective-model` | `/api` (alias `/api/v1`) | — | |
| PATCH/PUT/GET | `/api/cameras/<id>/module[/current]`, `/schedule` | `/api` | — | |
| GET/PUT | `/api/cameras/<id>/retention` | `/api` | — | |
| GET/PUT | `/api/cameras/tenant/retention` | `/api` | — | **Risco de shadowing de rota**: path estático `/tenant/retention` compete com o dinâmico `/<camera_id>/retention`; funciona hoje só porque `camera_id` é sempre UUID |

---

### 1.6 Chat (`services/api/app/api/v1/chat/routes.py`) — família `/api`

| Método | Path | Auth | Response | FE | Nota |
|---|---|---|---|---|---|
| POST | `/api/chat` | `jwt_required` | **SSE cru** (`text/event-stream`) no sucesso; `error()` só em validação/indisponibilidade | `ChatFAB.tsx:56` raw fetch **sem Authorization header** (`CONTRATO_FRONT_BACK.md` D6) | contrato assimétrico: sucesso é SSE, falha é envelope JSON — cliente precisa tratar dois formatos |
| GET | `/api/chat/health` | público | JSON bruto `{available,model}` (não usa `success/error`) | — | inconsistente com o padrão `app.core.responses` do resto do projeto |

---

### 1.7 Counting (`services/api/app/api/v1/counting/routes.py`) — família `/api`, Blueprint **sem `url_prefix`** (paths hardcoded)

FE: `countingService.ts`. Ver achados #1/#2 do resumo executivo.

| Método | Path | Request | Response | FE | Nota |
|---|---|---|---|---|---|
| POST | `/api/counting/sessions` | `{camera_id, module_code}` | `success({session}), 201` (tupla externa, não `status=201` nomeado) | — | padrão de retorno inconsistente com `auth/routes.py` |
| GET | `/api/counting/sessions` | — | `success({sessions})` | — | |
| DELETE | `/api/counting/sessions/<id>` | path param | `success({session})` | **`countingService.updateSession` chama PATCH neste mesmo path — não existe** | ver achado #1 |
| GET | `/api/counting/sessions/<id>/stats` | path param | `success(stats)` | — | |
| PATCH | `/api/counting/sessions/<id>/plate` | `{plate_text, plate_confidence?, plate_review?}` | `success({session_id,plate_text,...})` | **nenhum** — FE chama `.../{id}` sem `/plate` | isolamento por `tenant_id` correto no repo |
| GET | `/api/counting/sessions/plates` | query `review_only?` | `success({sessions})` | **FE espera `/validation-report`, que não existe** | risco de shadowing de rota estática vs `<session_id>` dinâmico (mesmo padrão de `tenant/retention`) |

---

### 1.8 Dashboard (`services/api/app/api/v1/dashboard/routes.py`) — família `/api/v1`, Blueprint **sem `url_prefix`**

| Método | Path | Response | FE | Nota |
|---|---|---|---|---|
| GET | `/api/v1/dashboard/stats` | `success(data)` | — (`reportService` usa `/reports/home` em vez disso — `CONTRATO_FRONT_BACK.md` D-adjacente) | join manual documentado (débito arquitetural, não bug) para tabelas sem `tenant_id` direto |
| GET | `/api/v1/dashboard/detections` | `success({daily_detections,period_days})` | — | interpolação `INTERVAL '%s days'` via `%s` parametrizado (psycopg2) — não é SQL injection, mas vale testar em runtime |
| GET | `/api/v1/reports/export` | `send_file()` binário xlsx (não usa envelope) | `DashboardPage.tsx:69` raw fetch **com path bugado, falta `/api`** | registrado no blueprint `dashboard` mas o path é `/reports/export` — nome de rota diverge do namespace do blueprint; **ver `CONTRATO_FRONT_BACK.md` D3 (P0 já documentado)** |

---

### 1.9 Datasets (`services/api/app/api/v1/datasets/routes.py`) — família `/api/v1`

| Método | Path | Auth | Response | FE | Nota |
|---|---|---|---|---|---|
| GET/POST | `/api/v1/datasets` | jwt (+`require_training_role('write')` no POST) | `success({datasets,total})`/`success({dataset},201)` | — | |
| GET | `/api/v1/datasets/<id>` | jwt | `success({dataset,versions})` | — | |
| POST | `/api/v1/datasets/<id>/versions` | jwt + write role | `success({task_id,...,status:'building'},202)` | — | só `format='coco'` aceito no build v2; export YOLO é task legada |
| GET | `/api/v1/dataset-versions/<id>` | jwt | `success({version})` | — | path `dataset-versions` (hífen, recurso plano) quebra o padrão nested do resto do blueprint |

---

### 1.10 Devices (`services/api/app/api/v1/devices/routes.py`) — família **`/api`** (não `/api/v1`, diferente dos vizinhos edge/datasets)

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| POST | `/api/devices/claim-codes` | jwt + `has_permission('devices:manage')` + rate limit 10/min | `success({claim_code,...},201)` | Blueprint usa `/api/devices` (sem `/v1`) — diferente de `edge/edge_commands/edge_events/datasets`, todos `/api/v1` — confirmar se intencional |
| POST | `/api/devices/claim` | público (rate limit 10/min) | `success({enrollment_token,...})` | público por design (troca claim code por token) — correto |

---

### 1.11 Edge — `edge/routes.py`, `edge_commands/routes.py`, `edge_events/routes.py` (todos `/api/v1`)

FE: `edgeService.ts` (`getOverview, getSitesHealth, getSiteHeartbeats, getHeartbeatSummary`) — todos com **camada de adaptação explícita** porque nomes de campo do backend (`derived_status`, `last_heartbeat_at`, `inference_fps`, `cpu_pct`) divergem dos nomes usados no frontend (`EdgeOverview`, `SiteHealth` em `types/edge.ts`).

| Método | Path | Auth | Response | FE | Nota |
|---|---|---|---|---|---|
| POST | `/api/v1/edge/heartbeat` | device RS256 (extração correta do token) | `success({id,received_at},201)` | — | referência de implementação **correta** de device auth |
| GET | `/api/v1/edge/config/poll` | device auth | `jsonify({'cameras':[...], 'config_version'})` cru + header `ETag`; `304` em `If-None-Match` (F1) — **desvio proposital documentado** | `config_poller.py` | SELECT nunca inclui `username`/`password_encrypted` (C-05); versionamento por conteúdo (sem migration) |
| GET | `/api/v1/edge/sites/health` | jwt custom + `has_permission('edge:manage')` | `success({sites})` | `edgeService.getSitesHealth` | |
| GET | `/api/v1/edge/overview` | idem | `success({sites_total,...})` | `edgeService.getOverview` | |
| POST/GET/PATCH | `/api/v1/edge/sites[/<id>]` | idem | `success({site\|sites})` | — | tenant_id do body é descartado explicitamente no PATCH (proteção correta) |
| POST/GET | `.../sites/<id>/enrollment-tokens` | idem | `success({token,...},201)`/`success({tokens})` | — | token plaintext só na criação; hash SHA-256 persistido — padrão correto |
| POST | `.../enrollment-tokens/<id>/revoke` | idem | `success({revoked:true,...})` | — | idempotente para token já expirado |
| POST | `/api/v1/edge/enroll` | público (segurança via enrollment_token one-time) | `success({tenant_id,site_id,device_id,scopes},201)` | — | `tenant_id`/`site_id` vêm só da linha `enrollment_tokens` (C-01) — correto |
| GET | `.../sites/<id>/heartbeats` | idem | `success({heartbeats})` | `edgeService.getSiteHeartbeats` | |
| GET | `.../sites/<id>/heartbeat-summary` | idem | `success({...})` | `edgeService.getHeartbeatSummary` | |
| GET | `.../sites/<id>/devices` | idem | `success({devices})` sem `public_key_pem`/fingerprint (C-05) | — | |
| POST | `.../devices/<id>/revoke` | idem | `success({revoked:true,...})` | — | idempotente, 404 cross-tenant sem vazar existência |
| POST | `/api/v1/edge/commands` | jwt custom + role admin/superadmin manual | `success({...}), 201` (tupla externa) | — | padrão de retorno inconsistente (mesmo tipo de desvio que `counting/routes.py`) |
| GET | `.../commands/pending` | device auth (long-poll) | `success({commands,count})` | — | |
| PATCH | `.../commands/<id>` | device auth | `success({command})` | — | |
| GET | `/api/v1/edge/commands` | jwt custom **sem checagem extra de role/permission** | `success({commands,count})` | — | **Achado**: única rota GET deste conjunto que qualquer usuário JWT do tenant pode chamar (as POST exigem admin) — não valida ownership explícito de `site_id` antes de listar; depende do repository filtrar corretamente |
| POST | `/api/v1/edge/events/ingest` | device auth RS256 (extração correta do token) | `success({ingested,submitted,batch_id,alerts_created,alerts_failed})` | `uploader.py` (após F0) | ~~P0 achado #8~~ **RESOLVIDO** (WS10): extrai o Bearer corretamente (`removeprefix`), como `/heartbeat`. `/edge/detections` NÃO existe — canônico = este endpoint (uploader aponta para cá). Evento `detection` também vira linha em `public.alerts` (mesmos portões do caminho servido); `alerts_failed>0` é FALHA visível, não "nada encontrado" |
| GET | `/api/v1/edge/events` | jwt custom sem checagem extra | `success({events,count})` | — | mesmo padrão de "sem validação explícita de ownership de site_id" do GET commands |

---

### 1.12 Events (`services/api/app/api/v1/events/routes.py`) — família `/api/v1`

FE: `eventsService.ts` (`getTimeline`, `getSummary`). **Ver achado #3 do resumo executivo — suspeita forte de mismatch de envelope.**

| Método | Path | Auth | Request | Response (backend real) | FE (assumido) | Nota |
|---|---|---|---|---|---|---|
| GET | `/api/v1/events/search` | jwt + tenant | paginação + filtros (`camera_id[]`, `class_name[]`, `module_code`, `from/to`, `min_confidence`, `include_demo`) | `success(data)` → `{status,data:{events,total,page,per_page,pages}}` | — (sem consumidor identificado) | presigned URL de evidência engole exceção com `except Exception: pass` — `frame_url` fica `None` silenciosamente |
| GET | `/api/v1/events/timeline` | jwt + tenant | `bucket`, `from/to` (obrigatórios), filtros | `success({timeline,bucket})` | `eventsService.getTimeline` espera `{success,message,data}` local | **mismatch de envelope suspeito** — ver achado #3 |
| GET | `/api/v1/events/summary` | jwt + tenant | `from/to` (obrigatórios, janela máx 92 dias) | `success({total,by_class,by_camera})` | `eventsService.getSummary` idem | idem |

---

### 1.13 Feedback (`services/api/app/api/v1/feedback/routes.py`) — família `/api/v1`

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| POST | `/api/v1/feedback` | `jwt_required_custom` (padrão diferente de `events`/`fueling`/`models`, que usam `jwt_required()` nativo) | `success({feedback},201)` | inconsistência de padrão de auth entre blueprints do mesmo serviço |
| GET | `/api/v1/feedback` | idem | `success({feedback,count})` | documentado como "export p/ pipeline de treino" (active learning), sem filtro de data |
| GET | `/api/v1/feedback/summary` | idem | `success({summary})` | — |

---

### 1.14 Frames (`services/api/app/api/v1/frames/routes.py`) — **BLUEPRINT VAZIO**

**Achado #9**: `frames_bp = Blueprint('frames', __name__, url_prefix='/api/frames')` é criado e registrado em `app/__init__.py`, mas o arquivo **não define nenhuma rota** (`@frames_bp.route` / `add_url_rule` inexistentes). O `AGENTS.md` do diretório e o `CLAUDE.md` raiz do projeto documentam `POST /api/frames/<id>/pre-annotate` e `POST /api/frames/prioritize` como se existissem. A funcionalidade de pre-annotate **existe**, mas em outro path: `POST /api/training/frames/<frame_id>/pre-annotate` (`training/routes.py`, `@require_training_role('write')`, feature flag OFF por padrão). **Ação recomendada**: atualizar documentação para apontar o path real, ou decidir se `/api/frames/*` deveria ser implementado (regressão vs nunca implementado — não determinado pelo levantamento).

---

### 1.15 Fueling (`services/api/app/api/v1/fueling/routes.py`) — família `/api`

Módulo `fueling` é "Placeholder" no CLAUDE.md (Sprint 5). Sem consumidor FE identificado nesta leva.

| Método | Path | Response | Nota |
|---|---|---|---|
| GET | `/api/fueling/stats` | `success({events_today,active_cameras,module_status})` | **Placeholder**: `module_status` hardcoded como string fixa `'configuring'` |
| GET | `/api/fueling/events` | `success({events,total})` | sem problema aparente |
| GET | `/api/fueling/dashboard` | `success(data)` variando conforme flag `fueling_use_mock` | feature flag resolvida em duas camadas (`tenants.feature_flags` JSONB > env `FUELING_USE_MOCK`, default `true`) — CD-03, comportamento documentado |
| GET | `/api/fueling/bays` | `success({bays[,no_data]})` | mesma lógica de flag mock/real |
| GET | `/api/fueling/bays/<int:bay_id>` | `success({bay})` / 404 | **Documentado como mock-only**: IDs inteiros 1-6 só existem no mock; com flag desligada retorna 404 sempre. Caminho real usa `/api/counting/sessions` com `bay_id` **UUID** — inconsistência de tipo de identificador (int vs UUID) entre mock e real |

---

### 1.16 Health (`services/api/app/api/v1/health/routes.py`)

| Método | Path | Família | Response | Nota |
|---|---|---|---|---|
| GET | `/health` | bare (nem `/api` nem `/api/v1`) | `jsonify` cru `{status,checks}` (não usa `app.core.responses`) | mesma view function registrada em **duas rotas simultâneas** (`/health` e `/api/v1/health`) via decorators empilhados |
| GET | `/api/v1/health` | `/api/v1` | idem | rota duplicada com `/health` |
| GET | `/api/v1/health/metrics` | `/api/v1` | `jsonify` cru `{database,redis,cameras_active}` | sempre 200 mesmo em falha interna (defaults `false`/`0`); usa `SET search_path TO %s` parametrizado — comportamento não-óbvio no psycopg2, vale confirmar em teste |

---

### 1.17 Models (`services/api/app/api/v1/models/routes.py` + `handlers.py`/`registry_handlers.py`) — família `/api/v1`

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| GET | `/api/v1/models/active` | jwt | `success(manifest)` (dict direto, sem chave aninhada) | Werkzeug resolve `/active` antes de `/<model_id>` sem colisão (já revisado) |
| POST | `/api/v1/models/<id>/pin` | jwt + checagem manual de role (não decorator dedicado) | `success({manifest,action})` | falha em `record_activation_log` é só logada (warning) |
| GET | `/api/v1/models` | jwt + tenant | `success({models,total})` | |
| GET | `/api/v1/models/<id>` | jwt + tenant | `success({model,lineage,active_deployments})` | remove `callback_token` do job antes de expor — boa prática |
| POST | `/api/v1/models/<id>/activate` | jwt + `@require_training_role('approve')` (+role check p/ `force`) | `success({model,rollout_synced,forced})` | sync best-effort do manifesto `{schema}.models`, engolida com warning (ADR-0037) |
| GET | `/api/v1/models/<id>/eval` | jwt + tenant | `success({evaluation})` | |
| GET | `/api/v1/models/<id>/drift` | jwt + tenant | `success({windows,total})` | |

---

### 1.18 Modules (`services/api/app/api/v1/modules/routes.py`) — família **`/api`** (apesar do arquivo estar em `api/v1/modules/`)

FE: `moduleService.ts` (`list, get, getClasses, getStats`) — todos OK.

| Método | Path | Auth | Response | FE | Nota |
|---|---|---|---|---|---|
| GET | `/api/modules/` | jwt | `success/error` | `moduleService.list()` | |
| GET | `/api/modules/<code>` | jwt | `success/error` | `moduleService.get(code)` | |
| GET | `/api/modules/<code>/classes` | jwt | `success/error` | `moduleService.getClasses(code)` | **NÃO filtra por `tenant_id` nem checa `tenant_has_module`** — lista classes globalmente |
| GET | `/api/modules/<code>/stats` | jwt + `tenant_has_module` | `success/error` (403 se módulo indisponível) | `moduleService.getStats(code)` | único endpoint do arquivo que valida `tenant_has_module` |
| PATCH | `/api/modules/<code>/classes/<id>` | jwt **sem tenant_id/role check** | `success/error` | — | **P0 achado #6**: qualquer usuário autenticado de qualquer tenant pode ativar/desativar qualquer classe globalmente |

---

### 1.19 Notifications (`services/api/app/api/v1/notifications/routes.py`) — família `/api/v1`

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| GET | `/api/v1/notifications/channels` | jwt custom + tenant | `success/error` | padrão consistente |
| POST | `.../channels` | jwt + `has_permission('notifications:manage')` | `success (201)/error` | tipos: whatsapp/telegram/email/webhook |
| PATCH/DELETE | `.../channels/<id>` | idem | `success/error` | — |

---

### 1.20 Operations (`services/api/app/api/v1/operations/routes.py`) — família **`/api`** (apesar do arquivo estar em `api/v1/operations/`)

| Método | Path | Response | Nota |
|---|---|---|---|
| GET | `/api/modules/<id>/operation-types` | `success/error` | nenhum erro tratado explicitamente (exceções propagam) |
| GET/POST | `/api/cameras/<id>/operations` | `success/error` | mesma inconsistência de família `/api` |
| PUT/DELETE | `/api/operations/<int:id>` | `success/error` | publica evento Redis best-effort p/ hot-reload do worker |
| GET | `/api/operations/<id>/results` | `success/error` | — |
| POST | `/api/operations/<id>/test` | `success/error` | dry-run, não persiste |

---

### 1.21 Quality (`services/api/app/api/v1/quality/routes.py`) — família `/api/v1`, **50 endpoints, zero consumidor em `services/`** (ver `CONTRATO_FRONT_BACK.md` §1.16 — exceção: `TabletTransition/Identified/ResultNOK.tsx` fazem raw fetch direto).

Auth predominante: "JWT manual via `_require_jwt()`" (padrão diferente do resto do projeto, funcionalmente equivalente a `@jwt_required`).

Destaques (tabela completa de 50 rotas não reproduzida aqui por volume — ver arquivo fonte; achados relevantes abaixo):

| Método | Path | Nota |
|---|---|---|
| GET | `/api/v1/quality/cameras` | **sem checagem `tenant_has_module('quality')`**, diferente de `assign`/`dashboard` |
| DELETE | `.../cameras/<id>/unassign` | **assimétrico com `assign`**: assign checa módulo, unassign não |
| PATCH | `.../cameras/<id>/config` | SET clause dinâmico via f-string, mas chaves de allowlist fixa — não injetável |
| GET | `/api/v1/quality/andon/<camera_id>` | **sem JWT** (só IP interno via `verify_andon_access`); itera por TODOS os schemas de tenant até achar a câmera — cross-tenant scan aceitável dado escopo read-only, mas vale registrar |
| GET | `.../reports/shift/pdf` | Response binário `application/pdf` via `make_response()` — **desvia do envelope padrão** |
| POST | `/api/v1/quality/demo/seed` | **P0 achado #5** — ver resumo executivo |

---

### 1.22 Recorders (`services/api/app/api/v1/recorders/routes.py`) — família `/api/v1`

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| GET/POST | `/api/v1/recorders` | jwt (+`require_training_role('write')` no POST) | `success/error` | — |
| GET/PUT/DELETE | `/api/v1/recorders/<id>` | jwt (+ write role em PUT/DELETE) | `success/error` | — |
| POST | `.../recorders/<id>/test` | jwt + write role | `success/error` | testa conexão NVR/DVR |
| GET | `.../recorders/<id>/recordings` | jwt + tenant | `success/error` (502 se NVR falhar) | timeline de gravações (ADR-0034) |
| POST | `.../recorders/<id>/extract-frames` | jwt + write role | `success (202)/error` | dispara Celery `extract_nvr_frames` |

---

### 1.23 Reports (`services/api/app/api/v1/reports/routes.py`) — família **`/api`** (apesar do arquivo estar em `api/v1/reports/`)

FE: `reportService.ts` (`getHomeReports`).

| Método | Path | Auth | Response | FE | Nota |
|---|---|---|---|---|---|
| GET | `/api/reports/home` | jwt + tenant | `success/error` | `reportService.getHomeReports()` | fallback silencioso `EMPTY_REPORTS` se `res.data` vier vazio |
| GET | `/api/reports/compliance` | jwt + tenant | `success/error` (400 se `period`/`from`/`to` inválidos) | — | relatório de compliance EPI, retorna `summary/pdf_url/period` |

---

### 1.24 Retention (`services/api/app/api/v1/retention/routes.py`) — família `/api/v1`

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| GET | `/api/v1/tenant/retention` | jwt + tenant | `success(data)` | override do tenant > plano > fallback 7 dias |
| PUT | `/api/v1/tenant/retention` | jwt + `has_permission('retention:write')` | `success(data)` (422 tier inválido) | tiers permitidos `{1,7,30,90}` |

---

### 1.25 Roles (`services/api/app/api/v1/roles/routes.py`) — família **`/api/admin/*`** (namespace distinto de `/api/v1/admin/*`)

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| GET/POST | `/api/admin/roles` | `@require_admin` | `success({roles,total})`/`success({role},201)` | `_resolve_tenant_id()` chama `request.get_json(silent=True, force=True)` duas vezes redundantemente |
| PUT/DELETE | `/api/admin/roles/<id>` | idem | `success/error` | DELETE: 409 se role tem usuários vinculados |
| GET/PUT | `/api/admin/users/<id>/role` | idem | `success/error` | |

**Nota de versionamento**: este é um **segundo namespace "admin"** (`/api/admin/*`) totalmente separado de `/api/v1/admin/*` (usado por todo o resto do painel admin). Achado (d) — ver seção de duplicatas.

---

### 1.26 Rules (`services/api/app/api/v1/rules/routes.py`) — família `/api`

| Método | Path | Response | Nota |
|---|---|---|---|
| GET/POST | `/api/rules` | `success({rules})`/`success({rule},201)` | Blueprint `url_prefix='/api/rules'` — legado, apesar do domínio (alertas/regras) já estar parcialmente em `/api/v1` |
| GET/PUT/DELETE | `/api/rules/<id>` | `success/error` | — |
| POST | `/api/rules/<id>/toggle` | `success({rule})` | — |

---

### 1.27 Scenarios (`services/api/app/api/v1/scenarios/routes.py`) — família `/api/v1`

FE: `types/scenario.ts` (`Scenario`, subtipado — `schedule: unknown[]`, `operations`/`alert_rules: Record<string,unknown>[]`).

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| GET | `/api/v1/cameras/<id>/scenario` | jwt + tenant (404 se câmera de outro tenant, C-01) | `success({scenario})` | `ScenarioEditor` existe no FE mas **sem chamada de service confirmada** (`CONTRATO_FRONT_BACK.md` §1.13) |
| GET | `/api/v1/scenarios/operation-types` | jwt | `success({types,module})` | módulo inválido retorna lista vazia sem erro — comportamento intencional documentado |

---

### 1.28 Site Gateways (`services/api/app/api/v1/site_gateways/routes.py`) — família `/api/v1`

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| GET/PUT | `/api/v1/site-gateways/<site_id>` | jwt custom (+ `has_permission('gateways:manage')` no PUT) | `success({gateway})` | — |
| PATCH | `.../status` | jwt custom, **sem checagem extra de permissão** (usado pelo device edge) | `success({gateway})` | condiz com o propósito documentado |

---

### 1.29 Storage (`services/api/app/api/v1/storage/routes.py`) — família `/api/v1`

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| GET | `/api/v1/storage/health` | **nenhuma** (público) | `success(data)` **sempre**, mesmo em erro (`connected:false` no catch) | **P1 segurança** — endpoint público executa upload/delete de teste real no storage |
| POST | `/api/v1/storage/test-upload` | `@jwt_required()` apenas | `success({key,...})`/`error(500)` | **Divergência doc-vs-código**: docstring do swagger diz "admin", mas código não valida role — qualquer usuário autenticado dispara upload de teste real |

---

### 1.30 Streams (`services/api/app/api/v1/streams/routes.py`) — família `/api`

| Método | Path | Auth | Response | Nota |
|---|---|---|---|---|
| GET | `/api/streams/status` | **nenhuma** (público, comentário explícito) | `jsonify({workers,status})` cru (não usa `app.core.responses`) | sempre 200, erro fica dentro do payload (`status:'error'`), nunca HTTP 500 |

---

### 1.31 Training (`services/api/app/api/v1/training/routes.py`) — família `/api` (majoritária), **um único outlier `/api/v1`**

Blueprint **sem `url_prefix`** — mistura paths `/api/training/*`, `/api/classes/*`, `/api/cameras/<id>/alerts`, `/api/alerts/<id>/acknowledge` no mesmo arquivo. FE: `trainingService.ts` (`listJobs, createJob, getJobStatus, getJobProgress, listModels, activateModel, listVideos, uploadVideo`) — todos os paths conferem, mas **response types são `unknown`/`unknown[]`** em quase todos os métodos (nenhuma interface `TrainingJob` existe no FE).

| Método | Path | Auth | FE | Nota |
|---|---|---|---|---|
| GET/POST | `/api/training/videos` | jwt | `trainingService.listVideos/uploadVideo` | existe rota paralela `POST /api/v1/videos/upload` com propósito semelhante — duplicação legado/v1 |
| GET | `/api/training/videos/<id>/frames` | jwt | raw fetch em `AnnotationInterface.jsx`/`AnnotationPage.tsx` | consumido por arquivo protegido (CLAUDE.md) |
| GET/POST | `/api/training/frames/<id>/annotations` | jwt | raw fetch `AnnotationInterface.jsx` | — |
| POST | `/api/training/frames/<id>/pre-annotate` | jwt + write role | — | feature OFF por padrão (ADR-0031 adendo); **é o path real da funcionalidade que `CLAUDE.md`/`AGENTS.md` documentam erroneamente como `/api/frames/{id}/pre-annotate`** (ver achado #9) |
| GET/POST | `/api/classes` | jwt (+write no POST) | raw fetch `AnnotationInterface.jsx` | path sem prefixo `/training` — fora do padrão do resto do blueprint |
| POST/GET | `/api/training/jobs` | jwt (rate limit 20/dia no POST) | `trainingService.createJob/listJobs` | — |
| GET | `/api/training/jobs/<id>/status` | jwt | `trainingService.getJobStatus` | — |
| GET | `/api/training/models` | jwt | `trainingService.listModels` | — |
| POST | `/api/training/models/<id>/activate` | jwt **sem `@require_training_role`** | `trainingService.activateModel` | inconsistência: create/update/delete de classes exigem role `write`, ativar modelo não |
| POST | `/api/training/frames/<id>/validate` | jwt | raw fetch `AnnotationPage.tsx` | — |
| GET | `/api/training/videos/<id>/validation-stats` | jwt | raw fetch `AnnotationPage.tsx`/`TrainingPage.tsx` | — |
| POST | `/api/training/images/upload` | jwt + write role | — | duplicado com `POST /api/v1/videos/images/upload` (achado #13) |
| POST | **`/api/v1/training/jobs/<id>/progress-callback`** | **sem JWT** — `X-Callback-Token` (hmac compare) | — | **único `/api/v1` dentro de um blueprint majoritariamente `/api`** — endpoint interno GPU→API; rate limit 60/min por IP, sem verificação de tenant a montante |
| GET | `/api/training/jobs/<id>/progress` | jwt | `trainingService.getJobProgress` | lê Redis **sem checar tenant_id do job** — job de outro tenant poderia ser consultado sabendo o id |
| GET | `/api/cameras/<id>/alerts` | jwt | — | rota de alertas registrada dentro do blueprint `training` — domínio errado |
| POST | `/api/alerts/<id>/acknowledge` | jwt | — | **duplicado com `alerts/routes.py`** (achado #11) |

---

### 1.32 Verification (`services/api/app/api/v1/verification/routes.py`) — família `/api`

| Método | Path | Response | Nota |
|---|---|---|---|
| GET | `/api/verification/queue` | `success({items,count})` | **`VerificationService.get_human_queue()` não recebe `tenant_id`** — nenhuma filtragem visível nesta rota; achado #14 |
| GET | `/api/verification/queue/count` | `success({count})` | mesma ausência de `tenant_id` |
| POST | `/api/verification/<id>/review` | `success({alert_id,verdict})` | idem — sem `tenant_id` explícito passado ao `human_review` |

---

### 1.33 Videos (`services/api/app/api/v1/videos/routes.py`) — família `/api/v1`, **14 endpoints, zero consumidor FE identificado**

Pipeline paralelo mais avançado (upload direto R2 + extração Celery) que nunca é chamado pelo frontend — `trainingService.uploadVideo()` usa o legado `/api/training/videos` (ver achado #13).

| Método | Path | Auth | Nota |
|---|---|---|---|
| POST | `/api/v1/videos/upload` | jwt, rate limit 10/h | multipart até 2GB |
| POST | `/api/v1/videos/upload-url` | jwt | presigned upload direto ao R2 |
| POST | `.../<id>/extract` | jwt | despacha Celery `extract_frames` |
| GET | `.../<id>/status` | jwt | — |
| DELETE | `.../<id>` | jwt + posse por `user_id` | idempotente (`already_gone:true` em vez de 404) |
| POST | `.../<id>/upload-complete`, `.../retry-extraction` | jwt + posse | — |
| GET | `.../<id>/download-url` | jwt + posse | — |
| POST | `.../<id>/frames/upload` | jwt + posse | — |
| POST | `.../<id>/finalize-extraction` | jwt **sem checagem de posse** | inconsistente com as rotas vizinhas do mesmo arquivo |
| GET | `.../<id>/blob` | jwt + posse | Response binário/streaming cru — exceção justificável ao padrão de envelope |
| POST | `.../<id>/server-extract` | jwt + posse | extração roda em `threading.Thread` daemon **dentro do processo Flask/gunicorn**, não via Celery — não escala, perde estado se o worker reiniciar |
| GET | `/api/v1/videos/storage` | jwt | `success(stats)` |
| POST | `/api/v1/videos/images/upload` | jwt | duplica `/api/training/images/upload` (achado #13) — cria `training_videos` sintético para reaproveitar contrato de frames |

**Nota geral**: checagem de posse neste arquivo é por `user_id`, não por `tenant_id` — pode ser intencional (vídeo como recurso pessoal), mas diverge do padrão multi-tenant do resto do projeto.

---

## 2. Frontend: tipos sem endpoint claro / infraestrutura

| Arquivo | Tipo/Export | Situação |
|---|---|---|
| `services/api.ts` | `api.fetchRaw(path, init)` | **Bypassa completamente** a lógica de 401 (refresh de impersonation/redirect login), toast de erro e timeout de 15s do `request()` padrão — qualquer endpoint chamado via `fetchRaw` não recebe tratamento de sessão expirada. Deveria ser documentado como uso exclusivo de SSE/streaming |
| `services/api.ts` | `api.downloadBlob(path)` | Timeout maior (30s), **não** trata 401 nem dispara toast — mesmo padrão de exceção do `fetchRaw` |
| `types/index.ts` | `ApiResponse<T>` | **Achado (b)**: modela envelope como `{success, message?, data?, error?}` — incompatível com `{status,data}` documentado como padrão obrigatório do backend. Pode ser tipo legado/morto ou usado por chamadas antigas fora do padrão atual — **verificar se há algum consumidor real deste tipo** |
| `types/cameraGrid.ts` | `GridPreset` | Tem `createdAt`/`cameraAssignments` (indício de persistência) mas **nenhum endpoint identificado** (`/api/grid-presets`?) nem confirmação de que é só `localStorage` — achado (c)/(e) ambíguo, verificar manualmente |
| `types/edge.ts` | `SiteHealth`, `Heartbeat`, `HeartbeatSummary` | Sem comentário de endpoint explícito no arquivo (diferente de `EdgeOverview`/`FleetSite`, que citam o path) — inferido por nome, não confirmado |
| `types/operations.ts` | `OperationType`, `OperationCreate/Update` | Endpoints prováveis (`GET /operations/types`, `POST/PUT /operations`) não citados no arquivo — não confirmado neste levantamento |
| `types/scenario.ts` | `Scenario.schedule` / `.operations` / `.alert_rules` | Tipados como `unknown[]`/`Record<string,unknown>[]` — contrato do DTO central da task-022 não foi totalmente modelado no frontend |

---

## 3. Família de Rota: `/api` (legado) × `/api/v1` (novo)

Conforme pedido pela task-069, esta seção lista **todos os endpoints da família legada `/api`** identificados no levantamento (ou seja, tudo que NÃO é `/api/v1`), organizados por blueprint, com destaque para os casos onde a família diverge do restante do diretório/domínio — que é exatamente a duplicidade que a Fase 1 deve flagar.

### 3.1 Blueprints **inteiramente** em `/api` (legado)

| Domínio | Arquivo | Endpoints legado | Observação de versionamento |
|---|---|---|---|
| Auth | `auth/routes.py` | `register`, `login`, `me` (3) | nunca existiu `/api/v1/auth` — aceitável, não há duplicata |
| Alerts | `alerts/routes.py` | `alerts`, `export`, `<id>/acknowledge`, `<id>/snapshot`, `stats` (5) | domínio "alerts" segue 100% `/api`, enquanto `edge`/`events`/`feedback` (funcionalmente próximos) já são `/api/v1` |
| Chat | `chat/routes.py` | `/api/chat`, `/api/chat/health` (2) | — |
| Counting | `counting/routes.py` | `sessions` + 5 sub-rotas (6) | Blueprint criado **sem `url_prefix`** — paths hardcoded no decorator |
| Devices | `devices/routes.py` | `claim-codes`, `claim` (2) | **inconsistente**: blueprints vizinhos `edge`/`edge_commands`/`edge_events`/`datasets` são todos `/api/v1` |
| Fueling | `fueling/routes.py` | `stats`, `events`, `dashboard`, `bays`, `bays/<id>` (5) | módulo inteiro "Placeholder" (CLAUDE.md), nunca migrado |
| Modules | `modules/routes.py` | `/`, `<code>`, `<code>/classes`, `<code>/stats`, `<code>/classes/<id>` (5) | **arquivo mora em `api/v1/modules/`, mas serve em `/api`** — só o path real importa, mas confunde na leitura do código |
| Operations | `operations/routes.py` | `operation-types`, `cameras/<id>/operations`, `operations/<id>[...]` (7) | mesma situação: arquivo em `api/v1/operations/`, serve em `/api` |
| Reports | `reports/routes.py` | `home`, `compliance` (2) | mesma situação: arquivo em `api/v1/reports/`, serve em `/api` |
| Roles | `roles/routes.py` | `/api/admin/roles[...]`, `/api/admin/users/<id>/role` (6) | **namespace "admin" paralelo e distinto** de `/api/v1/admin/*` (usado por 90+ outras rotas admin) |
| Rules | `rules/routes.py` | `rules`, `rules/<id>`, `rules/<id>/toggle` (6) | — |
| Streams | `streams/routes.py` | `/api/streams/status` (1) | público, sem envelope padrão |
| Verification | `verification/routes.py` | `queue`, `queue/count`, `<id>/review` (3) | — |
| Admin demo-videos | `admin/demo_videos_routes.py` | `/api/admin/demo-videos[...]` (3) | **inconsistente**: único arquivo de `admin/*.py` que não usa `/api/v1/admin` |
| Training (quase tudo) | `training/routes.py` | ~25 rotas (`training/videos`, `training/frames/*`, `classes`, `training/jobs*`, `training/images/upload`, `cameras/<id>/alerts`, `alerts/<id>/acknowledge`) | Blueprint **sem `url_prefix`**; mistura 4 domínios (training, classes, alerts, cameras) num único arquivo; **1 rota isolada é `/api/v1`** (`progress-callback`) — outlier na direção oposta ao padrão usual |

### 3.2 Blueprints com migração **parcial** (mistura `/api` + `/api/v1` no mesmo arquivo)

| Domínio | Arquivo | Rotas `/api` | Rotas `/api/v1` (aliases) | Observação |
|---|---|---|---|---|
| Cameras | `cameras/routes.py` | ~22 rotas | `probe`, `<id>/effective-model`, `<id>/config`, `<id>/health-context` (4) | Migração **confirmada incompleta pelo próprio código**: comentário explica que o alias `/api/v1/cameras/probe` foi criado porque clientes usando esse prefixo caíam num catch-all 405. `available-models` não tem alias mas `effective-model` (irmão direto) tem — inconsistência dentro do próprio arquivo |
| Training | `training/routes.py` | ~24 rotas | `/api/v1/training/jobs/<id>/progress-callback` (1) | outlier isolado, endpoint interno GPU→API |
| Health | `health/routes.py` | `/health` (bare, nem `/api`) | `/api/v1/health`, `/api/v1/health/metrics` | mesma view function serve `/health` e `/api/v1/health` simultaneamente |

### 3.3 Blueprints inteiramente `/api/v1` (referência — sem duplicidade)

`admin/*` (exceto `demo_videos_routes.py`), `branding/routes.py`, `dashboard/routes.py`, `datasets/routes.py`, `edge/routes.py`, `edge_commands/routes.py`, `edge_events/routes.py`, `events/routes.py`, `feedback/routes.py`, `models/routes.py`, `notifications/routes.py`, `quality/routes.py`, `recorders/routes.py`, `retention/routes.py`, `scenarios/routes.py`, `site_gateways/routes.py`, `storage/routes.py`, `videos/routes.py`.

---

## 4. Tabela de Divergências

Categorias conforme spec da task-069: **(a)** FE chama endpoint inexistente/renomeado · **(b)** mismatch de tipo/envelope · **(c)** endpoint sem consumidor (morto) · **(d)** duplicata `/api` × `/api/v1` · **(e)** placeholder.

| # | Categoria | Achado | Severidade | Evidência |
|---|---|---|---|---|
| D1 | (a) | **INVALIDADO (2026-07-15)** — corrigido pelo commit `5e47f09`, anterior a este levantamento. Endpoint `PATCH /api/counting/sessions/<id>` existe hoje | ~~P0~~ | `countingService.ts` + `counting/routes.py` |
| D2 | (a) | **INVALIDADO (2026-07-15)** — idem D1, `5e47f09`. `GET /api/counting/sessions/validation-report` existe hoje | ~~P0~~ | `countingService.ts` + `types/counting.ts` (`ValidationReport`) + `counting/routes.py` |
| D3 | (a) | **INVALIDADO (2026-07-15)** — `api.downloadBlob` já prefixa `/api`; `DashboardPage.tsx` resolve para `/api/v1/reports/export`, que bate com o backend. Sem 404 | ~~P0~~ | já documentado em `CONTRATO_FRONT_BACK.md` D3 |
| D4 | (a) | **INVALIDADO (2026-07-15)** — leitura equivocada; `/classes` (`training/routes.py`) e `/v1/quality/classes` são domínios diferentes (anotação EPI vs. controle de qualidade industrial), não a mesma rota mal-digitada | ~~P0~~ | já documentado em `CONTRATO_FRONT_BACK.md` D4 |
| D5 | (a) | **INVALIDADO (2026-07-15)** — ~~`TabletResultNOK.tsx:30` — `rework/start` não existe~~. Leitura equivocada: o achado leu um comentário/docstring desatualizado (linha 6 do arquivo), não a chamada de rede real (linha 30), que sempre foi `POST /api/v1/quality/gate/reworks` — rota que existe (`quality/routes.py:1733-1734`, `gate_start_rework`). Comentário corrigido | ~~P1~~ | já documentado em `CONTRATO_FRONT_BACK.md` D5 |
| D6 | (a)/(e) | `CLAUDE.md`/`AGENTS.md` documentam `POST /api/frames/{id}/pre-annotate`; blueprint `frames_bp` tem zero rotas; funcionalidade real vive em `POST /api/training/frames/<id>/pre-annotate` | **P1** | `frames/routes.py` vazio + `training/routes.py` |
| D7 | (b) | **INVALIDADO (2026-07-15)** — confirmado em `app/core/responses.py`: o envelope REAL é `{success,message,data}`. O `eventsService.ts` sempre esteve certo; era o `CLAUDE.md` que documentava `{status,data}` errado (agora corrigido) | ~~P0~~ | `eventsService.ts` + `events/routes.py` |
| D8 | (b) | **INVALIDADO (2026-07-15)** — mesma causa-raiz de D7: `{success,data}` é o padrão CORRETO, não um desvio. `impersonation.ts` estava certo | ~~P1~~ | `impersonation.ts` + `impersonation_routes.py` |
| D9 | (b) | **INVALIDADO (2026-07-15)** — mesma causa-raiz de D7: `{success,message?,data?,error?}` é compatível com o envelope real produzido por `success()/error()` em `responses.py`. Não é incompatibilidade, era o "padrão documentado" (CLAUDE.md) que estava errado | ~~P1~~ | `types/index.ts` |
| D10 | (b) | `countingService.setCameraModel` union `'epi'\|'quality'\|'counting'` vs CLAUDE.md que só documenta `epi`/`fueling` como módulos ativos — checar se a doc do projeto está desatualizada (quality/counting são domínios reais e implementados no backend) ou se o tipo FE está adiantado | **P2** | `countingService.ts` + CLAUDE.md módulos |
| D11 | (b) | `trainingService.ts`: `jobs`/`models`/`videos` tipados como `unknown[]`/`unknown` — nenhuma interface `TrainingJob` existe | **P2** | `trainingService.ts` |
| D12 | (b) | `cameraService.ts::formToApiPayload` mapeia `ip→host`, `path→rtsp_url_override` manualmente, sem checagem de tipo garantindo sync com o backend | **P2** | `cameraService.ts` |
| D13 | (c) | Blueprint `frames_bp` registrado com zero rotas (ver D6) — distinto de "sem UI", é backend morto | **P1** | `frames/routes.py` |
| D14 | (c) | `/api/v1/videos/*` (14 rotas, pipeline R2+Celery) sem nenhum consumidor FE identificado | **P2** (já mapeado como "sem UI" em `CONTRATO_FRONT_BACK.md` — reforço aqui por ser pipeline arquiteturalmente relevante, não só "tela faltando") | `videos/routes.py` |
| D15 | (c) | `GET /api/v1/admin/modules/catalog` e `GET /api/v1/admin/modules-registry` — dois endpoints admin com nomes quase idênticos, nenhum consumidor confirmado; confirmar se são duplicados ou domínios diferentes | **P2** | `admin/routes.py` |
| D16 | (d) | Câmeras: só 4 de ~26 rotas têm alias `/api/v1`; migração confirmada incompleta pelo próprio comentário do código | **P1** | §3.2 |
| D17 | (d) | Branding: API canônica (`admin/branding_routes.py`) × API legada nested marcada DEPRECATED no próprio docstring mas ainda viva (`branding/routes.py`) — dois endpoints de upload de logo com paths e auth diferentes | **P1** | §1.4 |
| D18 | (d) | `POST /api/alerts/<id>/acknowledge` registrado em dois blueprints (`alerts/routes.py` e `training/routes.py`) para o mesmo path+método | **P1** | §3.1 + `alerts/routes.py` + `training/routes.py` |
| D19 | (d) | `/api/admin/roles*` (roles/routes.py) é um namespace "admin" **paralelo** a `/api/v1/admin/*` (90+ rotas) | **P2** | §3.1 |
| D20 | (d) | `devices/routes.py` em `/api` enquanto vizinhos diretos (`edge`, `edge_commands`, `edge_events`, `datasets`) são `/api/v1` | **P2** | §3.1 |
| D21 | (d) | `admin/demo_videos_routes.py` em `/api/admin` enquanto todo o resto de `admin/*.py` é `/api/v1/admin` | **P2** | §3.1 |
| D22 | (d) | Três pipelines de upload de imagem/vídeo de treino sobrepostos: `/api/training/videos`, `/api/v1/videos/*`, `/api/training/images/upload`, `/api/v1/videos/images/upload` | **P1** | §1.33 |
| D23 | (d) | `health/routes.py`: mesma view function em `/health` e `/api/v1/health` | **P2** | §1.16 |
| D24 | (e) | `POST /api/v1/admin/tenants` — `temp_password` com padrão previsível e ano `2024` hardcoded | **P0 (segurança)** | `admin/routes.py` |
| D25 | (e) | `GET /api/v1/admin/dashboard` — `tickets_open` sempre 0 (branch morta `if False else SELECT 0`) | **P1** | `admin/routes.py` |
| D26 | (e) | `GET /api/v1/admin/inventory` — `edge_devices`/`sites` sempre `[]`, placeholder explícito "reservado para fase futura" | **P2** | `admin/routes.py` |
| D27 | (e) | `GET /api/fueling/stats` — `module_status` hardcoded `'configuring'` | **P2** | `fueling/routes.py` |
| D28 | (e) | `POST /api/v1/quality/demo/seed?force=true` — endpoint de demo destrutivo, sem restrição de admin, live em produção | **P0 (segurança)** | `quality/routes.py` |

**Nota**: não foram encontrados placeholders literais do tipo `my_domain`/`my_feature`/`{domain}` (nomes genéricos de scaffold) no levantamento — os placeholders reais identificados são os listados acima (senhas hardcoded, branches mortas, módulos "reservados para fase futura", strings de status fixas).

### 4.1 Achados de segurança correlatos (fora das 5 categorias estritas, mas relevantes ao contrato de auth)

| Achado | Severidade | Local |
|---|---|---|
| `PATCH /api/modules/<code>/classes/<id>` sem tenant/role check | **P0** | `modules/routes.py` |
| `GET /api/alerts/<id>/snapshot` sem filtro `tenant_id` | **P0** | `alerts/routes.py` |
| `GET/POST /api/verification/*` sem `tenant_id` explícito passado ao service | **P0 (verificar service)** | `verification/routes.py` |
| ~~`POST /api/v1/edge/events/ingest` — bug de extração de token~~ **RESOLVIDO (WS10)**: extrai Bearer corretamente | ✅ | `edge_events/routes.py` |
| `GET /api/v1/storage/health` público executa upload/delete real sem auth | **P1** | `storage/routes.py` |
| `POST /api/v1/storage/test-upload` — docstring diz "admin", código só exige JWT | **P2** | `storage/routes.py` |
| `services/api.ts::fetchRaw/downloadBlob` no frontend bypassam tratamento de 401/timeout padrão | **P2** | `api.ts` |

---

## 5. Referência Cruzada

- Para **operabilidade** (quais dos ~190 endpoints únicos têm tela/consumidor, quais são "NAO SURFACED", violações de raw `fetch()` fora de `api.ts`), ver **`docs/quality/CONTRATO_FRONT_BACK.md`** — não duplicado aqui.
- Este documento cobre o que aquele **não** cobre: shape de request/response por endpoint, família de versionamento (`/api` × `/api/v1`), placeholders/lógica morta encontrados na leitura linha a linha do código-fonte, e mismatches de tipo/envelope entre backend real e tipos TS do frontend.
- Achados D1–D5 e D18 (endpoints inexistentes já conhecidos) foram **cross-confirmados** nesta auditoria a partir da leitura direta do código-fonte de `counting/routes.py`, `dashboard/routes.py`, `quality/routes.py`, `training/routes.py` e `alerts/routes.py` — não são apenas herdados do documento de operabilidade, mas verificados de forma independente neste levantamento.

*Fase 1 da task-069 — zero mudança de comportamento. Próximos passos (Fase 2+) ficam fora do escopo deste documento: corrigir D1–D6 (P0), consolidar duplicatas de branding/versionamento (D16–D23), e decidir sobre os placeholders de segurança (D24, D28) — não corrigir nesta sprint conforme protocolo do CLAUDE.md.*

---

## Correção (2026-07-15) — execução do backlog da ADR-0041

Ao iniciar a execução do backlog (item 5 do ADR-0041), os achados **#1–#3 do resumo executivo**
e **D1, D2, D3, D4, D5, D7, D8, D9** da tabela de divergências foram revalidados contra o código
real (HEAD de `develop`, não o snapshot original de 2026-07-12) e marcados **INVALIDADOS**:

- **D1/D2** (counting): já corrigidos pelo commit `5e47f09`, anterior a este levantamento.
- **D3** (dashboard export): leitura equivocada — não considerou que `api.ts` já prefixa `/api`.
- **D4** (annotation classes): leitura equivocada — confundiu dois domínios distintos (`/classes`
  de anotação EPI vs. `/v1/quality/classes` de controle de qualidade industrial).
- **D5** (tablet rework): leitura equivocada — leu um comentário/docstring desatualizado
  (`TabletResultNOK.tsx:6`) em vez da chamada de rede real (linha 30), que sempre chamou
  `POST /api/v1/quality/gate/reworks` (existe, `quality/routes.py:1733`). Comentário corrigido.
- **D7/D8/D9** (envelope): causa-raiz era o próprio `CLAUDE.md`, que documentava `{status,data}`
  quando o envelope real (confirmado em `app/core/responses.py`) sempre foi `{success,message,data}`.
  `CLAUDE.md` e `apps/frontend/AGENT.md` corrigidos no mesmo commit desta nota.

Nenhuma mudança de código de produção foi necessária para esses itens (D5 só teve um comentário
corrigido, sem mudança de comportamento) — a auditoria original estava desatualizada ou
equivocada, não o código. D6, D16–D28 **não foram revalidados** nesta
passada; permanecem como estavam até confirmação individual.

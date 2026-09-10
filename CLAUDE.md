# CLAUDE.md

> **Princípios inegociáveis:** ver [`/constitution.md`](./constitution.md) (C-01..C-08). Em caso de conflito entre este arquivo e a constitution, **a constitution prevalece**.
>
> **Como atuar (diretriz operacional):** ver [`docs/DIRETRIZ_OPERACAO_CLAUDE_CODE.md`](./docs/DIRETRIZ_OPERACAO_CLAUDE_CODE.md) — fluxo develop→staging→main, equalização de ambientes, higiene de branch pós-merge, ADRs, evidência de PR e histórico. Precedência: **constitution → diretriz → este arquivo**.
>
> **Regra C-04 (a mais importante deste arquivo):** valide o estado real no código/git/banco. **Nunca confie neste arquivo nem em memória.** Este documento já esteve gravemente desatualizado uma vez (descrevia `backend/`, `frontend/` e 13 microserviços que não existem mais).
>
> **Última reconciliação com o repo:** 2026-08-14.

This file provides guidance to Claude Code when working with code in this repository.

---

## Identidade do Projeto

**Recognition** (nome anterior: EPI Monitor V2 / EPI-CATH-V2) — SaaS multi-tenant de visão computacional sobre CFTV. Desenvolvido por Vitor Emanuel (Logikos).

**O produto não é um detector fixo: cada cliente treina o próprio modelo.** Essa é a proposta de valor central — não implemente nada que assuma um modelo único global.

- **Módulos:** EPI/Segurança, Qualidade, Carga-descarga/Contagem
- **Cliente âncora:** RVB Isolantes (Blumenau/SC) — módulo EPI, ~28 câmeras
- **White-label** por tenant (ADR-0035)

**Taxonomia EPI da RVB (D-103) — 6 classes.** Cada cliente treina o próprio modelo; a lista
abaixo é a taxonomia de anotação vigente da RVB. **Capacete e colete NÃO são EPI exigido na
RVB** — `Capacete`/`Sem Capacete`/`Colete`/`Sem Colete`/`hardhat` saíram em definitivo
(D-103, ver `docs/REGISTRO_DE_DECISOES.md`). Não reintroduza como "classe futura".

<!-- RVB-EPI-CLASSES:start (D-103 — fonte: docs/REGISTRO_DE_DECISOES.md; gate: scripts/ci/check_docs_gate.py) -->
- Protetor auditivo
- Sem protetor de ouvido
- mascara
- Sem mascara
- Uso incorreto de mascara
- Botas
<!-- RVB-EPI-CLASSES:end -->

**Detector servido = ONNX Apache 2.0 (YOLOX / RF-DETR).**
**ZERO ultralytics/AGPL no caminho servido.** Há gate de licença no CI (task-055a). Ultralytics só é aceitável em scripts de treino offline, nunca no que é servido ao cliente.

---

## Deploy real (Railway)

`railway_start.py` roteia por `SERVICE_TYPE`. **Só existem 4 valores válidos:**

| `SERVICE_TYPE` | Serviço |
|---|---|
| `api` (padrão) | Flask + SocketIO, gunicorn/eventlet — o **monolito** |
| `worker` / `celery-worker` | Celery worker (todas as filas) |
| `pre-annotation` | DINO + SAM — **flag OFF**, plugável, não ativo |
| `landing-page` | Astro estático |

Mais `frontend` (React/Vite), `PostgreSQL` e `Redis` (plugins Railway).

**Os microserviços NÃO existem mais.** `auth-service`, `camera-gateway`, `ws-gateway`, `inference-service`, `scheduler-service`, `training-service` foram **absorvidos pelo monolito `api-v3` em mai/2026** (ADR-0014). Os diretórios remanescentes na raiz são histórico arquivado — não são deploy ativo. Não os referencie como se estivessem rodando.

**URLs produção**
- API: `https://api-v3-production-2b22.up.railway.app`
- Frontend: `https://frontend-production-bf96.up.railway.app`
- Landing: `https://landing-page-production-b659.up.railway.app`

---

## Branches

```
worktree (de origin/develop)  →  develop  →  staging  →  main
```

- **`staging` = PRODUÇÃO** (auto-deploy Railway). **NÃO é `main`.**
- `develop` = trabalho ativo. `main` = branch estável / gráfico de contribuições.
- `develop→staging→main` são **gates humanos**.
- Merge para `staging`/`main` = **merge commit, NUNCA squash** (runbook `docs/runbooks/GITHUB_CONTRIBUTIONS_MERGE_MAIN.md`).
- **Trabalho novo em worktree a partir de `origin/develop`** — nunca num checkout `wip/*`.

> ⚠️ **Estado em 2026-07-18 pós-consolidação (medido via `gh api .../compare`, NÃO por ref local):** a situação do
> "2026-07-13" **se inverteu** e a develop consolidou os PRs abertos. Números reais: **`develop` está 119 commits
> À FRENTE de `staging`** (−2: hotfixes de CI) e **+125/−3 vs `main`**. Plano: `docs/EQUALIZACAO_BRANCHES_2026-07-18.md`
> + `docs/CONSOLIDACAO_DEVELOP_2026-07-18.md`.
> 🔴 **A `staging` (= PRODUÇÃO) ainda tem AGPL no caminho servido** (`from ultralytics import YOLO` em
> `quality_inference.py`); a `develop` já está limpa (License gate verde). A **promoção develop→staging** (gate
> humano) é o que remove o AGPL de produção. Há também uma **colisão de migration `052` (6 arquivos)** latente na
> develop — `run_migrations` chaveia por prefixo → 5 puladas em deploy fresh (ver CONSOLIDACAO §Achado 2).
> **Regra:** estado de branch = `git fetch` fresco + `gh`, nunca ref local em cache (DIRETRIZ §6.1).

---

## Estrutura real do repositório (monorepo — ADR-0010)

```
services/
  api/app/                 # ← O BACKEND. Flask + SocketIO (NÃO é backend/)
    api/v1/                # blueprints: admin alerts auth branding cameras chat
                           # counting dashboard devices edge edge_commands
                           # edge_events events feedback frames fueling health
                           # models modules notifications operations quality
                           # reports retention roles rules scenarios
                           # site_gateways storage streams training verification videos
    core/                  # auth.py (get_tenant_schema/get_tenant_id), responses, exceptions
    domain/                # models/ + services/
    infrastructure/        # database/ (connection, repositories) + storage/ (R2)
  inference/               # engine de inferência (DeepStream / ONNX)
  edge-sync-agent/         # sincronização edge→cloud (buffer SQLite + backoff)

apps/
  frontend/src/            # ← O FRONTEND. React 18 + TS + Vite + vanilla-extract
  landing/                 # Astro

infra/migrations/          # ← AS MIGRATIONS. NNN_nome.sql (última: 117)
deepstream/                # pipelines epi/ quality/ fueling/ shared/
docs/decisions/adr/        # ADRs 0001–0062 (0057 e 0059 = números reservados)
requirements/              # base, api, worker, celery-worker, inference, training, pre-annotation
railway_start.py           # router por SERVICE_TYPE
```

**Os três erros que este arquivo já cometeu — não repita:**
- ❌ `backend/app/` → ✅ `services/api/app/`
- ❌ `frontend/` → ✅ `apps/frontend/`
- ❌ `backend/app/infrastructure/database/migrations/` → ✅ `infra/migrations/`

---

## Comandos de Desenvolvimento

```bash
# API local
cd services/api
export SERVICE_TYPE=api DATABASE_URL=... REDIS_URL=... JWT_SECRET_KEY=...
python3 ../../railway_start.py

# Frontend
cd apps/frontend && npm run dev

# Lint / types
cd services/api && python -m ruff check .
cd apps/frontend && npx tsc --noEmit

# Migrations (Railway roda automaticamente no startup do SERVICE_TYPE=api)
psql $DATABASE_URL -f infra/migrations/NNN_nome.sql

# Smoke test antes de merge
SMOKE_EMAIL=... SMOKE_PASSWORD=... ./scripts/smoke_test.sh https://api-v3-production-2b22.up.railway.app

# Deploy = push
git push origin staging
```

---

## Arquitetura ponta a ponta

**Usuário → Cloud.** Browser → React (`apps/frontend`) → Flask REST + SocketIO (`services/api`) no Railway. JWT com claims `tenant_id`, `tenant_schema`, `role`, `modules_enabled`.

**Cloud → Edge.** Mini PC **NVIDIA Jetson Orin NX Super 16GB** no site do cliente. **Baseline de plataforma
CONGELADA (go-live RVB): JP6.2 / L4T r36.4.3 / CUDA 12.6 / TRT 10.3 / DeepStream 7.1** — última combinação Orin
+ DS plenamente suportada; DS 8.0 é Thor-only e DS 9.1 (out 2026-07-14) exige JP7.2 (port = P0-CRÍTICO, não
fazer sem plano). Detalhe/matriz/landmines: `docs/edge/REGRAS_PLATAFORMA_JETSON.md §8`. Enrollment com token
one-time → device recebe **JWT RS256 com escopos** (ADR-0019, mas o **device auto-assina** — ver reconciliação S7
na ADR). Rede: **MikroTik + WireGuard hub-and-spoke, discagem outbound** (ADR-0020) — as câmeras Hikvision/Intelbras
**travam por lockout anti-brute-force** se expostas; port-forward é proibido por design.

**Edge → inferência.** MediaMTX faz proxy RTSP (ADR-0009) → DeepStream consome nativo via `nvurisrcbin`. Backend selecionável por `INFERENCE_ENGINE` (ADR-0001/0015). ADR-0040 (*proposta*, não aceita) quer ancorar o edge em Jetson Platform Services.

**Edge → Cloud.** Detecções em Redis pub/sub `detections:{camera_id}` (ADR-0002) → `edge-sync-agent` (buffer SQLite + backoff) → API → SocketIO → browser.

**Evidência.** **Recorder-first (ADR-0045):** o gravador/NVR do site é a fonte primária das evidências; o **Cloudflare R2** recebe apenas um **dataset seletivo** (clipes de ~20-30s ao redor do evento, ADR-0033), não toda evidência. O Orin tem **128GB = SO + app, NÃO é destino de armazenamento**. Buffer local é transitório, com **reserva de disco intocável** — disco cheio = intertravamento do device. *(A antiga política cloud-first foi superseded — não empurre toda evidência pro R2.)*

**`DEPLOYMENT_MODE`:** `edge` (produção) | `cloud_only` (flag suportada, sem cliente).

---

## Padrões Críticos

### Multi-tenancy: SCHEMA-PER-TENANT (ADR-0004)

Não é "coluna `tenant_id` em tudo". São **dois padrões coexistindo** — saiba qual usar:

| Onde | Padrão | Exemplos |
|---|---|---|
| `public.*` | coluna `tenant_id NOT NULL` | `tenants`, `edge_sites`, `device_tokens`, `ip_cameras`, `modules`, `operations`, `alerts` |
| `{tenant_schema}.*` | **sem** `tenant_id` — o schema É o isolamento | `cameras`, `models`, `frames`, `detections`, `training_jobs`, `quality_inspections` |

`get_tenant_schema()` e `get_tenant_id()` em `services/api/app/core/auth.py`.

**ADR-0017: sem fallback silencioso.** Token sem claim de tenant → falha. Não reintroduza defaults como `"public"` ou o tenant UUID zerado — isso é vetor de vazamento cross-tenant. **Cross-tenant → 404** (C-01), nunca 403 (não vaze existência).

### Database
- `psycopg2` direto, `RealDictCursor`. **Sem SQLAlchemy, sem ORM.**
- `DatabasePool.get_instance()` — nunca conexão avulsa.
- Todo SQL nos repositories.
- **Zero f-string com input do usuário em SQL** — inclusive em `SET search_path` (já foi vetor de injection real).

### API Response
```python
from app.core.responses import success, error
return success({"cameras": items})     # {"success":true,"message":"OK","data":{...}}
return error("Câmera não encontrada", 404)  # {"success":false,"error":"..."}
```

### Frontend
- `api.ts` retorna o envelope completo `{success, message, data}` (confirmado em `app/core/responses.py`) — **não** `{status, data}`.
- TypeScript strict. Zero `any` implícito.
- Bounding boxes: `pointerEvents: 'none'`, zero `onClick`.

### Qualidade
- Zero `print()` no backend — `logging.getLogger(__name__)`.
- `CORS(app, origins=config.CORS_ORIGINS)` — nunca `CORS(app)` bare.
- `RTSPUrlValidator` antes de qualquer URL chegar ao FFmpeg.

---

## Design — fonte de verdade visual

🔴 **`docs/design/handoff-f5/` + [`DESIGN-INDEX.md`](./docs/design/handoff-f5/DESIGN-INDEX.md) mandam
no visual.** O índice responde, sem perguntar a ninguém, **qual prancha rege qual tela** e **o que
ainda não tem desenho**.

**Antes de criar ou alterar qualquer tela:**

1. Abrir o `DESIGN-INDEX.md` e achar a prancha que rege a rota
2. Abrir a prancha (`.dc.html` — protótipo navegável, abre no navegador) e implementar **contra ela**
3. Declarar a prancha no PR (campo obrigatório do template)

**Tela SEM prancha → ⛔ NÃO desenhe em código.** Duas saídas, ⛔ nenhuma terceira:

- **produzir a prancha primeiro** — sessão de Claude Design de dentro do Code, `.dc.html` salvo
  direto no bundle e índice atualizado **no mesmo commit**; ou
- **registrar pendência de design** — linha `SEM-DESENHO` no índice + issue, e a tela espera

⚠️ *"Desenho em código e a prancha vem depois"* é como nasce a divergência que ninguém mais reconcilia.
Foi o que produziu as 22 perdas de paridade do front novo.

**Identidade:** sempre `--lk-*`. ⛔ **Zero hex solto em componente** — o white-label passa por
`ThemeProvider → /v1/tenant/branding → CSS vars`, e hex literal fura essa ponte em silêncio.

**Design novo nasce NO REPO.** Prancha que vive só como URL de artifact ⛔ não existe para quem vier
depois: exportar para `docs/design/handoff-f5/` **antes** de fechar a sessão.

**Cético de fidelidade (review de PR de tela):** compara *screenshot da implementação × prancha* —
estrutura, hierarquia, estados (vazio/carregando/erro), linguagem. Divergência não justificada
**reprova**. ⚠️ `IMPLEMENTADA` no índice significa *"existe prancha, existe rota, foi construída
contra ela"* — ⛔ **não** é atestado de fidelidade; quem atesta é o cético, PR a PR.

## Migrations — forward-only

1. Última: `ls infra/migrations/*.sql | sort | tail -1` (atualmente **117**)
2. **Permitido:** `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`
3. **NUNCA:** `DROP`, `ALTER COLUMN TYPE`, `DELETE FROM`, `TRUNCATE`
4. Nunca edite uma migration já aplicada — crie uma nova para corrigir
5. Rodar o **harness 2x** (idempotência). Sem exceção — "é só uma coluna" já quebrou deploy antes (ADR-0021: colisão de numeração derrubou o startup da API)
6. Nova tabela: decida `public` (com `tenant_id`) vs `{tenant_schema}` conforme ADR-0016

**Checklist pós-migration:** model → repository → service → route → types do frontend → testes → `docs/DATABASE.md`.

**Separe atomicamente:** migration e mudança de lógica **nunca no mesmo commit**.

---

## Anti-padrões (NÃO fazer)

- Inferir schema de migrations antigas / logs / memória em vez de consultar o banco real ou rodar o harness
- Referenciar tabela/coluna que não existe no schema final
- Tratar os microserviços arquivados como se estivessem em produção
- Colocar ultralytics (AGPL) no caminho servido
- Reintroduzir fallback de tenant em `auth.py`
- Trabalhar direto num checkout `wip/*` em vez de worktree de `origin/develop`

---

## Classificação de Impacto

| Nível | Escopo | Verificação |
|---|---|---|
| P0-CRÍTICO | Multi-serviço, risco de dados | Manual + testes + e2e |
| P1-ALTO | Serviço único, user-facing | Testes obrigatórios |
| P2-MÉDIO | Refactor interno | Self-review |
| P3-BAIXO | Documentação | Nenhuma |

Classificar **antes** de mudar. Verificação é proporcional ao nível. `risk:security` **para a fila** para revisão humana.

---

## Commits

```
feat(scope): descrição
fix(scope): descrição
refactor(scope): sem mudança de comportamento
```

Scopes: `api, frontend, migration, railway, edge, inference, training, landing, cameras, alerts, modules, quality, counting`

---

## Processo no GitHub

**Régua anti-burocracia: automação > cerimônia.** Nada aqui é aprovação manual na `develop`.

**PR.** Template curto (`.github/PULL_REQUEST_TEMPLATE.md`): o que muda · `Closes #N` · impacto P0–P3 · **zona quente tocada** · prova. Marcar a zona quente é o roteador — marcou `migration`, valem as regras de migration; marcou `caminho servido`, vale o gate de licença. PR pequeno > PR gordo.

**Cético como review.** O veredito do cético vai postado **como review no PR** (`gh pr review <N> --comment --body "..."`), não só no chat. Registro durável, **não-bloqueante na develop** — o objetivo é deixar rastro de que alguém olhou com prova reproduzida, não criar portão. Reprovação com evidência é sucesso do processo. O cético nunca é modelo menor que o autor.

**Issue = fila operacional.** Toda issue nasce com dono, classificação (P0–P3) e destino. Fecha por `Closes #N` **com prova**; fechamento manual exige justificativa escrita no comentário. Achado novo vira issue com dono — nunca execução silenciosa (exceto `risk:security`, que para a fila e vai ao Vitor antes de qualquer outra coisa).

**Labels.** `P0`–`P3` · `risk:security` · `faixa:*` (pista: modelo, migracao, ux, infra, github) · `humano-gated` (espera decisão/ação humana — dono nomeado na issue) · `flaky`.

**Teste instável é mentira de CI.** ⛔ Proibido `.skip`, `.retry`, retry-until-green ou afrouxar seletor para passar. Flaky mascarado ensina o time a "rerunar e seguir" — e é assim que um vermelho de verdade passa despercebido. Conserte a causa (estado compartilhado, setup/teardown, timing) e prove por mutação. Teste novo nasce isolado: mock/timer/DOM limpos no `afterEach`, zero dependência de ordem.

**Deploy.** Merge em `staging` ganha tag datada automática (`deploy/AAAA-MM-DD-sha`) com nota curta — complementa a proveniência de `/livez`.

---

## Session Protocol

### Iniciando Sessão
0. Ler [`docs/DIRETRIZ_OPERACAO_CLAUDE_CODE.md`](./docs/DIRETRIZ_OPERACAO_CLAUDE_CODE.md) — **como atuar** (fluxo, equalização, higiene de branch, ADRs, evidência, histórico)
1. Ler CLAUDE.md — **e verificar contra o repo real** (C-04)
2. `git branch --show-current` — está em worktree de `origin/develop`?
3. Health check: `cd services/api && python -m pytest tests/ -q`

**Antes de commitar:** testes da área afetada · `npx tsc --noEmit` (se front) · `ruff check .` (se back) · conventional commit.

**Definição de concluído:** compila · zero lint · commit no padrão · push · health check 200.

---

## Contexto vivo (leia se for retomar o projeto)

- `docs/HANDOFF_CONTINUIDADE.md` — estado, decisões pendentes, próximo passo
- `docs/PLANO_EXECUCAO_MIGRACAO_V3.md` — plano-mestre em 6 fases
- `docs/API_CONTRACT_MAP.md` — contrato FE↔BE canônico
- `docs/ROADMAP_GO_LIVE.md` — tasks até o go-live RVB
- `EDGE_DEPLOYMENT_PLAN.md` — as 10 fases do edge
- `docs/decisions/adr/` — ADRs 0001–0062 (0057 e 0059 reservados)

*Em caso de conflito entre este arquivo e o código real, **o código vence**. Corrija este arquivo.*

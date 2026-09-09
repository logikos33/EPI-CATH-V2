<!-- Parent: ../AGENTS.md -->

# alerts — EPI Violation Alerts

List, filter, export, and acknowledge alerts from violation detection.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/alerts` | GET | List alerts with pagination + filters (camera_id, date range, violation_type, acknowledged) |
| `/api/alerts/export` | GET | Export alerts to CSV (same filters) |
| `/api/alerts/<id>/acknowledge` | POST | Mark alert as acknowledged |
| `/api/alerts/acknowledge` | POST | Reconhece em LOTE: `{"ids": [...]}` (1..500) ou `{"all": true, "kind": ..., "camera_id": ...}` |
| `/api/alerts/stats` | GET | Alert counts by camera (total, unacknowledged) |

**Key Notes:**
- Pagination: `page`, `per_page` (default 20, max 100)
- Date filters: ISO 8601 format
- `?time_field=` — eixo de `start_date`/`end_date` E da ordenação. **Default
  `captured`** (`alerts.timestamp`, a hora do frame), que é a coluna que a
  lista e o CSV EXIBEM; `?time_field=created` volta a `created_at` (hora da
  gravação da linha). ⚠️ Default OPOSTO ao de `/v1/events/*`, que nasceu em
  `created` e tem consumidor antigo — ver issues #676 e #702.
- `?module_code=` — escopo de módulo, mesmo par de predicados de
  `/v1/events/summary`: a coluna `alerts.module_code` **e** o escopo de
  câmera do módulo (`camera_modules`). Ausente = sem escopo.
- `total_situacoes` (rajadas do recorte) agrupa SEMPRE pela hora de captura,
  independente de `time_field` — issue #674.
- CSV export includes violations array flattened (one row per violation)
- Lote: `all` tem de vir explícito (`true`). Corpo vazio nunca significa
  "reconheça tudo". A resposta traz `acknowledged` = linhas que MUDARAM
  (id de outro tenant / já reconhecido não contam) e `requested` (null no
  modo `all`). O recorte de `kind` usa os MESMOS predicados da listagem.
- Stats supports optional `camera_id` filter
- Confidence shown as percentage in CSV

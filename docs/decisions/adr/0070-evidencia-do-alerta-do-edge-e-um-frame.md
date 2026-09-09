# ADR-0070 — A evidência do alerta do edge é um FRAME, subido pela API no instante do evento

**Status:** Aceita · **Data:** 2026-09-09 · **Autores:** Logikos
**Relaciona:** ADR-0028 (superseded), ADR-0033 (clipe 20-30s), ADR-0045 (recorder-first), ADR-0002, ADR-0020

## Contexto

Medido em 09/09/2026: `public.edge_events` tinha 9 linhas — as detecções que o box da RVB publicou
entre 03:38 e 03:41 UTC, chegando à nuvem em ~20s. **Todas com `evidence_r2_key` NULL.** O alerta
nasce, aparece na tela, e o operador não tem frame para julgar. Alerta sem evidência não é alerta,
é ruído.

Três ADRs se cruzam aqui e é preciso dizer qual vale:

- **ADR-0028** (evidência cloud-first, tudo pro R2) está **Superseded**.
- **ADR-0045** (vigente) diz: fonte primária é o **gravador do site**; R2 é upload **seletivo**.
- **ADR-0033** diz: evidência **é clipe de 20-30s**, e "**o frame continua como thumbnail**" —
  fechando com "backend de captura de clipe = roadmap".

Não há contradição: o **clipe** fica no gravador e é servido sob demanda pelo mini-API local
(ADR-0045, já implementado em `evidence_api.py`); o que falta, e o que o operador precisa para
julgar sem sair da tela, é a **miniatura** — o frame do momento.

## Decisão

1. **Evidência de alerta do edge = UM frame JPEG**, não o clipe. O clipe permanece recorder-first
   sob demanda (ADR-0045). Isso não reabre o ADR-0028: o R2 recebe uma imagem por *alerta* (evento
   raro, já filtrado por `has_violation` no box), não o fluxo contínuo que aquele ADR queria empurrar.

2. **Quem sobe é o edge-sync-agent, pela API da nuvem** — `POST /api/v1/edge/cameras/<id>/evidence`,
   device token RS256, escopo `events:write`. O box **não tem, e não deve ter, credencial de R2**:
   todo caminho edge→nuvem já passa por device token (ADR-0019). A chave só pode vir de quem gravou
   o objeto, e é ela que o evento carrega até `alerta_de_evento_do_edge`.

   Não é o publicador de detecções: ele não tem credencial do gravador nem token da nuvem — só lê
   arquivo e publica no Redis. É o **relay**, que já tem os dois na mão.

3. **A chave segue a convenção do caminho ao vivo:** `evidence/{camera_id}/{timestamp}.jpg`, a mesma
   de `inference._save_alert`. Um segundo dialeto para o mesmo tipo de objeto seria dívida sem ganho.

4. **O pixel sai de uma captura RTSP ao vivo** (`RecorderClient.get_snapshot`), pelo mesmo
   `SnapshotExecutor` — e portanto pelo **mesmo circuit breaker anti-lockout**. O DeepStream não
   guarda frame (sink `fakesink`, `[osd] enable=0`) e `pyds` não está instalado no box, então não há
   frame já decodificado para reaproveitar. O coletor de frames tem frame na mão, mas dispara por
   movimento com cooldown próprio — nunca no instante do evento — e na RVB já atingiu o alvo nas 18
   câmeras (inerte desde 02/09).

5. **Evidência nunca bloqueia o alerta.** Gravador mudo, R2 fora, teto estourado: o evento sobe com
   `evidence_r2_key` ausente e a nuvem grava o alerta sem imagem (já logado como
   `edge_alert_sem_evidencia`).

## Números medidos no pandora (Orin NX, RVB, 09/09/2026)

| O quê | Medido |
|---|---|
| Uma captura ao vivo (`get_snapshot` → ffmpeg `-frames:v 1`) | **1,05–1,17 s**, ~410 KB |
| Conexões RTSP já abertas no gravador (DeepStream) | **17**, permanentes |
| Defasagem frame → barramento `det:*` | **1,25 s** |
| **Defasagem total do frame de evidência** | **≈ 2,4–4,3 s** após o frame que gerou a detecção |

A defasagem é real e não foi eliminada: a evidência mostra a cena alguns segundos **depois** do
quadro que disparou a detecção. Eliminá-la exigiria um buffer circular decodificando as 17 fontes em
paralelo ao DeepStream — o custo do coletor inteiro, multiplicado, para ganhar 3 segundos. Não vale
neste piloto. **A defasagem é documentada, não escondida.**

## Anti-lockout

Cada captura é UMA conexão RTSP nova no gravador — na RVB, 29 canais no MESMO aparelho
(192.168.35.18), que pune tentativa repetida de autenticação. Três defesas, nesta ordem:

1. **Breaker compartilhado:** a primeira `RecorderAuthError` de qualquer caminho (snapshot ou
   evidência) suspende os dois até reinício do processo. Credencial rejeitada nunca é reusada.
2. **Teto global de 20 capturas/min** (`EDGE_MAX_EVIDENCE_PER_MIN`, 0 desliga), que **não depende de
   quantas câmeras o site tem**. O publicador já espaça 30s por câmera, mas não enxerga o total: com
   29 câmeras o pior caso seria ~58/min.
3. **Pausa de 60s após qualquer falha** — existe pelo custo de *bloqueio*: a captura roda dentro do
   loop que lê o pub/sub, e uma nuvem fora do ar levaria o timeout inteiro (15s) a cada evento.

Conexões RTSP **bem-sucedidas** não alimentam contador de brute-force; o que o gravador pune é falha
de autenticação repetida, e é exatamente isso que o breaker corta no primeiro caso.

## Consequências

- O operador passa a ter o frame na tela de Eventos — o pré-requisito da tela de aprovação.
- +410 KB no R2 por alerta. Com teto de 20/min, o pior caso é ~8 MB/min por site; na prática a RVB
  produziu 3 alertas em 8 minutos.
- A evidência mostra a cena ~3s após a detecção. Quem for construir a tela de aprovação precisa
  saber disso.
- O clipe continua sendo roadmap (ADR-0033) e continua vivendo no gravador (ADR-0045).

# Runbook — destravar o caminho do edge (RVB Isolantes, Jetson Orin NX `pandora`)

> **Tudo aqui é AGNÓSTICO DE MODELO.** O detector entra por um único ponto
> (`--infer-config` do gerador → `[primary-gie] config-file`) e a taxonomia por
> outro (`--mapa-taxonomia`). Quem vencer o duelo não muda nenhum passo deste
> runbook.
>
> **🔴 ATO DO DONO** marca todo passo que ESCREVE no box do cliente ou na nuvem.
> A sessão que escreveu este runbook só leu. Cada passo tem uma **verificação** —
> o que não tem prova, não aconteceu.

**Medido em 2026-09-08** por SSH somente-leitura em `pandora@100.93.126.76` e por
`SELECT` no Postgres de Desenvolvimento.

---

## 0. Leia isto antes de qualquer coisa: existe um 5º bloqueio, e ele é o mais caro

Os quatro bloqueios conhecidos são de encanamento. Há um quinto, de **catálogo**,
que faz o cano perfeito entregar **zero alerta**:

Medido no DEV (`yolo_classes` do tenant RVB ∪ `module_classes` global do módulo
`epi`), o conjunto de classes com `is_violation = TRUE` é:

| origem | nomes com polaridade VIOLAÇÃO |
|---|---|
| `module_classes` (global, epi) | `no_helmet`/`Sem Capacete`, `no_vest`/`Sem Colete`, `no_gloves`/`Sem Luvas` |
| `yolo_classes` (tenant RVB) | `Sem botas` (id 12, **arquivada**) |

E o modelo do embarque (`b9243540`) só consegue emitir, depois da tradução pelo
mapa de taxonomia: `Botas`, `Luvas`, `mascara`, `Uso incorreto de mascara`,
`Óculos`, `Protetor auditivo`. **Nenhum desses nomes está no conjunto acima.**

Consequência, e ela é determinística, não probabilística:
`alerta_de_evento_do_edge` chama `_has_violation`, que consulta essa polaridade,
não encontra nada e devolve `False` — **nenhum alerta nasce**, e a tela lê isso
como turno limpo. `Uso incorreto de mascara` (o objetivo nº 1 do cliente) está
com polaridade **NULL de propósito** (calibração deu 20% de precisão).

**Isto NÃO se resolve com código.** É decisão humana, e são só duas saídas:

- **(a)** marcar alguma classe emitida pelo modelo como violação em
  `yolo_classes.is_violation` — hoje a única candidata é `Uso incorreto de
  mascara`, e ela reprovou na calibração; ou
- **(b)** treinar as classes de AUSÊNCIA (`Sem protetor de ouvido`, `Sem
  óculos`…) que o catálogo já tem, e servir esse modelo.

Enquanto isso não acontecer, use `--classes-violacao '*'` (§4) **apenas para
provar o cano** e desligue depois. O runbook diz onde.

---

## 1. Caminho crítico e o que roda em paralelo

```
  🔴 OTA (§3)  ──── é o que instala `redis` na venv ────┐
       │                                                │
       ▼                                                ▼
  agente novo (relay existe)                   Redis local (§2)
       │                                                │
       └──────────────► EDGE_REDIS_URL setado ◄─────────┘
                                │
   config real (§4a) ──► inferência (§4b) ──► publicador (§4c)
                                │
                                ▼
                      det:{camera_id} → relay → buffer → nuvem
                                │
                                ▼
                    ⛔ portão de polaridade (§0)
```

**Crítico e serial:** §3 (OTA) → §2 (Redis) → §4. O OTA vem primeiro porque a
release ativa no box (`b8668b72`, 18/08) **não tem** `detection_relay.py` e a
venv dela **não tem** o pacote `redis` (conferido: `pip list` do
`~/recognition/current/.venv` não lista redis). Setar `EDGE_REDIS_URL` hoje não
faria nada — e mesmo com o arquivo presente, o `import redis` falharia.

**Em paralelo, desde já, sem depender de nada:**
- §4a (gerar o config das câmeras reais) — só lê arquivos do box;
- §0 (decisão de catálogo) — é humana e é o gargalo real do produto;
- construir/validar o engine TensorRT do modelo vencedor.

---

## 2. Bloqueio 4 — Redis local no box

**O caminho é MUITO mais barato do que instalar Redis.** O binário **já existe**
no box, compilado da fonte na task-113 e nunca removido:

```
/home/pandora/soak113/redis/src/redis-server   # Redis 7.4.1, 17 MB
/home/pandora/soak113/redis/src/redis-cli
```

`redis-cli` "não respondia" porque **não está no PATH** — não porque falte. Não
há micromamba, nem build novo, nem sudo. Só falta uma config de produção (a do
soak escuta na 6390 dentro de `~/soak113`) e uma unit.

### 🔴 ATO DO DONO — 2.1 instalar config + unit

```bash
# no box, como pandora
install -d -m 700 ~/.config/recognition
install -d -m 755 ~/recognition/logs ~/.local/state/recognition/redis
install -m 644 ~/recognition-src/deployments/edge/redis-edge.conf \
               ~/.config/recognition/redis-edge.conf
install -m 644 ~/recognition-src/deployments/edge/systemd-user/recognition-redis.service \
               ~/.config/systemd/user/recognition-redis.service
systemctl --user daemon-reload
systemctl --user enable --now recognition-redis
```

### Verificação 2.1 — prova, não fé

```bash
systemctl --user is-active recognition-redis          # espera: active
~/soak113/redis/src/redis-cli -p 6379 ping            # espera: PONG
~/soak113/redis/src/redis-cli -p 6379 config get maxmemory   # espera: 402653184 (384mb)
systemctl --user show recognition-redis -p MemoryMax  # espera: MemoryMax=671088640
```

> ⚠️ `journalctl --user` **não funciona neste box** (`/var/log/journal` não
> existe; o comando devolve "No journal files were found"). Toda a evidência das
> units do edge vem de `~/recognition/logs/*.log` — é por isso que as units
> deste runbook trazem `StandardOutput=append:`.

### 🔴 ATO DO DONO — 2.2 apontar o agente para o Redis

Editar `~/.config/recognition/edge-sync-agent.env` e **descomentar/adicionar**:

```
EDGE_REDIS_URL=redis://127.0.0.1:6379/0
```

Depois: `systemctl --user restart edge-sync-agent`

**Verificação 2.2:** só faz sentido DEPOIS do §3 (a release atual não tem o
relay). Prova esperada no log:

```bash
grep -E "detection_relay_(subscribed|disabled)" ~/recognition/logs/*.log | tail -3
# espera: detection_relay_subscribed patterns=det:*,detections:*
# se aparecer detection_relay_disabled -> a variável não chegou na unit
```

---

## 3. Bloqueio 3 — atualizar o agente no box (OTA)

### O que foi medido, e por que a ação é MENOR do que parece

| fato | evidência |
|---|---|
| release ativa = `b8668b72` (18/08) | `readlink ~/recognition/current` |
| essa release posta em `/api/v1/edge/detections` (rota que nunca existiu) | `grep edge/detections ~/recognition/current/app/uploader.py` → linha 45 |
| ela **não tem** `detection_relay.py` | arquivo inexistente na release |
| `edge_software_channels.dev.target_ref` = **`b8668b72`**, publicado em 18/08 | `SELECT * FROM edge_software_channels` |
| o timer de OTA **está ativo e rodando a cada 10 min** | `systemctl --user list-timers` → próximo disparo em 30s |
| o agente está vivo | `heartbeat.ok` com mtime de 1 minuto atrás |

**O box não está travado: ele está exatamente no ref que a nuvem mandou.**
Ninguém publicou um ref novo desde 18/08. A ação não é mexer no box — é **uma
chamada na nuvem**. O box se atualiza sozinho em ≤10 min, reconstrói a venv (e é
aí que o pacote `redis` entra) e reinicia o daemon, com rollback automático se a
release nova não ficar saudável.

### 🔴 ATO DO DONO — 3.1 publicar o ref novo

```bash
# do laptop, com token de superadmin do DEV
curl -X PUT https://api-v3-desenvolvimento.up.railway.app/api/v1/admin/software-channels/dev \
  -H "Authorization: Bearer <token superadmin>" \
  -H "Content-Type: application/json" \
  -d '{"target_ref": "<sha da develop que contenha detection_relay.py>"}'
```

O ref precisa existir no remoto que o box busca
(`OTA_SOURCE_REPO=/home/pandora/recognition-src`, remote
`github.com/logikos33/Recognition.git`) — publique um SHA **já mergeado na
develop**, nunca um SHA só local.

### Verificação 3.1 — a build nova está rodando (prova, não presunção)

Esperar o timer (≤10 min) ou forçar: `systemctl --user start edge-sync-agent-updater`

```bash
# 1. o symlink apontando para o ref NOVO (não presumir: ler)
readlink ~/recognition/current
# 2. a peça que não existia agora existe
ls -l ~/recognition/current/app/detection_relay.py
# 3. a venv nova tem o cliente do barramento
~/recognition/current/.venv/bin/pip show redis | head -2
# 4. a rota certa (a antiga era /api/v1/edge/detections)
grep -n "edge/events/ingest" ~/recognition/current/app/uploader.py
# 5. o serviço voltou e o heartbeat é RECENTE (< 2x EDGE_HEARTBEAT_INTERVAL_S=45s)
systemctl --user is-active edge-sync-agent
date -r ~/.local/state/recognition/heartbeat.ok
```

Do lado da nuvem, o mesmo fato visto de fora:
`GET /api/v1/edge/sites/<SITE_ID>/heartbeats` com heartbeat dos últimos minutos.

**Se falhar:** o updater reverte sozinho (`current` volta ao ref anterior e o
serviço volta a ficar saudável). Prova de que reverteu: `readlink` mostra o ref
ANTIGO e `is-active` é `active`. Nesse caso, o problema está na release nova —
não no box.

---

## 4. Bloqueios 1 e 2 — câmeras reais + serviço de inferência de produção

### O que está no box hoje (medido)

- `~/jetson-experiments/mm/app_mm_*.txt`: 16 fontes `rtsp://127.0.0.1:8554/camN`
  (gerador sintético), `[sink0] type=1`. É config de **teste de carga**.
- `soak-infer-{epi,park,qaux,qmain}.service`: todas `disabled`, nenhuma iniciada
  neste boot. `GR3D_FREQ 0%` — a GPU está parada.
- **`pyds` NÃO está instalado** (sem probe Python sobre o metadado do
  DeepStream). O adaptador Redis do DeepStream fala Streams/`XADD` e o relay fala
  pub/sub — já descartado.
- **Mas o dump KITTI do GIE já foi usado no box** (`mm/kitti_park`,
  `mm/kitti_qmain`, 13.314 arquivos) e o formato traz **classe, caixa e
  confiança**. É por aí que a detecção sai do DeepStream sem código nativo novo,
  sem pyds e sem sudo.

### 4a. 🔴 ATO DO DONO — gerar o config das câmeras REAIS

> ### ⚠️⚠️ LOCKOUT — leia antes de rodar
> As Intelbras da RVB **travam por proteção anti-brute-force** se apanharem
> credencial errada em sequência. São **29 câmeras no mesmo gravador**
> (`192.168.35.18`, canais 1–29): um config errado é uma rajada de 29 tentativas
> falhas de uma vez, e o gravador inteiro sai do ar — incluindo o live view que
> hoje funciona.
>
> Por isso: **o gerador não aceita credencial nova**. Ele reusa exatamente a que
> já está em `~/.config/recognition/edge-sync-agent.env` (`RECORDER_*`) e que o
> `edge-live-view` já usa com sucesso. E **comece com UMA câmera.**

A senha nunca passa por linha de comando (apareceria em `ps` e no histórico) e
nunca é impressa (o resumo sai redigido, `admin:***@`). O config **gerado**
contém a senha na URI — o `nvurisrcbin` só aceita a URI pronta — e por isso nasce
`0600` em `~/.config/recognition/deepstream/`, **fora do repo. NUNCA COMMITAR.**

```bash
# canal 6 = "Corredor Segurança do trabalho", a câmera que a rodada de live view
# já provou boa. UMA fonte primeiro.
python3 ~/recognition-src/deployments/edge/gerar_config_deepstream.py \
  --nome epi \
  --cameras 4e261bef-f874-4bb1-b314-1451731089e7 \
  --infer-config ~/recognition/models/config_infer_<modelo>.txt \
  --kitti-dir /dev/shm/recognition-kitti-epi
```

`--kitti-dir` em `/dev/shm` (RAM) de propósito: no Orin **disco cheio é
intertravamento do device**, e o dump é buffer, não acervo — o publicador apaga
cada arquivo depois de ler.

**Verificação 4a:**
```bash
ls -l ~/.config/recognition/deepstream/app_epi.txt   # espera: -rw------- (0600)
grep -c "^\[source" ~/.config/recognition/deepstream/app_epi.txt   # espera: 1
grep -o "192.168.35.18:554/cam/realmonitor?channel=[0-9]*" \
     ~/.config/recognition/deepstream/app_epi.txt    # espera: channel=6, NÃO 127.0.0.1
cat ~/.config/recognition/deepstream/fontes_epi.json # espera: {"0": "4e261bef-..."}
```

Prova de que a credencial abre a câmera **antes** de subir o DeepStream (1
tentativa, não 29):
```bash
ffprobe -rtsp_transport tcp -v error -show_entries stream=codec_name,width,height \
  -of default=nw=1 "$(grep -m1 '^uri=' ~/.config/recognition/deepstream/app_epi.txt | cut -d= -f2-)"
# espera: codec_name/width/height. "401 Unauthorized" -> PARE, não repita.
```

> Se der 401: **pare**. Não tente de novo em sequência — cada tentativa conta no
> contador de lockout. Confira `RECORDER_USERNAME`/`RECORDER_PASSWORD` contra o
> que o live view usa e só então repita, uma vez.

### 4b. 🔴 ATO DO DONO — subir a inferência de produção

```bash
install -m 644 ~/recognition-src/deployments/edge/systemd-user/recognition-infer@.service \
               ~/.config/systemd/user/recognition-infer@.service
systemctl --user daemon-reload
systemctl --user start recognition-infer@epi      # start, NÃO enable, na 1ª vez
```

**Verificação 4b:**
```bash
systemctl --user is-active recognition-infer@epi           # espera: active
tail -20 ~/recognition/logs/infer-epi.log                  # espera: PERF com FPS > 0
ls /dev/shm/recognition-kitti-epi | head                   # espera: 00_000_*.txt aparecendo
timeout 3 tegrastats | head -1                             # espera: GR3D_FREQ > 0%
```

`GR3D_FREQ` saindo de 0% **sobre vídeo real** é a diferença entre este passo e as
`soak-infer-*`. Depois que estabilizar: `systemctl --user enable recognition-infer@epi`.

### 4c. 🔴 ATO DO DONO — subir o publicador (é o que fecha o cano)

O mapa de taxonomia é **dado**, e tem de ser o MESMO do caminho da nuvem:

```bash
install -d -m 755 ~/recognition/models
install -m 644 ~/recognition-src/services/api/app/domain/taxonomia/mapa_modelo_<id>.json \
               ~/recognition/models/mapa_taxonomia_epi.json
install -m 644 ~/recognition-src/deployments/edge/systemd-user/recognition-deteccoes@.service \
               ~/.config/systemd/user/recognition-deteccoes@.service
systemctl --user daemon-reload
```

**Primeiro em seco** (não publica nada, só mostra o que publicaria):
```bash
~/recognition/current/.venv/bin/python \
  ~/recognition-src/deployments/edge/publicar_deteccoes.py --seco \
  --kitti-dir /dev/shm/recognition-kitti-epi \
  --fontes ~/.config/recognition/deepstream/fontes_epi.json \
  --mapa-taxonomia ~/recognition/models/mapa_taxonomia_epi.json \
  --classes-violacao '*'
```

Espera-se `deteccao_publicada canal=det:<uuid> classes=['Protetor auditivo' ...]` —
**nome do CATÁLOGO, não `protetor_auricular`**. Se sair o nome cru do modelo, o
mapa não carregou e a nuvem descartaria tudo em silêncio (`_filtrar_por_escopo`
compara string exata). Classes internas (`orelha`, `mao`, `regiao_*`) **não podem
aparecer** — se aparecerem, o mapa está errado.

Depois, de verdade. O modo de prova é **uma linha** no
`~/.config/recognition/edge-sync-agent.env` (que a unit já lê) — **e sai de lá
depois**, ver §5:

```
EDGE_CLASSES_VIOLACAO=*
```
```bash
systemctl --user start recognition-deteccoes@epi
```

**Verificação 4c — a prova de ponta a ponta, em quatro degraus:**

```bash
# 1. o publicador está publicando
tail -5 ~/recognition/logs/deteccoes-epi.log
# 2. a mensagem está NO BARRAMENTO (assine e espere)
~/soak113/redis/src/redis-cli -p 6379 psubscribe 'det:*'
# 3. o relay enfileirou (a única prova local de que o agente ouviu)
grep detection_relay ~/recognition/logs/*.log | tail -5
sqlite3 ~/.local/share/recognition/edge-sync/buffer.db \
  "select event_type, count(*) from event_buffer group by 1"
# 4. a nuvem recebeu (hoje esta tabela tem ZERO linhas — medido em 08/09)
```
```sql
-- no Postgres do DEV
SELECT event_type, count(*), max(occurred_at) FROM edge_events GROUP BY 1;
SELECT count(*) FROM alerts
 WHERE tenant_id='63c219d8-fbef-4f3c-a7c9-058c742482e2' AND created_at > now()-interval '1 hour';
```

`edge_events` saindo de 0 é a prova de que a artéria carregou detecção pela
primeira vez. `alerts` continuar em 0 **com `edge_events` > 0** não é falha do
edge: é o portão do §0.

### 4d. Expandir para as 29 câmeras — só depois de 4a–4c verdes

```bash
python3 ~/recognition-src/deployments/edge/gerar_config_deepstream.py \
  --nome epi --infer-config ~/recognition/models/config_infer_<modelo>.txt \
  --kitti-dir /dev/shm/recognition-kitti-epi          # sem --cameras = todas do cache
systemctl --user restart recognition-infer@epi recognition-deteccoes@epi
```

O gerador só enxerga as câmeras que a **nuvem** mandou (18 hoje no
`config_cache.json`, das 29 cadastradas — as inativas não entram). Para incluir
uma câmera nova: ative na nuvem, espere o `config_poller`, **reinicie o
`edge-sync-agent`** (o cache é lido no boot do processo, ADR-0058) e regere.

**Verificação 4d:** `GR3D_FREQ` e RAM sob controle — a campanha de escala mediu
40 câmeras viáveis a GPU 45% com INT8, então 29 tem folga; confirme mesmo assim:
```bash
timeout 10 tegrastats | tail -3      # GPU e RAM: RAM total < ~12 GB
du -sh /dev/shm/recognition-kitti-epi # espera: alguns KB. Se crescer, o
                                      # publicador parou e o dump acumula.
```

---

## 5. Checklist de encerramento (não deixar o box num estado de teste)

- [ ] `EDGE_CLASSES_VIOLACAO=*` **removido** do `.env` (senão todo frame com
      qualquer detecção vira evento e o buffer cresce sem parar) e
      `systemctl --user restart recognition-deteccoes@epi`. Prova de que saiu:
      o log traz `violacao=[]` (ou a lista real) em vez de `violacao=*`.
- [ ] `systemctl --user enable` em `recognition-redis`, `recognition-infer@epi`,
      `recognition-deteccoes@epi` (sobrevivem a reboot — `Linger` já está ativo).
- [ ] `OTA_SECONDARY_UNIT_NAMES` no `.env` **incluindo** `recognition-deteccoes@epi`
      — senão ele fica preso no código da release anterior a cada OTA (é
      exatamente a dívida D-42, que já mordeu uma vez).
- [ ] `ENROLLMENT_TOKEN` vazio no `.env` (é one-time e já foi consumido).
- [ ] Nenhum arquivo de `~/.config/recognition/deepstream/` commitado.

---

## 6. O que NÃO foi possível confirmar (e por quê)

| item | por quê |
|---|---|
| Se o engine TensorRT do modelo vencedor carrega e detecta no box | Exigiria rodar `deepstream-app`/`trtexec` — **escrita/execução no box**, ato do dono. O engine em uso pelas configs de soak é `ppe_tiny_dyn_int8.engine` com `ppe_labels.txt` de 9 classes (taxonomia antiga, não a do embarque). |
| A senha do gravador (não foi lida, nem devia) | Só foi conferida a **presença** da chave `RECORDER_PASSWORD` no `.env`. O gerador foi testado com credencial sintética fora do box. |
| Se o dump KITTI acompanha 29 fontes sem virar gargalo de I/O | Precisa medir com o pipeline real rodando. `/dev/shm` + `unlink` após leitura limita o risco; a medição fica no passo 4d. |
| Se `pyds` poderia ser instalado sem sudo | Não testado: instalar é escrita no box. Fica como caminho de upgrade se o dump KITTI se mostrar caro. |
| A build nova do agente rodando (§3) | Depende de publicar o `target_ref` — ato do dono, na nuvem. |

## 7. Achados laterais (não bloqueiam, mas alguém vai tropeçar)

1. **`edge-log-rotate.service` rotaciona o diretório errado.** O `ExecStart` olha
   `%h/logs`, mas as units gravam em `%h/recognition/logs`. Resultado medido:
   `frame-collector.log` com **9,5 MB** e nunca rotacionado. Os logs deste
   runbook caem na mesma armadilha até isso ser corrigido.
2. **`deployments/edge/systemd/*.service` (as units de SISTEMA, com sudo) não
   correspondem ao box.** O box roda tudo em `systemd --user`. As novas units
   ficam em `deployments/edge/systemd-user/` para não confundir as duas famílias.
3. **`edge_events` tem ZERO linhas** em toda a história do DEV — a artéria nunca
   carregou uma detecção. É a linha de base contra a qual a verificação 4c mede.

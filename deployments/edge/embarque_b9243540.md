# Embarque do modelo `b9243540` no Orin NX da RVB

> **Estado: PACOTE PRONTO, NADA EXECUTADO NO BOX.** Este runbook foi escrito com
> acesso somente-leitura ao `pandora@100.93.126.76`. Nenhum arquivo foi criado,
> nenhum serviço habilitado, nenhuma conversão rodada lá. Cada passo abaixo diz
> o que exige a autorização do dono.
>
> Antes de rodar qualquer coisa, leia a seção **Bloqueios** — hoje o embarque
> **não produz um único alerta** mesmo se o engine converter perfeitamente.

## O que é o modelo

| | |
|---|---|
| id | `b9243540-ea14-4949-a870-6b8d6470faab` |
| nome | Logikos EPI 10 classes · 02/09 21h49 |
| arquitetura | RF-DETR (`framework=rfdetr`), mAP50 **0,677** |
| R2 | `models/63c219d8-.../runpod/11f1303c-.../model.onnx` |
| sha256 | `5f54c0e709dda097a0d4def9a613391cf630758b8aacaf6fba25c036fb8e0218` |
| tamanho | 113.869.845 bytes (108,6 MiB) |
| entrada ONNX | **estática `[1, 3, 560, 560]`** — batch 1, RGB, normalização ImageNet |
| saídas | `dets [B,Q,4]` cxcywh normalizado · `labels [B,Q,`**`13`**`]` logits crus |
| taxonomia | **13 índices**, não 10 — ver `services/api/app/domain/taxonomia/mapa_modelo_b9243540.json` |

**O nome mente por três.** "10 classes" são as 10 categorias com caixa; o export
COCO declara 13 (`recognition` id 0, `pessoa` id 8 e `rosto` id 12 com **zero
caixas**) e é isso que a cabeça do RF-DETR indexa. Um labelfile de 10 linhas
desloca todo rótulo a partir do índice 8. Por isso o labelfile é **gerado** do
mapa, nunca digitado (`embarque_b9243540.py artefatos`).

## Passo a passo

Tudo roda **no box**, em `tmux` (a conversão leva dezenas de minutos), a partir
de um checkout do repo ou dos dois arquivos (`embarque_b9243540.py` +
`mapa_modelo_b9243540.json`) copiados para lá.

### 1. Baixar e conferir integridade — **antes** de qualquer conversão

```bash
export R2_ENDPOINT=... R2_KEY=... R2_SECRET=... R2_BUCKET=...   # 🔐 do dono
python3 embarque_b9243540.py baixar --destino ~/recognition/models
```

Confere tamanho **e** sha256 contra o mapa e **para** se divergir. Converter um
download truncado gera um engine que carrega, roda e detecta errado — o pior
modo de falha num produto de segurança. Sem credencial R2 no box, o comando
aceita o `.onnx` já copiado por `scp` e só valida.

> 🔐 **Autorização necessária:** credenciais R2 no box, ou o `scp` do artefato.

### 2. Gerar labelfile e config do nvinfer

```bash
python3 embarque_b9243540.py artefatos --destino ~/recognition/models
```

Produz, a partir do **mesmo** mapa que o caminho da nuvem usa:

- `~/recognition/models/b9243540_labels.txt` — 13 linhas, na ordem dos índices;
- `~/recognition/models/config_infer_b9243540.txt` — `num-detected-classes=13`,
  `network-mode=2` (FP16), `cluster-mode=4`, `maintain-aspect-ratio=0`.

> ⚠️ **Não reaproveitar** `~/jetson-experiments/stress102/config_infer_rfdetr_b8.txt`
> nem o `rfdetr_labels.txt` ao lado dele. Aquele labelfile é do dataset PPE
> Roboflow de julho — `Safety-Equipment, Boots, Ear-protection, Glass, Glove,
> Helmet, Mask, Person, Vest, background`. Dez nomes, em inglês, de outro
> domínio, com `num-detected-classes=10`. Encaixa sem erro e rotula tudo errado.

### 3. Converter ONNX → TensorRT (**FP16**)

```bash
python3 embarque_b9243540.py engine --destino ~/recognition/models --mostrar  # confere o comando
tmux new -s trt 'python3 embarque_b9243540.py engine --destino ~/recognition/models'
```

O comando exato, para a TRT **10.3** desta plataforma:

```bash
/usr/src/tensorrt/bin/trtexec \
  --onnx=$HOME/recognition/models/b9243540.onnx \
  --saveEngine=$HOME/recognition/models/b9243540_fp16.engine \
  --fp16 \
  --memPoolSize=workspace:4096 \
  --skipInference \
  --exportLayerInfo=$HOME/recognition/models/b9243540_fp16.layers.json
```

Sem `--shapes`: a entrada é estática. `--skipInference` (nome do flag na TRT 10;
o antigo `--buildOnly` não existe mais) porque medir throughput é outro passo,
com o pipeline montado.

#### Precisão: **FP16**, e não INT8 — a campanha de escala já mediu isto

O projeto de fato elegeu **INT8 como config vencedora** — para **YOLOX**, que é
CNN. A mesma campanha mediu o contrário para DETR, e este modelo é DETR:

- `docs/edge/REGRAS_PLATAFORMA_JETSON.md` §3.2: *"INT8 PTQ com calibração real
  VALE em CNN … **Em DETR, NÃO vale.**"*
- `docs/edge/CAMPANHA_ESCALA_2026-07-17.md`, tabela head-to-head, linha INT8:
  YOLOX-Tiny *"viável (calibração real)"* · RF-DETR Nano *"não compensa"*.
- §8.5: o teto de quantização do Orin (SM 87) **é** INT8 — não há FP8/FP4 em
  hardware. Ou seja, não há terceira opção a perseguir.

Some-se o custo: INT8 exigiria montar um conjunto de calibração (≈300 imagens
reais), buildar, e **re-validar mAP** — e a classe que justifica este modelo,
`mascara_incorreta`, tem **153 caixas de treino**, a menor de todas. É
exatamente o tipo de classe que a quantização degrada primeiro, e a que já vem
com 20,0% de precisão medida em campo. FP16 preserva o pouco que existe.

Referência de custo (mesma campanha, RF-DETR **Nano 384×384**, batch-1):
28 câmeras → 15,4 FPS/stream, **3,1 inf/s/câmera**, GPU 67%. Este modelo é
**560×560 e ~108 MiB** — significativamente mais pesado que o Nano medido. **Os
números da campanha NÃO se aplicam;** medir antes de prometer cadência.

### 4. Onde o artefato fica e como o serviço o encontra

```
~/recognition/models/
├── b9243540.onnx                  ← origem, mantido para reconversão
├── b9243540_fp16.engine           ← o que o nvinfer carrega
├── b9243540_labels.txt            ← gerado do mapa
└── config_infer_b9243540.txt      ← gerado do mapa, aponta para os dois acima
```

O diretório já existe e já é o lugar de modelo no box (hoje contém
`yolox_nano.onnx`). O engine é referenciado por `model-engine-file=` no config,
e o config por `config-file=` no `[primary-gie]` do `deepstream-app`.

> ⚠️ **Não existe serviço para carregar isto.** Ver **Bloqueio E-2**. Os quatro
> `soak-infer-*.service` estão `disabled` e apontam para
> `~/jetson-experiments/mm/app_mm_all_prod_*.txt`, que são configs de soak
> contra o gerador sintético. Qual unit consome o config novo é **decisão do
> dono**, não deste runbook.

> 🔐 **Autorização necessária:** criar/habilitar qualquer unit systemd no box.

### 5. Verificar que embarcou certo — fumaça box × nuvem no MESMO frame

Se as duas saídas não baterem, o embarque **falhou**. Isto tem de ser detectável.

**5a. Referência da nuvem** (no laptop/worker, com o `.onnx` e o mesmo frame):

```python
from app.domain.detectors.onnx_rfdetr import RfDetrOnnxDetector
from app.domain.taxonomia import carregar_mapa
import cv2, json

mapa = carregar_mapa()
det = RfDetrOnnxDetector("b9243540.onnx", class_names=mapa.nomes_do_modelo,
                         confidence=0.4, input_size=mapa.entrada_hw)
saida = {p.name: det.predict(cv2.imread(str(p))) for p in frames}
json.dump(saida, open("nuvem.json", "w"), ensure_ascii=False)
```

Os frames: pegue 20–30 do split **val** do próprio dataset em
`dataset-exports/63c219d8-.../v17c-partes/val/*.jpg` (1000 disponíveis). São
imagens reais da RVB, o modelo não as viu no treino, e estão acessíveis dos dois
lados — nada de frame improvisado.

**5b. Saída do box:**

```bash
python3 embarque_b9243540.py fumaca --destino ~/recognition/models \
        --frames ~/embarque/frames --saida edge.json
```

Roda o **engine** com o **mesmo pré-processamento e o mesmo pós-processamento**
do caminho servido (sigmoid + top-k sobre query×classe — *não* argmax por
query). Cópia deliberada de `onnx_rfdetr.py::_postprocess_raw`: se este passo
usasse outro pós-processamento, estaria comparando o box com uma nuvem
imaginária.

**5c. Confronto:**

```bash
python3 embarque_b9243540.py comparar --edge edge.json --nuvem nuvem.json
```

Critério: para cada detecção da nuvem tem de existir uma do box com a **mesma
classe** e **IoU ≥ 0,85**; score pode diferir até 0,05. Apertado em classe e
geometria, frouxo em score — porque há dois erros conhecidos e limitados: FP16,
e o `net-scale-factor` **escalar** do nvinfer (a nuvem usa std por canal
`[58.395, 57.12, 57.375]`; o nvinfer só aceita um número, 1/57,63 → até ~1,3%
por canal). Detecção que **some** e falso positivo **novo** reprovam nos dois
sentidos: no RVB, uma detecção que some é uma violação que ninguém vê.

Exit 0 = aprovado. Exit 1 = reprovado, com a lista de qual detecção sumiu.

> **5d (separado, e não coberto pelo script):** repetir a fumaça **através do
> `deepstream-app`**, com o config gerado e o parser custom. 5b/5c medem "a
> conversão TRT preservou o modelo"; 5d mede "o parser do DeepStream concorda".
> São falhas diferentes e hoje **5d tende a reprovar** — ver Bloqueio P-1.

---

## Bloqueios

Ordenados por quanto impedem o embarque de significar alguma coisa.

### T-1 · Taxonomia: o embarque hoje produz **zero alertas** — 🔴 decisão humana

Das 13 saídas do modelo, exatamente **uma** (`botas`) casa por acaso com um nome
do catálogo do RVB. Todo o resto cai em "classe indecidida"
(`inference.py::_has_violation`): não alerta, não conta como conformidade, avisa
uma vez em WARNING e some. A tela lê isso como turno limpo.

O mapa de taxonomia (entrega 1) resolve a tradução. O que ele **não** pode
resolver sozinho:

- **`Uso incorreto de mascara` está com `is_violation = NULL`** — indecisa por
  decisão medida (`scripts/ops/aplicar_calibracao_rvb.py`: 20,0% de precisão em
  10 julgados). Enquanto for NULL, **o objetivo nº1 do cliente não gera
  alerta**, com mapa e tudo. Reverter para TRUE é decisão do dono, com nova
  rodada de calibração — não é ajuste de config.
- **`mascara` (yolo_classes id 6) está arquivada** desde 06/09. A polaridade
  ainda resolve (a query não filtra `archived_at`), mas é frágil de propósito.

### T-2 · `Luvas` e `Óculos` não estão em `yolo_classes` — ✅ resolve por dado, e **não** criando linha

Confirmado no banco DEV: `yolo_classes` do tenant RVB tem apenas 4 (Protetor
auditivo), 7 (Sem protetor de ouvido), 10 (Botas), 11 (Uso incorreto de mascara)
ativas. `Luvas` e `Óculos` vivem no **catálogo global** `module_classes`
(`gloves`/`Luvas` class_id 4 · `glasses`/`Óculos` class_id 6). A polaridade
servida é a **união** global ∪ tenant, então o mapa casa por `display_name` e
funciona. **⛔ Não criar homônimas em `yolo_classes`** — ADR-0071 e
`TenantClassService._reject_if_in_global_catalog` bloqueiam, e o efeito seria
partir o acervo em dois `class_id`.

### P-1 · O parser RF-DETR do box faz **argmax por query** — 🔴 código

`~/jetson-experiments/rfdetr-parser/nvdsparsebbox_rfdetr.cpp` escolhe **uma**
classe por query (`argmax` do logit) e descarta as outras. O caminho servido da
nuvem faz o oposto, e documenta por quê
(`onnx_rfdetr.py::_postprocess_raw`): *"uma query PODE emitir mais de uma
classe, e o argmax descartava todas menos a maior"* — RF-DETR treina com **focal
loss**, classes independentes.

Isto não é teórico neste modelo: `regiao_boca_nariz` e `mascara_incorreta`
descrevem **a mesma região da imagem** e tendem a cair na mesma query. Onde a
região vencer o argmax, o box perde exatamente a classe que o cliente quer — e
`regiao_boca_nariz` tem 917 caixas de treino contra 153 de `mascara_incorreta`.

**Falta:** um parser que faça top-k sobre query×classe, ou pelo menos emita
todas as classes acima do limiar por query. É C++, é pequeno, e precisa de
re-compilação no box (`~/jetson-experiments/rfdetr-parser/Makefile`).

### P-2 · Batch fixo 1 no ONNX — ⚙️ export/re-treino

A entrada é `[1, 3, 560, 560]` **estática**. O `batch-size` do nvinfer não pode
passar de 1 sem re-exportar o modelo com eixo de batch dinâmico. Em RF-DETR o
batching rende pouco (+21% em b8, contra +115% de CNN — campanha §5), então o
impacto é menor que em YOLOX, mas o teto de câmeras precisa ser **medido**, não
herdado do Nano 384×384.

### E-1 · Os configs do box apontam para o gerador sintético — ✅ configuração

Confirmado: todos os `~/jetson-experiments/mm/*.txt` têm
`uri=rtsp://127.0.0.1:8554/cam0..cam27` — o MediaMTX local
(`~/jetson-experiments/mediamtx`) servindo streams sintéticos da campanha de
julho. **Nenhuma câmera da RVB.** Resolve por configuração (gerar os `[sourceN]`
a partir do inventário real), mas o inventário e as credenciais das câmeras são
**dado do cliente** → depende do dono.

### E-2 · Não há serviço de inferência instalado — 🔴 decisão humana

No box rodam hoje 5 units `--user`: `edge-sync-agent`, `edge-live-view`,
`edge-frame-collector`, `edge-monitoring-collector`, `edge-telemetry-collector`.
**Nenhuma de inferência.** Os `soak-infer-{epi,park,qaux,qmain}.service` estão
`disabled` e são artefatos do soak task-113 (`ExecStart=/usr/bin/deepstream-app
-c ~/jetson-experiments/mm/app_mm_all_prod_epi.txt` — o config sintético do E-1).
Não há `recognition-deepstream@.service` instalado, embora exista no repo em
`deployments/edge/systemd/`.

**Falta:** decidir qual unit serve EPI em produção, com qual cap de memória, e
instalá-la. Escrever em `~/.config/systemd/user/` exige a autorização do dono.

### E-3 · O agente do box é anterior ao `detection_relay` e posta em rota morta — ✅ código, já pronto no repo

- Release corrente: `~/recognition/current` → `releases/b8668b72…` (**18/08**).
- `detection_relay.py` entrou na develop em `1228ab56` (**23/08**) — **5 dias
  depois**. Confirmado: o arquivo **não existe** em `~/recognition/current/app/`.
- O `uploader.py` do box posta em `POST /api/v1/edge/detections`. Essa rota
  **nunca existiu** na API (as rotas `edge_bp` são heartbeat, config/poll,
  software/target, frames, snapshot, live-view, sites, enroll…). A develop já
  corrigiu para `POST /api/v1/edge/events/ingest`, que existe
  (`api/v1/edge_events/routes.py`).

**Resolve por OTA** — o próprio agente troca o symlink `current`. Mas há um
quarto ponto, **de configuração**: mesmo com o código novo,
`build_detection_relay_from_env` só liga o relay se **`EDGE_REDIS_URL`** estiver
setada, e ela **não está** em `~/.config/recognition/edge-sync-agent.env`
(conferido: 30 chaves, nenhuma delas). Sem essa variável o relay nasce desligado
e o buffer segue vazio.

> 🔐 **Autorização necessária:** disparar o OTA e editar o `.env` do agente.

### E-4 · Sem Redis local publicando detecções — ⚙️ integração

`detection_relay` assina `det:*` / `detections:*` num Redis local. O
`soak-redis.service` está `disabled`, e **o publicador não existe no repo**: a
probe DeepStream que publicaria as detecções vive nos runners
`jetson-experiments/mm`, fora do controle de versão, e segundo
`docs/edge/DIAGNOSTICO_OBSERVABILIDADE_2026-07-21.md` ainda não publicava nada.
Entre "o engine detecta" e "o alerta aparece na tela do cliente" faltam, em
ordem: probe → Redis → relay → uploader → `events/ingest`.

---

## Resumo do que exige autorização do dono

| # | Ação no box | Por quê |
|---|---|---|
| 1 | Colocar credenciais R2 (ou `scp` do `.onnx`) | segredo do cliente |
| 2 | Escrever em `~/recognition/models/` | primeira escrita deste pacote no box |
| 3 | Rodar `trtexec` (dezenas de min, GPU ocupada) | concorre com os coletores em produção |
| 4 | Compilar o parser RF-DETR corrigido (P-1) | muda binário carregado pelo nvinfer |
| 5 | Criar/habilitar unit systemd de inferência (E-2) | serviço novo em produção |
| 6 | Disparar OTA do agente + setar `EDGE_REDIS_URL` (E-3) | reinicia o agente em produção |
| 7 | Reapontar os `[sourceN]` para as câmeras reais (E-1) | inventário e credenciais do cliente |
| 8 | **Reverter `Uso incorreto de mascara` para violação (T-1)** | decisão de produto, com calibração |

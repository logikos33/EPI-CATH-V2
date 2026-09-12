# Levantamento Pandora RVB → Pandora da feira

**Data:** 2026-09-12 · **Escopo:** inventário com prova, para provisionamento do Pandora novo (Jetson Orin NX 8GB, JetPack 6.2 alvo) e para a tabela de dimensionamento do plano de negócio. ⛔ Nada foi consertado, treinado ou otimizado nesta rodada.

---

## ⚠️ Divergências entre o prompt e a realidade medida

O prompt já avisava para tratá-lo como hipótese. Quatro pontos vieram de leitura desatualizada e foram corrigidos aqui:

| # | O prompt dizia | O que foi medido | Impacto |
|---|---|---|---|
| 1 | Repo local em `~/Logikos Recogntion/Recognition`, worktree próprio | O checkout compartilhado estava **460 commits atrás** de `origin/develop`, com mudanças soltas não commitadas | Todo trabalho saiu de um worktree novo em `~/Logikos-mutirao/wt-pandora`, a partir de `origin/develop@83c41159` |
| 2 | Runners de produção vivem em `/home/pandora/jetson-experiments/mm/` | Aquele diretório é a **campanha de shootout/escala de julho**, já documentada em `docs/decisions/` — não é produção. O runner de produção real roda via systemd (`recognition-infer@epi.service`) a partir de `/home/pandora/.config/recognition/deepstream/app_epi.txt`, fora de `jetson-experiments/` | Bloco 0 inteiro foi reapontado para o lugar certo — ver abaixo |
| 3 | "28 câmeras a 42% de GPU foram medidas em substream 480p" | Produção RVB roda **18 câmeras no stream PRINCIPAL** (`subtype=0`, não substream), streammux normalizando para **1280×720**, GPU médio 29,9% (pico 98%) | O número da campanha (480p) e o de produção (720p) não são a mesma coisa — não podem ser somados sem essa ressalva |
| 4 | "Observabilidade já instalada (VictoriaMetrics/Perses/tegrastats_exporter)" | **Nenhuma dessas portas está aberta no box** (`ss -ltn` confirma). A telemetria real é local: `edge-telemetry-collector.service` (tegrastats → JSONL) e `edge-monitoring-collector.service` (7 camadas → SQLite ring buffer local), sem stack de métricas externa rodando | Bloco 1 usa amostra passiva direta (tegrastats, 19 amostras, 19s), não uma dashboard pronta |

---

## Bloco 0 · Runners versionados

**PR aberto:** #927 (`levantamento/pandora-feira-2026-09-12` → `develop`) · **Issue:** #926 (achado) · **Issue:** #928 (drift descoberto no caminho)

### O runner de produção real

`recognition-infer@epi.service` (systemd --user) → `deepstream-app -c /home/pandora/.config/recognition/deepstream/app_epi.txt`. Publicador de detecções: `recognition-deteccoes@epi.service` → `publicar_deteccoes.py`.

| runner/artefato | cenário | caminho no box | segredo? | versionado agora em |
|---|---|---|---|---|
| config do deepstream-app | epi (produção) | `/home/pandora/.config/recognition/deepstream/app_epi.txt` | ✅ RTSP com usuário:senha — **redigido antes do commit** | `deepstream/epi/app_epi.txt` |
| índice fonte→câmera | epi | `.../fontes_epi.json` | não | `deepstream/epi/fontes_epi.json` |
| cooldown por câmera | epi | `~/.config/recognition/cooldown_por_camera.json` | não | `deepstream/epi/cooldown_por_camera.json` |
| taxonomia | epi | `~/recognition/models/mapa_taxonomia_epi.json` | não | `deepstream/epi/mapa_taxonomia_epi.json` |
| nvinfer do modelo servido | epi | `~/recognition/models/config_infer_46a30ed9.txt` | não | `deepstream/epi/config_infer_46a30ed9.txt` |
| labels do modelo servido | epi | `~/recognition/models/labels_46a30ed9.txt` | não | `deepstream/epi/labels_46a30ed9.txt` |
| 🔴 **parser bbox RF-DETR (fonte)** | epi | `~/jetson-experiments/rfdetr-parser/nvdsparsebbox_rfdetr.cpp` + `Makefile` | não | `deepstream/epi/custom_parsers/` — **maior severidade: só existia binário compilado no box** |
| amostragem de classe×confiança | epi (operação) | `~/recognition/bin/amostrar_classes.sh` | não | `deepstream/epi/scripts/amostrar_classes.sh` |
| degrau de medição de capacidade | epi (operação) | `~/recognition/degrau.sh` | não | `deepstream/epi/scripts/degrau.sh` |
| amostra passiva (tegrastats) | epi (operação) | `~/recognition/amostra.sh` | não | `deepstream/epi/scripts/amostra.sh` |
| gerador de config deepstream | epi | `~/recognition/gerar_config_deepstream.py` | não | ⚠️ **JÁ versionado** em `deployments/edge/gerar_config_deepstream.py` — box diverge (311 linhas de diff, ver #928) |
| publicador de detecções | epi | `~/recognition/bin/publicar_deteccoes.py` | não | ⚠️ **JÁ versionado** em `deployments/edge/publicar_deteccoes.py` — box diverge (195 linhas de diff, hotpatch de 09/09, ver #928) |

`deepstream/{epi,quality,fueling}/` no repo tinha só `.gitkeep` — confirmado (`git ls-files`). `deepstream/shared/custom_parsers/` já tinha o parser genérico YOLOX (Makefile, README, template, `.cpp`) — isso estava certo e não mudou.

**Não copiado, de propósito:** chaves de device (`~/.config/recognition/keys/*.pem`, `identity.json`) e `edge-sync-agent.env` — são segredo de device/identidade, não configuração de cenário; não pertencem a repositório algum.

---

## Bloco 1 · Capacidade medida (produção real, sem carga sintética)

**Janela:** amostra passiva de 19s (tegrastats, `--interval 1000`) sobre o processo já rodando + 1 contagem de fontes ativa em `ss`/`app_epi.txt`. Sem tocar câmera/DVR.

| O quê | Medido |
|---|---|
| Câmeras ativas | **18** fontes configuradas (`grep -c "^\[source" app_epi.txt`), **17** mapeadas em `fontes_epi.json` (fonte 17 sem `camera_id` — observado, não investigado) |
| Resolução por stream | Todas as 18 no **stream principal** (`subtype=0` — dialeto Dahua/Intelbras, não substream). `[streammux]` normaliza para **1280×720**, `batch-size=18` |
| Modelos por câmera | 1 modelo (RF-DETR `model_46a30ed9`) para todas as 18, `network-type=0`, `interval=0` (infere todo frame) |
| FPS real | ⛔ **não encontrado nesta rodada** — não há contador de FPS-por-stream exposto sem instrumentar o pipeline; fora do escopo (não é benchmark novo) |
| GPU | **média 29,9%** (min 0%, max **98%**) — 19 amostras, 1/s |
| CPU | **média 11,0%** (todos os 8 núcleos, todas as amostras), pico pontual 26% |
| RAM | **5.247 / 15.656 MB** (33,5%) |
| 🔴 NVDEC | ⛔ **não confirmado.** `tegrastats` não expôs campo NVDEC/NVENC nas amostras colhidas, e não há `nvv4l2decoder`/`avdec_h264` nos logs de inferência disponíveis (`grep` vazio). Item aberto — **resultado negativo, não investigação completa** (confirmar exigiria instrumentar o pipeline GStreamer, fora do escopo desta rodada) |
| Janela da métrica | Amostra pontual de 12/09, ~16h09 (horário do box). ⚠️ O DVR grava por movimento — não há garantia de que este instante representa o pico real do dia |

⚠️ **O que NÃO é derivável daqui:** RVB não roda módulo de fogo, e roda a 720p (não 480p). A calibragem fogo+EPI juntos, na resolução de produção, só sai medindo no Pandora novo.

---

## Bloco 2 · Ambiente e artefatos de provisionamento

| Componente | RVB hoje | Pandora da feira (alvo) | Precisa regerar? |
|---|---|---|---|
| JetPack / L4T | **R36.4.3** (= JetPack 6.2, confirmado via `/etc/nv_tegra_release`) | JetPack 6.2 | **Não**, se o flash usar a mesma imagem — mesma major/minor já |
| CUDA | **12.6** | (herdado do JetPack 6.2, deve bater) | A confirmar no flash real |
| TensorRT | **10.3.0.30** | (idem) | A confirmar no flash real |
| DeepStream | **7.1** (GCID 37774586, 04/out/2024) | (idem, se instalado o mesmo pacote) | A confirmar |
| Engine do modelo (`.engine`) | `model_46a30ed9_fp16_b1.engine`, buildado com `trtexec --onnx=model_46a30ed9.onnx --fp16 --memPoolSize=workspace:2048M` (TensorRT v10.3.0, batch implícito=1) | — | **SIM, sempre** — engine não é portável entre placas físicas, mesmo com JetPack idêntico. Regenerar a partir do `.onnx` (que É portável) |
| Parser bbox customizado (`.so`) | `libnvdsparsebbox_rfdetr.so`, compilado localmente | — | **SIM** — mas a fonte agora está versionada (`deepstream/epi/custom_parsers/`), então é `make` no destino, não reconstrução do zero |

**Modelo em uso:** `model_46a30ed9.onnx` (RF-DETR, Apache 2.0) · engine FP16 batch=1 · `num-detected-classes=13` no config (nota do próprio arquivo: o tensor real tem 11 — ver `docs/decisions/` sobre esse modelo, achado anterior a esta rodada) · 13 rótulos em `labels_46a30ed9.txt` · `pre-cluster-threshold=0.25` · pré-processamento RGB + ImageNet mean/std, resolvido por medida em 09/09 (comentário no próprio `config_infer_46a30ed9.txt`, correspondente ao D-66 do registro de decisões).

---

## Bloco 3 · O que a ferramenta já faz

_(preenchido a partir do inventário paralelo — ver seção ao final deste documento, gerada após o fan-out de 7 agentes sobre `origin/develop@83c41159`)_

---

## Bloco 4 · Estado do modelo de fogo

**Fonte:** repos `FireSet` (pipeline de treino) e `fire-demo` (demo pública), ambos existentes e ativos — clonados read-only para `/tmp/`. Estado datado de **2026-09-09** (3 dias atrás), conforme `docs/ESTADO.md` do próprio FireSet.

### Datasets (26 mantidos de 40 auditadas — licença de cada um, sem agregar)

Todas as 26 fontes **mantidas** têm licença no allowlist permissivo (`Apache-2.0`, `BSD-2/3-Clause`, `CC-BY-4.0`, `CC0-1.0`, `MIT`) — confirmado em `audit/REPORT.md`, gerado por `fireset/audit.py` em 2026-09-08. As 14 **excluídas** o foram majoritariamente por licença NÃO-DECLARADA fora do allowlist, ou por excesso de caixas degeneradas (ex.: `avian-ag-sd77w__spark-detector`, 24,29% de caixas degeneradas). Amostra das mantidas: `pyro_sdis` (Apache-2.0, 15.000 img), `firedetection-sserj__fire_detection-uhbdr` (CC-BY-4.0, 100.004 img, maior fonte), `computer-vision-mkpxu__...` (MIT), `furg` (CC0-1.0). Lista completa das 40 com veredito, imagens, caixas e licença: `audit/REPORT.md` no FireSet.

### Classes treinadas e contagem **por split** (nunca agregado)

Split de **teste** (`EVAL.md`, 7.145 imagens, modelo `fireset_yolox_s.onnx`):

| Classe | Caixas no split de teste | AP50 | Situação |
|---|---:|---:|---|
| fire | 8.709 | 0,6622 | avaliada |
| smoke | 6.603 | 0,5945 | avaliada |
| spark | 71 | — | **INAVALIÁVEL** (n < 100) |
| arc | 26 | — | **INAVALIÁVEL** (n < 100) |
| steam | 26 | — | **INAVALIÁVEL** (n < 100) |

⛔ Contagem de **train/val** por classe não foi levantada nesta rodada — resultado negativo explícito, não uma tentativa de inferir do agregado.

### Pesos e formato

`fireset_yolox_s.onnx` (YOLOX-s, **Apache-2.0**, ZERO ultralytics/AGPL — confirmado no README do `fire-demo`), exportado com paridade conclusiva contra o `.pth` de treino (453 = 453 detecções, `fireset_yolox_s.paridade.json`). Upload para R2 confirmado em `white-vision/fireset_v1/` (guarda de prefixo, per `docs/ESTADO.md`).

### Último treino

`runs/v1-20260908T232432Z-secure/`, **DONE em 2026-09-09T09:27:43Z**. 10h00 de treino, parada por relógio (não por convergência) na época 47 de 60 alvo. `best_ap` (proxy de treino) = 0,3287. **mAP50 real (split teste, 2 classes avaliáveis) = 0,6283.**

### Deploy no Jetson (relevante para o Pandora da feira)

`docs/ESTADO.md` do FireSet, seção "Não feito, e por quê": *"`deploy/README.md` com `trtexec` para JetPack 6 — o alvo Jetson foi **dispensado pelo Vitor** nesta rodada (não existe placa ainda); ONNX em x86/GPU basta."* ⚠️ **Consequência direta para esta rodada:** hoje **não existe nenhum artefato de deploy Jetson para o modelo de fogo** — nem `.engine`, nem parser bbox customizado, nem config nvinfer. O que existe é o `.onnx` (portável) e a validação em x86/GPU. Se o Pandora da feira for rodar fogo, o trabalho de portar para Jetson (engine + parser, mesmo padrão do RF-DETR do Bloco 2) ainda não começou.

### 🔴 Veredito do próprio FireSet — v1 não serve para uma planta

`docs/ESTADO.md`, textual: *"A v1 não serve para uma planta ainda [...] a métrica de aceitação do briefing é ≤ 1 falso alarme/hora e **nenhum limiar testado a cumpre**."* Isso está publicado no `fire-demo` ao vivo, com o motivo explícito — não é um resultado escondido.

✅ **Se não existisse runner/modelo pronto, essa seria a resposta — mas existe, e o próprio time documentou por que ele não está pronto para produção.** Resultado negativo explícito e já registrado por quem fez o trabalho, não uma lacuna desta rodada.


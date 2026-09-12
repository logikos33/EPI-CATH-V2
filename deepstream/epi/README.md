# Runner de produção — cenário `epi` (RVB Isolantes)

Versionado em 2026-09-12 (levantamento Pandora RVB → Pandora da feira). Até então
só existia no box `pandora` (100.93.126.76, Tailscale) — nenhuma cópia em repositório.

## O que é isto

O que `deepstream-app` (via `recognition-infer@epi.service`) e o publicador de
detecções (`recognition-deteccoes@epi.service`) consomem em produção, **hoje**:

| arquivo | papel |
|---|---|
| `app_epi.txt` | config do `deepstream-app` — 18 fontes RTSP (main stream, `subtype=0`), streammux 1280×720 batch=18. **RTSP redigido** (usuário:senha removidos da URI antes do commit) |
| `fontes_epi.json` | índice de fonte (0-16) → `camera_id` (UUID) — 17 mapeados, 18 fontes no `app_epi.txt` (fonte 17 sem `camera_id` — observado, não investigado nesta rodada) |
| `cooldown_por_camera.json` | cooldown de alerta por câmera (300s cada) |
| `mapa_taxonomia_epi.json` | mapa de taxonomia do módulo EPI |
| `config_infer_46a30ed9.txt` | nvinfer do modelo servido `model_46a30ed9` (RF-DETR, Apache 2.0) — preproc (D-66, resolvida por medida em 09/09), limiar 0.25 |
| `labels_46a30ed9.txt` | rótulos do modelo servido |
| `custom_parsers/nvdsparsebbox_rfdetr.cpp` + `Makefile` | 🔴 **parser bbox do RF-DETR — só existia como binário compilado em `/home/pandora/jetson-experiments/rfdetr-parser/`, sem fonte em repositório nenhum.** É o artefato de maior risco desta rodada: se o box saísse, o parser não seria reconstruível. |
| `scripts/amostra.sh` `scripts/degrau.sh` `scripts/amostrar_classes.sh` | scripts de operação/medição de capacidade que só existiam no box (`/home/pandora/recognition/{,bin/}`), sem home no repo |

## O que NÃO entrou aqui — já era versionado, ou é dívida separada

- `gerar_config_deepstream.py` e `publicar_deteccoes.py` **já são versionados** em
  `deployments/edge/`. A cópia rodando no box **diverge** do que está no repo — ver
  issue de drift aberta nesta rodada.
- O binário compilado `libnvdsparsebbox_rfdetr.so` não foi commitado — só a fonte +
  Makefile, que é o que importa para reconstrução; o `.so` se recompila no destino.
- Chaves de identidade do device (`/home/pandora/.config/recognition/keys/*`,
  `edge-sync-agent.env`) **não foram copiadas** — são segredo de device, não config
  de cenário, e não pertencem a repositório algum.

## Como isto foi puxado

`scp` de leitura, credencial RTSP redigida com `sed` **antes** de qualquer commit
ou exibição de conteúdo (varredura por `rtsp://`, `password=`, `Bearer` confirmando
zero residual pós-redação). Nenhum comando de escrita foi executado no box.

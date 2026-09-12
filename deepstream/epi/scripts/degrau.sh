#!/usr/bin/env bash
# Um degrau da medição de capacidade. NÃO inventa credencial: só chama o
# gerador, que reusa o .env que o live view já usa.
#   uso: degrau.sh <n_cameras> <subtype> <fps_alvo> <segundos_amostra>
set -euo pipefail
N=${1:?n_cameras}; SUB=${2:-0}; ALVO=${3:-5}; DUR=${4:-60}
CACHE=~/.local/share/recognition/edge-sync/config_cache.json
INFER=/home/pandora/recognition/models/config_infer_46a30ed9.txt
LOG=~/recognition/logs/infer-epi.log

CAMS=$(python3 -c "
import json,sys
m=json.load(open('$CACHE'))['channel_map']
ids=[k for k,_ in sorted(m.items(), key=lambda kv: kv[1])][:$N]
print(','.join(ids))")

echo \"### DEGRAU n=$N subtype=$SUB alvo=${ALVO}fps\"
systemctl --user stop recognition-infer@epi
: > "$LOG"
python3 ~/recognition/gerar_config_deepstream.py \
  --infer-config "$INFER" --cameras "$CAMS" --subtype "$SUB" \
  --fps-alvo "$ALVO" --kitti-dir /dev/shm/recognition-kitti-epi 2>&1 | grep -E "cadência|carga|gerado" || true

systemctl --user start recognition-infer@epi
echo "-- aquecendo 60s (conexão RTSP das $N fontes) --"; sleep 60

# LOCKOUT: se apareceu erro de autenticação/conexão, para AQUI.
if grep -aiE "unauthoriz|not-authorized|authentication fail|403 forbidden" "$LOG" >/dev/null; then
  echo "!!! ERRO DE AUTENTICAÇÃO NO LOG — PARANDO (lockout)"; grep -aiE "unauthoriz|not-authorized|403 forbidden" "$LOG" | head -5
  systemctl --user stop recognition-infer@epi; exit 3
fi

echo "-- amostrando ${DUR}s --"
timeout $((DUR+3)) tegrastats --interval 100 > /tmp/teg_${N}_${SUB}.txt 2>&1 &
TP=$!; sleep "$DUR"; kill $TP 2>/dev/null || true; wait $TP 2>/dev/null || true

python3 - "$N" "$SUB" "$ALVO" <<'PY'
import re,sys,statistics as st
n,sub,alvo=sys.argv[1],sys.argv[2],sys.argv[3]
txt=open(f"/tmp/teg_{n}_{sub}.txt",errors="ignore").read().splitlines()
g=[int(m.group(1)) for l in txt if (m:=re.search(r"GR3D_FREQ (\d+)%",l))]
r=[int(m.group(1)) for l in txt if (m:=re.search(r"RAM (\d+)/",l))]
t=[float(m.group(1)) for l in txt if (m:=re.search(r"tj@([\d.]+)C",l))]
c=[sum(map(int,re.findall(r"(\d+)%@",m.group(1))))/len(re.findall(r"(\d+)%@",m.group(1)))
   for l in txt if (m:=re.search(r"CPU \[([^\]]+)\]",l))]
f=lambda v,k: f"{k(v):.1f}" if v else "-"
print(f"AMOSTRAS={len(g)} GR3D_med={f(g,st.mean)}% GR3D_max={f(g,max)}% "
      f"RAM_med={f(r,st.mean)}MB RAM_max={f(r,max)}MB CPU_med={f(c,st.mean)}% TJ_max={f(t,max)}C")
PY

echo "-- PERF (deepstream) --"
grep -a "PERF" "$LOG" | tail -4
echo "-- erros/drops --"
grep -aciE "warn|error|drop" "$LOG" || true

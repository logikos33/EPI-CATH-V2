#!/usr/bin/env bash
# Só amostra o que já está rodando (sem restart). uso: amostra.sh <tag> <segundos>
set -euo pipefail
TAG=${1:?tag}; DUR=${2:-60}; LOG=~/recognition/logs/infer-epi.log
timeout $((DUR+3)) tegrastats --interval 100 > /tmp/teg_${TAG}.txt 2>&1 &
TP=$!; sleep "$DUR"; kill $TP 2>/dev/null || true; wait $TP 2>/dev/null || true
python3 - "$TAG" <<'PY'
import re,sys,statistics as st
tag=sys.argv[1]
txt=open(f"/tmp/teg_{tag}.txt",errors="ignore").read().splitlines()
g=[int(m.group(1)) for l in txt if (m:=re.search(r"GR3D_FREQ (\d+)%",l))]
r=[int(m.group(1)) for l in txt if (m:=re.search(r"RAM (\d+)/",l))]
t=[float(m.group(1)) for l in txt if (m:=re.search(r"tj@([\d.]+)C",l))]
c=[]
for l in txt:
    m=re.search(r"CPU \[([^\]]+)\]",l)
    if m:
        v=[int(x) for x in re.findall(r"(\d+)%@",m.group(1))]
        if v: c.append(sum(v)/len(v))
f=lambda v,k: f"{k(v):.1f}" if v else "-"
print(f"[{tag}] n={len(g)} GR3D_med={f(g,st.mean)}% GR3D_max={f(g,max)}% "
      f"RAM_med={f(r,st.mean)}MB RAM_max={f(r,max)}MB CPU_med={f(c,st.mean)}% TJ_max={f(t,max)}C")
PY
echo "-- PERF --"; grep -a "PERF" "$LOG" | tail -3
true

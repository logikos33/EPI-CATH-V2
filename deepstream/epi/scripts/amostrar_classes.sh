#!/usr/bin/env bash
# Amostra a distribuicao de classe x confianca x camera direto do dump KITTI.
# Existe porque o publicador so registra o que passa do corte (0,5): sem isto
# nao da para saber se o modelo esta calado ou se esta vendo e sendo filtrado.
# Read-only sobre /dev/shm; nao toca em config nem em unit.
KITTI=/dev/shm/recognition-kitti-epi
OUT=~/recognition/logs/amostra_classes.csv
[ -f "$OUT" ] || echo "ts,fonte,classe,confianca" > "$OUT"
while true; do
  ts=$(date +%FT%T)
  for f in "$KITTI"/*.txt; do
    [ -s "$f" ] || continue
    fonte=$(basename "$f" | cut -d_ -f2)
    awk -v ts="$ts" -v fonte="$fonte" "{print ts \",\" fonte \",\" \$1 \",\" \$16}" "$f"
  done >> "$OUT"
  sleep 60
done

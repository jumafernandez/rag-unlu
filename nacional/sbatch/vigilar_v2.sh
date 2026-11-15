#!/bin/bash
# Estado de las corridas de Liminar (lanzar_entrenamientos_v2.sh, trabajos "lim-<modelo>"): una línea por corrida,
# la última validación y alertas (quieto más de 30 min, reintentos, menos de 4 GiB libres, terminó).
L=~/rag-unlu-adaptacion/logs
P=${PREFIJO:-lim}
ahora=$(date +%s)
alertas=""
for n in gemma-4-e4b ministral-3-8b qwen3.5-9b gemma-4-12b mistral-small-3.2-24b qwen3.5-27b gemma-4-31b qwen3.5-122b-a10b; do
  j=$(squeue -h -u "$USER" -n "$P-$n" -t RUNNING -o "%i %T" | head -1)
  [ -z "$j" ] && j=$(squeue -h -u "$USER" -n "$P-$n" -o "%i %T" | head -1)
  if [ -n "$j" ]; then f=$L/$P-${n}_${j%% *}.out; [ -f "$f" ] || f=""; else f=$(ls -t $L/$P-${n}_*.out 2>/dev/null | head -1); fi
  ultimo=""; quieto=0; intentos=0; libre=""; val=""
  if [ -n "$f" ]; then
    ultimo=$(grep -a -E "paso [0-9]+/[0-9]+:" "$f" | tail -1 | sed -E 's/.*paso ([0-9]+\/[0-9]+): pérdida ([0-9.]+).*· ([0-9.]+) s\/paso.*libre ([0-9.]+)\).*/\1 pérdida \2 · \3 s\/paso · libre \4 GiB/')
    libre=$(grep -a -E "paso [0-9]+/[0-9]+:" "$f" | tail -1 | sed -nE 's/.*libre ([0-9.]+)\).*/\1/p')
    val=$(grep -a -E "validación (en el paso|al terminar|inicial)|pérdida de validación inicial" "$f" | tail -1 | sed -E 's/^ +//')
    quieto=$(( ahora - $(stat -c %Y "$f" "${f%.out}.err" 2>/dev/null | sort -n | tail -1) ))
    intentos=$(grep -a -c "^=== intento" "$f")
    grep -a -q "ENTRENAMIENTO-OK" "$f" && ultimo="$ultimo · TERMINADO"
  fi
  estado=${j:-"fuera de la cola"}
  echo "$n: $estado · intentos $intentos · $ultimo · $val · quieto ${quieto}s"
  case "$estado" in *RUNNING*)
    [ "$quieto" -gt 1800 ] && alertas="$alertas | $n quieto ${quieto}s"
    [ "$intentos" -gt 1 ] && alertas="$alertas | $n en el intento $intentos"
    if [ -n "$libre" ] && awk "BEGIN{exit !($libre < 4)}"; then alertas="$alertas | $n con ${libre} GiB libres"; fi;;
  esac
  [ -n "$f" ] && [ -z "$j" ] && [ "$quieto" -lt 600 ] && alertas="$alertas | $n terminó ($(grep -a -q ENTRENAMIENTO-OK "$f" && echo bien || echo con error))"
done
echo "ALERTAS:${alertas:- ninguna}"

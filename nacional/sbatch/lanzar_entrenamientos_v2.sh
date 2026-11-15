#!/bin/bash
# Reentrenamiento con la aumentación (Liminar v1): cada modelo en un nodo entero (8 tiles), con FSDP, con
#   - las 5 versiones de cada fragmento (original con cabecera + 4 reescrituras; 15 en las normas centrales, 21 en la LES),
#   - las preguntas viejas con el alcance reescrito, las del "no sé", identidad y los datos generales del propio modelo,
#   - todo lo marcado como evaluación adentro salvo las 300 del benchmark (validación),
#   - la LES repetida dos veces por época, primero una época de solo preguntas y después 4 épocas de todo,
#   - un modelo guardado por época (para elegir la época), y LoRA de rango 256 en los grandes.
# Las corridas que pasan del límite de 2 días se encadenan en tramos (cada tramo retoma desde el último punto).
#
# Uso, parado en ~/rag-unlu-adaptacion/codigo y con la generación terminada (y aumentar juntar --salida $A/replay):
#     bash nacional/sbatch/lanzar_entrenamientos_v2.sh                 # todos
#     bash nacional/sbatch/lanzar_entrenamientos_v2.sh qwen3.5-9b ...  # algunos, por nombre
#     HORAS=48 EXTRA_TODOS="--rag ... --agrupar-largos --atencion-largos flex_attention" bash ... gemma-4-31b   # con RAG
set -uo pipefail
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
A=$N/aumentacion
M=/data/contrib/pci_146/hf-models
SALIDAS=${SALIDAS:-$N/modelos-v2}   # SALIDAS=... para otra ronda (p. ej. $N/modelos-v2r2)
X="--exclude=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072"
COMUN="--epocas ${EPOCAS:-4} --preguntas-primero ${PREGUNTAS_PRIMERO:-1} --guardar-epocas --eval-cada 200 --guardar-cada 200
 --sin-mmap --verificar --aumentacion $A --alcance $A/alcance --retener $N/linea_base-preguntas.jsonl --repetir 25394:2
 ${EXTRA_TODOS:-}"
LOTES="--tokens-por-lote 2048 --acumulacion 2"
mkdir -p "$SALIDAS" ../logs

# nombre | ruta del modelo | modo | opciones propias | horas estimadas en un nodo | nodos (opcional; con más de uno,
# HSDP con entrenar_multinodo.sbatch: el modelo repartido en cada nodo y replicado entre nodos)
CORRIDAS=(
  "gemma-4-e4b|$M/gemma-4-E4B-it|completo|$LOTES --congelar embed_tokens_per_layer|12"
  "ministral-3-8b|$M/Ministral-3-8B-Instruct-2512-BF16|completo|$LOTES|12"
  "qwen3.5-9b|$M/Qwen3.5-9B|completo|$LOTES|26"
  "gemma-4-12b|$M/gemma-4-12B-it|completo|$LOTES|20"
  "mistral-small-3.2-24b|$M/Mistral-Small-3.2-24B-Instruct-2506|lora|--rango 256|18"
  "qwen3.5-27b|$M/Qwen3.5-27B|lora|$LOTES --rango 256|48"
  "gemma-4-31b|$M/gemma-4-31B-it|lora|$LOTES --rango 256|36"
  "qwen3.5-122b-a10b|$M/Qwen3.5-122B-A10B|lora|$LOTES --rango 256 --cargas-simultaneas 1|84|${NODOS_122B:-1}"
)
# en varios nodos, las horas de un nodo se dividen por los nodos y se agrega este porcentaje por la comunicación
# entre nodos (ajustarlo con lo medido en el canario de 2 nodos)
EXTRA_MULTI=${EXTRA_MULTI:-20}

for c in "${CORRIDAS[@]}"; do
  IFS='|' read -r nombre ruta modo propias horas nodos <<< "$c"
  horas=${HORAS:-$horas}   # HORAS=... pisa la estimación (para lanzar un modelo solo; p. ej. con la tarea RAG, más larga)
  nodos=${nodos:-1}
  if [ $# -gt 0 ] && ! printf '%s\n' "$@" | grep -qx "$nombre"; then continue; fi
  if [ ! -f "$ruta/config.json" ]; then echo "falta el modelo $ruta: no encolo $nombre"; continue; fi
  if [ ! -s "$A/replay/replay-$nombre.jsonl" ]; then echo "AVISO: sin datos generales para $nombre ($A/replay/replay-$nombre.jsonl)"; fi
  mc=""; case "$nombre" in mistral-small*|ministral*) mc=1;; esac
  extra="--modelo $ruta --modo $modo --estrategia fsdp $propias $COMUN --identidad nacional/identidad/$nombre.json --replay $A/replay/replay-$nombre.jsonl"
  script=nacional/sbatch/entrenar.sbatch; recursos=""
  if [ "$nodos" -gt 1 ]; then
    # con N nodos hay N veces más procesos: la acumulación se divide por N para que cada paso del optimizador vea
    # los mismos tokens que en un nodo (misma receta que los demás modelos)
    k=$(echo "$propias" | sed -n 's/.*--acumulacion \([0-9]*\).*/\1/p')
    if [ -n "$k" ] && [ $(( k % nodos )) -eq 0 ]; then
      propias=$(echo "$propias" | sed "s/--acumulacion $k/--acumulacion $(( k / nodos ))/")
      extra="--modelo $ruta --modo $modo --estrategia fsdp $propias $COMUN --identidad nacional/identidad/$nombre.json --replay $A/replay/replay-$nombre.jsonl"
    else
      echo "AVISO: $nombre en $nodos nodos con acumulación ${k:-1}: el lote efectivo por paso cambia"
    fi
    horas=$(( horas * (100 + EXTRA_MULTI) / (100 * nodos) + 1 ))
    script=nacional/sbatch/entrenar_multinodo.sbatch; recursos="-N $nodos"
  fi
  # tramos de hasta 47 horas (el máximo de las particiones es 2 días); cada uno retoma desde el último punto de reanudación
  tramos=$(( (horas + 46) / 47 )); limite=$(( horas < 47 ? horas + 2 : 47 )); previo=""
  for t in $(seq 1 "$tramos"); do
    dep=${previo:+--dependency=afterany:$previo}
    previo=$(MISTRAL_COMMON=$mc DATOS=$N GENERADOS="$N/gen/documentos-1a,$N/gen/documentos-1b,$N/gen/fragmentos" \
      ABIERTAS=$N/abiertas SALIDA=$SALIDAS/$modo-$nombre EXTRA="$extra" \
      sbatch --parsable -p gpunode,ayor $recursos --gres=gpu:4 -c 64 --mem=480G -t "$limite:00:00" $X $dep \
      -J "lim${SUFIJO:-}-$nombre" --export=ALL $script)
    echo "$nombre ($nodos nodo/s) tramo $t/$tramos: $previo"
  done
done

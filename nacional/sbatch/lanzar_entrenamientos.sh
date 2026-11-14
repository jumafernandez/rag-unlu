#!/bin/bash
# Encola el entrenamiento de la capa nacional para los modelos elegidos, cada uno en un nodo entero (8 tiles):
# ajuste completo para los de ~4B a ~12B y LoRA para los de ~24B a ~31B, todos con FSDP y los mismos datos.
#
# Uso, parado en ~/rag-unlu-adaptacion/codigo y con la etapa de fragmentos terminada:
#     bash nacional/sbatch/lanzar_entrenamientos.sh                 # todos
#     bash nacional/sbatch/lanzar_entrenamientos.sh qwen3.5-9b ...  # algunos, por nombre
# Variables: EPOCAS (2), TIEMPO (16:00:00; con límites largos el planificador no las mete en huecos), EXTRA_TODOS (opciones para todas las corridas).
set -uo pipefail
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
M=/data/contrib/pci_146/hf-models
SALIDAS=$N/modelos
X="--exclude=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072"
COMUN="--epocas ${EPOCAS:-2} --eval-cada 200 --guardar-cada 200 --sin-mmap --verificar ${EXTRA_TODOS:-}"
# Lotes de 2.048 tokens con acumulación de 2 (los mismos tokens por paso que 4.096) para el ajuste completo y para los
# LoRA de vocabulario grande (Qwen 248.000, Gemma 262.000): con 4.096, los logits ocupan ~17 GB transitorios, la
# memoria se fragmenta y el tile llega al tope; ahí el asignador devuelve bloques que los demás procesos tienen
# mapeados por oneCCL y se caen todos (Gemma 4 12B completo y Gemma 4 31B LoRA, en los primeros pasos). El Mistral
# Small (131.000) queda con 4.096: partir el lote duplica las comunicaciones de FSDP.
LOTES_COMPLETO="--tokens-por-lote 2048 --acumulacion 2"
mkdir -p "$SALIDAS" ../logs

# nombre | ruta del modelo | modo | opciones propias
CORRIDAS=(
  "gemma-4-e4b|$M/gemma-4-E4B-it|completo|$LOTES_COMPLETO --congelar embed_tokens_per_layer"
  "ministral-3-8b|$M/Ministral-3-8B-Instruct-2512-BF16|completo|$LOTES_COMPLETO"
  "qwen3.5-9b|$M/Qwen3.5-9B|completo|$LOTES_COMPLETO"
  "gemma-4-12b|$M/gemma-4-12B-it|completo|$LOTES_COMPLETO"
  "mistral-small-3.2-24b|$M/Mistral-Small-3.2-24B-Instruct-2506|lora|"
  "qwen3.5-27b|$M/Qwen3.5-27B|lora|$LOTES_COMPLETO"
  "gemma-4-31b|$M/gemma-4-31B-it|lora|$LOTES_COMPLETO"
)

for c in "${CORRIDAS[@]}"; do
  IFS='|' read -r nombre ruta modo propias <<< "$c"
  if [ $# -gt 0 ] && ! printf '%s\n' "$@" | grep -qx "$nombre"; then continue; fi
  if [ ! -f "$ruta/config.json" ]; then echo "falta el modelo $ruta: no encolo $nombre"; continue; fi
  mc=""; case "$nombre" in mistral-small*|ministral*) mc=1;; esac   # mistral-common: el tokenizer.json del Ministral trae mal la regex
  # Las variables van por el entorno (--export=ALL): GENERADOS lleva comas, que --export=VAR=... partiría.
  MISTRAL_COMMON=$mc DATOS=$N GENERADOS="$N/gen/documentos-1a,$N/gen/documentos-1b,$N/gen/fragmentos" \
  ABIERTAS=$N/abiertas SALIDA=$SALIDAS/$modo-$nombre EXTRA="--modelo $ruta --modo $modo --estrategia fsdp $propias $COMUN" \
    sbatch -p gpunode,ayor --gres=gpu:4 -c 64 --mem=480G -t "${TIEMPO:-16:00:00}" $X -J "nac-$nombre" --export=ALL \
    nacional/sbatch/entrenar.sbatch
done

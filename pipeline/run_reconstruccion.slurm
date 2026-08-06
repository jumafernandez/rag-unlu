#!/bin/bash
#SBATCH --job-name=rag-reconstruccion
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
#SBATCH --partition=gpunode
#SBATCH --gres=gpu:intel_xt1550:1
#SBATCH --cpus-per-task=16
#SBATCH --time=08:00:00
#
# Reconstrucción total del índice en Clementina.
#
# NO reimplementa el procedimiento: llama a la misma rutina que ejecuta el panel de
# administración de cualquier instalación. Lo propio de acá es el entorno ---la GPU, el
# modelo en disco, el intérprete--- y eso viaja por variables. Si la lógica cambia, cambia
# en un solo lugar y las dos puertas la heredan.
#
#   sbatch pipeline/run_reconstruccion.slurm
#
set -euo pipefail

BASE="${RAG_BASE:-${SLURM_SUBMIT_DIR:-$PWD}}"
VENV="${VENV:-$HOME/trace-repro/venv}"
PY="$VENV/bin/python"
MODELO="${MODELO:-$BASE/modelos/bge-m3}"
PROCESADOS="${PROCESADOS:-resultados/portal}"
METADATA="${METADATA:-resultados/join_metadata.csv}"
BATCH="${BATCH:-64}"

cd "$BASE"
mkdir -p logs

[[ -x "$PY" ]]                  || { echo "ERROR: no está el venv en $VENV" >&2; exit 1; }
[[ -d "$MODELO" ]]              || { echo "ERROR: faltan los pesos en $MODELO" >&2; exit 1; }
[[ -d "$BASE/$PROCESADOS" ]]    || { echo "ERROR: no está $PROCESADOS" >&2; exit 1; }
[[ -f "$BASE/$METADATA" ]]      || { echo "ERROR: falta la metadata en $METADATA" >&2; exit 1; }

# Sin salida a internet: que las librerías usen los pesos locales en vez de fallar con un
# tiempo de espera confuso.
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export LANG=C.UTF-8 LC_ALL=C.UTF-8
export PYTHONPATH="$BASE"

echo "=== nodo $(hostname) | inicio: $(date) ==="
"$PY" -m pipeline.actualizar --reconstruir \
    --procesados "$PROCESADOS" \
    --metadata "$METADATA" \
    --modelo "$MODELO" \
    --dispositivo xpu \
    --batch "$BATCH"
echo "=== fin: $(date) ==="

#!/bin/bash
#
# Procesa el corpus con un JOB ARRAY: cada tarea toma un shard disjunto de los PDFs,
# trabaja en el disco local de su nodo y copia el resultado al final.
#
# Reemplaza a run_extractor.slurm (que corría todo en un nodo, escribiendo miles de
# archivos chicos al filesystem compartido: así se colgó a los 686 documentos).
#
# Encolar:
#     sbatch pipeline/run_corpus.slurm
#
# Para cambiar la cantidad de shards, ajustá --array Y la variable NSHARDS: tienen que
# coincidir (--array=0-3 -> NSHARDS=4).
#
#SBATCH --job-name=rag-unlu
#SBATCH --output=logs/%x-%A_%a.out
#SBATCH --error=logs/%x-%A_%a.err
#SBATCH --array=0-3
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --time=02:00:00
#SBATCH --partition=cpunode

set -euo pipefail

NSHARDS=4

BASE="$HOME/rag-unlu"
PDFS="${PDFS:-$BASE/data/portal}"
SALIDA="${SALIDA:-$BASE/resultados/portal}"
VENV="${VENV:-$BASE/extractor-venv}"

mkdir -p logs "$SALIDA"
source "$VENV/bin/activate"

# Los documentos tienen acentos; con LANG=C algunos open() rompen.
export LANG=C.UTF-8 LC_ALL=C.UTF-8

echo "=== nodo $(hostname) | shard ${SLURM_ARRAY_TASK_ID}/${NSHARDS} | ${SLURM_CPUS_PER_TASK} cores ==="
echo "=== inicio: $(date) ==="

# --scratch: disco local del nodo. Si el cluster define otra ruta rápida por job,
# cambiala acá (p.ej. /scratch/$SLURM_JOB_ID).
python "$BASE/pipeline/procesar_corpus.py" \
    --pdfs "$PDFS" \
    --salida "$SALIDA" \
    --shard "${SLURM_ARRAY_TASK_ID}/${NSHARDS}" \
    --scratch "${TMPDIR:-/tmp}" \
    --timeout 300

echo "=== fin: $(date) ==="

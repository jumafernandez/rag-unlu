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

# La base se deduce de dónde está este script: funciona sin importar cómo se llame al
# repo en el cluster.
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(dirname "$AQUI")"
PDFS="${PDFS:-$BASE/data/portal}"
SALIDA="${SALIDA:-$BASE/resultados/portal}"

# El venv puede estar en distintos lugares según cómo se instaló. Se usa su python por
# ruta absoluta en vez de `activate`: así sigue funcionando aunque el venv se haya movido.
if [[ -n "${VENV:-}" ]]; then
  CANDIDATOS=("$VENV")
else
  CANDIDATOS=("$BASE/extractor-venv" "$BASE/venv" "$HOME/rag-unlu/extractor/venv")
fi
PY=""
for v in "${CANDIDATOS[@]}"; do
  [[ -x "$v/bin/python" ]] && { PY="$v/bin/python"; break; }
done
[[ -n "$PY" ]] || { echo "ERROR: no encontré el venv (probé: ${CANDIDATOS[*]}). Pasalo con VENV=/ruta sbatch $0" >&2; exit 1; }
"$PY" -c 'import fitz, yaml, Levenshtein' || { echo "ERROR: al venv le faltan dependencias" >&2; exit 1; }

mkdir -p logs "$SALIDA"

# Los documentos tienen acentos; con LANG=C algunos open() rompen.
export LANG=C.UTF-8 LC_ALL=C.UTF-8

echo "=== nodo $(hostname) | shard ${SLURM_ARRAY_TASK_ID}/${NSHARDS} | ${SLURM_CPUS_PER_TASK} cores ==="
echo "=== inicio: $(date) ==="

# --scratch: disco local del nodo. Si el cluster define otra ruta rápida por job,
# cambiala acá (p.ej. /scratch/$SLURM_JOB_ID).
"$PY" "$BASE/pipeline/procesar_corpus.py" \
    --pdfs "$PDFS" \
    --salida "$SALIDA" \
    --shard "${SLURM_ARRAY_TASK_ID}/${NSHARDS}" \
    --scratch "${TMPDIR:-/tmp}" \
    --timeout 300

echo "=== fin: $(date) ==="

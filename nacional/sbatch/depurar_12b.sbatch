#!/bin/bash
# Diagnóstico del Gemma 4 12B completo con FSDP: la memoria del tile fuera de torch crece en cada paso (1 → 18 GB en 4
# pasos) hasta agotarlo. Hipótesis: oneCCL conserva en caché los handles abiertos de búferes que torch ya liberó.
# 14 pasos por variante, sin reintentos, registrando la memoria en cada paso:
#   E. sin torch.xpu.empty_cache() después de cada paso (torch reusa sus bloques)
#   F. sin caché de handles abiertos de oneCCL (CCL_ZE_CACHE_OPEN_IPC_HANDLES=0)
#   G. con esa caché limitada a 64 handles (CCL_ZE_CACHE_OPEN_IPC_HANDLES_THRESHOLD=64)
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     sbatch -p gpunode,ayor nacional/sbatch/depurar_12b.sbatch
#
#SBATCH --job-name=nac-depurar-12b
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=01:00:00
#SBATCH --exclude=cn016,cn022,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
M=/data/contrib/pci_146/hf-models
SAL=$N/canarios/depurar-12b
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
NPROC=8

ulimit -n "$(ulimit -Hn)"
NUCLEOS=$($PY -c "import os; print(','.join(map(str, sorted(os.sched_getaffinity(0))[:$NPROC])))")
export CCL_WORKER_COUNT=1 CCL_WORKER_AFFINITY=$NUCLEOS CCL_PROCESS_LAUNCHER=torchrun CCL_ZE_IPC_EXCHANGE=sockets
export FI_PROVIDER=tcp TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 OMP_NUM_THREADS=4
unset OPENAI_API_KEY RAG_LLM_BASE
mkdir -p "$SAL"
echo "nodo $(hostname) · $(date '+%F %T')"

probar() {
  local nombre=$1; shift
  echo "=== $nombre · $(date '+%T') · $(env | grep '^CCL_ZE' | tr '\n' ' ')"
  rm -rf "$SAL/$nombre"
  $PY -m torch.distributed.run --standalone --nproc_per_node $NPROC -m nacional.entrenar \
    --datos "$N" --generados "$N/gen/documentos-1a,$N/gen/documentos-1b,$N/gen/fragmentos" \
    --modelo "$M/gemma-4-12B-it" --modo completo --estrategia fsdp --sin-mmap --max-ejemplos 2000 --max-validacion 16 \
    --eval-cada 1000 --guardar-cada 1000 --salida "$SAL/$nombre" "$@" 2>&1 \
    | grep --line-buffered -E "proceso [0-9]: memoria tras el paso|paso [0-9]+/|Segmentation|OutOfMemory|repartido en|Root Cause|local_rank"
  echo "--- código de salida ${PIPESTATUS[0]} · $(date '+%T')"
}

probar I-2048x2 --max-pasos 14 --memoria-pasos 14 --tokens-por-lote 2048 --acumulacion 2
echo "=== fin · $(date '+%T')"

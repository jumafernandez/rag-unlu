#!/bin/bash
# Canario de los modelos que faltaban, en un nodo entero, con la configuración de las corridas reales (FSDP con
# las colectivas síncronas y en el flujo de cómputo, carga sin mapeo de archivos, memoria reservada devuelta después de
# cada paso), sin reintentos:
#   1. ajuste completo del Gemma 4 12B con lotes de 4.096 (antes se quedó sin memoria en el tile) y con 2.048 x 2;
#   2. ajuste completo del Ministral 3 8B con el tokenizador de mistral-common (el tokenizer.json trae mal la regex).
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     sbatch -p gpunode,ayor nacional/sbatch/canario_largo.sbatch
#
#SBATCH --job-name=nac-canario-largo
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=03:00:00
#SBATCH --exclude=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
M=/data/contrib/pci_146/hf-models
SAL=$N/canarios/largo
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
NPROC=8

ulimit -n "$(ulimit -Hn)"
NUCLEOS=$($PY -c "import os; print(','.join(map(str, sorted(os.sched_getaffinity(0))[:$NPROC])))")
export CCL_WORKER_COUNT=1 CCL_WORKER_AFFINITY=$NUCLEOS CCL_PROCESS_LAUNCHER=torchrun CCL_ZE_IPC_EXCHANGE=sockets
export FI_PROVIDER=tcp TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 OMP_NUM_THREADS=4
unset OPENAI_API_KEY RAG_LLM_BASE
mkdir -p "$SAL"
echo "nodo $(hostname) · $(date '+%F %T')"

entrenar() {
  local nombre=$1; shift
  echo "=== $nombre · $(date '+%T')"
  rm -rf "$SAL/$nombre"
  $PY -m torch.distributed.run --standalone --nproc_per_node $NPROC -m nacional.entrenar \
    --datos "$N" --generados "$N/gen/documentos-1a,$N/gen/documentos-1b,$N/gen/fragmentos" --abiertas "$N/abiertas" \
    --max-ejemplos 20000 --max-validacion 64 --verificar --salida "$SAL/$nombre" "$@" 2>&1 \
    | grep --line-buffered -v "CCL_WARN\|it/s\]\|s/it\]\|proceso [1-7]: memoria tras"
  echo "--- código de salida ${PIPESTATUS[0]} · $(date '+%T')"
}

entrenar completo-gemma-12b --modelo "$M/gemma-4-12B-it" --modo completo --estrategia fsdp --sin-mmap --max-pasos 6 --guardar-cada 6 --eval-cada 6
entrenar completo-gemma-12b-2048 --modelo "$M/gemma-4-12B-it" --modo completo --estrategia fsdp --sin-mmap --max-pasos 6 --guardar-cada 6 --eval-cada 6 --tokens-por-lote 2048 --acumulacion 2
export PYTHONPATH=/home/jfernandez/rag-unlu-adaptacion/extra-mistral
entrenar completo-ministral-8b --modelo "$M/Ministral-3-8B-Instruct-2512-BF16" --modo completo --estrategia fsdp --sin-mmap --max-pasos 3 --guardar-cada 3 --eval-cada 3
echo "=== fin · $(date '+%T')"

#!/bin/bash
# Diagnóstico del NaN del LoRA sobre los expertos (Qwen 3.5 122B-A10B: validación 2,26, entrenamiento NaN desde el
# paso 1). Mismo tipo de modelo pero chico (Qwen 3.6 35B-A3B, qwen3_5_moe), 3 pasos por variante:
#   A. LoRA también sobre los expertos (--expertos, rango 8)
#   B. LoRA solo sobre las lineales (atención y experto compartido)
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     sbatch -p gpunode,ayor nacional/sbatch/depurar_moe.sbatch
#
#SBATCH --job-name=nac-depurar-moe
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=01:00:00
#SBATCH --exclude=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
M=/data/contrib/pci_146/hf-models
SAL=$N/canarios/depurar-moe
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
  echo "=== $nombre · $(date '+%T')"
  rm -rf "$SAL/$nombre"
  $PY -m torch.distributed.run --standalone --nproc_per_node $NPROC -m nacional.entrenar \
    --datos "$N" --generados "$N/gen/documentos-1a,$N/gen/documentos-1b,$N/gen/fragmentos" \
    --modelo "$M/Qwen3.6-35B-A3B" --modo lora --estrategia fsdp --max-ejemplos 2000 --max-validacion 16 \
    --eval-cada 3 --guardar-cada 1000 --max-pasos 3 --tokens-por-lote 2048 --acumulacion 2 --salida "$SAL/$nombre" "$@" 2>&1 \
    | grep --line-buffered -E "parámetros entrenables|repartido en|validación|paso [0-9]+/|Segmentation|OutOfMemory|Error"
  echo "--- código de salida ${PIPESTATUS[0]} · $(date '+%T')"
}

probar A-expertos --expertos --rango-expertos 8
probar B-sin-expertos
echo "=== fin · $(date '+%T')"

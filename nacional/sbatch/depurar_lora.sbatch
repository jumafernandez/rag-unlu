#!/bin/bash
# Diagnóstico de la falla de página del LoRA con FSDP en los modelos grandes (Qwen 3.5 27B y Gemma 4 31B se caen en
# la primera pasada; el ajuste completo del 9B y el LoRA del 9B andan). Qwen 3.5 27B, 3 pasos, sin reintentos:
#   V1. lotes de 1.024 tokens (¿presión de memoria en el asignador?)
#   V2. intercambio de memoria entre procesos de oneCCL por drmfd en lugar de sockets
#   V3. colectivas síncronas (cada una espera a terminar antes de seguir)
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     sbatch -p gpunode,ayor nacional/sbatch/depurar_lora.sbatch
#
#SBATCH --job-name=nac-depurar-lora
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
SAL=$N/canarios/depurar-lora
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
    --modelo "$M/Qwen3.5-27B" --modo lora --estrategia fsdp --sin-mmap --max-ejemplos 2000 --max-validacion 16 \
    --eval-cada 1000 --guardar-cada 1000 --max-pasos 3 --salida "$SAL/$nombre" "$@" 2>&1 \
    | grep --line-buffered -E "memoria tras|paso [0-9]|pérdida|Segmentation|Error|repartido en"
  echo "--- código de salida ${PIPESTATUS[0]} · $(date '+%T')"
}

probar V1-lotes-1024 --tokens-por-lote 1024
CCL_ZE_IPC_EXCHANGE=drmfd probar V2-drmfd
probar V3-sincronas --colectivas-sincronas
echo "=== fin · $(date '+%T')"

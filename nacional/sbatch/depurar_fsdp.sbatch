#!/bin/bash
# Diagnóstico de la falla del ajuste completo con FSDP (Segmentation fault from GPU en la primera pasada de
# entrenamiento; la validación sin gradientes anda). Nodo entero, Qwen 3.5 9B, 2 pasos por variante:
#   A. sin recomputar activaciones (checkpointing de transformers dentro de la unidad de FSDP)
#   B. sin liberar los pesos juntados entre la pasada hacia adelante y la de atrás
#   C. LoRA con FSDP sobre el mismo modelo (¿es el ajuste completo o es el modelo?)
#   D. ajuste completo con los núcleos de flash-linear-attention (¿es la implementación en torch de la atención lineal?)
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     sbatch -p gpunode,ayor nacional/sbatch/depurar_fsdp.sbatch
#
#SBATCH --job-name=nac-depurar
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=01:30:00
#SBATCH --exclude=cn016,cn022,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
M=/data/contrib/pci_146/hf-models
SAL=$N/canarios/depurar
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
    --modelo "$M/Qwen3.5-9B" --estrategia fsdp --max-ejemplos 2000 --max-validacion 16 --eval-cada 1000 \
    --guardar-cada 1000 --max-pasos 2 --salida "$SAL/$nombre" "$@" 2>&1 | grep -v "CCL_WARN\|it/s\]\|s/it\]" \
    | grep -E "memoria|paso|pérdida|Segmentation|Error|error|repartido en" | head -60
  echo "--- código de salida ${PIPESTATUS[0]}"
}

probar A-sin-checkpointing --modo completo --sin-checkpointing --tokens-por-lote 1024
probar B-sin-reshard --modo completo --sin-reshard --tokens-por-lote 2048
probar C-lora --modo lora --tokens-por-lote 2048
probar D-fla --modo completo --fla --tokens-por-lote 2048
echo "=== fin · $(date '+%T')"

#!/bin/bash
# Canario de una sola corrida con FSDP en un nodo entero (8 tiles), con la configuración de las corridas reales
# (reintentos incluidos). La corrida va por el entorno: NOMBRE y ARGS (opciones de entrenar.py);
# MISTRAL_COMMON=1 para Mistral Small 3.2.
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     NOMBRE=lora-gemma31b ARGS="--modelo .../gemma-4-31B-it --modo lora --max-pasos 6 --sin-mmap" \
#         sbatch -p gpunode,ayor --export=ALL nacional/sbatch/canario_uno.sbatch
#
#SBATCH --job-name=nac-canario-uno
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=01:30:00
#SBATCH --exclude=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
NPROC=${NPROC:-8}   # con --gres=gpu:2 van 4
NOMBRE="${NOMBRE:?falta NOMBRE}"
ARGS="${ARGS:?falta ARGS}"

ulimit -n "$(ulimit -Hn)"
NUCLEOS=$($PY -c "import os; print(','.join(map(str, sorted(os.sched_getaffinity(0))[:$NPROC])))")
export CCL_WORKER_COUNT=1 CCL_WORKER_AFFINITY=$NUCLEOS CCL_PROCESS_LAUNCHER=torchrun CCL_ZE_IPC_EXCHANGE=sockets
export FI_PROVIDER=tcp TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 OMP_NUM_THREADS=4
unset OPENAI_API_KEY RAG_LLM_BASE
if [ -n "${MISTRAL_COMMON:-}" ]; then export PYTHONPATH=/home/jfernandez/rag-unlu-adaptacion/extra-mistral${PYTHONPATH:+:$PYTHONPATH}; fi
SAL=$N/canarios/uno/$NOMBRE
[ -n "${CONSERVAR:-}" ] || rm -rf "$SAL"   # CONSERVAR=1: no borra la salida (prueba de reanudación desde "ultimo")
echo "nodo $(hostname) · $NOMBRE · $(date '+%F %T')"
echo "oneCCL: $(env | grep '^CCL_' | sort | tr '\n' ' ')"
for intento in 1 2 3; do
  echo "=== intento $intento · $(date '+%T')"
  $PY -m torch.distributed.run --standalone --nproc_per_node $NPROC -m nacional.entrenar \
    --datos "$N" --generados "$N/gen/documentos-1a,$N/gen/documentos-1b,$N/gen/fragmentos" --abiertas "$N/abiertas" \
    --estrategia fsdp --max-ejemplos 20000 --max-validacion 64 --verificar --salida "$SAL" $ARGS 2>&1 \
    | grep --line-buffered -v "CCL_WARN\|it/s\]\|s/it\]\|proceso [1-7]: memoria tras"
  rc=${PIPESTATUS[0]}
  echo "--- código de salida $rc · $(date '+%T')"
  [ "$rc" -eq 0 ] && break
done

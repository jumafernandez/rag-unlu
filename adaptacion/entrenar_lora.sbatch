#!/bin/bash
# Entrena el adaptador LoRA en un nodo: ocho procesos, uno por tile, con DDP sobre oneCCL.
# La configuración de oneCCL es la de llm-tod/lanzar_ddp.sh: los hilos de oneCCL se fijan a
# núcleos de la asignación del trabajo (la afinidad por omisión cae fuera del cgroup).
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     DATOS=../datos SALIDA=../adaptadores/9b-digesto sbatch adaptacion/entrenar_lora.sbatch
#     EXTRA="--max-ejemplos 64 --epocas 1" SALIDA=../adaptadores/canario sbatch -t 01:00:00 adaptacion/entrenar_lora.sbatch
#
#SBATCH --job-name=dig-lora
#SBATCH --partition=batch,ayor
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=12:00:00
#SBATCH --exclude=cn013,cn021,cn023,cn024,cn042,cn045,cn069,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
DATOS="${DATOS:-$BASE/../datos}"
SALIDA="${SALIDA:-$BASE/../adaptadores/9b-digesto}"
EXTRA="${EXTRA:-}"
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
NPROC=8

NUCLEOS=$($PY -c "import os; print(','.join(map(str, sorted(os.sched_getaffinity(0))[:$NPROC])))")
export CCL_WORKER_COUNT=1 CCL_WORKER_AFFINITY=$NUCLEOS CCL_PROCESS_LAUNCHER=torchrun CCL_ZE_IPC_EXCHANGE=sockets
export FI_PROVIDER=tcp TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 OMP_NUM_THREADS=4
unset OPENAI_API_KEY RAG_LLM_BASE
TILES=$($PY -c "import torch; print(torch.xpu.device_count())" 2>/dev/null)
if [ "$TILES" -lt "$NPROC" ]; then echo "solo $TILES tiles visibles en $(hostname), se piden $NPROC" >&2; exit 1; fi

echo "nodo $(hostname) · datos $DATOS · salida $SALIDA · $EXTRA"
exec $PY -m torch.distributed.run --standalone --nproc_per_node $NPROC -m adaptacion.entrenar_lora \
  --datos "$DATOS" --salida "$SALIDA" $EXTRA

#!/bin/bash
# Entrena la capa nacional con LoRA en un nodo: ocho procesos, uno por tile, con DDP sobre oneCCL.
# La configuración de oneCCL es la de llm-tod/lanzar_ddp.sh: los hilos de oneCCL se fijan a
# núcleos de la asignación del trabajo (la afinidad por omisión cae fuera del cgroup).
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     DATOS=... GENERADOS=dir1,dir2 SALIDA=../adaptadores/9b-nacional sbatch nacional/sbatch/entrenar.sbatch
#     EXTRA="--max-ejemplos 256 --epocas 1" SALIDA=../adaptadores/canario-nacional sbatch -t 01:00:00 nacional/sbatch/entrenar.sbatch
#
#SBATCH --job-name=nac-lora
#SBATCH --partition=gpunode
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=12:00:00
#SBATCH --exclude=cn013,cn016,cn021,cn022,cn023,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
DATOS="${DATOS:?falta DATOS}"
GENERADOS="${GENERADOS:?falta GENERADOS}"
ABIERTAS="${ABIERTAS:-}"
SALIDA="${SALIDA:-$BASE/../adaptadores/9b-nacional}"
EXTRA="${EXTRA:-}"
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
NPROC=${NPROC:-8}   # procesos (tiles); con --gres=gpu:2 van 4

ulimit -n "$(ulimit -Hn)"   # el límite blando de archivos abiertos (1024) no alcanza para DDP sobre oneCCL
NUCLEOS=$($PY -c "import os; print(','.join(map(str, sorted(os.sched_getaffinity(0))[:$NPROC])))")
export CCL_WORKER_COUNT=1 CCL_WORKER_AFFINITY=$NUCLEOS CCL_PROCESS_LAUNCHER=torchrun CCL_ZE_IPC_EXCHANGE=sockets
export FI_PROVIDER=tcp TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 OMP_NUM_THREADS=4
unset OPENAI_API_KEY RAG_LLM_BASE
TILES=$($PY -c "import torch; print(torch.xpu.device_count())" 2>/dev/null)
if [ "$TILES" -lt "$NPROC" ]; then echo "solo $TILES tiles visibles en $(hostname), se piden $NPROC" >&2; exit 1; fi

echo "nodo $(hostname) · datos $DATOS · salida $SALIDA · $EXTRA"
exec $PY -m torch.distributed.run --standalone --nproc_per_node $NPROC -m nacional.entrenar \
  --datos "$DATOS" --generados "$GENERADOS" --salida "$SALIDA" ${ABIERTAS:+--abiertas "$ABIERTAS"} $EXTRA

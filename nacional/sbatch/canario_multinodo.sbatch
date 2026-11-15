#!/bin/bash
# Canario de entrenamiento en varios nodos (HSDP: el modelo repartido en los tiles de cada nodo y replicado entre
# nodos). Encolar parado en ~/rag-unlu-adaptacion/codigo, con los nodos y GPU por nodo en la línea de sbatch:
#     NOMBRE=multi-9b NPROC=2 ARGS="--modelo .../Qwen3.5-9B --modo lora --max-pasos 4 --max-ejemplos 2000" \
#         sbatch -N 2 --gres=gpu:1 -c 16 --mem=120G --export=ALL nacional/sbatch/canario_multinodo.sbatch
#SBATCH --job-name=nac-canario-multi
#SBATCH --partition=gpunode,ayor
#SBATCH --ntasks-per-node=1
#SBATCH --time=01:00:00
#SBATCH --exclude=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn061,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err
set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}" || exit 1
export N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
export PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
export NPROC=${NPROC:?falta NPROC} NOMBRE=${NOMBRE:?falta NOMBRE} ARGS=${ARGS:?falta ARGS}
export MAESTRO=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -1)
export PUERTO=$((29500 + SLURM_JOB_ID % 1000))
export SAL=$N/canarios/multi/$NOMBRE
rm -rf "$SAL"
echo "nodos $SLURM_JOB_NODELIST · rendezvous $MAESTRO:$PUERTO · $NOMBRE · $(date '+%F %T')"
srun --ntasks-per-node=1 --kill-on-bad-exit=1 bash nacional/sbatch/nodo_multinodo.sh 2>&1 \
  | grep --line-buffered -v "CCL_WARN\|it/s\]\|s/it\]"
echo "--- código de salida ${PIPESTATUS[0]} · $(date '+%T')"

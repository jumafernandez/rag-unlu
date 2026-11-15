#!/bin/bash
# Entrena un modelo en varios nodos enteros con HSDP: el modelo se reparte entre los 8 tiles de cada nodo y se
# replica entre nodos; entre nodos solo viaja la suma de los gradientes. Mismas variables que entrenar.sbatch
# (DATOS, GENERADOS, ABIERTAS, SALIDA, EXTRA, MISTRAL_COMMON); la cantidad de nodos va en la línea de sbatch:
#     DATOS=... SALIDA=... EXTRA="..." sbatch -N 2 --export=ALL nacional/sbatch/entrenar_multinodo.sbatch
# Como en entrenar.sbatch, si un proceso se cae se relanza todo (con otro puerto de rendezvous) y el entrenamiento
# retoma desde el último punto de reanudación.
#SBATCH --job-name=nac-multi
#SBATCH --partition=gpunode,ayor
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=47:00:00
#SBATCH --exclude=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn061,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err
set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}" || exit 1
export N=${DATOS:?falta DATOS}
export PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
export NPROC=${NPROC:-8} SAL=${SALIDA:?falta SALIDA} ARGS=${EXTRA:-}
export MAESTRO=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -1)
echo "nodos $SLURM_JOB_NODELIST · salida $SAL · $ARGS"
for intento in 1 2 3 4; do
  export PUERTO=$((29500 + (SLURM_JOB_ID % 100) * 5 + intento))
  echo "=== intento $intento · rendezvous $MAESTRO:$PUERTO · $(date '+%F %T')"
  srun --ntasks-per-node=1 --kill-on-bad-exit=1 bash nacional/sbatch/nodo_multinodo.sh 2>&1 \
    | grep --line-buffered -v "CCL_WARN\|it/s\]\|s/it\]"
  rc=${PIPESTATUS[0]}
  [ "$rc" -eq 0 ] && exit 0
  echo "--- falló el intento $intento (código $rc) · $(date '+%F %T')"
done
exit 1

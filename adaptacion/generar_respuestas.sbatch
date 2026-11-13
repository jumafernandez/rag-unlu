#!/bin/bash
# Genera las respuestas de prueba del 9B sin adaptar (tiles 0-3) y con el adaptador
# (tiles 4-7) en el mismo trabajo, sobre los mismos contextos.
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     ADAPTADOR=../adaptadores/9b-digesto/final sbatch adaptacion/generar_respuestas.sbatch
#
#SBATCH --job-name=dig-gen
#SBATCH --partition=batch,ayor
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=06:00:00
#SBATCH --exclude=cn013,cn021,cn023,cn024,cn042,cn045,cn069,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
DATOS="${DATOS:-$BASE/../datos}"
SALIDA="${SALIDA:-$BASE/../respuestas}"
ADAPTADOR="${ADAPTADOR:?falta ADAPTADOR}"
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
mkdir -p "$SALIDA"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=8
unset OPENAI_API_KEY RAG_LLM_BASE

echo "nodo $(hostname) · datos $DATOS · adaptador $ADAPTADOR"
# Hasta tres intentos por proceso: la generación retoma donde quedó (ver armar_datos.sbatch).
lanzar() {   # lanzar TILE NOMBRE PARTE [--adaptador DIR]
  local tile=$1 nombre=$2 p=$3; shift 3
  : > "$SALIDA/$nombre-$p.log"
  for intento in 1 2 3; do
    echo "--- intento $intento" >> "$SALIDA/$nombre-$p.log"
    ZE_AFFINITY_MASK=$tile "$PY" -m adaptacion.generar_respuestas --datos "$DATOS" --salida "$SALIDA" \
      --nombre "$nombre" --parte "$p" --partes 4 "$@" >> "$SALIDA/$nombre-$p.log" 2>&1 && break
  done
}
for p in 0 1 2 3; do
  lanzar $p 9b-base $p &
  lanzar $((p + 4)) 9b-lora $p --adaptador "$ADAPTADOR" &
done
wait
tail -n 2 "$SALIDA"/*.log

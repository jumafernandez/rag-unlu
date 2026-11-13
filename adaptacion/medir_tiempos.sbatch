#!/bin/bash
# Tiempo de respuesta de los tres modelos locales, una pregunta por vez, en un solo trabajo:
# el 27B en una GPU entera (tiles 0 y 1) y cada 9B en un tile de la otra (2 y 3).
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     sbatch adaptacion/medir_tiempos.sbatch
#
#SBATCH --job-name=dig-tiempos
#SBATCH --partition=batch,ayor
#SBATCH --gres=gpu:2
#SBATCH --cpus-per-task=32
#SBATCH --mem=240G
#SBATCH --time=06:00:00
#SBATCH --exclude=cn013,cn021,cn023,cn024,cn042,cn045,cn069,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
R=/home/jfernandez/rag-unlu-adaptacion
DATOS="${DATOS:-$R/datos}"
SALIDA="${SALIDA:-$R/tiempos}"
LIMITE="${LIMITE:-0}"
PY=$R/venv/bin/python
mkdir -p "$SALIDA"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=8
unset OPENAI_API_KEY RAG_LLM_BASE

echo "nodo $(hostname) · datos $DATOS · límite $LIMITE"
lanzar() {   # lanzar TILES NOMBRE MODELO [--adaptador DIR]
  local tiles=$1 nombre=$2 modelo=$3; shift 3
  : > "$SALIDA/$nombre.log"
  for intento in 1 2 3; do
    echo "--- intento $intento" >> "$SALIDA/$nombre.log"
    ZE_AFFINITY_MASK=$tiles "$PY" -m adaptacion.medir_tiempos --datos "$DATOS" --salida "$SALIDA" \
      --modelo "$modelo" --nombre "$nombre" --limite "$LIMITE" "$@" >> "$SALIDA/$nombre.log" 2>&1 && break
  done
}
lanzar 0,1 27b ~/hf-models/Qwen3.5-27B &
lanzar 2 9b-base ~/hf-models/Qwen3.5-9B &
lanzar 3 9b-lora ~/hf-models/Qwen3.5-9B --adaptador $R/adaptadores/9b-digesto/final &
wait
tail -n 2 "$SALIDA"/*.log

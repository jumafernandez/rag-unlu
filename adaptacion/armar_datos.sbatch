#!/bin/bash
# Arma los datos de la adaptación en un nodo: cuatro procesos, cada uno con el maestro de
# 27B repartido en un par de tiles (ZE_AFFINITY_MASK) y su parte de los actos.
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     sbatch adaptacion/armar_datos.sbatch                      # corrida completa
#     LIMITE=3 SALIDA=../datos-canario sbatch -t 01:00:00 adaptacion/armar_datos.sbatch
#
#SBATCH --job-name=dig-datos
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
SALIDA="${SALIDA:-$BASE/../datos}"
LIMITE="${LIMITE:-0}"
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
mkdir -p "$SALIDA"

export RAG_INDICE=/home/jfernandez/rag-unlu-git/indice
export RAG_MODELO_EMB=/home/jfernandez/rag-unlu-git/modelos/bge-m3
export RAG_DISPOSITIVO=xpu
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=16
# Sin clave ni endpoint: el backend no llama a ningún LLM externo (hay_generador() da falso).
unset OPENAI_API_KEY RAG_LLM_BASE

echo "nodo $(hostname) · salida $SALIDA · límite $LIMITE"
# Cada parte se reintenta hasta tres veces: el armado retoma donde quedó, y un "Segmentation
# fault from GPU" del runtime de Intel mata el proceso sin que Python lo pueda atrapar.
: > "$SALIDA/parte-0.log"; : > "$SALIDA/parte-1.log"; : > "$SALIDA/parte-2.log"; : > "$SALIDA/parte-3.log"
for p in 0 1 2 3; do
  (
    for intento in 1 2 3; do
      echo "--- intento $intento" >> "$SALIDA/parte-$p.log"
      RAG_BD="$SALIDA/bd-$p.sqlite" ZE_AFFINITY_MASK="$((2 * p)),$((2 * p + 1))" \
        "$PY" -m adaptacion.armar_datos --parte "$p" --partes 4 --salida "$SALIDA" --limite "$LIMITE" \
        >> "$SALIDA/parte-$p.log" 2>&1 && break
    done
  ) &
done
wait
tail -n 3 "$SALIDA"/parte-*.log

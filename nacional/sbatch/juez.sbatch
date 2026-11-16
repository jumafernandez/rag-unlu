#!/bin/bash
# Juez de Liminar (Llama 3.3 70B, dos placas) sobre todo lo que falte juzgar, con vigía:
#   1. el benchmark a libro cerrado (linea_base juzgar: retoma por archivo de respuestas) y su resumen;
#   2. la muestra RAG de 60 preguntas (juez_rag: retoma por modelo y pregunta) y su resumen, con gpt-4.1-mini.
# Se puede encolar cuantas veces haga falta: cada corrida juzga solo lo nuevo. ETAPAS elige qué corre (por defecto
# "benchmark rag duelos"), por ejemplo: sbatch --export=ALL,ETAPAS=benchmark ...
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo-rag:
#     sbatch -p gpunode,ayor nacional/sbatch/juez.sbatch
#
#SBATCH --job-name=nac-juez
#SBATCH --gres=gpu:2
#SBATCH --cpus-per-task=12
#SBATCH --mem=200G
#SBATCH --time=03:00:00
#SBATCH --exclude=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}" || exit 1
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
JUEZ=/data/contrib/pci_146/hf-models/Llama-3.3-70B-Instruct
REG=/home/jfernandez/rag-unlu-adaptacion/logs/${SLURM_JOB_NAME:-nac-juez}_${SLURM_JOB_ID:-0}.out   # el vigía mira este archivo
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset OPENAI_API_KEY RAG_LLM_BASE
echo "nodo $(hostname) · $(date '+%F %T')"
ETAPAS=" ${ETAPAS:-benchmark rag duelos} "

if [[ $ETAPAS == *" benchmark "* ]]; then
echo "=== benchmark a libro cerrado · $(date '+%T')"
bash nacional/sbatch/con_vigia.sh "$REG" 40 6 $PY -m nacional.linea_base juzgar \
  --respuestas $N/linea_base-v2/respuestas --preguntas $N/linea_base-preguntas-v2.jsonl --juez $JUEZ \
  --salida $N/linea_base-v2/juicios --lote 8
$PY -m nacional.linea_base resumen --respuestas $N/linea_base-v2/respuestas --juicios $N/linea_base-v2/juicios
fi

if [[ $ETAPAS == *" rag "* ]]; then
echo "=== muestra RAG de 60 · $(date '+%T')"
# primero los modelos base y la ronda vigente; la v1 queda para el final. El runtime de las XPU aborta (139) cada
# pocos minutos con este juez y contextos largos, con 2 o 3 placas: avanza de a una docena de juicios por intento,
# así que el vigía lo reintenta muchas veces.
bash nacional/sbatch/con_vigia.sh "$REG" 40 40 $PY -m nacional.juez_rag --datos /home/jfernandez/rag-unlu-adaptacion/datos \
  --respuestas "$N/rag-escenarios/muestra-60/base-*.jsonl" "$N/rag-escenarios/muestra-60/liminar-v2r*.jsonl" \
  "$N/rag-escenarios/muestra-60/liminar-v1-*.jsonl" \
  "$N/rag-escenarios/referencias/gpt-*.jsonl" --salida $N/rag-escenarios/juicios-muestra-60 --muestra 60 --lote 1
fi

if [[ $ETAPAS == *" duelos "* ]]; then
echo "=== generales: duelos de m-ArenaHard ES, ajustado contra base · $(date '+%T')"
B=$N/benchmarks
# cada modelo ajustado de una ronda (liminar-v2r<k>-<modelo>) que tenga respuestas abiertas, contra su base
PARES=$(for f in $B/abiertas/liminar-*.jsonl; do n=$(basename "$f" .jsonl)
          for m in ministral-3-8b gemma-4-e4b qwen3.5-9b gemma-4-12b mistral-small-3.2-24b qwen3.5-27b gemma-4-31b; do
            if [[ $n == liminar-v2r?-$m ]]; then printf 'base-%s:%s,' $m "$n"; fi; done; done)
bash nacional/sbatch/con_vigia.sh "$REG" 40 6 $PY -m nacional.generales duelo --respuestas $B/abiertas \
  --pares "${PARES%,}" --consignas $B/arenahard-es-200.jsonl --juez $JUEZ --salida $B/duelos --lote 2
$PY -m nacional.generales resumen --opcion $B/opcion --duelos $B/duelos
fi
echo "=== fin · $(date '+%T')"

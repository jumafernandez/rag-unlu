#!/bin/bash
# Canario de la tarea RAG (--rag): ¿entran en memoria los ejemplos largos (consulta con los fragmentos recuperados,
# hasta 8.192 tokens) con la receta de cada modelo de la ronda 1? Un nodo entero, los 7 modelos en orden, cada uno con
# las opciones de lanzar_entrenamientos_v2.sh que tocan la memoria (modo, lotes, rango, congelado, carga sin mapeo) y
# solo los ejemplos RAG más largos más 16 de los demás, 5 pasos, con la memoria del tile de cada paso en el registro.
# Cada modelo sigue aunque falle el anterior; el resultado queda en el registro.
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo-rag (el código con --rag; codigo/ queda fijo para la ronda 1):
#     sbatch -p gpunode,ayor --nice=1000 nacional/sbatch/canario_rag.sbatch
#     SOLO=lora-gemma-31b sbatch -p gpunode,ayor --nice=1000 -t 00:30:00 nacional/sbatch/canario_rag.sbatch   # algunos
#     SOLO=lora-gemma-31b RAG_MAX=7168 sbatch ...                                                     # otro corte
#     PYTORCH_ALLOC_CONF=expandable_segments:True SOLO=lora-gemma-31b sbatch ...   # asignador de torch con segmentos
#                                                                                  # expandibles (contra la fragmentación)
#     LARGOS=$N/canarios/rag-largos-6144.jsonl RAG_MAX=6144 SOLO=lora-gemma-31b sbatch ...   # los más largos bajo un tope
#     EXTRA_CANARIO="--atencion-largos flex_attention --max-pasos 15 --memoria-pasos 15" ...   # opciones de más (pisan)
#
#SBATCH --job-name=nac-canario-rag
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=04:00:00
#SBATCH --exclude=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
M=/data/contrib/pci_146/hf-models
SAL=$N/canarios/rag
LARGOS=${LARGOS:-$N/canarios/rag-largos.jsonl}   # los 120 ejemplos RAG más largos (por caracteres)
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
NPROC=8

ulimit -n "$(ulimit -Hn)"
NUCLEOS=$($PY -c "import os; print(','.join(map(str, sorted(os.sched_getaffinity(0))[:$NPROC])))")
export CCL_WORKER_COUNT=1 CCL_WORKER_AFFINITY=$NUCLEOS CCL_PROCESS_LAUNCHER=torchrun CCL_ZE_IPC_EXCHANGE=sockets
export FI_PROVIDER=tcp TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 OMP_NUM_THREADS=4
unset OPENAI_API_KEY RAG_LLM_BASE
mkdir -p "$SAL"
echo "nodo $(hostname) · $(date '+%F %T') · $(wc -l < "$LARGOS") ejemplos RAG largos · tope ${RAG_MAX:-8192} tokens" \
     "· asignador ${PYTORCH_ALLOC_CONF:-por omisión}"
if [ -n "${PYTORCH_ALLOC_CONF:-}" ]; then   # confirmar que el asignador de la XPU toma la configuración
  $PY -c "
import torch
x = torch.empty(1 << 28, device='xpu')
try:
    s = torch.xpu.memory_snapshot()
    print('segmentos expandibles:', any(g.get('is_expandable') for g in s), f'({len(s)} segmentos)')
except Exception as e:
    print('no pude leer la instantánea del asignador:', e)
" 2>&1 | grep -v -i "warn"
fi

LOTES="--tokens-por-lote 2048 --acumulacion 2"
SOLO=${SOLO:-}   # nombres separados por comas: solo esos modelos (por omisión, los 7)
RAG_MAX=${RAG_MAX:-8192}   # tope de tokens de un ejemplo RAG (--rag-max-tokens)
entrenar() {
  local nombre=$1; shift
  if [ -n "$SOLO" ] && [[ ",$SOLO," != *",$nombre,"* ]]; then return; fi
  echo "=== $nombre · $(date '+%T')"
  rm -rf "$SAL/$nombre"
  $PY -m torch.distributed.run --standalone --nproc_per_node $NPROC -m nacional.entrenar \
    --datos "$N" --generados "$N/gen/documentos-1a,$N/gen/documentos-1b,$N/gen/fragmentos" \
    --estrategia fsdp --sin-mmap --rag "$LARGOS" --rag-max-tokens "$RAG_MAX" --max-ejemplos 16 --max-validacion 8 \
    --epocas 1 --max-pasos 5 --memoria-pasos 5 --eval-cada 1000 --guardar-cada 1000 --salida "$SAL/$nombre" "$@" ${EXTRA_CANARIO:-} 2>&1 \
    | grep --line-buffered -v "CCL_WARN\|it/s\]\|s/it\]"
  echo "--- código de salida ${PIPESTATUS[0]} · $(date '+%T')"
  rm -rf "$SAL/$nombre"   # solo interesa el registro
}

entrenar completo-gemma-12b --modelo "$M/gemma-4-12B-it" --modo completo $LOTES
entrenar lora-gemma-31b --modelo "$M/gemma-4-31B-it" --modo lora $LOTES --rango 256
entrenar lora-qwen-27b --modelo "$M/Qwen3.5-27B" --modo lora $LOTES --rango 256
entrenar completo-qwen-9b --modelo "$M/Qwen3.5-9B" --modo completo $LOTES
entrenar completo-gemma-e4b --modelo "$M/gemma-4-E4B-it" --modo completo $LOTES --congelar embed_tokens_per_layer
export PYTHONPATH=/home/jfernandez/rag-unlu-adaptacion/extra-mistral
entrenar lora-mistral-24b --modelo "$M/Mistral-Small-3.2-24B-Instruct-2506" --modo lora --rango 256
entrenar completo-ministral-8b --modelo "$M/Ministral-3-8B-Instruct-2512-BF16" --modo completo $LOTES
echo "=== fin · $(date '+%T')"

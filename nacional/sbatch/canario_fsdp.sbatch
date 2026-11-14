#!/bin/bash
# Canario de las estrategias con FSDP, en medio nodo (4 tiles: con 8 la memoria por tile es menor), en orden:
#   1. formatos de chat de cada familia (cierre del turno, fin de texto, BOS), sin GPU;
#   2. ajuste completo del Qwen 3.5 9B: 4 pasos con punto de reanudación, y otra corrida que retoma
#      desde ahí hasta el paso 6; guarda en formato de Hugging Face y lo relee. En 4 tiles, lotes de
#      2.048 tokens con acumulación de 2 (los mismos tokens por paso): con 4.096 se agotó la memoria;
#   3. LoRA del Gemma 4 31B: 4 pasos;
#   4. LoRA del Qwen 3.5 27B con los núcleos de flash-linear-attention: 4 pasos.
# Cada etapa sigue aunque falle la anterior; el resultado queda en el registro.
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     sbatch nacional/sbatch/canario_fsdp.sbatch
#     sbatch --gres=gpu:4 -c 64 --mem=480G --export=ALL,NPROC=8,LOTES_COMPLETO="--tokens-por-lote 4096" nacional/sbatch/canario_fsdp.sbatch
#
#SBATCH --job-name=nac-canario
#SBATCH --partition=gpunode
#SBATCH --gres=gpu:2
#SBATCH --cpus-per-task=32
#SBATCH --mem=240G
#SBATCH --time=03:00:00
#SBATCH --exclude=cn013,cn016,cn021,cn022,cn023,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
M=/data/contrib/pci_146/hf-models
SAL=$N/canarios
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
NPROC=${NPROC:-4}   # 8 con --gres=gpu:4
# Lotes del ajuste completo: en 4 tiles, 2.048 tokens con acumulación de 2 (con 4.096 se agotó la memoria).
LOTES_COMPLETO="${LOTES_COMPLETO:---tokens-por-lote 2048 --acumulacion 2}"

ulimit -n "$(ulimit -Hn)"
NUCLEOS=$($PY -c "import os; print(','.join(map(str, sorted(os.sched_getaffinity(0))[:$NPROC])))")
export CCL_WORKER_COUNT=1 CCL_WORKER_AFFINITY=$NUCLEOS CCL_PROCESS_LAUNCHER=torchrun CCL_ZE_IPC_EXCHANGE=sockets
export FI_PROVIDER=tcp TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 OMP_NUM_THREADS=4
unset OPENAI_API_KEY RAG_LLM_BASE
mkdir -p "$SAL"
echo "nodo $(hostname) · $(date '+%F %T')"

echo "=== 1. formatos"
PYTHONPATH=/home/jfernandez/rag-unlu-adaptacion/extra-mistral $PY - "$M/Qwen3.5-9B" "$M/gemma-4-31B-it" \
    "$M/Mistral-Small-3.2-24B-Instruct-2506" <<'EOF'
import sys
from transformers import AutoTokenizer
from nacional.entrenar import Formato
for ruta in sys.argv[1:]:
    try:
        tok = AutoTokenizer.from_pretrained(ruta)
        f = Formato(tok)
        x = f.chat('¿Qué regula la Ley 24.521?', 'La educación superior.', 4096)
        resp = [i for i, l in zip(x['input_ids'], x['labels']) if l != -100]
        print(f'{ruta.split("/")[-1]}: {type(tok).__name__} · cierre {f.cierre!r} {f.cierre_ids} · bos {f.bos} · fin {f.fin}')
        print(f'   prompt {tok.decode(x["input_ids"][:len(x["input_ids"]) - len(resp)])!r}')
        print(f'   respuesta (con pérdida) {tok.decode(resp)!r}')
        t = f.texto('Artículo 1. Texto de prueba.', 4096)
        print(f'   texto plano {tok.decode(t["input_ids"])!r}')
    except Exception as e:
        print(f'{ruta}: ERROR {type(e).__name__}: {e}')
EOF

entrenar() {
  $PY -m torch.distributed.run --standalone --nproc_per_node $NPROC -m nacional.entrenar \
    --datos "$N" --generados "$N/gen/documentos-1a,$N/gen/documentos-1b,$N/gen/fragmentos" \
    --max-ejemplos 3000 --max-validacion 32 --eval-cada 1000 --verificar "$@"
  echo "--- código de salida $?"
}

echo "=== 2. ajuste completo, Qwen 3.5 9B · $(date '+%T')"
rm -rf "$SAL/completo-9b"
entrenar --modelo "$M/Qwen3.5-9B" --modo completo --estrategia fsdp --salida "$SAL/completo-9b" --max-pasos 4 --guardar-cada 2 \
  $LOTES_COMPLETO
echo "=== 2b. retoma hasta el paso 6 · $(date '+%T')"
entrenar --modelo "$M/Qwen3.5-9B" --modo completo --estrategia fsdp --salida "$SAL/completo-9b" --max-pasos 6 --guardar-cada 2 \
  $LOTES_COMPLETO

echo "=== 3. LoRA, Gemma 4 31B · $(date '+%T')"
rm -rf "$SAL/lora-gemma31b"
entrenar --modelo "$M/gemma-4-31B-it" --modo lora --estrategia fsdp --salida "$SAL/lora-gemma31b" --max-pasos 4 --guardar-cada 4

echo "=== 4. LoRA con fla, Qwen 3.5 27B · $(date '+%T')"
rm -rf "$SAL/lora-qwen27b"
entrenar --modelo "$M/Qwen3.5-27B" --modo lora --estrategia fsdp --fla --salida "$SAL/lora-qwen27b" --max-pasos 4 --guardar-cada 4

echo "=== fin · $(date '+%T')"

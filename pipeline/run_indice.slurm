#!/bin/bash
#
# Construye el índice completo, de punta a punta y sin intervención:
#
#     1. metadata autoritativa   (une cada PDF con su fila del sistema fuente)
#     2. chunking estructural    (corta por unidad normativa)
#     3. embeddings en GPU       (BGE-m3 sobre las Intel Max 1550)
#
# Los pasos 1 y 2 son de CPU y duran pocos minutos; se hacen dentro del mismo job de GPU
# a propósito. Gastar unos minutos de nodo GPU en trabajo de CPU es preferible a encadenar
# dos jobs que pueden quedar esperando cola por separado, y en este proyecto las horas de
# CPU son el recurso escaso, no las de GPU.
#
# Encolar después del parseo, encadenado para que espere a que termine:
#     sbatch --dependency=afterok:<JOBID_DEL_PARSEO> pipeline/run_indice.slurm
#
# O directamente, si el parseo ya terminó:
#     sbatch pipeline/run_indice.slurm
#
# Ensayo corto antes de la corrida larga:
#     LIMITE=500 sbatch pipeline/run_indice.slurm
#
#SBATCH --job-name=rag-indice
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
#SBATCH --partition=gpunode
#SBATCH --gres=gpu:intel_xt1550:1
#SBATCH --cpus-per-task=16
#SBATCH --time=06:00:00

set -euo pipefail

BASE="${RAG_BASE:-${SLURM_SUBMIT_DIR:-$PWD}}"
[[ -f "$BASE/pipeline/embeddings.py" ]] || {
  echo "ERROR: '$BASE' no es la raíz del repo. Encolá parado ahí o pasá RAG_BASE=/ruta" >&2; exit 1; }

RESULTADOS="${RESULTADOS:-$BASE/resultados/portal}"
JOIN="${JOIN:-$BASE/resultados/join_metadata.csv}"
CHUNKS="${CHUNKS:-$BASE/resultados/chunks.jsonl}"
SALIDA="${SALIDA:-$BASE/indice}"
MODELO="${MODELO:-$BASE/modelos/bge-m3}"
VENV="${VENV:-$HOME/trace-repro/venv}"
BATCH="${BATCH:-32}"

mkdir -p logs "$SALIDA" "$(dirname "$CHUNKS")"

PY="$VENV/bin/python"
[[ -x "$PY" ]]         || { echo "ERROR: no está el venv en $VENV" >&2; exit 1; }
[[ -d "$RESULTADOS" ]] || { echo "ERROR: no está la salida del parseo en $RESULTADOS" >&2; exit 1; }
[[ -d "$MODELO" ]]     || { echo "ERROR: faltan los pesos en $MODELO" >&2; exit 1; }
[[ -f "$BASE/scrapers/metadatos.csv" ]] || { echo "ERROR: falta scrapers/metadatos.csv" >&2; exit 1; }

export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export LANG=C.UTF-8 LC_ALL=C.UTF-8

echo "=== nodo $(hostname) | inicio: $(date) ==="
echo "=== documentos parseados: $(find "$RESULTADOS" -name '*_canonico.yaml' | wc -l) ==="

echo
echo ">>> 1/3  Metadata autoritativa del sistema fuente"
"$PY" "$BASE/pipeline/unir_metadata.py" \
    --scrapers "$BASE/scrapers" \
    --pdfs "$BASE/data/portal" \
    --yaml "$RESULTADOS" \
    --salida "$JOIN"

echo
echo ">>> 2/3  Chunking estructural"
"$PY" "$BASE/pipeline/chunkear.py" \
    --resultados "$RESULTADOS" \
    --metadata "$JOIN" \
    --salida "$CHUNKS"

echo
echo ">>> 3/3  Embeddings en GPU"
"$PY" - <<'PY'
import torch
if not (hasattr(torch, "xpu") and torch.xpu.is_available()):
    raise SystemExit("ERROR: no se ve la GPU desde este job")
print("torch", torch.__version__, "| dispositivos:", torch.xpu.device_count(),
      "|", torch.xpu.get_device_name(0))
PY

ARGS=(--chunks "$CHUNKS" --salida "$SALIDA" --modelo "$MODELO"
      --dispositivo xpu --batch "$BATCH")
[[ -n "${LIMITE:-}" ]] && ARGS+=(--limite "$LIMITE")

"$PY" "$BASE/pipeline/embeddings.py" "${ARGS[@]}"

echo
echo "=== fin: $(date) ==="
echo "=== índice en $SALIDA ==="
ls -lh "$SALIDA"

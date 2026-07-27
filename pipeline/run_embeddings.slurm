#!/bin/bash
#
# Genera los embeddings del corpus en las GPU Intel de Clementina.
#
# No usa contenedor: reutiliza el venv que ya tiene torch con soporte XPU
# (~/trace-repro/venv, torch 2.11.0+xpu + sentence-transformers), verificado sobre
# Intel Data Center GPU Max 1550. El backend es `xpu`, no CUDA.
#
# Antes de la corrida completa conviene un ensayo corto:
#     LIMITE=500 sbatch pipeline/run_embeddings.slurm
# Valida que el modelo carga, que la GPU responde y da una velocidad real para estimar.
#
# Encolar (parado en la raíz del repo):
#     sbatch pipeline/run_embeddings.slurm
#
#SBATCH --job-name=rag-emb
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
#SBATCH --partition=gpunode
#SBATCH --gres=gpu:intel_xt1550:1
#SBATCH --cpus-per-task=16
#SBATCH --time=03:00:00

set -euo pipefail

# sbatch copia el script a un spool del nodo: la ruta buena es la de encolado.
BASE="${RAG_BASE:-${SLURM_SUBMIT_DIR:-$PWD}}"
[[ -f "$BASE/pipeline/embeddings.py" ]] || {
  echo "ERROR: '$BASE' no es la raíz del repo. Encolá parado ahí, o pasá RAG_BASE=/ruta" >&2; exit 1; }

CHUNKS="${CHUNKS:-$BASE/resultados/chunks.jsonl}"
SALIDA="${SALIDA:-$BASE/indice}"
MODELO="${MODELO:-$BASE/modelos/bge-m3}"
VENV="${VENV:-$HOME/trace-repro/venv}"
BATCH="${BATCH:-32}"

mkdir -p logs "$SALIDA"

PY="$VENV/bin/python"
[[ -x "$PY" ]]      || { echo "ERROR: no está el venv en $VENV" >&2; exit 1; }
[[ -f "$CHUNKS" ]]  || { echo "ERROR: falta $CHUNKS (corré chunkear.py primero)" >&2; exit 1; }
[[ -d "$MODELO" ]]  || { echo "ERROR: faltan los pesos en $MODELO (ver 03_preparar_embeddings.sh)" >&2; exit 1; }

# Sin internet: que las librerías no intenten salir a HuggingFace y fallen con un
# timeout confuso en vez de usar los pesos locales.
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export LANG=C.UTF-8 LC_ALL=C.UTF-8

echo "=== nodo $(hostname) | inicio: $(date) ==="
"$PY" - <<'PY'
import torch
print("torch:", torch.__version__)
disponible = hasattr(torch, "xpu") and torch.xpu.is_available()
print("xpu disponible:", disponible)
if disponible:
    print("dispositivos:", torch.xpu.device_count())
    print("modelo:", torch.xpu.get_device_name(0))
else:
    raise SystemExit("ERROR: no se ve la GPU desde este job")
PY

ARGS=(--chunks "$CHUNKS" --salida "$SALIDA" --modelo "$MODELO"
      --dispositivo xpu --batch "$BATCH")
[[ -n "${LIMITE:-}" ]] && ARGS+=(--limite "$LIMITE")

"$PY" "$BASE/pipeline/embeddings.py" "${ARGS[@]}"

echo "=== fin: $(date) ==="

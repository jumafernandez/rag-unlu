#!/usr/bin/env bash
#
# Prepara TODO lo que hace falta para generar embeddings en Clementina, que no tiene
# internet: las wheels del stack y los pesos del modelo.
#
# Se corre EN TU MAC. Después se manda por rsync (el script te imprime el comando).
#
# Uso:
#     ./03_preparar_embeddings.sh [version_python_del_cluster]   # default 3.9

set -euo pipefail

PYVER="${1:-3.9}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WHEELS="$HERE/wheels-embeddings-py${PYVER}"
MODELO="$HERE/modelos/bge-m3"
PIP=("${PYTHON:-python3}" -m pip)

mkdir -p "$WHEELS" "$(dirname "$MODELO")"

echo ">> 1/2  Wheels del stack de embeddings (Linux x86_64, Python $PYVER)"
# torch CPU: el índice de PyTorch para CPU evita bajar las variantes CUDA (varios GB
# de más que en Clementina no sirven, porque las GPU son Intel, no NVIDIA).
"${PIP[@]}" download \
  --dest "$WHEELS" \
  --only-binary=:all: \
  --platform manylinux2014_x86_64 \
  --python-version "$PYVER" \
  --implementation cp \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  torch FlagEmbedding numpy

echo ">> wheels: $(du -sh "$WHEELS" | cut -f1)  ($(ls "$WHEELS" | wc -l | tr -d ' ') archivos)"

echo
echo ">> 2/2  Pesos de BGE-m3 (~2,3 GB)"
if [[ -d "$MODELO" && -n "$(ls -A "$MODELO" 2>/dev/null)" ]]; then
  echo "   ya está en $MODELO, no lo bajo de nuevo"
else
  "${PYTHON:-python3}" - "$MODELO" <<'PY'
import sys
try:
    from huggingface_hub import snapshot_download
except ImportError:
    sys.exit("falta huggingface_hub: pip install huggingface_hub")
destino = sys.argv[1]
# Solo lo necesario para inferencia: se evitan los checkpoints de entrenamiento y ONNX.
snapshot_download(
    "BAAI/bge-m3",
    local_dir=destino,
    allow_patterns=["*.json", "*.txt", "*.model", "pytorch_model.bin", "sentencepiece*", "*.safetensors"],
    ignore_patterns=["onnx/*", "*.onnx", "colbert*", "sparse*"],
)
print("descargado en", destino)
PY
fi
echo ">> modelo: $(du -sh "$MODELO" | cut -f1)"

echo
echo "Para mandarlo a Clementina:"
echo "    rsync -avh --progress '$WHEELS' '$MODELO' clementina:rag-unlu-git/pipeline/"
echo
echo "Y allá, instalar en el venv:"
echo "    ~/rag-unlu-git/extractor-venv/bin/pip install --no-index \\"
echo "        --find-links ~/rag-unlu-git/pipeline/$(basename "$WHEELS") torch FlagEmbedding"

#!/usr/bin/env bash
#
# Baja los pesos de BGE-m3 para llevarlos a Clementina, que no tiene internet.
#
# NO hace falta bajar wheels: el venv del cluster (~/trace-repro/venv) ya tiene
# torch 2.11.0+xpu y sentence-transformers, verificado sobre las GPU Intel Max 1550.
#
# Se corre EN TU MAC. Al final imprime el comando de rsync.
#
# Uso:  ./03_preparar_embeddings.sh

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="$(dirname "$HERE")"
DESTINO="$BASE/modelos/bge-m3"
PY="${PYTHON:-python3}"

mkdir -p "$(dirname "$DESTINO")"

if [[ -d "$DESTINO" && -n "$(ls -A "$DESTINO" 2>/dev/null)" ]]; then
  echo ">> Ya está en $DESTINO ($(du -sh "$DESTINO" | cut -f1)); no lo bajo de nuevo."
else
  echo ">> Bajando BAAI/bge-m3 (~2,3 GB)..."
  "$PY" - "$DESTINO" <<'PY'
import sys
try:
    from huggingface_hub import snapshot_download
except ImportError:
    sys.exit("falta huggingface_hub:  pip install huggingface_hub")

destino = sys.argv[1]
# Solo lo necesario para inferencia con sentence-transformers. Se excluyen ONNX y los
# heads de recuperación esparsa/colbert: acá la parte léxica la resuelve BM25.
snapshot_download(
    "BAAI/bge-m3",
    local_dir=destino,
    allow_patterns=[
        "*.json", "*.txt", "*.md",
        "sentencepiece.bpe.model", "tokenizer.json", "tokenizer_config.json",
        "model.safetensors", "pytorch_model.bin",
        "1_Pooling/*", "modules.json", "config_sentence_transformers.json",
    ],
    ignore_patterns=["onnx/*", "*.onnx", "colbert_linear.pt", "sparse_linear.pt"],
)
print("descargado en", destino)
PY
fi

echo
echo ">> Contenido:"
du -sh "$DESTINO"
ls -1 "$DESTINO" | head -12

echo
echo ">> Verificando que carga y produce vectores..."
"$PY" - "$DESTINO" <<'PY'
import sys
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    print("   (sentence-transformers no está en esta Mac; se omite la verificación local)")
    sys.exit(0)
m = SentenceTransformer(sys.argv[1], device="cpu")
v = m.encode(["Disposición DISPCD-CB 528/2025 — Artículo 1"], normalize_embeddings=True)
print(f"   OK: vector de dimensión {v.shape[1]}")
PY

echo
echo "Para mandarlo a Clementina:"
echo "    rsync -avh --progress '$BASE/modelos' clementina:rag-unlu-git/"

#!/usr/bin/env bash
#
# ALTERNATIVA a download_portal.sh: baja la carpeta Drive "Archivos portal"
# completa con rclone (lista sola, no necesita manifest.tsv). Resumible y, si
# agregan archivos, volvés a correr y trae solo lo nuevo.
#
# Requiere rclone instalado y un remote configurado una sola vez.
# Ver: rag-unlu/SETUP_RCLONE.md
#
# Uso:
#   ./download_portal_rclone.sh [DEST_DIR]
#
# Variables opcionales:
#   RCLONE_REMOTE=unludrive   (nombre del remote que creaste en rclone config)
#   FOLDER_ID=...             (id de la carpeta; por defecto "Archivos portal")

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REMOTE="${RCLONE_REMOTE:-unludrive}"
FOLDER_ID="${FOLDER_ID:-1F8yOUefIDSny6ByPvtpHCt3_FjlW_hnQ}"
DEST="${1:-$HERE/../data/portal}"

if ! command -v rclone >/dev/null 2>&1; then
  echo "ERROR: rclone no está instalado. Instalalo con:  brew install rclone" >&2
  echo "Después configurá el remote (una vez): ver rag-unlu/SETUP_RCLONE.md" >&2
  exit 1
fi

if ! rclone listremotes | grep -q "^${REMOTE}:$"; then
  echo "ERROR: no existe el remote '${REMOTE}' en rclone." >&2
  echo "Configuralo con 'rclone config' (ver rag-unlu/SETUP_RCLONE.md)" >&2
  echo "o indicá el nombre correcto:  RCLONE_REMOTE=otro $0" >&2
  exit 1
fi

mkdir -p "$DEST"
echo ">> Remote:  ${REMOTE}:  (carpeta ${FOLDER_ID})"
echo ">> Destino: $DEST"
echo

rclone copy "${REMOTE}:" "$DEST" \
  --drive-root-folder-id "$FOLDER_ID" \
  --drive-acknowledge-abuse \
  --transfers 16 --checkers 16 \
  -P --stats-one-line

echo
echo ">> PDFs en destino: $(find "$DEST" -maxdepth 1 -type f -name '*.pdf' | wc -l | tr -d ' ')"

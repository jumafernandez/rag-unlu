#!/usr/bin/env bash
#
# Verifica que lo subido a Clementina coincida con la copia local, comparando
# NOMBRE y TAMAÑO de cada archivo (no solo el conteo).
#
# Usa `find` en ambos lados a propósito: con 19.959 archivos, un glob como
# `ls dir/*.pdf` puede exceder el límite de argumentos (ARG_MAX) y devolver un
# resultado equivocado o vacío.
#
# Uso:
#   ./verify_upload.sh
#
# Requiere VPN levantada.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$HERE/../data/portal}"
REMOTE="${REMOTE:-clementina}"
DESTDIR="${DESTDIR:-rag-unlu/data/portal}"
TMP="${TMPDIR:-/tmp}/rag-unlu-verify.$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

if ! nc -z -G 5 172.29.3.3 22 >/dev/null 2>&1; then
  echo "ERROR: no llego a Clementina. ¿VPN levantada?" >&2
  exit 1
fi

echo ">> Inventario local..."
# stat de BSD/macOS; -exec ... + agrupa para no romper ARG_MAX.
find "$SRC" -maxdepth 1 -type f -name '*.pdf' -exec stat -f '%N %z' {} + \
  | sed 's|^.*/||' | awk '{print $1"\t"$2}' | sort > "$TMP/local.tsv"

echo ">> Inventario remoto..."
# find de GNU en el cluster; -printf evita cualquier glob.
ssh "$REMOTE" "find '$DESTDIR' -maxdepth 1 -type f -name '*.pdf' -printf '%f\t%s\n'" \
  | sort > "$TMP/remoto.tsv"

nl=$(wc -l < "$TMP/local.tsv"  | tr -d ' ')
nr=$(wc -l < "$TMP/remoto.tsv" | tr -d ' ')
bl=$(awk -F'\t' '{s+=$2} END {print s+0}' "$TMP/local.tsv")
br=$(awk -F'\t' '{s+=$2} END {print s+0}' "$TMP/remoto.tsv")

echo
echo "  local : $nl archivos, $bl bytes"
echo "  remoto: $nr archivos, $br bytes"
echo

if diff -q "$TMP/local.tsv" "$TMP/remoto.tsv" >/dev/null; then
  echo "✅ IDÉNTICOS: los $nl archivos coinciden en nombre y tamaño."
  exit 0
fi

echo "⚠️  HAY DIFERENCIAS:"
echo
echo "--- faltan en el remoto (o difieren) ---"
comm -23 "$TMP/local.tsv" "$TMP/remoto.tsv" | head -40
echo
echo "--- están en el remoto pero no en local (o difieren) ---"
comm -13 "$TMP/local.tsv" "$TMP/remoto.tsv" | head -40
echo
echo "Faltantes por nombre (ignorando tamaño):"
comm -23 <(cut -f1 "$TMP/local.tsv") <(cut -f1 "$TMP/remoto.tsv") | wc -l | tr -d ' '
echo
echo "Si faltan archivos, volvé a correr ./upload_portal.sh (es incremental)."
exit 1

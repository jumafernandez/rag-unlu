#!/usr/bin/env bash
#
# Descarga los PDFs del digesto UNLu (carpeta Drive "Archivos portal") a partir
# del manifiesto id<TAB>tamaño<TAB>título. Los archivos son "cualquiera con el
# link", así que se bajan con curl sin autenticación.
#
# Uso:
#   ./download_portal.sh [DEST_DIR]
#
# Variables de entorno opcionales:
#   MANIFEST=/ruta/manifest.tsv   (por defecto: ../manifest.tsv junto al script)
#   JOBS=8                        (descargas en paralelo)
#
# Es RESUMIBLE: si un PDF ya está bajado y es válido, lo saltea. Podés cortar con
# Ctrl-C y volver a correr; sigue donde quedó. Los que fallan quedan en failed.tsv;
# volvé a correr el script para reintentarlos.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="${MANIFEST:-$HERE/../manifest.tsv}"
DEST="${1:-$HERE/../data/portal}"
JOBS="${JOBS:-8}"
FAIL="$HERE/../failed.tsv"

if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: no encuentro el manifiesto en $MANIFEST" >&2
  exit 1
fi

mkdir -p "$DEST"
: > "$FAIL"

total=$(awk -F'\t' 'NF>=3 && $1!=""' "$MANIFEST" | wc -l | tr -d ' ')
echo ">> Manifiesto: $MANIFEST ($total archivos)"
echo ">> Destino:    $DEST"
echo ">> Paralelo:   $JOBS  |  reintentos en: $FAIL"
echo

# Descarga un archivo. Saltea si ya existe y tiene cabecera %PDF.
dl() {
  local id="$1" title="$2" out="$DEST/$title"
  if [[ -s "$out" ]] && head -c 5 "$out" | grep -q '%PDF'; then
    return 0
  fi
  local url="https://drive.google.com/uc?export=download&id=${id}"
  if curl -fsSL --retry 3 --retry-delay 2 --max-time 120 -o "$out.part" "$url" \
     && head -c 5 "$out.part" | grep -q '%PDF'; then
    mv -f "$out.part" "$out"
  else
    rm -f "$out.part"
    printf '%s\t%s\n' "$id" "$title" >> "$FAIL"
    echo "  FALLO: $title ($id)" >&2
  fi
}
export -f dl
export DEST FAIL

# Pool de paralelismo por lotes (compatible con bash 3.2 de macOS).
i=0
while IFS=$'\t' read -r id title; do
  [[ -z "$id" ]] && continue
  dl "$id" "$title" &
  i=$((i + 1))
  if (( i % JOBS == 0 )); then wait; fi
done < <(awk -F'\t' 'NF>=3 && $1!="" {print $1"\t"$3}' "$MANIFEST")
wait

bajados=$(find "$DEST" -maxdepth 1 -type f -name '*.pdf' | wc -l | tr -d ' ')
fallidos=$(wc -l < "$FAIL" | tr -d ' ')
echo
echo ">> Listo. PDFs en destino: $bajados / $total   |   fallidos: $fallidos"
if [[ "$fallidos" -gt 0 ]]; then
  echo ">> Reintentá los fallidos volviendo a correr:  $0 $DEST"
fi

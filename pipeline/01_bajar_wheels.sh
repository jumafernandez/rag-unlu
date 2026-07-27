#!/usr/bin/env bash
#
# PASO 1 (en tu Mac, que SÍ tiene internet).
#
# Clementina no tiene salida a internet, así que no se puede `pip install` allá.
# Este script descarga las wheels de Linux y las deja listas para subir.
#
# ANTES de correrlo necesitás saber la versión de Python del cluster:
#     ssh clementina 'python3 -V'
# y pasarla acá. Ejemplo:
#     ./01_bajar_wheels.sh 3.11
#
# Si el cluster usa modules, mirá primero qué hay:  module avail python

set -euo pipefail

PYVER="${1:-}"
if [[ -z "$PYVER" ]]; then
  echo "Uso: $0 <version_python_del_cluster>   (ej: $0 3.11)" >&2
  echo "Averiguala con:  ssh clementina 'python3 -V'" >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
REQ="$HERE/requirements-clementina.txt"
OUT="$HERE/wheels-py${PYVER}"

# `pip` pelado puede no estar en el PATH (en macOS suele no estarlo). Usamos el
# módulo pip del intérprete, configurable con PYTHON=.
PIP=("${PYTHON:-python3}" -m pip)

mkdir -p "$OUT"

echo ">> Descargando wheels para Linux x86_64 / Python $PYVER"
echo ">> Destino: $OUT"
echo

# --only-binary=:all: fuerza wheels precompiladas (en el cluster no queremos compilar).
# La plataforma manylinux cubre prácticamente cualquier Linux de HPC.
"${PIP[@]}" download \
  --requirement "$REQ" \
  --dest "$OUT" \
  --only-binary=:all: \
  --platform manylinux2014_x86_64 \
  --python-version "$PYVER" \
  --implementation cp

echo
echo ">> Wheels descargadas:"
ls -1sh "$OUT"
echo
echo ">> Total: $(du -sh "$OUT" | cut -f1)"
echo
echo "Siguiente paso: subir esto y el repo al cluster."
echo "  rsync -avh --progress '$OUT' clementina:rag-unlu/extractor/"

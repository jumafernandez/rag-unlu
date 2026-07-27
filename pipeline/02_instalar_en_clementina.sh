#!/usr/bin/env bash
#
# PASO 3: se corre EN CLEMENTINA (login node), después de subir wheels + repo.
# Crea el venv e instala las dependencias SIN internet, desde las wheels subidas.
#
# Uso (en el cluster):
#   cd ~/rag-unlu/extractor && ./02_instalar_en_clementina.sh wheels-py3.11

set -euo pipefail

WHEELS="${1:-}"
if [[ -z "$WHEELS" || ! -d "$WHEELS" ]]; then
  echo "Uso: $0 <carpeta_de_wheels>   (ej: $0 wheels-py3.11)" >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/venv"

# Guardia: este paso corre DENTRO de Clementina. Las wheels son Linux/cp39, así que
# en una Mac (Darwin, otra versión de Python) no matchea nada y falla confuso.
_os="$(uname -s)"
_py="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
if [[ "$_os" != "Linux" || "$_py" != "3.9" ]]; then
  echo "ERROR: este paso se corre DENTRO de Clementina (Linux + Python 3.9)." >&2
  echo "       Detecté: ${_os} / Python ${_py}.  Si dice 'Darwin', estás en tu Mac." >&2
  echo "       Entrá con 'ssh clementina' y corré esto allá." >&2
  exit 1
fi

echo ">> Python del sistema: $(python3 -V)"
echo ">> Creando venv en $VENV"
python3 -m venv "$VENV"

# --no-index: no intentes ir a PyPI (no hay internet).
# --find-links: usá SOLO las wheels locales.
echo ">> Instalando desde wheels locales (sin internet)"
"$VENV/bin/pip" install --no-index --find-links "$WHEELS" \
  --requirement "$HERE/requirements-clementina.txt"

echo
echo ">> Verificación de imports:"
"$VENV/bin/python" - <<'EOF'
import sys
ok = True
for mod, paquete in [("fitz", "PyMuPDF"), ("yaml", "PyYAML"), ("Levenshtein", "Levenshtein")]:
    try:
        m = __import__(mod)
        print(f"   OK  {mod:12s} ({paquete})  v{getattr(m, '__version__', '?')}")
    except Exception as e:
        ok = False
        print(f"   FALLA {mod}: {e}")
print("\nTODO OK" if ok else "\nHAY IMPORTS ROTOS")
sys.exit(0 if ok else 1)
EOF

echo
echo ">> El venv provee un binario 'python'? (lo necesita procesador_masivo.py)"
ls -l "$VENV/bin/python" >/dev/null && echo "   OK: $VENV/bin/python"
echo
echo "Listo. Siguiente: preparar el work dir y encolar el job."

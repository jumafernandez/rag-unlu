#!/bin/bash
#
# Job de SLURM: corre el extractor sobre los PDFs del digesto UNLu.
# Usa el repo PARCHEADO (ver extractor_pdf_clementina.patch).
#
#SBATCH --job-name=rag-unlu-extractor
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --time=01:00:00
#SBATCH --partition=cpunode
### Particiones de Clementina (sinfo -s): cpunode / batch (default) / testing (30min).
### cpunode es la de CPU. Si te rebota, probá 'batch'.

# El parche hace que procesador_masivo.py lea SLURM_CPUS_PER_TASK, así que los
# workers respetan lo que pediste acá arriba (sin el parche usaba os.cpu_count(),
# que ve el nodo entero y sobresuscribe).

set -euo pipefail

BASE="$HOME/rag-unlu"
PDFS="$BASE/data/portal"
WORK="$BASE/extractor/work"
VENV="$BASE/extractor/venv"

source "$VENV/bin/activate"

# Los documentos tienen acentos (ÁÉÍÓÚñ). Con LANG=C algunos open() pueden romper.
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

# Corta un PDF que cuelgue al extractor en vez de bloquear al worker para siempre.
export EXTRACTOR_TIMEOUT=300

# El orquestador resuelve los scripts con os.path.abspath("script_extractor.py"),
# o sea RELATIVO AL CWD -> hay que pararse donde están los .py.
cd "$WORK"

echo "=== nodo: $(hostname) ==="
echo "=== cpus-per-task: ${SLURM_CPUS_PER_TASK:-?} ==="
echo "=== PDFs a procesar: $(find "$PDFS" -maxdepth 1 -name '*.pdf' | wc -l) ==="
echo "=== inicio: $(date) ==="

# Es reanudable: si el job se corta, volvé a encolarlo y omite los ya hechos.
python procesador_masivo.py "$PDFS"

# Arma el archivo canónico final (front-matter YAML + cuerpo Markdown) que pide el
# tutorial. Idempotente: se puede re-correr sin problema.
python "$HOME/rag-unlu/extractor/construir_canonicos.py" resultados_extractor

echo "=== fin: $(date) ==="
echo "=== salidas en $WORK/resultados_extractor ==="
for ext in md json yaml; do
  echo "  .$ext: $(find resultados_extractor -maxdepth 1 -name "*.$ext" | wc -l)"
done
echo "  canónicos: $(find resultados_extractor/canonicos -name '*.md' 2>/dev/null | wc -l)"

#!/usr/bin/env bash
#
# Deja el repositorio armado y listo para publicar en GitHub.
#
# Hace, en orden:
#   1. git init + primer commit con lo nuestro (ingesta, pipeline, docs, parches)
#   2. trae el extractor de los becarios con `git subtree`, CONSERVANDO su historial
#      (sus commits y su autoría quedan en el log de este repo)
#   3. aplica nuestras mejoras al extractor como commits propios encima
#
# No crea el repo en GitHub ni hace push: eso lo hacés vos al final (el script te
# imprime los comandos).
#
# Uso:  ./armar_repo.sh

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

UPSTREAM_EXTRACTOR="https://github.com/SebasUNLu/Extractor_pdf.git"
UPSTREAM_SCRAPERS="https://github.com/Frantamasi/Scraping-UNLU.git"

if [[ -d .git ]]; then
  echo "ERROR: acá ya hay un repo git. Si querés rehacerlo: rm -rf .git" >&2
  exit 1
fi

echo ">> 1/4  Inicializando repo y commiteando lo nuestro"
git init -q -b main
git add .gitignore README.md CREDITS.md armar_repo.sh ingesta pipeline patches docs
git commit -q -m "Ingesta del digesto UNLu, pipeline para Clementina y auditoría RAG

Trae el corpus del Drive a Clementina (rclone + rsync, verificado documento por
documento), prepara el entorno del cluster sin internet (wheels precompiladas) y
audita la salida del parseo como insumo de un RAG: chunkabilidad, filtrado por
metadata, integridad de contenido y enlaces entre normas."

echo ">> 2/4  Trayendo el extractor de los becarios (subtree, con su historial)"
git remote add extractor-upstream "$UPSTREAM_EXTRACTOR"
git fetch -q extractor-upstream
RAMA="$(git remote show extractor-upstream | sed -n 's/.*HEAD branch: //p')"
git subtree add --prefix=extractor extractor-upstream "$RAMA"

echo ">> 3/4  Trayendo los scrapers (subtree, con su historial)"
git remote add scrapers-upstream "$UPSTREAM_SCRAPERS"
git fetch -q scrapers-upstream
RAMA_S="$(git remote show scrapers-upstream | sed -n 's/.*HEAD branch: //p')"
git subtree add --prefix=scrapers scrapers-upstream "$RAMA_S"

echo ">> 4/4  Aplicando nuestras mejoras al extractor"
git apply --directory=extractor patches/extractor_pdf_clementina.patch
git add extractor
git commit -q -m "Extractor: portabilidad a Linux/SLURM y robustez para lotes grandes

- usa sys.executable en vez del literal \"python\" (que no existe en Linux moderno:
  sin esto fallaba el 100% de los archivos)
- calcula los workers con SLURM_CPUS_PER_TASK / afinidad de CPU, en vez de
  os.cpu_count(), que ve el nodo entero e ignora lo que asignó el planificador
- timeout por archivo, para que un PDF que cuelgue no bloquee al worker
- reanudación: omite los documentos que ya tienen su YAML canónico
- requirements: fitz no es PyMuPDF (es otro paquete); faltaban Levenshtein y tabulate"

git apply --directory=extractor patches/extractor_pdf_calidad.patch
git add extractor
git commit -q -m "Extractor: metadata más fiel a la especificación de los tutoriales

- fecha de emisión: elegir entre los candidatos usando el año del número de acto,
  en vez de tomar el primero (que suele ser una vigencia o el sello digital).
  Corrige el 10% de documentos que quedaban con el año equivocado
- signature_mode dentro del vocabulario embedded|separate_page
- ciudad normalizada (LUJÁN -> Luján), conservando acentos
- firmantes consolidados: une las variantes de una misma persona y prioriza la
  que trae el rol, en vez de volcar los candidatos crudos"

echo
echo "=== Listo. Historial: ==="
git log --oneline | head -12
echo
echo "Commits de los becarios preservados: $(git log --oneline | wc -l | tr -d ' ') commits en total"
echo
echo "Para publicarlo en GitHub (esto lo corrés vos):"
echo "    gh repo create rag-unlu --public --source=. --remote=origin --push"
echo "  o, si preferís crearlo a mano en github.com:"
echo "    git remote add origin git@github.com:<tu-usuario>/rag-unlu.git && git push -u origin main"

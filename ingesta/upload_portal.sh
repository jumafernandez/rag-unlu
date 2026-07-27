#!/usr/bin/env bash
#
# Sube los PDFs ya descargados a Clementina, a rag-unlu/data/portal (relativo al
# home del usuario en el cluster). Usa rsync sobre el alias SSH "clementina".
#
# REQUISITO: la VPN FortiGate tiene que estar levantada (la VPN corre en tu Mac).
# Chequeo previo:  nc -z 172.29.3.3 22
#
# Uso:
#   ./upload_portal.sh [SRC_DIR]
#
# Variables de entorno opcionales:
#   REMOTE=clementina                 (host del ~/.ssh/config)
#   DESTDIR=rag-unlu/data/portal      (ruta destino en el cluster)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$HERE/../data/portal}"
REMOTE="${REMOTE:-clementina}"
DESTDIR="${DESTDIR:-rag-unlu/data/portal}"

if [[ ! -d "$SRC" ]]; then
  echo "ERROR: no encuentro el directorio de origen $SRC" >&2
  exit 1
fi

# Verificar alcance de la VPN antes de intentar el rsync.
if ! nc -z -G 5 172.29.3.3 22 >/dev/null 2>&1; then
  echo "ERROR: no llego a Clementina (172.29.3.3:22). ¿Está levantada la VPN?" >&2
  exit 1
fi

n=$(find "$SRC" -maxdepth 1 -type f -name '*.pdf' | wc -l | tr -d ' ')
echo ">> Origen:  $SRC ($n PDFs)"
echo ">> Destino: $REMOTE:$DESTDIR"
echo

# Crear el directorio destino y sincronizar.
ssh "$REMOTE" "mkdir -p '$DESTDIR'"

# macOS trae rsync 2.6.9 (de Apple), que no soporta --info=progress2 ni
# --append-verify. Usamos esas opciones solo si hay un rsync 3.x (p.ej. brew).
# --partial-dir: openrsync escribe in-place, así que un corte con --partial a secas
#   deja el archivo truncado CON SU NOMBRE FINAL (.pdf). Con --partial-dir el pedazo
#   va a un subdirectorio oculto y nunca se confunde con un PDF completo.
# --timeout: sin esto, si la VPN se cae en caliente el socket queda medio abierto y
#   rsync se cuelga mudo (hasta ~2 h por el keepalive de macOS). Con timeout corta
#   con exit !=0 y set -e aborta, así el fallo es ruidoso y el re-run reanuda.
RSYNC_OPTS=(-avh --partial --partial-dir=.rsync-partial --timeout=300)
if rsync --version 2>/dev/null | head -1 | grep -qE 'version +3'; then
  RSYNC_OPTS+=(--info=progress2 --append-verify)
else
  echo ">> rsync viejo (Apple) detectado: uso --progress en vez de --info=progress2"
  RSYNC_OPTS+=(--progress)
fi

rsync "${RSYNC_OPTS[@]}" \
  --include='*.pdf' --exclude='*' \
  "$SRC"/ "$REMOTE:$DESTDIR"/

echo
echo ">> Verificación de conteo remoto:"
ssh "$REMOTE" "ls -1 '$DESTDIR'/*.pdf 2>/dev/null | wc -l"

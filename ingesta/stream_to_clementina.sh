#!/usr/bin/env bash
#
# Copia la carpeta Drive "Archivos portal" DIRECTO a Clementina, sin stagear en
# la Mac (rclone remoto-a-remoto: baja de Drive y sube por SFTP a la vez, con
# buffer en memoria de unos MB). Ideal porque el total (~8,5 GiB) no entra en el
# disco local.
#
# Requisitos:
#   - rclone con el remote de Drive configurado (por defecto "unludrive").
#   - VPN FortiGate levantada (para llegar a Clementina por SSH/SFTP).
#   - Acceso SSH a Clementina que ya funcione (alias "clementina").
#
# Uso:
#   ./stream_to_clementina.sh
#
# Variables opcionales:
#   RCLONE_REMOTE=unludrive
#   FOLDER_ID=1F8yOUefIDSny6ByPvtpHCt3_FjlW_hnQ
#   CLEM_HOST=172.29.3.3   CLEM_USER=jfernandez   CLEM_KEY=~/.ssh/clementina
#   DESTDIR=rag-unlu/data/portal        (ruta en Clementina, relativa al home)
#   TRANSFERS=8
#   CLEM_USE_AGENT=1                    (forzar uso del ssh-agent para la llave)

set -euo pipefail

SRC_REMOTE="${RCLONE_REMOTE:-unludrive}"
FOLDER_ID="${FOLDER_ID:-1F8yOUefIDSny6ByPvtpHCt3_FjlW_hnQ}"
CLEM_HOST="${CLEM_HOST:-172.29.3.3}"
CLEM_USER="${CLEM_USER:-jfernandez}"
CLEM_KEY="${CLEM_KEY:-$HOME/.ssh/clementina}"
KNOWN_HOSTS="${KNOWN_HOSTS:-$HOME/.ssh/known_hosts}"
DESTDIR="${DESTDIR:-rag-unlu/data/portal}"
TRANSFERS="${TRANSFERS:-8}"

command -v rclone >/dev/null 2>&1 || { echo "ERROR: rclone no está instalado." >&2; exit 1; }
rclone listremotes | grep -q "^${SRC_REMOTE}:$" || {
  echo "ERROR: no existe el remote '${SRC_REMOTE}' en rclone (ver SETUP_RCLONE.md)." >&2; exit 1; }

# VPN / alcance a Clementina.
if ! nc -z -G 5 "$CLEM_HOST" 22 >/dev/null 2>&1; then
  echo "ERROR: no llego a Clementina ($CLEM_HOST:22). ¿Está levantada la VPN?" >&2
  exit 1
fi

# Elegir cómo autenticar la llave: ssh-agent si tiene llaves cargadas, si no key_file.
if [[ -n "${CLEM_USE_AGENT:-}" ]] || ssh-add -l >/dev/null 2>&1; then
  KEYPART="key_use_agent=true"
  echo ">> Auth SFTP: ssh-agent"
else
  KEYPART="key_file=${CLEM_KEY}"
  echo ">> Auth SFTP: key_file=${CLEM_KEY}"
fi

SFTP=":sftp,host=${CLEM_HOST},user=${CLEM_USER},${KEYPART},known_hosts_file=${KNOWN_HOSTS}:${DESTDIR}"

echo ">> Origen:  ${SRC_REMOTE}:  (carpeta ${FOLDER_ID})"
echo ">> Destino: Clementina ${CLEM_USER}@${CLEM_HOST}:${DESTDIR}"
echo ">> Streaming (sin staging local). Es resumible: podés cortar y volver a correr."
echo

# --size-only: al reintentar, compara solo por tamaño (SFTP no expone hash y las
# modtimes de Drive no coinciden), así saltea lo ya subido sin re-subir.
rclone copy "${SRC_REMOTE}:" "$SFTP" \
  --drive-root-folder-id "$FOLDER_ID" \
  --drive-acknowledge-abuse \
  --size-only \
  --transfers "$TRANSFERS" --checkers "$TRANSFERS" \
  --retries 3 --low-level-retries 10 \
  -P --stats-one-line

echo
echo ">> Verificación de conteo remoto:"
rclone size "$SFTP" 2>/dev/null || true

#!/bin/bash
# Corre un comando que escribe su avance en un archivo (y que retoma desde lo escrito) y lo reinicia si se cuelga:
# si ARCHIVO no cambia en MINUTOS (contando desde el arranque del intento, que incluye la carga del modelo), mata el
# proceso y lo vuelve a lanzar, hasta INTENTOS veces. Con el 122B, la generación quedó colgada 3 horas en el segundo
# lote (la GPU esperando, sin error), y el Gemma 31B cayó siempre en el mismo lote.
# Uso: con_vigia.sh ARCHIVO MINUTOS INTENTOS comando [argumentos...]
set -u
f=$1; lim=$(( $2 * 60 )); intentos=$3; shift 3
for i in $(seq 1 "$intentos"); do
  echo "[vigía] $(date '+%F %T') intento $i de $intentos"
  "$@" &
  pid=$!; ini=$(date +%s); colgado=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 60
    ult=$ini
    if [ -f "$f" ]; then m=$(stat -c %Y "$f"); [ "$m" -gt "$ult" ] && ult=$m; fi
    if [ $(( $(date +%s) - ult )) -gt "$lim" ]; then
      echo "[vigía] $(date '+%F %T') $f sin cambios hace más de $(( lim / 60 )) min: mato el proceso $pid"
      kill -9 "$pid" 2>/dev/null; colgado=1; break
    fi
  done
  wait "$pid" 2>/dev/null; rc=$?
  if [ "$colgado" -eq 0 ] && [ "$rc" -eq 0 ]; then echo "[vigía] $(date '+%F %T') terminó bien"; exit 0; fi
  echo "[vigía] $(date '+%F %T') intento $i terminó con código $rc (colgado: $colgado)"
done
exit 1

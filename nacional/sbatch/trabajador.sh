#!/bin/bash
# Adaptado de doctorado-unsl/packages/d2f-multilingual/sbatch/d2f_trabajador.sh.
# Trabajador de un piloto de nodo entero: toma corridas de una cola y las ejecuta, una por vez, en los tiles de su
# máscara, hasta que la cola se vacía o se acaba el tiempo del piloto.
# Cada corrida es un archivo .env de pendientes/ con variables y un COMANDO. Mientras corre queda en tomadas/ con un
# latido (el archivo se toca cada LATIDO segundos). Al terminar pasa a hechas/; si se acaba el turno vuelve a
# pendientes/ y la retoma el trabajador que la tome después; si falla vuelve a pendientes/ y a la tercera falla pasa
# a fallidas/. Con ESPERAR_JOB=J la corrida no se toma hasta que sacct informe que el trabajo J de SLURM terminó.
# Con ESPERAR_ARCHIVO=RUTA no se toma hasta que exista ese archivo (p. ej. el resumen.json que el entrenamiento
# escribe al terminar: hace falta cuando el entrenamiento corre en varios tramos y el primero no deja el modelo).
# Las corridas se toman en orden alfabético del nombre del archivo, de modo que un prefijo numérico fija la prioridad.
# Uso (lo llama piloto.sbatch): trabajador.sh COLA MASCARA NOMBRE FIN
#   MASCARA: valor de ZE_AFFINITY_MASK (un tile, "3", o los dos de una GPU, "2,3"); FIN: segundos desde 1970
set -u
COLA=$1; MASCARA=$2; YO=$3; FIN=$4
TURNO=${TURNO:-0}                 # máximo de segundos por corrida antes de devolverla a la cola (0: hasta FIN)
LATIDO=${LATIDO:-300}
ESPERA_VACIA=${ESPERA_VACIA:-3600}  # cuánto espera con la cola vacía antes de terminar
HILOS=${HILOS:-8}                 # hilos de CPU por corrida (OMP y MKL); conviene núcleos del piloto / trabajadores
PY=${PY:-/home/jfernandez/rag-unlu-adaptacion/venv/bin/python}
ID=${SLURM_JOB_ID:-local}
export YO ID
cd "$COLA" || exit 1
mkdir -p pendientes tomadas hechas fallidas registros

log() { echo "[$(date '+%F %T')] $YO: $*"; }

terminado() {   # verdadero solo si sacct informa un estado final; una respuesta vacía cuenta como trabajo en curso
  local s
  s=$(sacct -j "$1" -n -X -o State 2>/dev/null | head -1 | tr -d ' ')
  case "$s" in ""|PENDING*|RUNNING*|REQUEUED*|SUSPENDED*|CONFIGURING*|COMPLETING*|RESIZING*|STAGE_OUT*) return 1 ;; esac
  return 0
}

elegir() {      # primera corrida pendiente en orden alfabético que no espere a un trabajo en curso ni a un archivo
  local f j a
  for f in $(ls pendientes | sort); do
    j=$(sed -n 's/^ESPERAR_JOB=//p' "pendientes/$f" | tail -1)
    if [ -n "$j" ] && ! terminado "$j"; then continue; fi
    a=$(sed -n 's/^ESPERAR_ARCHIVO=//p' "pendientes/$f" | tail -1)
    if [ -n "$a" ] && [ ! -e "$a" ]; then continue; fi
    echo "$f"; return 0
  done
  return 1
}

vacia=0; esperando=0
while :; do
  quedan=$(( FIN - $(date +%s) ))
  if [ "$quedan" -le 900 ]; then log "sin tiempo para otra corrida"; break; fi
  if ! f=$(elegir); then
    # COLA/.espera-maxima (segundos; sin archivo o 0: como antes) acorta la espera sin nada para tomar, para que un
    # piloto no retenga placas mientras las corridas que quedan esperan a un entrenamiento que tarda horas.
    tope=$(cat .espera-maxima 2>/dev/null | tr -cd '0-9'); tope=${tope:-0}
    if [ -z "$(ls pendientes)" ]; then
      lim=$ESPERA_VACIA; [ "$tope" -gt 0 ] && [ "$tope" -lt "$lim" ] && lim=$tope
      [ "$vacia" -ge "$lim" ] && { log "cola vacía"; break; }
      vacia=$(( vacia + 300 )); sleep 300
    else
      # hay corridas que esperan a un trabajo de SLURM o a un archivo
      [ "$tope" -gt 0 ] && [ "$esperando" -ge "$tope" ] && { log "solo quedan corridas que esperan: termino"; break; }
      esperando=$(( esperando + 300 )); sleep 300
    fi
    continue
  fi
  vacia=0; esperando=0
  t="tomadas/$f@$ID.$YO"
  mv "pendientes/$f" "$t" 2>/dev/null || continue    # otro trabajador la tomó primero
  touch "$t"                                         # primer latido: mv conserva la fecha vieja del archivo
  n=${f%.env}
  limite=$quedan
  if [ "$TURNO" -gt 0 ] && [ "$TURNO" -lt "$limite" ]; then limite=$TURNO; fi
  ( while sleep "$LATIDO"; do [ -e "$t" ] || exit 0; touch -c "$t" 2>/dev/null; done ) &   # -c: nunca crea el archivo
  latido=$!
  disp=$(ZE_AFFINITY_MASK=$MASCARA timeout 120 "$PY" -c "import torch; print(torch.xpu.device_count(), torch.xpu.get_device_name(0) if torch.xpu.device_count() else '-')" 2>/dev/null)
  log "toma $n por hasta $limite s, ZE_AFFINITY_MASK=$MASCARA, $HILOS hilos, dispositivos visibles: ${disp:-?}"
  inicio=$(date +%s)
  ZE_AFFINITY_MASK=$MASCARA OMP_NUM_THREADS=$HILOS MKL_NUM_THREADS=$HILOS \
    timeout --signal=TERM --kill-after=120 "$limite" \
    bash -c 'set -a; . "$1"; set +a; echo "[$(date "+%F %T")] $YO en $(hostname), job $ID, ZE_AFFINITY_MASK=$ZE_AFFINITY_MASK"; eval "$COMANDO"' _ "$t" \
    >> "registros/$n.out" 2>> "registros/$n.err"
  rc=$?
  kill "$latido" 2>/dev/null; wait "$latido" 2>/dev/null
  dur=$(( $(date +%s) - inicio ))
  case $rc in
    0)
      mv "$t" "hechas/$f"; log "terminó $n en $dur s" ;;
    124|137)
      mv "$t" "pendientes/$f"; log "fin de turno de $n tras $dur s: vuelve a la cola" ;;
    *)
      echo "# falla rc=$rc job=$ID $YO $(date '+%F %T') duracion=$dur" >> "$t"
      if [ "$(grep -c '^# falla' "$t")" -ge 3 ]; then
        mv "$t" "fallidas/$f"; log "$n falló por tercera vez (rc=$rc): pasa a fallidas"
      else
        mv "$t" "pendientes/$f"; log "$n falló (rc=$rc) tras $dur s: vuelve a la cola"
      fi
      if [ "$dur" -lt 600 ]; then log "falla en menos de 10 min: este trabajador se detiene"; break; fi ;;
  esac
done

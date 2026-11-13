#!/bin/bash
# Adaptado de doctorado-unsl/packages/d2f-multilingual/sbatch/d2f_piloto.sbatch (pilotos de nodo entero de D2F).
#SBATCH -p gpunode
#SBATCH -N 1
#SBATCH --gres=gpu:4
#SBATCH -c 64
#SBATCH --mem=480G
#SBATCH -t 2-00:00:00
#SBATCH --exclude=cn013,cn016,cn021,cn022,cn023,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH -o /home/jfernandez/rag-unlu-adaptacion/logs/%x_%j.out
#SBATCH -e /home/jfernandez/rag-unlu-adaptacion/logs/%x_%j.err
# Piloto de nodo entero: las 4 GPU (8 tiles) de un nodo, con un trabajador por tile o, con POR_TRABAJADOR=2, uno por
# GPU (para modelos que ocupan los dos tiles, como el parafraseo con Qwen 27B). Cada trabajador toma corridas de la
# cola con trabajador.sh hasta 10 min antes del final del piloto. Al arrancar devuelve a pendientes/ las corridas
# que quedaron tomadas por un piloto que ya terminó según sacct (y sin latido hace más de 30 min).
# Con menos GPU (por ejemplo --gres=gpu:1 y TILES=2) el piloto usa los tiles que se le asignan.
# Uso: sbatch -J NOMBRE [--export=ALL,POR_TRABAJADOR=2,TURNO=3600,TILES=8] piloto.sbatch COLA
set -u
COLA=$1; POR=${POR_TRABAJADOR:-1}; TILES=${TILES:-8}
D=/home/jfernandez/rag-unlu-adaptacion
export ZE_FLAT_DEVICE_HIERARCHY=FLAT
fin_slurm=$(date -d "$(squeue -h -j "$SLURM_JOB_ID" -o %e)" +%s 2>/dev/null) || fin_slurm=0
if [ "${fin_slurm:-0}" -le "$(date +%s)" ]; then fin_slurm=$(( $(date +%s) + 3600 )); fi
FIN=$(( fin_slurm - 600 ))
echo "piloto $SLURM_JOB_ID en $(hostname): cola $COLA, $POR tile(s) por trabajador, fin $(date -d @"$FIN" '+%F %T')"
n=$(timeout 300 $D/venv/bin/python -c "import torch; print(torch.xpu.device_count())" 2>/dev/null)
echo "tiles visibles: ${n:-?}"
if [ "${n:-0}" != "$TILES" ]; then echo "$(hostname): se esperaban $TILES tiles y hay ${n:-?}; el piloto no arranca" >&2; exit 3; fi
mkdir -p "$COLA/pendientes" "$COLA/tomadas"
for f in "$COLA"/tomadas/*; do
  [ -e "$f" ] || continue
  if [ $(( $(date +%s) - $(stat -c %Y "$f") )) -gt 1800 ]; then
    b=$(basename "$f")
    j=${b##*@}; j=${j%%.*}      # trabajo de SLURM que la tomó: solo se recupera si sacct informa que ya terminó
    s=$(sacct -j "$j" -n -X -o State 2>/dev/null | head -1 | tr -d ' ')
    case "$s" in ""|PENDING*|RUNNING*|REQUEUED*|SUSPENDED*|CONFIGURING*|COMPLETING*|RESIZING*|STAGE_OUT*) continue ;; esac
    [ -s "$f" ] || { rm -f "$f"; echo "descartada $b (vacía)"; continue; }
    mv "$f" "$COLA/pendientes/${b%@*}" 2>/dev/null && echo "recuperada ${b%@*} (estaba en $b, de un trabajo $s)"
  fi
done
for (( w=0; w<TILES/POR; w++ )); do
  if [ "$POR" -eq 1 ]; then m=$w; else m="$((2*w)),$((2*w+1))"; fi
  bash $D/codigo/nacional/sbatch/trabajador.sh "$COLA" "$m" "t$w" "$FIN" &
  sleep 30
done
wait
echo "piloto $SLURM_JOB_ID termina $(date '+%F %T')"

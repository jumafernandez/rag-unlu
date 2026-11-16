#!/bin/bash
#SBATCH -N 1
#SBATCH --gres=gpu:4
#SBATCH -c 16
#SBATCH --mem=300G
#SBATCH -t 05:00:00
#SBATCH --exclude=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn069,cn070,cn071,cn072
#SBATCH -o /home/jfernandez/rag-unlu-adaptacion/logs/%x_%j.out
#SBATCH -e /home/jfernandez/rag-unlu-adaptacion/logs/%x_%j.err
# Piloto de un solo trabajador con los 8 tiles del nodo, para los modelos que ocupan el nodo entero (Qwen 3.5 122B):
# toma las corridas de una cola propia con trabajador.sh, una por vez, hasta 10 min antes del final. Las corridas
# del 122B llevan en su COMANDO LIMINAR_COPIAS_POR_CPU=1 (copias entre tiles vía CPU; sin eso la generación se cuelga).
# Uso: sbatch -J NOMBRE -p gpunode,ayor [-t HH:MM:SS] piloto_entero.sbatch COLA
set -u
COLA=$1; D=/home/jfernandez/rag-unlu-adaptacion
export ZE_FLAT_DEVICE_HIERARCHY=FLAT
fin_slurm=$(date -d "$(squeue -h -j "$SLURM_JOB_ID" -o %e)" +%s 2>/dev/null) || fin_slurm=0
if [ "${fin_slurm:-0}" -le "$(date +%s)" ]; then fin_slurm=$(( $(date +%s) + 3600 )); fi
FIN=$(( fin_slurm - 600 ))
echo "piloto entero $SLURM_JOB_ID en $(hostname): cola $COLA, fin $(date -d @"$FIN" '+%F %T')"
n=$(timeout 300 $D/venv/bin/python -c "import torch; print(torch.xpu.device_count())" 2>/dev/null)
echo "tiles visibles: ${n:-?}"
if [ "${n:-0}" != 8 ]; then echo "$(hostname): se esperaban 8 tiles y hay ${n:-?}; el piloto no arranca" >&2; exit 3; fi
mkdir -p "$COLA/pendientes" "$COLA/tomadas"
for f in "$COLA"/tomadas/*; do   # corridas que quedaron tomadas por un piloto que ya terminó (como en piloto.sbatch)
  [ -e "$f" ] || continue
  if [ $(( $(date +%s) - $(stat -c %Y "$f") )) -gt 1800 ]; then
    b=$(basename "$f"); j=${b##*@}; j=${j%%.*}
    s=$(sacct -j "$j" -n -X -o State 2>/dev/null | head -1 | tr -d ' ')
    case "$s" in ""|PENDING*|RUNNING*|REQUEUED*|SUSPENDED*|CONFIGURING*|COMPLETING*|RESIZING*|STAGE_OUT*) continue ;; esac
    [ -s "$f" ] || { rm -f "$f"; echo "descartada $b (vacía)"; continue; }
    mv "$f" "$COLA/pendientes/${b%@*}" 2>/dev/null && echo "recuperada ${b%@*} (estaba en $b, de un trabajo $s)"
  fi
done
HILOS=16 bash $D/codigo/nacional/sbatch/trabajador.sh "$COLA" 0,1,2,3,4,5,6,7 t0 "$FIN"
echo "piloto entero $SLURM_JOB_ID termina $(date '+%F %T')"

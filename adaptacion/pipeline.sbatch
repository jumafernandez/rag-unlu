#!/bin/bash
# La adaptación completa en un solo trabajo: ocupa una única tarea de la cuota del proyecto
# y pasa una sola vez por la cola. Primero prueba en minutos el entrenamiento y la
# generación sobre los datos del canario, para no descubrir una falla después de horas de
# armado de datos; después corre las tres etapas reales. Cada etapa reanuda lo que haya
# quedado hecho, así que si el trabajo se corta alcanza con volver a encolarlo.
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     sbatch adaptacion/pipeline.sbatch
#
#SBATCH --job-name=dig-adaptacion
#SBATCH --partition=batch,ayor
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --mem=480G
#SBATCH --time=24:00:00
#SBATCH --exclude=cn013,cn021,cn023,cn024,cn042,cn045,cn069,cn071,cn072
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
R=/home/jfernandez/rag-unlu-adaptacion
L=$R/logs
paso() { echo "=== $(date '+%d/%m %H:%M') $*"; }
falla() { echo "FALLA: $*"; exit 1; }
# Cada proceso termina con LISTO, o avisa al empezar que no le queda nada pendiente.
completos() { for f in "$@"; do grep -qE "LISTO|: 0 pendientes|, 0 pendientes" "$f" || return 1; done; }

# Los canarios corren una sola vez: si ya pasaron, una reanudación va directo a lo pendiente.
if grep -qs ENTRENAMIENTO-OK $L/pipeline-canario-lora.log && completos $R/respuestas-canario/9b-*.log 2>/dev/null; then
  paso "1-2/5 canarios ya aprobados en una corrida anterior"
else
  paso "1/5 canario de entrenamiento"
  SALIDA=$R/adaptadores/canario DATOS=$R/datos-canario \
    EXTRA="--particiones prueba --acum 1 --epocas 3 --eval-cada 3 --guardar-cada 3" \
    bash adaptacion/entrenar_lora.sbatch > $L/pipeline-canario-lora.log 2>&1
  grep -q ENTRENAMIENTO-OK $L/pipeline-canario-lora.log || falla "canario de entrenamiento"
  grep -E "ejemplos:|paso 3/|memoria" $L/pipeline-canario-lora.log | tail -3

  paso "2/5 canario de generación"
  SALIDA=$R/respuestas-canario DATOS=$R/datos-canario ADAPTADOR=$R/adaptadores/canario/final \
    bash adaptacion/generar_respuestas.sbatch > $L/pipeline-canario-gen.log 2>&1
  completos $R/respuestas-canario/9b-*.log || falla "canario de generación"
  cat $R/respuestas-canario/9b-*_?.jsonl | wc -l
fi

paso "3/5 datos"
SALIDA=$R/datos bash adaptacion/armar_datos.sbatch > $L/pipeline-datos.log 2>&1
completos $R/datos/parte-?.log || falla "armado de datos"
cat $R/datos/ejemplos_*.jsonl | wc -l

paso "4/5 entrenamiento"
SALIDA=$R/adaptadores/9b-digesto DATOS=$R/datos \
  bash adaptacion/entrenar_lora.sbatch > $L/pipeline-lora.log 2>&1
grep -q ENTRENAMIENTO-OK $L/pipeline-lora.log || falla "entrenamiento"

paso "5/5 respuestas de prueba"
SALIDA=$R/respuestas DATOS=$R/datos ADAPTADOR=$R/adaptadores/9b-digesto/final \
  bash adaptacion/generar_respuestas.sbatch > $L/pipeline-gen.log 2>&1
completos $R/respuestas/9b-*.log || falla "generación de respuestas"
paso "ADAPTACION-OK"

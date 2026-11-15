#!/bin/bash
# Generación completa de la aumentación (aumentar.py) en Clementina. Correr parado en ~/rag-unlu-adaptacion/codigo.
#   1. Fichas corregidas: 4 trabajos de una GPU y uno de CPU que junta las partes (con el control de números).
#   2. Pedidos generales: un trabajo de una GPU.
#   3. Cola de pilotos (trabajador.sh, una GPU por trabajador): versiones (esperan a las fichas), alcance de las
#      preguntas viejas y del benchmark, "no sé", identidad, benchmark de las normas centrales (espera a las fichas) y
#      el replay de los modelos de una GPU (espera a los pedidos generales).
#   4. Replay del 122B: un nodo entero (espera a los pedidos generales).
# Uso: bash nacional/sbatch/lanzar_aumentacion.sh [PILOTOS] [PARTES_VERSIONES]
set -euo pipefail
PILOTOS=${1:-5}
PV=${2:-20}
N=/data/contrib/pci_146/rag-unlu-adaptacion/nacional
A=$N/aumentacion
M=/data/contrib/pci_146/hf-models
COD=/home/jfernandez/rag-unlu-adaptacion/codigo
PY=/home/jfernandez/rag-unlu-adaptacion/venv/bin/python
X=cn016,cn022,cn024,cn025,cn029,cn042,cn045,cn049,cn061,cn069,cn070,cn071,cn072
GEN=$N/gen/documentos-1a,$N/gen/documentos-1b,$N/gen/fragmentos
ENV="export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1"
AUM="$PY -m nacional.aumentar"
COLA=$N/cola-aumentacion
mkdir -p "$COLA/pendientes" ../logs

uno() {   # trabajo de una GPU: nombre, tiempo, comando
  sbatch --parsable -p gpunode,ayor --gres=gpu:1 -c 16 --mem=160G -t "$2" --nice=50 --exclude=$X -J "$1" \
    -o ../logs/%x_%j.out -e ../logs/%x_%j.err --wrap "$ENV; cd $COD && $3"
}

if [ -z "${J:-}" ]; then
# 1. fichas corregidas
ms=()
for p in 0 1 2 3; do
  ms+=($(uno nac-aum-meta-$p 02:00:00 "$AUM metadatos --entrada $A/fichas-crudas.jsonl --salida $A/metadatos --parte $p --partes 4"))
done
J=$(sbatch --parsable -p cpunode -c 4 --mem=16G -t 00:20:00 --dependency=afterok:$(IFS=:; echo "${ms[*]}") \
  -J nac-aum-juntar -o ../logs/%x_%j.out -e ../logs/%x_%j.err \
  --wrap "cd $COD && $AUM juntar --salida $A/metadatos --fichas $A/fichas-crudas.jsonl")
echo "fichas: ${ms[*]} · juntar: $J"

# 2. pedidos generales (14 categorías × 22 tandas × 10 pedidos)
G=$(uno nac-aum-generales 02:00:00 "$AUM generales --n 10 --tandas 22 --salida $A/generales")
echo "pedidos generales: $G"
fi   # con J y G en el entorno se retoma desde la cola (trabajos ya lanzados)

# 3. cola
encolar() {   # archivo, nombre, comando [, trabajo a esperar]
  { echo "NOMBRE=$2"; echo "COMANDO='$ENV; cd $COD && $3'"; if [ -n "${4:-}" ]; then echo "ESPERAR_JOB=$4"; fi; } \
    > "$COLA/pendientes/$1.env"
}
for p in $(seq 0 $((PV - 1))); do
  encolar "10_versiones_$(printf %02d $p)" "versiones-$p" \
    "$AUM versiones --datos $N --metadatos $A/metadatos --salida $A/versiones --parte $p --partes $PV" "$J"
done
for p in 0 1 2 3 4 5; do
  encolar "20_alcance_$p" "alcance-$p" \
    "$AUM alcance --generados $GEN --fichas $A/fichas-crudas.jsonl --salida $A/alcance --parte $p --partes 6"
done
encolar 21_alcance_benchmark alcance-benchmark \
  "$AUM alcance --entrada $N/linea_base-preguntas.jsonl --fichas $A/fichas-crudas.jsonl --salida $A/alcance-benchmark"
encolar 22_nose_0 nose-0 "$AUM nose --entrada $A/nose-semillas.jsonl --salida $A/nose"
encolar 23_identidad identidad "$AUM identidad --n 10 --tandas 30 --salida $A/identidad"
encolar 24_bench_nucleo bench-nucleo "$AUM benchnucleo --datos $N --metadatos $A/metadatos --n 2 --salida $A/bench-nucleo" "$J"
# replay de los modelos que entran en una GPU (en partes, para repartirlo entre trabajadores)
replay() {   # nombre, carpeta del modelo, partes, lote [, prefijo de entorno]
  for p in $(seq 0 $(($3 - 1))); do
    encolar "40_replay_$1_$p" "replay-$1-$p" \
      "${5:-}$AUM replay --entrada $A/generales --modelo $M/$2 --nombre $1 --salida $A/replay --max-nuevos 1024 --lote $4 --parte $p --partes $3" "$G"
  done
}
MC="export PYTHONPATH=/home/jfernandez/rag-unlu-adaptacion/extra-mistral; "
replay gemma-4-e4b gemma-4-E4B-it 2 32
replay ministral-3-8b Ministral-3-8B-Instruct-2512-BF16 2 32 "$MC"
replay qwen3.5-9b Qwen3.5-9B 2 32
replay gemma-4-12b gemma-4-12B-it 2 32
replay mistral-small-3.2-24b Mistral-Small-3.2-24B-Instruct-2506 2 16 "$MC"
replay qwen3.5-27b Qwen3.5-27B 3 16
replay gemma-4-31b gemma-4-31B-it 3 16
echo "cola: $(ls $COLA/pendientes | wc -l) corridas"

# pilotos de nodo entero, una GPU (dos tiles) por trabajador
for k in $(seq 1 "$PILOTOS"); do
  sbatch -J nac-aum-piloto-$k -t 12:00:00 --export=ALL,POR_TRABAJADOR=2,TILES=8 $COD/nacional/sbatch/piloto.sbatch "$COLA"
done

# 4. replay del 122B en un nodo entero (hasta 800 pedidos)
sbatch -p gpunode,ayor --gres=gpu:4 -c 32 --mem=480G -t 10:00:00 --exclude=$X --dependency=afterok:$G \
  -J nac-aum-replay-122b -o ../logs/%x_%j.out -e ../logs/%x_%j.err \
  --wrap "$ENV; cd $COD && $AUM replay --entrada $A/generales --modelo $M/Qwen3.5-122B-A10B --nombre qwen3.5-122b-a10b --salida $A/replay --max-nuevos 1024 --lote 16 --limite 800"

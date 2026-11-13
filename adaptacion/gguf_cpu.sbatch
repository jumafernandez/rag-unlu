#!/bin/bash
# Lleva los dos 9B (sin ajustar y ajustado) a llama.cpp y mide el ajustado en un servidor
# sin GPU, en un nodo de CPU y sin tocar ninguna GPU:
#   1. compila llama.cpp (llama-server y llama-quantize) para AVX2/FMA, el juego de
#      instrucciones de cualquier servidor x86 de la última década, sin las extensiones
#      AVX-512 propias del Xeon Max (que además el ensamblador del sistema no conoce);
#   2. guarda cada 9B como modelo de Hugging Face (el ajustado, con el LoRA fusionado), lo
#      convierte a GGUF y lo cuantiza a 4 bits (Q4_K_M), el formato que se lleva a la M2;
#   3. emula un servidor convencional: llama-server limitado a 4 núcleos (como la VM de
#      LICDIA) y a 8, y responde las primeras preguntas de prueba de a una, con streaming,
#      con el mismo cliente que midió gpt-4.1-mini (respuestas_api.py).
# Cada paso saltea lo que ya esté hecho.
#
# Encolar parado en ~/rag-unlu-adaptacion/codigo:
#     sbatch adaptacion/gguf_cpu.sbatch
#
#SBATCH --job-name=dig-gguf
#SBATCH --partition=cpunode
#SBATCH --cpus-per-task=32
#SBATCH --mem=160G
#SBATCH --time=12:00:00
#SBATCH --output=../logs/%x_%j.out
#SBATCH --error=../logs/%x_%j.err

set -uo pipefail
BASE="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$BASE" || exit 1
R=/home/jfernandez/rag-unlu-adaptacion
L=$R/llama.cpp
M=/data/contrib/pci_146/rag-unlu-adaptacion/gguf
PY=$R/venv/bin/python
PREGUNTAS="${PREGUNTAS:-20}"
mkdir -p "$M" "$R/tiempos"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset OPENAI_API_KEY RAG_LLM_BASE
paso() { echo "=== $(date '+%d/%m %H:%M') $*"; }
falla() { echo "FALLA: $*"; exit 1; }

paso "nodo $(hostname) · $(lscpu | sed -n 's/^Model name: *//p') · $(nproc) núcleos asignados"

paso "1/3 compilar llama.cpp"
if [ ! -x "$L/build/bin/llama-server" ] || [ ! -x "$L/build/bin/llama-quantize" ]; then
  module load cmake
  cmake -S "$L" -B "$L/build" -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DGGML_AVX512=OFF \
    -DLLAMA_OPENSSL=OFF -DHF_ENABLED=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF > "$M/compilar.log" 2>&1 &&
  cmake --build "$L/build" -j 32 --target llama-server llama-quantize >> "$M/compilar.log" 2>&1 ||
    { tail -n 20 "$M/compilar.log"; falla "compilación"; }
fi

paso "2/3 convertir a GGUF de 4 bits"
for nombre in 9b-base 9b-digesto; do
  q="$M/$nombre-Q4_K_M.gguf"
  [ -s "$q" ] && { echo "$nombre: ya está"; continue; }
  hf="$M/$nombre-hf"
  extra=""
  [ "$nombre" = 9b-digesto ] && extra="--adaptador $R/adaptadores/9b-digesto/final"
  if [ ! -s "$hf/config.json" ]; then
    "$PY" -m adaptacion.fusionar --salida "$hf" $extra 2>&1 | grep -v "frozen site" | tail -n 3
    [ -s "$hf/config.json" ] || falla "fusionar $nombre"
  fi
  "$PY" "$L/convert_hf_to_gguf.py" "$hf" --outtype bf16 --outfile "$M/$nombre-bf16.gguf" > "$M/convertir-$nombre.log" 2>&1 ||
    { tail -n 20 "$M/convertir-$nombre.log"; falla "convertir $nombre"; }
  "$L/build/bin/llama-quantize" "$M/$nombre-bf16.gguf" "$q" Q4_K_M 32 > "$M/cuantizar-$nombre.log" 2>&1 ||
    { tail -n 20 "$M/cuantizar-$nombre.log"; falla "cuantizar $nombre"; }
  rm -f "$M/$nombre-bf16.gguf"
  ls -la "$q"
done

paso "3/3 servidor sin GPU emulado: 9B ajustado, $PREGUNTAS preguntas"
# Los números de CPU asignados por SLURM no empiezan necesariamente en 0: se toman de la
# afinidad del propio trabajo. El servidor usa los primeros n; el cliente, el último.
CPUS=($("$PY" -c "import os; print(' '.join(map(str, sorted(os.sched_getaffinity(0)))))" 2>/dev/null))
echo "CPU asignadas: ${#CPUS[@]}"
for n in 4 8; do
  taskset -c "$(IFS=,; echo "${CPUS[*]:0:$n}")" "$L/build/bin/llama-server" -m "$M/9b-digesto-Q4_K_M.gguf" --host 127.0.0.1 --port 8180 \
    -c 16384 -np 1 -t $n -tb $n --jinja --chat-template-kwargs '{"enable_thinking":false}' \
    > "$M/servidor-cpu$n.log" 2>&1 &
  srv=$!
  for i in $(seq 120); do curl -sf http://127.0.0.1:8180/health > /dev/null && break; sleep 5; done
  curl -sf http://127.0.0.1:8180/health > /dev/null || { kill $srv; tail -n 20 "$M/servidor-cpu$n.log"; falla "servidor con $n núcleos"; }
  taskset -c "${CPUS[-1]}" "$PY" -m adaptacion.respuestas_api --datos "$R/datos" --salida "$R/tiempos" \
    --modelo 9b-digesto --nombre "cpu$n-9b-digesto" --base-url http://127.0.0.1:8180/v1 \
    --stream --hilos 1 --limite "$PREGUNTAS" 2>&1 | grep -v "frozen site" | tail -n 2
  kill $srv; wait $srv 2>/dev/null
done
paso "GGUF-OK"

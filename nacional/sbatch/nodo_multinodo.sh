#!/bin/bash
# Lo corre srun en cada nodo de un entrenamiento en varios nodos (HSDP): un torchrun por nodo, con el rendezvous
# estático en el primer nodo. Las variables (PY, N, NPROC, MAESTRO, PUERTO, SAL, ARGS y, si están, GENERADOS y
# ABIERTAS) llegan del sbatch.
# Entre nodos, oneCCL y gloo van por TCP sobre la interfaz de InfiniBand (IPoIB) si el nodo la tiene.
set -uo pipefail
ulimit -n "$(ulimit -Hn)"
NUCLEOS=$($PY -c "import os; print(','.join(map(str, sorted(os.sched_getaffinity(0))[:$NPROC])))")
export CCL_WORKER_COUNT=1 CCL_WORKER_AFFINITY=$NUCLEOS CCL_PROCESS_LAUNCHER=torchrun CCL_ZE_IPC_EXCHANGE=sockets
export FI_PROVIDER=${FI_PROVIDER:-tcp} TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 OMP_NUM_THREADS=4
unset OPENAI_API_KEY RAG_LLM_BASE
# interfaz de InfiniBand con dirección IPv4 (IPoIB); si no hay, gloo y oneCCL usan la interfaz por omisión
IF=$(ip -4 -o addr show 2>/dev/null | awk '$2 ~ /^ib/ {print $2; exit}')
if [ -n "$IF" ]; then export FI_TCP_IFACE=$IF GLOO_SOCKET_IFNAME=$IF; fi
if [ -n "${MISTRAL_COMMON:-}" ]; then export PYTHONPATH=/home/jfernandez/rag-unlu-adaptacion/extra-mistral${PYTHONPATH:+:$PYTHONPATH}; fi
echo "$(hostname): nodo $SLURM_NODEID de $SLURM_NNODES · interfaz ${IF:-por omisión} · tiles $($PY -c 'import torch; print(torch.xpu.device_count())' 2>/dev/null)"
exec $PY -m torch.distributed.run --nnodes "$SLURM_NNODES" --node_rank "$SLURM_NODEID" --nproc_per_node "$NPROC" \
  --master_addr "$MAESTRO" --master_port "$PUERTO" -m nacional.entrenar \
  --datos "$N" --generados "${GENERADOS:-$N/gen/documentos-1a,$N/gen/documentos-1b,$N/gen/fragmentos}" \
  --abiertas "${ABIERTAS:-$N/abiertas}" \
  --estrategia fsdp --salida "$SAL" $ARGS

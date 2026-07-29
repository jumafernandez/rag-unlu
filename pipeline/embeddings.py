#!/usr/bin/env python3
"""
Genera los embeddings densos de los chunks y arma el índice.

Modelo: BGE-m3 vía sentence-transformers. Se eligió por dos razones concretas del dominio:
acepta contextos largos (un artículo entero entra sin recortar) y rinde bien en español,
que es donde muchos modelos de recuperación flojean.

La otra mitad de la recuperación —la coincidencia léxica exacta, que es la que encuentra
"RESHCS 893/2025" o "expediente 175/2008"— NO sale de acá: se resuelve con BM25 en
`backend/recuperacion.py`. Se prefirió BM25 antes que los pesos esparsos aprendidos del
propio modelo porque es recuperación clásica, auditable y explicable línea por línea, sin
depender de que un modelo haya aprendido bien a pesar identificadores.

Corre en la GPU Intel de Clementina (backend `xpu`) si está disponible; si no, en CPU.

El cluster no tiene internet: los pesos deben estar en disco (ver --modelo y
03_preparar_embeddings.sh).

Uso:
    python embeddings.py --chunks chunks.jsonl --salida indice/ --modelo RUTA
"""

import argparse
import json
import os
import time

import numpy as np


def elegir_dispositivo(preferido=None):
    import torch
    if preferido:
        return preferido
    # Las GPU de Clementina son Intel Max 1550: el backend es 'xpu', no 'cuda'.
    if hasattr(torch, 'xpu') and torch.xpu.is_available():
        return 'xpu'
    if torch.cuda.is_available():
        return 'cuda'
    # GPU integrada de los Mac con chip Apple. Se contempla para poder incorporar tandas
    # chicas desde el escritorio sin depender de la cola del clúster: para unos pocos
    # miles de documentos, esperar el turno cuesta más que calcular.
    if getattr(torch.backends, 'mps', None) is not None and torch.backends.mps.is_available():
        return 'mps'
    return 'cpu'


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--chunks', required=True)
    p.add_argument('--salida', required=True)
    p.add_argument('--modelo', default='BAAI/bge-m3', help='id de HuggingFace o ruta local')
    p.add_argument('--batch', type=int, default=32)
    p.add_argument('--dispositivo', default=None, help='xpu | cuda | cpu (autodetecta)')
    p.add_argument('--max-tokens', type=int, default=1024,
                   help='techo por chunk; 1024 cubre un artículo y es mucho más rápido que 8192')
    p.add_argument('--limite', type=int, default=None, help='solo N chunks, para probar')
    a = p.parse_args()

    os.makedirs(a.salida, exist_ok=True)

    chunks = []
    with open(a.chunks, encoding='utf-8') as fh:
        for linea in fh:
            if linea.strip():
                chunks.append(json.loads(linea))
    if a.limite:
        chunks = chunks[: a.limite]
    if not chunks:
        raise SystemExit('no hay chunks')

    disp = elegir_dispositivo(a.dispositivo)
    print(f'chunks     : {len(chunks)}', flush=True)
    print(f'modelo     : {a.modelo}', flush=True)
    print(f'dispositivo: {disp}', flush=True)

    from sentence_transformers import SentenceTransformer

    modelo = SentenceTransformer(a.modelo, device=disp)
    modelo.max_seq_length = a.max_tokens

    textos = [c['texto_indexado'] for c in chunks]
    t0 = time.time()
    densos = modelo.encode(
        textos,
        batch_size=a.batch,
        normalize_embeddings=True,   # así la similitud coseno es un producto punto
        show_progress_bar=True,
        convert_to_numpy=True,
    ).astype(np.float32)
    dur = time.time() - t0

    np.save(os.path.join(a.salida, 'densos.npy'), densos)

    # La metadata de cada chunk viaja con el índice: sin ella una respuesta no se puede
    # rastrear hasta su documento.
    with open(os.path.join(a.salida, 'chunks.jsonl'), 'w', encoding='utf-8') as fh:
        for c in chunks:
            c.pop('texto_indexado', None)
            fh.write(json.dumps(c, ensure_ascii=False) + '\n')

    with open(os.path.join(a.salida, 'indice.json'), 'w', encoding='utf-8') as fh:
        json.dump({'modelo': a.modelo, 'chunks': len(chunks),
                   'dimension': int(densos.shape[1]), 'max_tokens': a.max_tokens,
                   'dispositivo': disp, 'segundos': round(dur, 1),
                   'lexico': 'BM25 (se construye al cargar el índice)'}, fh, indent=2)

    print(f'\nlisto en {dur/60:.1f} min  ({len(chunks)/max(1e-6, dur):.1f} chunks/s)')
    print(f'  densos: {densos.shape} -> {densos.nbytes/1e6:.0f} MB')
    print(f'  -> {a.salida}')


if __name__ == '__main__':
    main()

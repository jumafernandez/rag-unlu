"""Incorpora una tanda nueva de fragmentos y vectores al índice existente.

La regla que gobierna todo: **el fragmento i de chunks.jsonl es la fila i de densos.npy**.
No hay ningún identificador que los ate, solo la posición. Por eso este script concatena
las dos cosas en el mismo orden y en la misma corrida, y verifica antes y después.

Rechaza la operación si los tamaños no coinciden, en lugar de escribir un índice torcido:
un desalineamiento no se nota al usarlo ---el sistema responde igual de rápido--- pero
devuelve el texto de un acto con el vector de otro.

Uso:
    python -m pipeline.fusionar_indice \\
        --chunks-nuevos data/chunks-incremental.jsonl \\
        --densos-nuevos indice/densos-incremental.npy
"""
import argparse
import json
import os
import shutil
import time

import numpy as np


def contar_lineas(ruta):
    with open(ruta, encoding='utf-8') as f:
        return sum(1 for _ in f)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--indice', default='indice')
    p.add_argument('--chunks-nuevos', required=True)
    p.add_argument('--densos-nuevos', required=True)
    p.add_argument('--aplicar', action='store_true')
    a = p.parse_args()

    chunks = os.path.join(a.indice, 'chunks.jsonl')
    densos = os.path.join(a.indice, 'densos.npy')

    n_viejo = contar_lineas(chunks)
    v_viejo = np.load(densos, mmap_mode='r')
    n_nuevo = contar_lineas(a.chunks_nuevos)
    v_nuevo = np.load(a.densos_nuevos, mmap_mode='r')

    print(f'actual : {n_viejo} fragmentos · {v_viejo.shape}')
    print(f'nuevo  : {n_nuevo} fragmentos · {v_nuevo.shape}')

    problemas = []
    if n_viejo != v_viejo.shape[0]:
        problemas.append(f'el índice actual ya está desalineado: {n_viejo} vs {v_viejo.shape[0]}')
    if n_nuevo != v_nuevo.shape[0]:
        problemas.append(f'la tanda nueva no coincide: {n_nuevo} vs {v_nuevo.shape[0]}')
    if v_viejo.shape[1] != v_nuevo.shape[1]:
        problemas.append(f'dimensiones distintas: {v_viejo.shape[1]} vs {v_nuevo.shape[1]}')

    # Documentos repetidos: incorporar dos veces el mismo acto no rompe nada, pero infla el
    # índice y hace que una consulta gaste posiciones en fragmentos duplicados.
    ya = set()
    with open(chunks, encoding='utf-8') as f:
        for linea in f:
            ya.add(json.loads(linea).get('documento'))
    repetidos = set()
    with open(a.chunks_nuevos, encoding='utf-8') as f:
        for linea in f:
            d = json.loads(linea).get('documento')
            if d in ya:
                repetidos.add(d)
    if repetidos:
        problemas.append(f'{len(repetidos)} documentos de la tanda ya están en el índice '
                         f'(ej: {sorted(repetidos)[:3]})')

    if problemas:
        print('\nNO se fusiona:')
        for x in problemas:
            print('  -', x)
        raise SystemExit(1)

    print(f'\nresultado: {n_viejo + n_nuevo} fragmentos')
    if not a.aplicar:
        print('(verificación: no se escribió nada. Usar --aplicar)')
        return

    t0 = time.time()
    for ruta in (chunks, densos):
        respaldo = ruta + '.antes-de-fusionar'
        if not os.path.exists(respaldo):
            shutil.copy2(ruta, respaldo)
    print(f'respaldos: {chunks}.antes-de-fusionar y {densos}.antes-de-fusionar')

    # Primero los vectores: si algo falla, el jsonl sigue coincidiendo con el npy viejo.
    juntos = np.vstack([np.load(densos), np.load(a.densos_nuevos)]).astype(np.float32)
    np.save(densos + '.tmp.npy', juntos)
    os.replace(densos + '.tmp.npy', densos)

    with open(chunks, 'a', encoding='utf-8') as destino:
        with open(a.chunks_nuevos, encoding='utf-8') as origen:
            for linea in origen:
                destino.write(linea)

    final_n = contar_lineas(chunks)
    final_v = np.load(densos, mmap_mode='r').shape
    print(f'\nfusionado en {time.time()-t0:.0f}s: {final_n} fragmentos · {final_v}')
    if final_n != final_v[0]:
        raise SystemExit(f'ERROR: quedó desalineado ({final_n} vs {final_v[0]})')
    print('alineado')
    print('\nfalta: actualizar_metadata (para las URL de los actos nuevos) y construir_indice')


if __name__ == '__main__':
    main()

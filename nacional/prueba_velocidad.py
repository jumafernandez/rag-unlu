#!/usr/bin/env python3
"""Velocidad del maestro (Qwen 3.5 27B) al generar versiones: con o sin los núcleos de flash-linear-attention (fla)
y con distintos presupuestos de tokens por lote. Usa los mismos pedidos que aumentar.py versiones sobre una muestra
fija de fragmentos y escribe las salidas, para comparar velocidad y texto.

    python -m nacional.prueba_velocidad --datos N --metadatos DIR --fragmentos 16 [--fla] [--presupuesto 20000] --salida DIR
"""
import argparse
import json
import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True)
    ap.add_argument('--metadatos', required=True)
    ap.add_argument('--salida', required=True)
    ap.add_argument('--modelo', default='/data/contrib/pci_146/hf-models/Qwen3.5-27B')
    ap.add_argument('--fragmentos', type=int, default=16)
    ap.add_argument('--presupuesto', type=int, default=20000)
    ap.add_argument('--max-nuevos', type=int, default=3000)
    ap.add_argument('--fla', action='store_true')
    a = ap.parse_args()
    if a.fla:   # antes de crear el modelo: reemplaza la atención lineal de Qwen 3.5 por los núcleos de fla
        from nacional.entrenar import activar_fla
        activar_fla()
    from nacional import aumentar as A
    from nacional.generar import n_preguntas
    from adaptacion.armar_datos import Maestro
    frags = A.fragmentos_utiles(A.leer(os.path.join(a.datos, 'fragmentos.jsonl')))
    frags = [f for k, f in enumerate(frags) if k % 500 == 7][:a.fragmentos]   # muestra fija y repartida
    cab = {r['id']: r['cabecera'] for r in A.leer(os.path.join(a.metadatos, 'metadatos.jsonl'))}
    msgs = []
    for f in frags:
        for v in A.PEDIDOS:
            pedido = A.PEDIDOS[v].format(n=n_preguntas(f['texto']), nombre='', indice='')
            msgs.append(A.mensajes(A.CONTEXTO.format(ficha=cab.get(f['id']) or f['documento'], donde=A.donde(f),
                                                     texto=f['texto']) + pedido))
    t0 = time.time()
    m = Maestro(a.modelo, a.max_nuevos)
    carga = time.time() - t0
    t1 = time.time()
    res = m.generar(msgs, presupuesto=a.presupuesto)
    seg = time.time() - t1
    tokens = sum(len(m.tok(t, add_special_tokens=False)['input_ids']) for t, _, _ in res)
    nombre = f'{"fla" if a.fla else "sin-fla"}-{a.presupuesto}'
    os.makedirs(a.salida, exist_ok=True)
    with open(os.path.join(a.salida, nombre + '.jsonl'), 'w', encoding='utf-8') as out:
        for t, termino, n in res:
            out.write(json.dumps({'texto': t, 'termino': termino, 'tokens_prompt': n}, ensure_ascii=False) + '\n')
    resumen = {'config': nombre, 'pedidos': len(msgs), 'tokens_generados': tokens, 'segundos': round(seg, 1),
               'tokens_por_segundo': round(tokens / max(seg, 1e-6), 1), 'carga_s': round(carga, 1),
               'terminados': sum(1 for _, t, _ in res if t)}
    json.dump(resumen, open(os.path.join(a.salida, nombre + '.json'), 'w'), indent=1)
    print('RESUMEN', json.dumps(resumen), flush=True)


if __name__ == '__main__':
    main()

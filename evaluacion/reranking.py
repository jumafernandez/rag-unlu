#!/usr/bin/env python3
"""¿Un reranker mejora lo que el híbrido ya ordena? Experimento acotado y medido.

Diseño: el sistema de producción recupera su top-50 (fusión RRF + anclaje); un
cross-encoder ---que lee consulta y fragmento JUNTOS, cosa que el bi-encoder no puede---
reordena esos 50, y se comparan las métricas del top-10 de cada ordenamiento sobre el
mismo conjunto de consultas de la evaluación principal. El reranker no puede traer
documentos nuevos: solo reordenar. Eso aísla la pregunta que importa ---¿el orden es el
cuello de botella?--- de la cobertura, que ya se mide aparte.

Modelo: BAAI/bge-reranker-v2-m3, el compañero natural del BGE-m3 que genera los densos
(misma familia, multilingüe, contexto largo). Se baja de HuggingFace la primera vez.

Uso:
    OMP_NUM_THREADS=1 python -m evaluacion.reranking \
        --consultas evaluacion/consultas.jsonl --salida evaluacion/reranking.json
"""
import argparse
import collections
import json
import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from evaluacion.correr_evaluacion import cargar_env, metricas, rango_del_relevante

CANDIDATOS = 50
K_EVAL = 10


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--consultas', default=os.path.join(RAIZ, 'evaluacion', 'consultas.jsonl'))
    p.add_argument('--salida', default=os.path.join(RAIZ, 'evaluacion', 'reranking.json'))
    p.add_argument('--modelo', default='BAAI/bge-reranker-v2-m3')
    p.add_argument('--limite', type=int, default=None, help='solo N consultas, para probar')
    a = p.parse_args()

    cargar_env()
    os.environ.setdefault('OMP_NUM_THREADS', '1')

    consultas = [json.loads(l) for l in open(a.consultas, encoding='utf-8')
                 if not json.loads(l)['tipo'].startswith('conversacional')]
    if a.limite:
        consultas = consultas[:a.limite]

    from backend import api as backend_api
    ix = backend_api.indice()
    codificador = backend_api.codificador()

    from sentence_transformers import CrossEncoder
    reranker = CrossEncoder(a.modelo, max_length=1024)
    print(f'{len(consultas)} consultas; reranker {a.modelo}', flush=True)

    rangos = collections.defaultdict(lambda: collections.defaultdict(list))
    detalle = []
    t0 = time.time()
    for k, c in enumerate(consultas, 1):
        denso = codificador.encode([c['consulta']], normalize_embeddings=True)[0]
        base = ix.buscar(denso, texto_consulta=c['consulta'], k=CANDIDATOS)
        indices = [int(i) for i, _, _ in base]

        fila = {'consulta': c['consulta'], 'tipo': c['tipo']}
        fila['hibrido'] = rango_del_relevante(ix, indices[:K_EVAL], c)

        pares = [(c['consulta'], ix.chunk(i)['texto'][:2000]) for i in indices]
        puntajes = reranker.predict(pares, show_progress_bar=False)
        reordenado = [i for _, i in sorted(zip(puntajes, indices),
                                           key=lambda x: -float(x[0]))]
        fila['rerank'] = rango_del_relevante(ix, reordenado[:K_EVAL], c)

        for config in ('hibrido', 'rerank'):
            rangos[config][c['tipo']].append(fila[config])
        detalle.append(fila)
        if k % 20 == 0:
            print(f'  {k}/{len(consultas)} ({time.time() - t0:.0f}s)', flush=True)

    resumen = {}
    for config, por_tipo in rangos.items():
        todos = [r for lista in por_tipo.values() for r in lista]
        resumen[config] = {'global': metricas(todos),
                           **{tipo: metricas(lista) for tipo, lista in por_tipo.items()}}
    with open(a.salida, 'w', encoding='utf-8') as f:
        json.dump({'resumen': resumen, 'detalle': detalle, 'candidatos': CANDIDATOS,
                   'modelo': a.modelo}, f, ensure_ascii=False, indent=1)

    print(f'\n{"config":<10}{"n":>5}{"R@1":>8}{"R@5":>8}{"MRR":>8}{"nDCG":>8}')
    for config in ('hibrido', 'rerank'):
        m = resumen[config]['global']
        print(f'{config:<10}{m["n"]:>5}{m["recall@1"]:>8.3f}{m["recall@5"]:>8.3f}'
              f'{m["mrr@10"]:>8.3f}{m["ndcg@10"]:>8.3f}')
    print(f'-> {a.salida}  ({(time.time() - t0) / 60:.1f} min)', flush=True)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Corre la evaluación de recuperación sobre el set sintético y produce las métricas.

Qué se compara. Cuatro configuraciones, de la señal cruda al sistema completo:

    denso     solo el vecino más cercano en el espacio de embeddings (BGE-m3)
    bm25      solo la señal léxica (nuestro BM25 con tokenizador que conserva
              identificadores)
    hibrido   el sistema de producción: fusión RRF de ambas señales más el anclaje
              exacto de identificadores
    estado    solo para las consultas conversacionales: el híbrido con el estado de
              diálogo armado desde el primer turno por el MISMO camino que usa la API
              (_preparar), contra el híbrido recibiendo el segundo turno pelado

La relevancia es A NIVEL DOCUMENTO: la consulta se generó desde un fragmento, pero
recuperar cualquier fragmento del mismo acto cuenta como acierto, porque el sistema cita
y enlaza actos completos. El fragmento exacto queda registrado por si después se quiere
la mirada estricta.

Métricas: Recall@1, Recall@5, Recall@8, MRR@10 y nDCG@10 (con un solo relevante,
nDCG@10 = 1/log2(1+rango)). Se informan globales y por tipo de consulta.

Uso:
    OMP_NUM_THREADS=1 python -m evaluacion_automatica.correr_evaluacion \
        --consultas evaluacion_automatica/consultas.jsonl \
        --salida evaluacion_automatica/resultados.json
"""
import argparse
import collections
import json
import math
import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

K_EVAL = 10


def cargar_env():
    ruta = os.path.join(RAIZ, '.env')
    if os.path.exists(ruta):
        for linea in open(ruta):
            linea = linea.strip()
            if linea and not linea.startswith('#') and '=' in linea:
                k, _, v = linea.partition('=')
                os.environ.setdefault(k.strip(), v.strip())


def _identidad_acto(cita):
    """'Disposicion CS 33/2026 — Artículo 1' -> 'disposicion cs 33/2026'."""
    doc = (cita or '').split('—')[0].strip().lower()
    return doc or None


def rango_del_relevante(ix, indices, esperado):
    """Posición (1..k) del primer fragmento que pertenece al acto buscado, o None.

    La pertenencia se decide por id_archivo O por la identidad del acto en la cita. El
    doble criterio no es cortesía: el índice actual tiene actos DUPLICADOS por la fusión
    incremental (misma disposición con dos id_archivo), y medir solo por id contaría como
    fallo recuperar la otra copia del acto correcto, que para quien consulta es acierto.
    """
    id_archivo = esperado.get('id_archivo')
    identidad = _identidad_acto(esperado.get('cita'))
    for puesto, i in enumerate(indices, 1):
        c = ix.chunk(int(i)) or {}
        if id_archivo and c.get('id_archivo') == id_archivo:
            return puesto
        if identidad and _identidad_acto(c.get('cita')) == identidad:
            return puesto
    return None


def metricas(rangos):
    n = len(rangos)
    if not n:
        return {}
    return {
        'n': n,
        'recall@1': sum(1 for r in rangos if r and r <= 1) / n,
        'recall@5': sum(1 for r in rangos if r and r <= 5) / n,
        'recall@8': sum(1 for r in rangos if r and r <= 8) / n,
        'mrr@10': sum(1 / r for r in rangos if r) / n,
        'ndcg@10': sum(1 / math.log2(1 + r) for r in rangos if r) / n,
        'sin_encontrar': sum(1 for r in rangos if not r),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--consultas', default=os.path.join(RAIZ, 'evaluacion', 'consultas.jsonl'))
    p.add_argument('--salida', default=os.path.join(RAIZ, 'evaluacion', 'resultados.json'))
    p.add_argument('--sin-estado', action='store_true',
                   help='saltear la configuración con estado (no llama al LLM)')
    a = p.parse_args()

    cargar_env()
    os.environ.setdefault('OMP_NUM_THREADS', '1')

    consultas = [json.loads(l) for l in open(a.consultas, encoding='utf-8')]
    print(f'{len(consultas)} consultas de {a.consultas}', flush=True)

    from backend import api as backend_api
    ix = backend_api.indice()
    codificador = backend_api.codificador()
    print(f'índice: {len(ix)} fragmentos ({type(ix).__name__})', flush=True)

    rangos = collections.defaultdict(lambda: collections.defaultdict(list))
    detalle = []
    t0 = time.time()

    simples = [c for c in consultas if not c['tipo'].startswith('conversacional')]
    conversacionales = [c for c in consultas if c['tipo'].startswith('conversacional')]

    # ------------------------------------------------ señales y sistema
    for k, c in enumerate(simples, 1):
        denso = codificador.encode([c['consulta']], normalize_embeddings=True)[0]
        fila = {'consulta': c['consulta'], 'tipo': c['tipo'], 'cita': c['cita']}

        # denso solo
        vecinos = ix.vecinos(denso, K_EVAL)
        fila['denso'] = rango_del_relevante(ix, [i for i, _ in vecinos], c)

        # bm25 solo (devuelve {indice: puntaje} disperso)
        puntajes = ix.puntuar_lexico(c['consulta'])
        top = [i for i, _ in sorted(puntajes.items(), key=lambda kv: -kv[1])[:K_EVAL]]
        fila['bm25'] = rango_del_relevante(ix, top, c)

        # híbrido de producción
        resultados = ix.buscar(denso, texto_consulta=c['consulta'], k=K_EVAL)
        fila['hibrido'] = rango_del_relevante(ix, [r[0] for r in resultados], c)

        for config in ('denso', 'bm25', 'hibrido'):
            rangos[config][c['tipo']].append(fila[config])
        detalle.append(fila)
        if k % 25 == 0:
            print(f'  {k}/{len(simples)} simples ({time.time() - t0:.0f}s)', flush=True)

    # ------------------------------------------------ estado en conversación
    # Todas las condiciones comparten UNA pasada de _preparar (una llamada al modelo por
    # consulta); lo que cambia es qué piezas del estado usa la búsqueda. Es la ablación
    # de la Tabla de procedencia del paper:
    #   sin_estado        turno 2 pelado, sin historial
    #   sin_reescritura   estado sí, pero buscando con el turno 2 crudo
    #   sin_entidad       consulta reescrita, sin la ranura de entidad
    #   sin_actos         consulta reescrita y entidad, sin la ranura de actos
    #   con_estado        el sistema completo (estado inferido)
    #   estado_corregido  igual, pero la entidad pesa como fijada por el usuario:
    #                     simula la corrección humana SUPONIENDO que la inferencia era
    #                     correcta; donde no lo era, castiga, y eso también es dato.
    if conversacionales and not a.sin_estado:
        print(f'\nconversacionales: {len(conversacionales)}', flush=True)
        for k, c in enumerate(conversacionales, 1):
            fila = {'consulta': f"{c['turno1']} → {c['turno2']}",
                    'tipo': c['tipo'], 'cita': c['cita']}

            denso2 = codificador.encode([c['turno2']], normalize_embeddings=True)[0]
            res = ix.buscar(denso2, texto_consulta=c['turno2'], k=K_EVAL)
            fila['sin_estado'] = rango_del_relevante(ix, [r[0] for r in res], c)

            try:
                consulta_obj = backend_api.Consulta(
                    pregunta=c['turno2'],
                    historial=[backend_api.Turno(rol='user', texto=c['turno1'])],
                    k=K_EVAL)
                consulta_ef, estado = backend_api._preparar(consulta_obj)
                fila['consulta_efectiva'] = consulta_ef
                denso_ef = codificador.encode([consulta_ef], normalize_embeddings=True)[0]
                entidad = estado.get('entidad')
                peso_inferido = backend_api.peso_de(estado.get('entidad_origen'))
                peso_usuario = backend_api.peso_de('usuario')
                pesos_actos = backend_api.pesos_de_actos(estado)

                condiciones = {
                    'sin_reescritura': (denso2, c['turno2'], entidad, peso_inferido, pesos_actos),
                    'sin_entidad': (denso_ef, consulta_ef, None, 0.0, pesos_actos),
                    'sin_actos': (denso_ef, consulta_ef, entidad, peso_inferido, None),
                    'con_estado': (denso_ef, consulta_ef, entidad, peso_inferido, pesos_actos),
                    'estado_corregido': (denso_ef, consulta_ef, entidad, peso_usuario, pesos_actos),
                }
                for nombre, (dv, txt, ent, pe, pa) in condiciones.items():
                    res = ix.buscar(dv, texto_consulta=txt, k=K_EVAL,
                                    entidad=ent, peso_entidad=pe, pesos_actos=pa)
                    fila[nombre] = rango_del_relevante(ix, [r[0] for r in res], c)
            except Exception as e:
                print(f'  [conv {k}] error: {type(e).__name__}: {str(e)[:100]}', flush=True)

            for config in ('sin_estado', 'sin_reescritura', 'sin_entidad', 'sin_actos',
                           'con_estado', 'estado_corregido'):
                if config in fila:
                    rangos[config][c['tipo']].append(fila[config])
            detalle.append(fila)
            if k % 10 == 0:
                print(f'  {k}/{len(conversacionales)}', flush=True)

    # ------------------------------------------------ resumen
    resumen = {}
    for config, por_tipo in rangos.items():
        todos = [r for lista in por_tipo.values() for r in lista]
        resumen[config] = {'global': metricas(todos),
                           **{tipo: metricas(lista) for tipo, lista in por_tipo.items()}}

    with open(a.salida, 'w', encoding='utf-8') as f:
        json.dump({'resumen': resumen, 'detalle': detalle,
                   'indice_fragmentos': len(ix)}, f, ensure_ascii=False, indent=1)

    print(f'\n{"config":<12}{"n":>5}{"R@1":>8}{"R@5":>8}{"R@8":>8}{"MRR":>8}{"nDCG":>8}')
    for config in ('denso', 'bm25', 'hibrido', 'sin_estado', 'sin_reescritura',
                   'sin_entidad', 'sin_actos', 'con_estado', 'estado_corregido'):
        if config not in resumen:
            continue
        m = resumen[config]['global']
        print(f'{config:<12}{m["n"]:>5}{m["recall@1"]:>8.3f}{m["recall@5"]:>8.3f}'
              f'{m["recall@8"]:>8.3f}{m["mrr@10"]:>8.3f}{m["ndcg@10"]:>8.3f}')
    print(f'\n-> {a.salida}  ({time.time() - t0:.0f}s)', flush=True)


if __name__ == '__main__':
    main()

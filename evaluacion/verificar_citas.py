#!/usr/bin/env python3
"""Verificación determinista de la fidelidad de las citas en respuestas generadas.

Dos comprobaciones que no necesitan juez, sobre respuestas generadas de verdad por el
sistema completo (recuperación + generación):

    (i)  ANCLAJE   todo acto citado en la respuesta tiene que estar entre lo
                   recuperado. Citar un acto que no vino del índice es, a los efectos
                   de quien lee, inventarlo: no hay fuente detrás del enlace.

    (ii) ATRIBUCION en consultas que nombran a una persona, el nombre tiene que
                   aparecer en el texto de alguno de los fragmentos citados. Es la
                   regla del prompt ("solo podés afirmar que participa si su nombre
                   aparece en el fragmento que citás") medida desde afuera.

Los identificadores citados se extraen con el mismo patrón que usa la recuperación para
el anclaje exacto, así que las dos partes del sistema hablan el mismo idioma.

Uso:
    OMP_NUM_THREADS=1 python -m evaluacion.verificar_citas \
        --consultas evaluacion/consultas.jsonl --muestra 50 \
        --salida evaluacion/citas.json
"""
import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# Código + número + año: "RESHCS 893/2025", "DISPCD-CB : 528 / 2025", "CS 33/2026".
RE_ACTO = re.compile(r'\b([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\-]{1,14})\s*:?\s*(\d{1,5})\s*/\s*(\d{2,4})')

# Dos o más palabras con mayúscula inicial seguidas que no abren oración ni son
# vocabulario institucional: aproximación a "la consulta nombra a una persona".
INSTITUCIONAL = {'universidad', 'nacional', 'lujan', 'luján', 'departamento', 'ciencias',
                 'consejo', 'superior', 'division', 'división', 'direccion', 'dirección',
                 'secretaria', 'secretaría', 'basicas', 'básicas', 'sociales', 'educacion',
                 'educación', 'tecnologia', 'tecnología', 'rector', 'rectorado', 'programa',
                 'plan', 'estudios', 'resolucion', 'resolución', 'disposicion', 'disposición'}
RE_NOMBRE = re.compile(r'(?<![.\n¿¡])\s((?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+){1,3}[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)')


def sin_tildes(t):
    return ''.join(c for c in unicodedata.normalize('NFD', t or '')
                   if unicodedata.category(c) != 'Mn').lower()


def nombres_de_persona(texto):
    candidatos = []
    for m in RE_NOMBRE.finditer(' ' + texto):
        palabras = m.group(1).split()
        if all(sin_tildes(p) not in INSTITUCIONAL for p in palabras) and len(palabras) >= 2:
            candidatos.append(' '.join(palabras))
    return candidatos


def actos_de(texto):
    actos = set()
    for m in RE_ACTO.finditer(texto or ''):
        anio = m.group(3)[-4:]
        if len(anio) == 2:
            # El corpus escribe años de dos y de cuatro cifras para el mismo acto
            # ("121/25" y "121/2025"): se normaliza para que no cuenten como distintos.
            anio = ('19' if anio > '50' else '20') + anio
        actos.add((m.group(1).upper(), m.group(2).lstrip('0'), anio))
    return actos


def cargar_env():
    ruta = os.path.join(RAIZ, '.env')
    if os.path.exists(ruta):
        for linea in open(ruta):
            linea = linea.strip()
            if linea and not linea.startswith('#') and '=' in linea:
                k, _, v = linea.partition('=')
                os.environ.setdefault(k.strip(), v.strip())


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--consultas', default=os.path.join(RAIZ, 'evaluacion', 'consultas.jsonl'))
    p.add_argument('--muestra', type=int, default=50,
                   help='cuántas consultas generar (cada una es una llamada al LLM)')
    p.add_argument('--k', type=int, default=8)
    p.add_argument('--salida', default=os.path.join(RAIZ, 'evaluacion', 'citas.json'))
    a = p.parse_args()

    cargar_env()
    os.environ.setdefault('OMP_NUM_THREADS', '1')
    if not os.environ.get('OPENAI_API_KEY'):
        sys.exit('falta OPENAI_API_KEY: esta verificación genera respuestas reales')

    consultas = [json.loads(l) for l in open(a.consultas, encoding='utf-8')
                 if not json.loads(l)['tipo'].startswith('conversacional')]
    rng = random.Random(2028)
    rng.shuffle(consultas)
    consultas = consultas[:a.muestra]

    from backend import api as backend_api
    ix = backend_api.indice()
    codificador = backend_api.codificador()
    print(f'{len(consultas)} consultas; índice de {len(ix)} fragmentos', flush=True)

    resultados = []
    t0 = time.time()
    for k, c in enumerate(consultas, 1):
        denso = codificador.encode([c['consulta']], normalize_embeddings=True)[0]
        recuperados = ix.buscar(denso, texto_consulta=c['consulta'], k=a.k)
        fragmentos = [ix.chunk(int(i)) for i, _, _ in recuperados]
        contexto = ix.contexto(recuperados)

        try:
            respuesta = backend_api.generar(c['consulta'], contexto)
        except Exception as e:
            print(f'  [{k}] generación falló: {e}', flush=True)
            continue

        # (i) anclaje: actos citados vs actos recuperados
        citados = actos_de(respuesta)
        # El anclaje se comprueba contra la cita Y contra el texto completo de cada
        # fragmento recuperado: hay actos (las órdenes de compra del índice actual) cuya
        # identidad no está en la cita pero sí en el cuerpo, y citarlos desde ahí es
        # citar con fuente, no inventar.
        recuperados_ids = set()
        for f in fragmentos:
            recuperados_ids |= actos_de(f.get('cita', ''))
            recuperados_ids |= actos_de(f.get('texto', ''))
        sueltos = {x for x in citados if x not in recuperados_ids}

        # (ii) atribución: nombres de la consulta presentes en fragmentos citados
        nombres = nombres_de_persona(c['consulta'])
        atribucion = None
        if nombres and citados:
            texto_citado = ' '.join(sin_tildes(f.get('texto', '')) for f in fragmentos
                                    if actos_de(f.get('cita', '')) & citados)
            atribucion = all(sin_tildes(n) in texto_citado for n in nombres)

        resultados.append({
            'consulta': c['consulta'], 'tipo': c['tipo'],
            'actos_citados': len(citados), 'actos_sueltos': len(sueltos),
            'sueltos': [' '.join(x) for x in sueltos],
            'nombres': nombres, 'atribucion_ok': atribucion,
        })
        if k % 10 == 0:
            print(f'  {k}/{len(consultas)} ({time.time() - t0:.0f}s)', flush=True)

    con_citas = [r for r in resultados if r['actos_citados']]
    con_sueltos = [r for r in resultados if r['actos_sueltos']]
    con_nombres = [r for r in resultados if r['atribucion_ok'] is not None]
    resumen = {
        'respuestas': len(resultados),
        'con_citas': len(con_citas),
        'actos_citados': sum(r['actos_citados'] for r in resultados),
        'actos_sueltos': sum(r['actos_sueltos'] for r in resultados),
        'respuestas_con_cita_suelta': len(con_sueltos),
        'consultas_con_persona': len(con_nombres),
        'atribucion_correcta': sum(1 for r in con_nombres if r['atribucion_ok']),
    }
    with open(a.salida, 'w', encoding='utf-8') as f:
        json.dump({'resumen': resumen, 'detalle': resultados}, f, ensure_ascii=False, indent=1)

    print('\nresumen:', json.dumps(resumen, ensure_ascii=False, indent=1))
    print('->', a.salida, flush=True)


if __name__ == '__main__':
    main()

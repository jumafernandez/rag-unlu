#!/usr/bin/env python3
"""Genera el set de consultas sintéticas para evaluar la recuperación.

La idea: tomar fragmentos reales del índice, pedirle a un modelo que escriba la consulta
que una persona haría si buscara ESO, y quedarse solo con los pares que pasan un control
de calidad automático. El resultado es un conjunto con respuesta conocida ---se sabe qué
acto responde cada consulta--- sobre el que se puede medir recuperación sin evaluadores
humanos.

Tres tipos de consulta, porque el sistema se usa de tres maneras:

    tematica       "¿qué normativa hay sobre pasantías en Ciencias Básicas?"
                   (generada por el modelo a partir del fragmento, sin nombrar el acto)
    identificador  "¿Qué establece la RESHCS 893/2025?"
                   (por plantilla: mide la vía léxica y el anclaje exacto)
    conversacional dos turnos donde el segundo no se sostiene solo
                   ("¿Qué se aprobó sobre X?" → "¿de qué año es?")
                   (mide el aporte del estado de diálogo)

Muestreo estratificado por sección y tipo de acto: sin eso, el set queda dominado por las
secciones grandes y no dice nada de las chicas.

Control de calidad: un segundo pase pregunta, con el fragmento y la consulta a la vista,
si el fragmento la responde. Lo que no pasa, se descarta y queda contado. Sin este pase
el set hereda las alucinaciones del generador y la evaluación mide ruido.

Uso:
    python -m evaluacion_automatica.generar_consultas --n 200 --salida evaluacion_automatica/consultas.jsonl
    python -m evaluacion_automatica.generar_consultas --n 20 --sin-conversacionales   # prueba corta
"""
import argparse
import collections
import json
import os
import random
import sqlite3
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


def cargar_env():
    ruta = os.path.join(RAIZ, '.env')
    if os.path.exists(ruta):
        for linea in open(ruta):
            linea = linea.strip()
            if linea and not linea.startswith('#') and '=' in linea:
                k, _, v = linea.partition('=')
                os.environ.setdefault(k.strip(), v.strip())


def llm(mensajes, esquema_json=True):
    from openai import OpenAI
    r = OpenAI().chat.completions.create(
        model=os.environ.get('RAG_MODELO_GEN', 'gpt-4o-mini'),
        temperature=0.7,   # acá SÍ se busca variedad: consultas distintas entre sí
        response_format={'type': 'json_object'} if esquema_json else None,
        messages=mensajes)
    return r.choices[0].message.content


def muestrear(bd, n):
    """Fragmentos sustantivos, estratificados por (sección, código de acto).

    Un documento por celda hasta llenar el cupo, recorriendo las celdas a la manera de un
    reparto por rondas: las secciones chicas quedan representadas y las grandes no ahogan
    al resto.
    """
    filas = bd.execute(
        "SELECT i, documento, id_archivo, seccion_portal, document_code, cita, titulo, "
        "       texto, tipo_seccion AS unidad "
        "FROM chunk WHERE LENGTH(texto) > 250 AND tipo_seccion NOT IN ('anexo', 'otra')"
    ).fetchall()

    por_celda = collections.defaultdict(dict)   # (seccion, codigo) -> {documento: fila}
    for f in filas:
        celda = (f['seccion_portal'] or '(sin sección)', f['document_code'] or '?')
        docs = por_celda[celda]
        # Un fragmento por documento; se prefiere la parte resolutiva o un artículo.
        actual = docs.get(f['documento'])
        if actual is None or ((f['unidad'] or '') in ('articulo', 'parte_resolutiva')
                              and (actual['unidad'] or '') not in ('articulo', 'parte_resolutiva')):
            docs[f['documento']] = f

    rng = random.Random(2026)
    celdas = {c: rng.sample(list(d.values()), len(d)) for c, d in por_celda.items()}
    elegidos, ronda = [], 0
    while len(elegidos) < n and any(celdas.values()):
        for c in sorted(celdas, key=lambda x: (x[0], x[1])):
            if celdas[c]:
                elegidos.append(celdas[c].pop())
                if len(elegidos) >= n:
                    break
        ronda += 1
    return elegidos


def consulta_tematica(fila):
    cuerpo = llm([
        {'role': 'system', 'content':
            'Escribís consultas de prueba para un buscador de normativa universitaria '
            'argentina. Devolvé un JSON {"consulta": "..."}.'},
        {'role': 'user', 'content':
            'Este fragmento pertenece a un acto administrativo:\n\n'
            f'CITA: {fila["cita"]}\nTITULO: {fila["titulo"] or ""}\n\n{fila["texto"][:1600]}\n\n'
            'Escribí LA consulta que haría una persona que necesita justamente esta '
            'información pero no tiene el documento delante. Reglas:\n'
            '- español rioplatense, natural, como se le escribe a un buscador con lenguaje '
            'de persona (puede ser pregunta u oración nominal)\n'
            '- SIN nombrar el número del acto ni copiar frases textuales del fragmento\n'
            '- concreta: sobre el tema, hecho, cargo o programa del que trata el fragmento, '
            'no genérica\n'
            '- una sola consulta'},
    ])
    return json.loads(cuerpo).get('consulta', '').strip()


def controla_calidad(fila, consulta):
    cuerpo = llm([
        {'role': 'system', 'content':
            'Sos un revisor estricto de conjuntos de evaluación. Devolvé un JSON '
            '{"responde": true|false, "motivo": "..."}.'},
        {'role': 'user', 'content':
            f'CONSULTA: {consulta}\n\nFRAGMENTO ({fila["cita"]}):\n{fila["texto"][:1600]}\n\n'
            '¿Este fragmento responde esa consulta de manera directa? Contestá false si '
            'la consulta es tan genérica que cualquier acto parecido la respondería '
            'igual de bien, o si pide algo que el fragmento no dice.'},
    ])
    return json.loads(cuerpo)


def consulta_conversacional(fila):
    cuerpo = llm([
        {'role': 'system', 'content':
            'Escribís diálogos de prueba para un buscador conversacional de normativa '
            'universitaria. Devolvé un JSON {"turno1": "...", "turno2": "..."}.'},
        {'role': 'user', 'content':
            f'CITA: {fila["cita"]}\nTITULO: {fila["titulo"] or ""}\n\n{fila["texto"][:1600]}\n\n'
            'Escribí un diálogo de DOS turnos de la misma persona:\n'
            '- turno1: pregunta por el tema o la entidad de este acto (sin el número del '
            'acto)\n'
            '- turno2: repregunta que NO se sostiene sola: sin el turno1 no se sabe de qué '
            'habla ("¿y de qué año es?", "¿quién lo firmó?", "¿eso sigue en ese cargo?"). '
            'El turno2 también debe poder responderse con este mismo acto.\n'
            'Nada de números de acto en ninguno de los dos.'},
    ])
    d = json.loads(cuerpo)
    return d.get('turno1', '').strip(), d.get('turno2', '').strip()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--n', type=int, default=200, help='consultas temáticas a intentar')
    p.add_argument('--identificadores', type=int, default=30)
    p.add_argument('--conversacionales', type=int, default=40)
    p.add_argument('--conversacionales-acto', type=int, default=30,
                   help='diálogos por identificador (plantillas, sin LLM): miden la '
                        'ranura de actos, que los diálogos temáticos no pueden activar '
                        'porque se generan sin números de acto')
    p.add_argument('--sin-conversacionales', action='store_true')
    p.add_argument('--indice', default=os.path.join(RAIZ, 'indice', 'chunks.sqlite'))
    p.add_argument('--salida', default=os.path.join(RAIZ, 'evaluacion', 'consultas.jsonl'))
    a = p.parse_args()

    cargar_env()
    if not os.environ.get('OPENAI_API_KEY'):
        sys.exit('falta OPENAI_API_KEY')

    bd = sqlite3.connect(a.indice)
    bd.row_factory = sqlite3.Row

    total_pedido = (a.n + a.identificadores + a.conversacionales_acto
                    + (0 if a.sin_conversacionales else a.conversacionales))
    muestra = muestrear(bd, total_pedido)
    print(f'{len(muestra)} fragmentos muestreados de '
          f'{bd.execute("SELECT COUNT(DISTINCT documento) FROM chunk").fetchone()[0]} documentos',
          flush=True)

    rng = random.Random(2027)
    rng.shuffle(muestra)
    tramo_ident = muestra[:a.identificadores]
    tramo_conv = [] if a.sin_conversacionales else muestra[a.identificadores:a.identificadores + a.conversacionales]
    corte = a.identificadores + len(tramo_conv)
    tramo_acto = muestra[corte:corte + a.conversacionales_acto]
    tramo_tema = muestra[corte + len(tramo_acto):]

    salida = open(a.salida, 'w', encoding='utf-8')
    escritas = collections.Counter()
    descartadas = collections.Counter()

    def emitir(registro):
        salida.write(json.dumps(registro, ensure_ascii=False) + '\n')
        salida.flush()
        escritas[registro['tipo']] += 1

    # --- identificador: por plantilla, sin LLM ---
    plantillas = ['¿Qué establece la {cita}?', '¿De qué trata la {cita}?',
                  'texto de la {cita}', '{cita}']
    for i, fila in enumerate(tramo_ident):
        cita_doc = (fila['cita'] or '').split('—')[0].strip()
        if not cita_doc:
            descartadas['identificador'] += 1
            continue
        emitir({'tipo': 'identificador',
                'consulta': plantillas[i % len(plantillas)].format(cita=cita_doc),
                'documento': fila['documento'], 'id_archivo': fila['id_archivo'],
                'chunk': fila['i'], 'seccion': fila['seccion_portal'],
                'codigo': fila['document_code'], 'cita': fila['cita']})

    # --- conversacional por acto: el turno 1 nombra el acto, el 2 es elíptico ---
    # Sin LLM: el acto es la verdad de referencia por construcción. Es el único tipo de
    # diálogo donde la ranura de actos se activa, porque el estado la llena desde los
    # identificadores que aparecen en la conversación.
    repreguntas = ['¿y de qué fecha es?', '¿quién la firmó?', '¿qué dice su primer artículo?',
                   '¿y qué establece en concreto?', '¿de qué trata su parte resolutiva?']
    for i, fila in enumerate(tramo_acto):
        cita_doc = (fila['cita'] or '').split('—')[0].strip()
        if not cita_doc or cita_doc.lower() in ('unknown', 'orden_compra'):
            descartadas['conversacional_acto'] += 1
            continue
        emitir({'tipo': 'conversacional_acto',
                'turno1': f'¿Qué establece la {cita_doc}?',
                'turno2': repreguntas[i % len(repreguntas)],
                'consulta': repreguntas[i % len(repreguntas)],
                'documento': fila['documento'], 'id_archivo': fila['id_archivo'],
                'chunk': fila['i'], 'seccion': fila['seccion_portal'],
                'codigo': fila['document_code'], 'cita': fila['cita']})

    # --- temática: generada + controlada ---
    for k, fila in enumerate(tramo_tema, 1):
        try:
            consulta = consulta_tematica(fila)
            if not consulta:
                raise ValueError('vacía')
            control = controla_calidad(fila, consulta)
            if not control.get('responde'):
                descartadas['tematica'] += 1
                print(f'  [{k}] descartada: {control.get("motivo", "?")[:80]}', flush=True)
                continue
            emitir({'tipo': 'tematica', 'consulta': consulta,
                    'documento': fila['documento'], 'id_archivo': fila['id_archivo'],
                    'chunk': fila['i'], 'seccion': fila['seccion_portal'],
                    'codigo': fila['document_code'], 'cita': fila['cita']})
        except Exception as e:
            descartadas['tematica'] += 1
            print(f'  [{k}] error: {type(e).__name__}: {str(e)[:80]}', flush=True)
        if k % 20 == 0:
            print(f'  temáticas: {escritas["tematica"]} de {k} intentadas', flush=True)
        time.sleep(0.2)

    # --- conversacional: dos turnos, con control sobre el segundo ---
    for k, fila in enumerate(tramo_conv, 1):
        try:
            t1, t2 = consulta_conversacional(fila)
            if not t1 or not t2:
                raise ValueError('turnos vacíos')
            control = controla_calidad(fila, f'{t1} … {t2}')
            if not control.get('responde'):
                descartadas['conversacional'] += 1
                continue
            emitir({'tipo': 'conversacional', 'turno1': t1, 'turno2': t2,
                    'consulta': t2,
                    'documento': fila['documento'], 'id_archivo': fila['id_archivo'],
                    'chunk': fila['i'], 'seccion': fila['seccion_portal'],
                    'codigo': fila['document_code'], 'cita': fila['cita']})
        except Exception as e:
            descartadas['conversacional'] += 1
            print(f'  [conv {k}] error: {type(e).__name__}: {str(e)[:80]}', flush=True)
        time.sleep(0.2)

    salida.close()
    print('\nescritas:   ', dict(escritas), flush=True)
    print('descartadas:', dict(descartadas), flush=True)
    print('->', a.salida, flush=True)


if __name__ == '__main__':
    main()

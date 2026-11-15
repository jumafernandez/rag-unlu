#!/usr/bin/env python3
"""Aumentación de los datos de la capa nacional: lo mismo, dicho de varias formas.

Cada fragmento entra al entrenamiento en cinco versiones: el original con una cabecera de metadatos
(la ficha de InfoLEG) y la cita al final, y cuatro reescrituras del 27B (paráfrasis, explicación,
preguntas de conocimiento general y relaciones con otras normas). Además se generan ejemplos de
abstención ("no sé"), de identidad del modelo y pedidos generales que responde cada modelo base
(datos generales para que el ajuste no borre lo que el modelo ya sabía).

Pasos:
  preparar    (en la Mac, sin modelo) fichas crudas con tema y relaciones recortadas, semillas de
              abstención y la muestra de la prueba chica. Lee normas.csv y modificadas.csv.
  metadatos   el 27B corrige la ortografía de cada ficha (InfoLEG viene en mayúsculas y sin tildes)
              sin cambiar datos; si cambia algún número, queda la ficha cruda.
  versiones   las cuatro reescrituras de cada fragmento (salvo los de pura fórmula y las copias).
  nose        preguntas por normas inexistentes y por actos internos de universidades, con respuestas
              que no inventan.
  identidad   preguntas sobre el propio modelo, con marcadores {NOMBRE}, {BASE}, {INSTITUCION}, {CORTE}.
  generales   pedidos generales en español de la Argentina, por categoría.
  replay      un modelo base responde los pedidos generales (greedy).
  mostrar     (en la Mac) arma un Markdown con la muestra para leerla.

    python -m nacional.aumentar preparar --datos nacional/datos --salida nacional/datos/aumentacion
    python -m nacional.aumentar versiones --datos N --metadatos DIR --salida DIR [--ids muestra.json]
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import random
import re
import sys
from collections import defaultdict

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from nacional.generar import SISTEMA, leer_json, n_preguntas  # noqa: E402

MAX_RELACIONES = 8
FORMULA = re.compile(r'comun[ií]quese|publ[ií]quese|reg[ií]strese|arch[ií]vese', re.I)
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre',
         'noviembre', 'diciembre']


def leer(ruta):
    return [json.loads(l) for l in open(ruta, encoding='utf-8')] if os.path.exists(ruta) else []


def fecha_es(iso):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', iso or '')
    return f'{int(m.group(3))} de {MESES[int(m.group(2)) - 1]} de {m.group(1)}' if m else (iso or '')


def nombre_norma(r):
    anio = (r.get('fecha_sancion') or '')[:4]
    return f'{r["tipo_norma"]} {r["numero_norma"]}' + (f'/{anio}' if anio else '')


def es_formula(f):
    return len(f['texto'].split()) < 60 and bool(FORMULA.search(f['texto']))


def fragmentos_utiles(frags):
    """Sin los de pura fórmula ("comuníquese, regístrese…") ni las copias exactas."""
    vistos, res = set(), []
    for f in frags:
        k = re.sub(r'\W+', ' ', f['texto'].lower()).strip()
        if es_formula(f) or k in vistos:
            continue
        vistos.add(k)
        res.append(f)
    return res


def donde(f):
    ub = ', '.join(x for x in (f['ubicacion'], f['articulos']) if x)
    return f['documento'] + (f', {ub}' if ub else '')


def original(f, cabecera):
    """Versión 0: la ficha como cabecera, el fragmento y la cita al final."""
    return f'{cabecera}\n\n{donde(f)}:\n\n{f["texto"]}\n\n({donde(f)})'


# ---------------------------------------------------------------- preparar (Mac)
TEMAS = ['el ingreso a la universidad', 'las becas para estudiantes', 'la acreditación de carreras de grado',
         'la validez nacional de los títulos', 'las carreras de posgrado', 'los concursos docentes',
         'la carrera docente', 'el régimen del personal nodocente', 'el presupuesto universitario',
         'los programas de investigación', 'la extensión universitaria', 'los convenios con otras instituciones',
         'las residencias universitarias', 'la educación a distancia', 'la evaluación institucional',
         'el reconocimiento de títulos extranjeros', 'la creación de nuevas universidades',
         'los programas de bienestar estudiantil', 'la movilidad e intercambio estudiantil',
         'el financiamiento de obras de infraestructura', 'los incentivos a docentes investigadores',
         'la formación docente', 'los hospitales universitarios', 'las carreras de interés público',
         'la gratuidad de los estudios de grado']
UNIVERSIDADES = ['Universidad de Buenos Aires', 'Universidad Nacional de La Plata', 'Universidad Nacional de Córdoba',
                 'Universidad Nacional de Luján', 'Universidad Nacional de Rosario', 'Universidad Nacional de Cuyo',
                 'Universidad Tecnológica Nacional', 'Universidad Nacional del Sur', 'Universidad Nacional de Tucumán',
                 'Universidad Nacional del Nordeste', 'Universidad Nacional del Litoral',
                 'Universidad Nacional de Mar del Plata', 'Universidad Nacional de Quilmes',
                 'Universidad Nacional de San Martín', 'Universidad Nacional de General Sarmiento',
                 'Universidad Nacional del Comahue', 'Universidad Nacional de San Luis',
                 'Universidad Nacional del Centro de la Provincia de Buenos Aires', 'Universidad Nacional de Salta',
                 'Universidad Nacional de Misiones']
ACTOS = ['Ordenanza {n}/{a} del Consejo Superior', 'Resolución {n}/{a} del Consejo Superior',
         'Resolución Rectoral {n}/{a}', 'Resolución {n}/{a} del Consejo Directivo de la Facultad de Ingeniería',
         'Resolución {n}/{a} del Consejo Directivo de la Facultad de Ciencias Económicas']


def preparar(a):
    d, s = a.datos, a.salida
    os.makedirs(s, exist_ok=True)
    csv.field_size_limit(sys.maxsize)
    base = {}
    with open(os.path.join(d, 'normas.csv'), encoding='utf-8-sig', errors='replace', newline='') as f:
        for r in csv.DictReader(f):
            base[r['id_norma']] = {k: r[k] for k in ('id_norma', 'tipo_norma', 'numero_norma', 'organismo_origen',
                                                     'fecha_sancion', 'fecha_boletin', 'titulo_resumido',
                                                     'titulo_sumario', 'texto_resumido')}
    modifica, modificada_por = defaultdict(set), defaultdict(set)
    with open(os.path.join(d, 'modificadas.csv'), encoding='utf-8-sig', errors='replace', newline='') as f:
        for r in csv.DictReader(f):
            x, y = r.get('id_norma_modificada'), r.get('id_norma_modificatoria')
            if x and y:
                modificada_por[x].add(y)
                modifica[y].add(x)

    # fichas crudas: las de fichas.jsonl, con el tema y las relaciones recortadas a las más recientes
    n = 0
    with open(os.path.join(s, 'fichas-crudas.jsonl'), 'w', encoding='utf-8') as out:
        for fi in leer(os.path.join(d, 'fichas.jsonl')):
            b = base.get(fi['id'])
            if not b:   # sin fila en la base: queda la ficha de corpus.py
                out.write(json.dumps({'id': fi['id'], 'norma': fi['norma'], 'ficha': fi['ficha']},
                                     ensure_ascii=False) + '\n')
                continue
            lineas = [f'{nombre_norma(b)}.', f'Organismo: {b["organismo_origen"].strip()}.']
            if b['fecha_sancion']:
                lineas.append(f'Fecha de sanción: {fecha_es(b["fecha_sancion"])}.')
            if b['fecha_boletin']:
                lineas.append(f'Publicada en el Boletín Oficial el {fecha_es(b["fecha_boletin"])}.')
            if b['titulo_resumido'].strip():
                lineas.append(f'Título: {b["titulo_resumido"].strip()}.')
            if b['titulo_sumario'].strip():
                lineas.append(f'Tema: {b["titulo_sumario"].strip()}.')
            if b['texto_resumido'].strip():
                lineas.append(f'Resumen: {b["texto_resumido"].strip()}')
            for etiqueta, conj in (('Modifica o complementa a', modifica[fi['id']]),
                                   ('Es modificada o complementada por', modificada_por[fi['id']])):
                otras = sorted((base[j] for j in conj if j in base), key=lambda r: r['fecha_sancion'] or '',
                               reverse=True)
                if otras:
                    m = '; '.join(f'{nombre_norma(r)} ({r["titulo_resumido"].strip()})' for r in otras[:MAX_RELACIONES])
                    extra = f'; y {len(otras) - MAX_RELACIONES} más' if len(otras) > MAX_RELACIONES else ''
                    lineas.append(f'{etiqueta}: {m}{extra}.')
            out.write(json.dumps({'id': fi['id'], 'norma': fi['norma'], 'ficha': '\n'.join(lineas)},
                                 ensure_ascii=False) + '\n')
            n += 1
    print(f'{n} fichas crudas con datos de la base')

    # semillas de abstención: normas que no están en la base y actos internos de universidades
    rnd = random.Random(a.semilla)
    rnd_forma = random.Random(a.semilla + 1)
    # Normas inexistentes: organismos frecuentes de la selección (los que dictan normas universitarias), con un número
    # dentro del rango que ese organismo usó ese año y que no esté en la base para ese tipo, organismo y año.
    existentes = {(r['tipo_norma'], r['numero_norma'], (r['fecha_sancion'] or '')[:4], r['organismo_origen'])
                  for r in base.values()}
    tope = defaultdict(int)
    for r in base.values():
        if r['numero_norma'].isdigit():
            k = (r['tipo_norma'], r['organismo_origen'], (r['fecha_sancion'] or '')[:4])
            tope[k] = max(tope[k], int(r['numero_norma']))
    curadas = [r for r in leer(os.path.join(d, 'normas.jsonl')) if r.get('tipo') and r.get('organismo')]
    frecuentes = defaultdict(int)
    for r in curadas:
        if r['tipo'] != 'Ley':   # las leyes tienen numeración nacional correlativa: casi todo número existe
            frecuentes[(r['tipo'], r['organismo'])] += 1
    pares = [k for k, _ in sorted(frecuentes.items(), key=lambda x: -x[1])[:12]]
    semillas = []
    while len(semillas) < a.n_inexistentes:
        tipo, org = rnd.choice(pares)
        anio = str(rnd.randint(1996, 2025))
        t = tope.get((tipo, org, anio), 0)
        if t < 20:
            continue
        num = str(rnd.randint(1, t))
        if (tipo, num, anio, org) in existentes:
            continue
        semillas.append({'clave': f'inexistente-{len(semillas)}', 'clase': 'inexistente',
                         'norma': f'{tipo} {num}/{anio}', 'organismo': org, 'forma': rnd_forma.choice(FORMAS)})
    for k in range(a.n_internos):
        acto = rnd.choice(ACTOS).format(n=rnd.randint(1, 3500), a=rnd.randint(2005, 2025))
        semillas.append({'clave': f'interno-{k}', 'clase': 'interno', 'acto': acto,
                         'universidad': rnd.choice(UNIVERSIDADES), 'tema': rnd.choice(TEMAS),
                         'forma': rnd_forma.choice(FORMAS)})
    with open(os.path.join(s, 'nose-semillas.jsonl'), 'w', encoding='utf-8') as out:
        for x in semillas:
            out.write(json.dumps(x, ensure_ascii=False) + '\n')
    print(f'{len(semillas)} semillas de abstención')

    # muestra de la prueba chica: fragmentos variados, con la Resolución 1610/2004 que ya vimos
    frags = fragmentos_utiles(leer(os.path.join(d, 'fragmentos.jsonl')))
    con_rel = [f for f in frags if modificada_por.get(f['id']) or modifica.get(f['id'])]
    muestra = [f for f in frags if f['id_fragmento'] == '102084-1-000']
    for pref, tipo in (('Ley', 'articulo'), ('Ley', 'preambulo'), ('Decreto', 'articulo'), ('Decreto', 'texto'),
                       ('Resolución', 'articulo'), ('Resolución', 'texto'), ('Resolución', 'preambulo'),
                       ('Disposición', None)):
        cand = [f for f in con_rel if f['norma'].startswith(pref) and (tipo is None or f['tipo'] == tipo)
                and f not in muestra] or [f for f in frags if f['norma'].startswith(pref) and f not in muestra]
        if cand:
            muestra.append(rnd.choice(cand))
    ruta_m = os.path.join(s, 'muestra.json')
    if os.path.exists(ruta_m):
        print('muestra.json ya existe: queda la misma')
        return
    json.dump({'fragmentos': [f['id_fragmento'] for f in muestra], 'normas': sorted({f['id'] for f in muestra})},
              open(ruta_m, 'w'), indent=1, ensure_ascii=False)
    print(f'muestra: {len(muestra)} fragmentos de {len({f["id"] for f in muestra})} normas')


# ---------------------------------------------------------------- pedidos al 27B
NORMALIZAR = """La siguiente ficha de una norma argentina viene de InfoLEG, donde los títulos, los organismos y los resúmenes están en mayúsculas y sin tildes.

<<<
{ficha}
>>>

Reescribila con la ortografía correcta del español: tildes, y mayúsculas solo al comienzo de cada oración, en los nombres propios (organismos, universidades, provincias, personas, leyes con nombre) y en las siglas. No agregues, quites ni cambies datos: los mismos números, fechas, nombres, normas y relaciones, en el mismo orden y con las mismas etiquetas ("Organismo:", "Fecha de sanción:", etc.). Respondé solo con la ficha corregida."""

CONTEXTO = """Ficha de la norma (no es parte del fragmento):
{ficha}

Fragmento ({donde}):
<<<
{texto}
>>>

Reglas:
- Nombrá la norma completa (tipo, número y año, y el anexo o el artículo cuando corresponda). Nunca escribas "esta norma", "la presente", "dicha resolución" ni expresiones parecidas sin nombrarla.
- Usá solo lo que dicen la ficha y el fragmento: no agregues datos, consecuencias ni interpretaciones.
- Si la ficha indica que la norma fue modificada o complementada por otras, mencionalo cuando sea pertinente, sin inventar en qué consistió el cambio.

"""

PEDIDOS = {
    'parafrasis': """Reescribí el fragmento con otras palabras y en otro orden, sin perder ni agregar contenido y con un largo parecido al original. Puede ir en prosa aunque el original esté dividido en artículos. Nombrá la norma al principio y volvé a nombrarla al final.
Respondé solo con un objeto JSON: {{"texto": "..."}}""",
    'explicacion': """Explicá qué establece el fragmento y para qué sirve, para alguien de la comunidad universitaria que no conoce la norma, en lenguaje claro y entre 120 y 250 palabras. Empezá por el contenido y cerrá indicando la norma como fuente.
Respondé solo con un objeto JSON: {{"texto": "..."}}""",
    'preguntas': """Escribí hasta {n} preguntas de conocimiento general sobre lo que establece el fragmento, como las haría alguien de la comunidad universitaria (voz "estudiante", "docente", "nodocente" o "gestión", la que mejor corresponda, sin forzarla). Cada pregunta tiene que entenderse sola: no nombra la norma ni se refiere a "el convenio", "estos sistemas", "el anexo" ni a otros elementos que solo se entienden con el texto a la vista. Si lo que establece el fragmento vale solo para una universidad, una carrera, un título, un organismo o un grupo de personas, la pregunta lo nombra, para que la respuesta no se aplique a casos a los que no corresponde. Cada respuesta da primero el contenido y cierra con una oración que cite la norma como respaldo, con redacción variada (por ejemplo: "Así lo establece el artículo 98 del Estatuto de la Universidad Nacional del Alto Uruguay, aprobado por la Resolución 3297/2019."). No repitas preguntas con otras palabras; si el fragmento no da para {n}, devolvé menos o ninguna.
Respondé solo con un objeto JSON: {{"preguntas": [{{"voz": "...", "pregunta": "...", "respuesta": "..."}}]}}""",
    'relaciones': """Escribí entre 80 y 200 palabras que ubiquen el fragmento y su norma en el sistema normativo: qué organismo la dictó y cuándo, a quién alcanza, qué otras normas menciona el fragmento y con cuáles se relaciona según la ficha (las que modifica o complementa y las que la modifican o complementan).
Respondé solo con un objeto JSON: {{"texto": "..."}}""",
}

# Normas centrales del sistema (elegidas con JM): diez versiones más por fragmento.
NUCLEO = {'25394': 'la Ley de Educación Superior (Ley 24.521)',
          '37263': 'el Decreto 576/1996, reglamentario de la Ley de Educación Superior',
          '123542': 'la Ley de Educación Nacional (Ley 26.206)',
          '248779': 'el Convenio Colectivo para los Docentes de las Instituciones Universitarias Nacionales '
                    '(Decreto 1246/2015)',
          '115242': 'el Convenio Colectivo de Trabajo para el Sector Nodocente de las Instituciones Universitarias '
                    'Nacionales (Decreto 366/2006)',
          '310461': 'la Resolución 1254/2018 del Ministerio de Educación, sobre las actividades reservadas de los '
                    'títulos del artículo 43 de la Ley de Educación Superior'}
VOCES = {'estudiante': 'un estudiante', 'docente': 'un docente', 'nodocente': 'alguien del personal nodocente',
         'gestion': 'alguien que trabaja en la gestión de una universidad'}
FORMATO_TEXTO = 'Respondé solo con un objeto JSON: {{"texto": "..."}}'
FORMATO_PREGUNTAS = 'Respondé solo con un objeto JSON: {{"preguntas": [{{"voz": "...", "pregunta": "...", "respuesta": "..."}}]}}'
PEDIDOS_NUCLEO = {
    'parafrasis-2': """Reescribí el fragmento organizado por temas, con oraciones cortas y otra redacción, sin perder ni agregar contenido. Nombrá la norma al principio y al final, y los artículos cuando corresponda.
""" + FORMATO_TEXTO,
    'parafrasis-3': """Reescribí el fragmento como un texto de divulgación para alguien que se acerca por primera vez a la universidad: claro y preciso, sin perder ni agregar contenido, en registro formal. Nombrá la norma y los artículos.
""" + FORMATO_TEXTO,
    **{f'explicacion-{v}': f"""Explicá qué significa el fragmento para {d}: qué derechos, obligaciones, plazos o procedimientos le importan, entre 100 y 220 palabras, y cerrá indicando la norma y los artículos como fuente. Si el fragmento no tiene nada que ver con esa persona, devolvé un texto vacío.
""" + FORMATO_TEXTO for v, d in VOCES.items()},
    'preguntas-articulos': """Para cada artículo del fragmento (hasta {n}), escribí una pregunta que lo nombre junto con el nombre corriente de la norma, {nombre}; por ejemplo: "¿Qué establece el artículo 43 de la Ley de Educación Superior?". La respuesta explica el artículo en lenguaje claro y lo cita.
""" + FORMATO_PREGUNTAS,
    'casos': """Escribí hasta {n} casos aplicados: situaciones concretas de la vida universitaria planteadas como pregunta ("¿Qué pasa si…?", "Si un docente…, ¿puede…?"), con una respuesta que las resuelva según el fragmento y cite la norma y el artículo. Solo casos que el fragmento permite resolver; si no hay ninguno, devolvé una lista vacía.
""" + FORMATO_PREGUNTAS,
    'estructura': """Índice de la norma:
{indice}

Ubicá el fragmento dentro de {nombre}: de qué título o capítulo forma parte, qué regulan las partes vecinas y cómo se relaciona con el resto de la norma, entre 100 y 200 palabras. Usá solo el índice, la ficha y el fragmento.
""" + FORMATO_TEXTO,
    'preguntas-2': """Escribí hasta {n} preguntas de conocimiento general sobre el fragmento, distintas de las más obvias: sobre excepciones, plazos, quién decide, qué pasa en casos límite. Cada pregunta se entiende sola, no nombra la norma y, si lo que pregunta vale solo para un grupo de personas, lo dice. La respuesta da primero el contenido y cierra con una oración que cite la norma y el artículo.
""" + FORMATO_PREGUNTAS,
}

# La Ley de Educación Superior pesa más que las otras centrales (JM): una segunda tanda de las versiones del núcleo
# (con otra redacción) y la historia de sus modificaciones.
LES = '25394'
SEGUNDA_TANDA = ('\n(Segunda tanda: escribí contenido distinto del de una primera tanda con el mismo pedido: otras '
                 'preguntas, otros casos, otra redacción.)')
PEDIDOS_LES = {
    **{f'{v}#2': PEDIDOS_NUCLEO[v] + SEGUNDA_TANDA for v in ('parafrasis-2', 'parafrasis-3', 'preguntas-articulos',
                                                           'casos', 'preguntas-2')},
    'historia': """Si el fragmento tiene notas de modificación (por ejemplo, "artículo sustituido por…", "incorporado por…", "derogado por…"), explicá qué artículos cambiaron, por qué norma y cuándo, según esas notas y la ficha, entre 80 y 200 palabras. Si no tiene notas de modificación, devolvé un texto vacío.
""" + FORMATO_TEXTO,
}

# Tanda nueva de los pedidos base para todos los fragmentos (JM, 14/11): el maestro ve lo que escribió con el mismo
# pedido en la tanda anterior y escribe algo distinto. Cada registro guarda su ROUGE-L con lo anterior (por pregunta en
# las de preguntas) y entrenar.py descarta las casi copias (0,7, el umbral de novedad de Self-Instruct, Wang et al.,
# ACL 2023). Va a otra carpeta (versiones-t2): entra al entrenamiento solo con --versiones-extra.
TANDA_NUEVA = """
Con este mismo pedido ya se escribió lo siguiente:
<<<
{previo}
>>>
Escribí una versión nueva y distinta: otra redacción, otro orden y, si son preguntas, otras preguntas (sobre otros aspectos del fragmento, otros casos o desde otra voz). No repitas oraciones ni preguntas de lo anterior; si el fragmento no da para más preguntas distintas, devolvé menos o ninguna. Respetá las reglas y el formato pedidos."""


def texto_previo(r):
    """Lo que se escribió en la tanda anterior, para mostrárselo al maestro: el texto, o solo las preguntas."""
    d = (r or {}).get('datos') or {}
    if not (r and r.get('json_ok') and r.get('termino')):
        return ''
    if isinstance(d.get('texto'), str):
        return d['texto'].strip()
    return '\n'.join('- ' + q['pregunta'].strip() for q in d.get('preguntas') or []
                     if isinstance(q, dict) and isinstance(q.get('pregunta'), str) and q['pregunta'].strip())


def palabras(t):
    return re.findall(r'\w+', t.lower())


def lcs(a, b):
    """Largo de la subsecuencia común más larga entre dos listas de palabras, con bits en paralelo (Allison y Dix,
    1986; Hyyrö, 2004): igual que la programación dinámica y mucho más rápido en Python."""
    if not a or not b:
        return 0
    m = {}
    for i, w in enumerate(a):
        m[w] = m.get(w, 0) | (1 << i)
    tope = (1 << len(a)) - 1
    v = tope
    for w in b:
        u = v & m.get(w, 0)
        v = ((v + u) | (v - u)) & tope
    return len(a) - bin(v).count('1')


def rouge_l(x, y):
    """ROUGE-L (F1, por palabras) entre dos textos."""
    a, b = palabras(x), palabras(y)
    n = lcs(a, b)
    return round(2 * n / (len(a) + len(b)), 3) if n else 0.0


def novedad(datos, previo_r):
    """Similitud de lo nuevo con la tanda anterior: un número para los textos y, para las preguntas, una lista con la
    máxima similitud de cada pregunta nueva con las anteriores."""
    d0 = (previo_r or {}).get('datos') or {}
    if isinstance(datos, dict) and isinstance(datos.get('texto'), str):
        return {'similitud_previa': rouge_l(datos['texto'], d0.get('texto') or '')}
    if isinstance(datos, dict) and isinstance(datos.get('preguntas'), list):
        viejas = [q['pregunta'] for q in d0.get('preguntas') or [] if isinstance(q, dict) and isinstance(q.get('pregunta'), str)]
        return {'similitud_previa': [max([rouge_l(q.get('pregunta') or '', v) for v in viejas] or [0.0])
                                     if isinstance(q, dict) else 0.0 for q in datos['preguntas']]}
    return {}


BENCH_NUCLEO = """Escribí {n} preguntas de evaluación sobre el fragmento, como las que haría una persona real en una consulta (estudiante, docente, nodocente o gestión): concretas, que se entiendan solas, que no nombren la norma y que, cuando haga falta, combinen más de una disposición del fragmento. Para cada una, escribí la respuesta de referencia, completa y precisa, citando la norma y los artículos.
""" + FORMATO_PREGUNTAS


FORMAS = ['breve y directa, en una sola oración', 'con algo de contexto sobre por qué lo pregunta',
          'como consulta de alguien de gestión que necesita resolver un trámite', 'como pedido de un resumen de la norma',
          'en tono coloquial pero respetuoso', 'como pregunta de un estudiante', 'como pregunta de un docente',
          'como pregunta de alguien del personal nodocente', 'preguntando si la norma sigue vigente',
          'preguntando qué artículo dice algo puntual']

RESPUESTA_NOSE = ('La respuesta dice con claridad que no tenés información sobre {que}{donde_esta}; no describe su contenido, '
                  'no lo supone y no habla de "inventar". Puede ofrecer, si sirve, explicar el tema en general.')

NOSE = {
    'inexistente': """Escribí una pregunta sobre la {norma} del organismo "{organismo}", acerca de un tema verosímil para ese organismo y relacionado con las universidades o con quienes trabajan o estudian en ellas. La pregunta va {forma}, con el nombre del organismo con la ortografía correcta (no en mayúsculas). Esa norma no está entre las que conocés. Escribí también la respuesta. """ + RESPUESTA_NOSE.format(
        que='esa norma', donde_esta=' (puede que no exista o que no esté entre las que conocés) y sugiere verificarla en InfoLEG (www.infoleg.gob.ar) o en el Boletín Oficial') + """
Respondé solo con un objeto JSON: {{"pregunta": "...", "respuesta": "..."}}""",
    'interno': """Escribí una pregunta sobre la {acto} de la {universidad} en relación con {tema}, como la haría alguien de esa universidad. La pregunta va {forma}. Escribí también la respuesta. """ + RESPUESTA_NOSE.format(
        que='los actos internos de cada universidad (ordenanzas y resoluciones de sus consejos y de su rectorado)',
        donde_esta=' y que esa información está en el digesto o en el boletín oficial de la propia universidad') + """ Si ofrece explicar el tema, se refiere a lo que dice la normativa nacional.
Respondé solo con un objeto JSON: {{"pregunta": "...", "respuesta": "..."}}""",
}

IDENTIDAD = """Vas a escribir ejemplos de conversación para que un modelo de lenguaje sepa qué es. Datos del modelo; usalos tal cual, con los marcadores entre llaves sin reemplazar:
- Se llama {{NOMBRE}}.
- Es un ajuste de {{BASE}} hecho por {{INSTITUCION}} con normativa universitaria nacional argentina tomada de InfoLEG y con textos públicos de argentina.gob.ar, el CONICET y el Consejo Interuniversitario Nacional.
- Conoce la normativa del sistema universitario nacional publicada hasta {{CORTE}}. No conoce los actos internos de cada universidad ni las normas posteriores.
- Puede citar normas, pero no reemplaza la consulta del texto oficial: lo vigente se verifica en InfoLEG o en el Boletín Oficial.
- Responde en español de la Argentina, en registro formal.

Escribí {n} preguntas distintas que alguien le haría sobre sí mismo (quién es, quién lo entrenó, qué sabe, en qué se lo puede consultar, si puede citar normas, qué límites tiene, en qué idioma responde, de cuándo es lo que sabe, si conoce las normas de una universidad en particular, etc.), cada una con una respuesta breve y coherente con los datos. Variá la forma de preguntar. No agregues datos que no estén en la lista.
Respondé solo con un objeto JSON: {{"ejemplos": [{{"pregunta": "...", "respuesta": "..."}}]}}"""

CATEGORIAS = ['redacción de correos, cartas y textos formales', 'resumen y comprensión de un texto que se incluye en el pedido',
              'matemática y problemas con números', 'programación en Python', 'ciencias naturales',
              'historia y geografía de la Argentina y del mundo', 'salud, alimentación y vida cotidiana',
              'razonamiento y lógica', 'traducción entre español e inglés', 'técnicas de estudio y organización',
              'tecnología y uso de la computadora', 'economía doméstica y trámites', 'literatura y lengua',
              'ideas, planificación y consejos de trabajo']

GENERALES = """Escribí {n} pedidos distintos que una persona de la Argentina le haría a un asistente de inteligencia artificial sobre {categoria}. Variá el largo, el tono y la dificultad: algunos breves y otros con más contexto. Si un pedido trabaja sobre un texto (resumir, corregir, traducir, analizar), incluí el texto completo dentro del pedido. Que no sean sobre normativa universitaria ni preguntas sobre el propio asistente. Usá español de la Argentina.
Respondé solo con un objeto JSON: {{"pedidos": ["...", "..."]}}"""


ALCANCE = """Ficha de la norma:
{ficha}

Pregunta: {pregunta}
Respuesta: {respuesta}

La pregunta se escribió mirando la norma y puede no entenderse sola. Reescribila para que la entienda cualquier persona que no tiene la norma a la vista:
- Si lo que pregunta vale solo para una universidad, una carrera, un título, un organismo o un grupo de personas (según la ficha y la respuesta), nombralo.
- Sacá las referencias que solo se entienden con el texto a la vista ("esta norma", "este documento", "el anexo", "el convenio", "la universidad" sin decir cuál, y parecidas).
- No nombres la norma (tipo, número y año), salvo que la pregunta sea justamente sobre esa norma (qué establece, quién la dictó, cuándo).
- No cambies lo que se pregunta ni agregues datos. Si ya se entiende sola, devolvela igual.
Respondé solo con un objeto JSON: {{"pregunta": "..."}}"""


def mensajes(texto):
    return [{'role': 'system', 'content': SISTEMA}, {'role': 'user', 'content': texto}]


def huella(texto):
    return hashlib.sha1(texto.encode()).hexdigest()[:8]


def correr(m, items, clave, armar, ruta, lote=32, crudo=False, pedido=lambda x: '', anotar=None):
    """Genera con el 27B lo que falte en `ruta` (retoma por `clave`). Cada registro lleva su procedencia: el modelo
    que lo generó y el nombre y la huella del pedido (el dataset se publica, así que cada dato tiene que poder
    rastrearse hasta el pedido exacto que lo produjo)."""
    hechos = {r[clave] for r in leer(ruta)}
    pend = [x for x in items if x[clave] not in hechos]
    print(f'{os.path.basename(ruta)}: {len(pend)} pendientes de {len(items)}', flush=True)
    with open(ruta, 'a', encoding='utf-8') as out:
        for i in range(0, len(pend), lote):
            parte = pend[i:i + lote]
            for x, (texto, termino, n_prompt) in zip(parte, m.generar([armar(x) for x in parte],
                                                                      presupuesto=getattr(m, 'presupuesto', 20000))):
                datos = texto.strip() if crudo else leer_json(texto)
                out.write(json.dumps({clave: x[clave], **{k: v for k, v in x.items() if k in ('id', 'id_fragmento', 'norma', 'version', 'clase', 'pregunta')},
                                      'generador': os.path.basename(m.ruta.rstrip('/')), 'pedido': pedido(x),
                                      'termino': termino, 'json_ok': datos is not None, 'datos': datos,
                                      'crudo': None if datos else texto, 'tokens_prompt': n_prompt,
                                      **(anotar(x, datos) if anotar and datos is not None else {})},
                                     ensure_ascii=False) + '\n')
            out.flush()
            print(f'  {min(i + lote, len(pend))}/{len(pend)}', flush=True)


_MAESTRO = {}


def maestro(a):
    """El 27B se carga una sola vez por proceso (la prueba corre varios pasos seguidos)."""
    if a.modelo not in _MAESTRO:
        if a.fla:   # antes de crear el modelo: los núcleos de fla para la atención lineal de Qwen 3.5
            from nacional.entrenar import activar_fla
            activar_fla()
        from adaptacion.armar_datos import Maestro
        m = Maestro(a.modelo, a.max_nuevos)
        m.ruta = a.modelo
        # Tokens por lote (con relleno). Sin fla, el prellenado de la atención lineal pasa la secuencia entera a
        # float32 y con más de 20.000 se agotaba el tile; con fla, 60.000 anduvo y casi duplicó la velocidad
        # (46 frente a 25 tokens generados por segundo, con textos equivalentes).
        m.presupuesto = a.presupuesto
        _MAESTRO[a.modelo] = m
    return _MAESTRO[a.modelo]


def prueba(a):
    """La prueba chica: todos los pasos del 27B sobre la muestra, con el modelo cargado una vez.
    --datos es la carpeta del corpus y --salida la de la aumentación (con fichas-crudas, semillas y muestra)."""
    A, P = a.salida, os.path.join(a.salida, a.carpeta_prueba)
    m = os.path.join(A, 'muestra.json')
    paso = lambda **kw: argparse.Namespace(**{**vars(a), **kw})
    metadatos(paso(entrada=os.path.join(A, 'fichas-crudas.jsonl'), ids=m, salida=os.path.join(P, 'metadatos')))
    versiones(paso(metadatos=os.path.join(P, 'metadatos'), ids=m, salida=os.path.join(P, 'versiones'), parte=0, partes=1))
    nose(paso(entrada=os.path.join(A, 'nose-semillas.jsonl'), limite=4, salida=os.path.join(P, 'nose')))
    identidad(paso(n=8, tandas=1, salida=os.path.join(P, 'identidad')))
    generales(paso(n=2, tandas=1, salida=os.path.join(P, 'generales')))


def filtrar(items, clave, ids_ruta, campo):
    if not ids_ruta:
        return items
    ids = set(json.load(open(ids_ruta))[campo])
    return [x for x in items if x[clave] in ids]


def numeros_iguales(x, y):
    """Mismos números en el mismo orden, sin contar los puntos de miles (el 27B escribe "Ley 24.521" donde InfoLEG
    dice "24521": es formato, no un dato distinto)."""
    nums = lambda t: re.findall(r'\d+', re.sub(r'(?<=\d)\.(?=\d{3}\b)', '', t or ''))
    return nums(x) == nums(y)


def metadatos(a):
    fichas = filtrar(leer(a.entrada), 'id', a.ids, 'normas')
    fichas = [x for k, x in enumerate(fichas) if k % a.partes == a.parte]
    os.makedirs(a.salida, exist_ok=True)
    ruta = os.path.join(a.salida, 'metadatos.jsonl' if a.partes == 1 else f'metadatos_{a.parte}_de_{a.partes}.jsonl')
    correr(maestro(a), fichas, 'id', lambda x: mensajes(NORMALIZAR.format(ficha=x['ficha'])), ruta, crudo=True,
           pedido=lambda x: f'normalizar@{huella(SISTEMA + NORMALIZAR)}')
    # control: los números de la ficha corregida tienen que ser los mismos que los de la cruda
    crudas = {x['id']: x for x in fichas}
    n_ok = 0
    filas = leer(ruta)
    for r in filas:
        c = crudas.get(r['id'])
        r['numeros_ok'] = bool(c and r['datos'] and numeros_iguales(r['datos'], c['ficha']))
        r['cabecera'] = r['datos'] if r['numeros_ok'] else (c['ficha'] if c else '')
        n_ok += r['numeros_ok']
    with open(ruta, 'w', encoding='utf-8') as out:
        for r in filas:
            out.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'fichas corregidas con los mismos números: {n_ok} de {len(filas)}')


def versiones(a):
    frags = filtrar(fragmentos_utiles(leer(os.path.join(a.datos, 'fragmentos.jsonl'))), 'id_fragmento', a.ids,
                    'fragmentos')
    cab = {r['id']: r['cabecera'] for r in leer(os.path.join(a.metadatos, 'metadatos.jsonl'))}
    indices = {d['id_doc']: '\n'.join(d.get('indice') or []) for d in leer(os.path.join(a.datos, 'documentos.jsonl'))}
    todos = {**PEDIDOS, **PEDIDOS_NUCLEO, **PEDIDOS_LES}
    if a.previas:
        return tanda_nueva(a, frags, cab)
    items = []
    for k, f in enumerate(frags):
        if k % a.partes != a.parte:
            continue
        extra = [v for v in PEDIDOS_NUCLEO if f['id'] in NUCLEO and (v != 'estructura' or indices.get(f['id_doc']))]
        extra += list(PEDIDOS_LES) if f['id'] == LES else []
        for v in list(PEDIDOS) + extra:
            items.append({'clave': f'{f["id_fragmento"]}:{v}', 'id_fragmento': f['id_fragmento'], 'id': f['id'],
                          'norma': f['norma'], 'version': v, '_f': f})
    os.makedirs(a.salida, exist_ok=True)

    def armar(x):
        f = x['_f']
        ficha = cab.get(f['id']) or f['documento']
        n_art = max(1, min(15, len(re.findall(r'(?im)^\s*art[ií]culo\s+\d+|^\s*art\.\s*\d+', f['texto']))))
        pedido = todos[x['version']].format(n=n_art if x['version'].startswith('preguntas-articulos') else n_preguntas(f['texto']),
                                            nombre=NUCLEO.get(f['id'], ''), indice=indices.get(f['id_doc'], ''))
        return mensajes(CONTEXTO.format(ficha=ficha, donde=donde(f), texto=f['texto']) + pedido)

    correr(maestro(a), items, 'clave', armar, os.path.join(a.salida, f'parte_{a.parte}_de_{a.partes}.jsonl'),
           pedido=lambda x: f'{x["version"]}@{huella(SISTEMA + CONTEXTO + todos[x["version"]])}')


def tanda_nueva(a, frags, cab):
    """Los cuatro pedidos base otra vez para cada fragmento, mostrando lo escrito en la tanda anterior (--previas)."""
    previas = {r['clave']: r for p in glob.glob(os.path.join(a.previas, 'parte_*.jsonl')) for r in leer(p)}
    t = a.tanda_nueva
    items, sin_previa = [], 0
    for k, f in enumerate(frags):
        if k % a.partes != a.parte:
            continue
        for v in PEDIDOS:
            r = previas.get(f'{f["id_fragmento"]}:{v}')
            previo = texto_previo(r)
            if not previo:   # sin nada válido en la tanda anterior (o sin preguntas posibles): no se pide otra
                sin_previa += 1
                continue
            items.append({'clave': f'{f["id_fragmento"]}:{v}#t{t}', 'id_fragmento': f['id_fragmento'], 'id': f['id'],
                          'norma': f['norma'], 'version': f'{v}#t{t}', '_f': f, '_v': v, '_previo': previo, '_r': r})
    print(f'tanda {t}: {len(items)} pedidos ({sin_previa} sin versión previa válida)', flush=True)
    os.makedirs(a.salida, exist_ok=True)

    def armar(x):
        f = x['_f']
        pedido = PEDIDOS[x['_v']].format(n=n_preguntas(f['texto']), nombre='', indice='')
        return mensajes(CONTEXTO.format(ficha=cab.get(f['id']) or f['documento'], donde=donde(f), texto=f['texto'])
                        + pedido + TANDA_NUEVA.format(previo=x['_previo']))

    correr(maestro(a), items, 'clave', armar, os.path.join(a.salida, f'parte_{a.parte}_de_{a.partes}.jsonl'),
           pedido=lambda x: f'{x["version"]}@{huella(SISTEMA + CONTEXTO + PEDIDOS[x["_v"]] + TANDA_NUEVA)}',
           anotar=lambda x, datos: novedad(datos, x['_r']))


def benchnucleo(a):
    """Benchmark propio de las normas centrales: preguntas de consulta con respuesta de referencia. No entra al
    entrenamiento (entrenar.py no lee esta carpeta)."""
    frags = [f for f in fragmentos_utiles(leer(os.path.join(a.datos, 'fragmentos.jsonl'))) if f['id'] in NUCLEO]
    cab = {r['id']: r['cabecera'] for r in leer(os.path.join(a.metadatos, 'metadatos.jsonl'))} if a.metadatos else {}
    items = [{'clave': f'{f["id_fragmento"]}:bench', 'id_fragmento': f['id_fragmento'], 'id': f['id'], 'norma': f['norma'],
              'version': 'bench', '_f': f} for f in frags]
    os.makedirs(a.salida, exist_ok=True)
    correr(maestro(a), items, 'clave',
           lambda x: mensajes(CONTEXTO.format(ficha=cab.get(x['id']) or x['_f']['documento'], donde=donde(x['_f']),
                                              texto=x['_f']['texto']) + BENCH_NUCLEO.format(n=a.n)),
           os.path.join(a.salida, 'bench-nucleo.jsonl'), pedido=lambda x: f'bench@{huella(SISTEMA + CONTEXTO + BENCH_NUCLEO)}')


def nose(a):
    semillas = leer(a.entrada)
    if a.limite:
        semillas = [x for x in semillas if x['clase'] == 'inexistente'][:a.limite] + \
                   [x for x in semillas if x['clase'] == 'interno'][:a.limite]
    os.makedirs(a.salida, exist_ok=True)
    correr(maestro(a), semillas, 'clave', lambda x: mensajes(NOSE[x['clase']].format(**x)),
           os.path.join(a.salida, 'nose.jsonl'), pedido=lambda x: f'nose-{x["clase"]}@{huella(SISTEMA + NOSE[x["clase"]])}')


def identidad(a):
    os.makedirs(a.salida, exist_ok=True)
    items = [{'clave': f'identidad-{k}'} for k in range(a.tandas)]
    # cada tanda pide n ejemplos; el pedido varía con el número de tanda para no repetir lo mismo
    correr(maestro(a), items, 'clave',
           lambda x: mensajes(IDENTIDAD.format(n=a.n) + f'\n(Tanda {x["clave"].split("-")[1]}: buscá formas de preguntar distintas de las más obvias.)'),
           os.path.join(a.salida, 'identidad.jsonl'), pedido=lambda x: f'identidad@{huella(SISTEMA + IDENTIDAD)}')


def generales(a):
    os.makedirs(a.salida, exist_ok=True)
    items = [{'clave': f'{c}#{t}', 'categoria': c} for c in CATEGORIAS for t in range(a.tandas)]
    correr(maestro(a), items, 'clave',
           lambda x: mensajes(GENERALES.format(n=a.n, categoria=x['categoria'])
                              + (f'\n(Tanda {x["clave"].split("#")[1]}: evitá los pedidos más obvios.)' if a.tandas > 1 else '')),
           os.path.join(a.salida, 'generales.jsonl'), lote=a.lote,
           pedido=lambda x: f'generales@{huella(SISTEMA + GENERALES)}')


REGISTRO = ('Respondé en español de la Argentina y en registro formal: tratá a la persona de usted o con construcciones '
            'impersonales y no uses formas del español de España (vosotros, habéis, os, ordenador, móvil, vale, coger). '
            'Por lo demás, respondé como lo harías normalmente.')


def preguntas_viejas(generados):
    """Las preguntas de generar.py (entrenamiento y evaluación), con la misma clave que linea_base."""
    res = []
    for ruta in (r for c in generados.split(',') for r in glob.glob(os.path.join(c, 'parte_*.jsonl'))):
        for r in leer(ruta):
            if not r.get('json_ok') or not r.get('termino'):
                continue
            clave = r.get('id_fragmento') or r['id_doc']
            for k, q in enumerate((r.get('datos') or {}).get('preguntas') or []):
                if isinstance(q, dict) and q.get('pregunta') and q.get('respuesta'):
                    res.append({'clave': f'{clave}-q{k}', 'id': r['id'], 'norma': r['norma'],
                                'pregunta': q['pregunta'], 'respuesta': q['respuesta']})
    return res


def alcance(a):
    """Reescribe las preguntas de generar.py (y las del benchmark, con --entrada) para que se entiendan solas: con el
    alcance explícito (universidad, carrera, organismo) y sin "esta norma". La respuesta no cambia."""
    if a.entrada:   # el benchmark
        items = [{'clave': q['id_pregunta'], 'id': q['id_doc'].split('-')[0], 'norma': q['norma'],
                  'pregunta': q['pregunta'], 'respuesta': q['referencia']} for q in leer(a.entrada)]
    else:
        items = preguntas_viejas(a.generados)
    if a.ids:
        ids = set(json.load(open(a.ids))['preguntas'])
        items = [x for x in items if x['clave'] in ids]
    items = [x for k, x in enumerate(items) if k % a.partes == a.parte]
    cab = {r['id']: r.get('cabecera') or r.get('ficha') for r in leer(os.path.join(a.metadatos, 'metadatos.jsonl'))} \
        if a.metadatos else {}
    crudas = {r['id']: r['ficha'] for r in leer(a.fichas)} if a.fichas else {}
    os.makedirs(a.salida, exist_ok=True)
    correr(maestro(a), items, 'clave',
           lambda x: mensajes(ALCANCE.format(ficha=cab.get(x['id']) or crudas.get(x['id']) or x['norma'],
                                             pregunta=x['pregunta'], respuesta=x['respuesta'])),
           os.path.join(a.salida, f'parte_{a.parte}_de_{a.partes}.jsonl'), lote=64,
           pedido=lambda x: f'alcance@{huella(SISTEMA + ALCANCE)}')


def replay(a):
    """Un modelo base responde los pedidos generales: sus propias respuestas entran al entrenamiento."""
    from nacional.linea_base import cargar, generar, plantilla
    pedidos = []
    for r in leer(os.path.join(a.entrada, 'generales.jsonl')):
        for k, p in enumerate((r.get('datos') or {}).get('pedidos') or []):
            if isinstance(p, str) and p.strip():
                pedidos.append({'clave': f'{r["clave"]}#{k}', 'pedido': p.strip()})
    if a.limite:
        pedidos = pedidos[:a.limite]
    pedidos = [p for k, p in enumerate(pedidos) if k % a.partes == a.parte]
    os.makedirs(a.salida, exist_ok=True)
    ruta = os.path.join(a.salida, f'replay-{a.nombre}.jsonl' if a.partes == 1 else f'replay-{a.nombre}_{a.parte}_de_{a.partes}.jsonl')
    hechos = {r['clave'] for r in leer(ruta)}
    pedidos = [p for p in pedidos if p['clave'] not in hechos]
    print(f'{a.nombre}: {len(pedidos)} pedidos', flush=True)
    if not pedidos:
        return
    tok, m = cargar(a.modelo)
    # Destilación de contexto: el modelo responde con su propio conocimiento pero en el registro buscado; al entrenar,
    # el ejemplo lleva solo el pedido, así el registro queda incorporado sin la indicación. Se escribe por tandas, así
    # un corte no pierde lo hecho (y se retoma por clave).
    tokens, seg = 0, 0.0

    def contar(r):
        try:
            return len(tok(r, add_special_tokens=False)['input_ids'])
        except Exception:   # tokenizadores de mistral-common: aproximación por palabras
            return int(len(r.split()) * 1.4)

    for i in range(0, len(pedidos), a.lote * 4):
        tanda = pedidos[i:i + a.lote * 4]
        resp, tk, sg = generar(tok, m, [plantilla(tok, REGISTRO + '\n\n' + p['pedido'], a.modelo) for p in tanda],
                               a.max_nuevos, a.lote)
        tokens, seg = tokens + tk, seg + sg
        with open(ruta, 'a', encoding='utf-8') as out:
            for p, r in zip(tanda, resp):
                n = contar(r)
                # una respuesta que llegó al tope de tokens quedó cortada: no entra al entrenamiento
                out.write(json.dumps({**p, 'modelo': a.nombre, 'respuesta': r, 'tokens': n,
                                      'cortada': n >= a.max_nuevos - 2, 'registro': f'registro@{huella(REGISTRO)}'},
                                     ensure_ascii=False) + '\n')
    print(f'LISTO {a.nombre}: {tokens} tokens en {seg:.0f} s')


def escribir_si_cambia(ruta, filas):
    """Escribe el jsonl en un temporal y lo renombra de una vez (un entrenamiento que lo lea al arrancar o al retomar
    nunca ve un archivo a medio escribir); si el contenido es el mismo, no lo toca."""
    texto = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in filas)
    if os.path.exists(ruta) and open(ruta, encoding='utf-8').read() == texto:
        return
    with open(ruta + '.tmp', 'w', encoding='utf-8') as out:
        out.write(texto)
    os.replace(ruta + '.tmp', ruta)


def juntar(a):
    """Une las partes: metadatos_*_de_*.jsonl -> metadatos.jsonl (con el control de números) y
    replay-<modelo>_*_de_*.jsonl -> replay-<modelo>.jsonl."""
    s = a.salida
    partes = sorted(glob.glob(os.path.join(s, 'metadatos_*_de_*.jsonl')))
    if partes:
        crudas = {r['id']: r['ficha'] for r in leer(a.fichas)}
        filas = [r for p in partes for r in leer(p)]
        for r in filas:
            r['numeros_ok'] = bool(r.get('datos') and numeros_iguales(r['datos'], crudas.get(r['id'], '')))
            r['cabecera'] = r['datos'] if r['numeros_ok'] else crudas.get(r['id'], '')
        escribir_si_cambia(os.path.join(s, 'metadatos.jsonl'), filas)
        print(f'metadatos: {len(filas)} fichas, {sum(r["numeros_ok"] for r in filas)} corregidas con los mismos números')
    por_modelo = defaultdict(list)
    for p in glob.glob(os.path.join(s, 'replay-*_*_de_*.jsonl')):
        por_modelo[re.sub(r'_\d+_de_\d+\.jsonl$', '', os.path.basename(p))].append(p)
    for m, ps in por_modelo.items():
        filas = [r for p in sorted(ps) for r in leer(p)]
        escribir_si_cambia(os.path.join(s, m + '.jsonl'), filas)
        print(f'{m}: {len(filas)} respuestas, {sum(r.get("cortada", False) for r in filas)} cortadas')


# ---------------------------------------------------------------- controles
DEICTICO = re.compile(r'\b(esta|este|estas|estos|dicha|dicho|la presente|el presente|la misma|el mismo)\s+'
                      r'(norma|resoluci[oó]n|ley|decreto|disposici[oó]n|anexo|reglamento|r[eé]gimen|ordenanza|'
                      r'convenio|acta|art[ií]culo)\b', re.I)


def sin_miles(t):
    return re.sub(r'(?<=\d)\.(?=\d{3}\b)', '', t or '')


def nombra(texto, norma):
    """¿El texto nombra la norma? Número con o sin puntos de miles; con el año, o sin él en las leyes (su número es
    único). "Ley 24.521", "Ley 24521/1995" y "Ley Nacional N° 24.521" cuentan."""
    m = re.search(r'(\d+)/(\d{4})', norma or '')
    if not m:
        return False
    num, anio = m.groups()
    t = sin_miles(texto)
    for x in re.finditer(rf'(?<!\d){num}(?!\d)(\s*/\s*(\d{{2,4}}))?', t):
        a = x.group(2)
        if a and (a == anio or (len(a) == 2 and anio.endswith(a))):
            return True
        if not a and (norma or '').startswith('Ley'):
            return True
    return False


def controles(r):
    """Señales simples por versión: si nombra la norma, cuántas palabras tiene y, en las preguntas, cuántas usan
    deícticos ("esta norma"…) o nombran la norma (las dos cosas van contra el pedido: se descartan al entrenar).
    En los textos, "esta norma" después de nombrarla es una anáfora normal y no cuenta."""
    d = r.get('datos') or {}
    norma = r.get('norma')
    if 'preguntas' in d:
        qs = [q for q in d['preguntas'] if isinstance(q, dict) and q.get('pregunta') and q.get('respuesta')]
        return {'preguntas': len(qs), 'palabras': sum(len((q['pregunta'] + ' ' + q['respuesta']).split()) for q in qs),
                'respuestas_que_nombran_la_norma': sum(nombra(q['respuesta'], norma) for q in qs),
                'preguntas_con_deicticos': sum(bool(DEICTICO.search(q['pregunta'])) for q in qs),
                'preguntas_que_nombran_la_norma': sum(nombra(q['pregunta'], norma) for q in qs)}
    texto = d.get('texto') or ''
    return {'palabras': len(texto.split()), 'nombra_norma': nombra(texto, norma)}


# ---------------------------------------------------------------- mostrar (Mac)
def mostrar(a):
    d, s = a.datos, a.salida
    base = a.entrada or os.path.dirname(s.rstrip('/'))   # donde están muestra.json y fichas-crudas.jsonl
    frags = {f['id_fragmento']: f for f in leer(os.path.join(d, 'fragmentos.jsonl'))}
    muestra = json.load(open(os.path.join(base, 'muestra.json')))
    crudas = {r['id']: r['ficha'] for r in leer(os.path.join(base, 'fichas-crudas.jsonl'))}
    meta = {}
    for r in leer(os.path.join(s, 'metadatos', 'metadatos.jsonl')):
        r['numeros_ok'] = bool(r.get('datos') and numeros_iguales(r['datos'], crudas.get(r['id'], '')))
        r['cabecera'] = r['datos'] if r['numeros_ok'] else crudas.get(r['id'], '')
        meta[r['id']] = r
    vers = defaultdict(dict)
    for r in (r for p in glob.glob(os.path.join(s, 'versiones', 'parte_*.jsonl')) for r in leer(p)):
        vers[r['id_fragmento']][r['version']] = r
    L = ['# Prueba chica de la aumentación', '']
    for idf in muestra['fragmentos'][:a.max_fragmentos]:
        f = frags[idf]
        mt = meta.get(f['id'], {})
        L += [f'## {donde(f)}', '', f'Ficha corregida con los mismos números: {"sí" if mt.get("numeros_ok") else "no"}', '',
              '### 0. Original con cabecera', '', '```', original(f, mt.get('cabecera') or f['documento']), '```', '']
        for k, v in enumerate(PEDIDOS, 1):
            r = vers[idf].get(v)
            L += [f'### {k}. {v.capitalize()}', '']
            if not r or not r['json_ok']:
                L += ['(sin salida válida)', '']
                continue
            L += [f'*Controles: {json.dumps(controles(r), ensure_ascii=False)}*', '']
            if v == 'preguntas':
                for q in r['datos'].get('preguntas') or []:
                    L += [f'- **{q.get("voz", "")}:** {q.get("pregunta", "")}', f'  {q.get("respuesta", "")}', '']
            else:
                L += [r['datos'].get('texto', ''), '']
    L += ['## "No sé"', '']
    for r in leer(os.path.join(s, 'nose', 'nose.jsonl')):
        if r['json_ok']:
            L += [f'- **{r.get("clase")}:** {r["datos"].get("pregunta", "")}', f'  {r["datos"].get("respuesta", "")}', '']
    L += ['## Identidad (con marcadores)', '']
    for r in leer(os.path.join(s, 'identidad', 'identidad.jsonl')):
        for e in ((r.get('datos') or {}).get('ejemplos') or []):
            L += [f'- {e.get("pregunta", "")}', f'  {e.get("respuesta", "")}', '']
    L += ['## Datos generales: pedidos del 27B y respuestas del modelo base', '']
    for p in glob.glob(os.path.join(s, 'replay', 'replay-*.jsonl')):
        for r in leer(p)[:a.max_replay]:
            L += [f'**Pedido:** {r["pedido"]}', '', f'**{r["modelo"]}:** {r["respuesta"]}', '', '---', '']
    open(a.md, 'w', encoding='utf-8').write('\n'.join(L))
    print(f'-> {a.md}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paso', choices=['preparar', 'metadatos', 'versiones', 'nose', 'identidad', 'generales', 'replay',
                                     'mostrar', 'prueba', 'alcance', 'benchnucleo', 'juntar'])
    ap.add_argument('--generados', help='carpetas de generar.py, separadas por coma (para alcance)')
    ap.add_argument('--fichas', help='fichas-crudas.jsonl (para alcance, si falta la ficha corregida)')
    ap.add_argument('--datos', help='carpeta con fragmentos.jsonl, fichas.jsonl (y normas.csv para preparar)')
    ap.add_argument('--entrada')
    ap.add_argument('--salida')
    ap.add_argument('--metadatos')
    ap.add_argument('--ids', help='muestra.json: limita a sus normas o fragmentos')
    ap.add_argument('--parte', type=int, default=0)
    ap.add_argument('--partes', type=int, default=1)
    ap.add_argument('--modelo', default='/data/contrib/pci_146/hf-models/Qwen3.5-27B')
    ap.add_argument('--nombre')
    ap.add_argument('--max-nuevos', type=int, default=3000)
    ap.add_argument('--lote', type=int, default=16)
    ap.add_argument('--fla', action='store_true', help='núcleos de flash-linear-attention en el maestro')
    ap.add_argument('--presupuesto', type=int, default=20000, help='tokens por lote del maestro (con relleno)')
    ap.add_argument('--previas', help='versiones: carpeta de la tanda anterior; genera una tanda nueva de los pedidos base')
    ap.add_argument('--tanda-nueva', type=int, default=2, help='número de la tanda nueva (sufijo #tN de las claves)')
    ap.add_argument('--limite', type=int, default=0)
    ap.add_argument('--n', type=int, default=10)
    ap.add_argument('--tandas', type=int, default=1)
    ap.add_argument('--semilla', type=int, default=13)
    ap.add_argument('--n-inexistentes', type=int, default=1500)
    ap.add_argument('--n-internos', type=int, default=1000)
    ap.add_argument('--carpeta-prueba', default='prueba')
    ap.add_argument('--md')
    ap.add_argument('--max-fragmentos', type=int, default=4)
    ap.add_argument('--max-replay', type=int, default=6)
    a = ap.parse_args()
    {'preparar': preparar, 'metadatos': metadatos, 'versiones': versiones, 'nose': nose, 'identidad': identidad,
     'generales': generales, 'replay': replay, 'mostrar': mostrar, 'prueba': prueba, 'alcance': alcance,
     'benchnucleo': benchnucleo, 'juntar': juntar}[a.paso](a)


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(1)

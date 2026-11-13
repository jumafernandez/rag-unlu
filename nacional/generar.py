#!/usr/bin/env python3
"""Datos de la capa nacional generados por el 27B, en dos etapas.

Etapa `documentos`: por cada documento (el texto principal de una norma o uno de sus anexos;
documentos.jsonl de `corpus.py`) el modelo escribe un resumen en lenguaje claro y preguntas
globales (qué regula, a quién alcanza, qué modifica), a partir de la ficha, el índice, el
preámbulo y el comienzo del texto. Las normas que solo tienen ficha también pasan por esta
etapa, con preguntas que se responden únicamente con la ficha.

Etapa `fragmentos`: por cada fragmento (fragmentos.jsonl) el modelo escribe una explicación y
preguntas de detalle, con el resumen del documento y la ubicación del fragmento (título,
capítulo, artículos) como contexto. La cantidad de preguntas es proporcional al largo: una
cada CARACTERES_POR_PREGUNTA caracteres, entre PREGUNTAS_MIN y PREGUNTAS_MAX.

Todo en español de la Argentina, en registro formal y lenguaje claro. Un 10 % de los
documentos y de los fragmentos, elegido por su id (estable entre corridas), queda marcado
como evaluación: sus preguntas no se usan para entrenar.

Reutiliza el generador por lotes del ajuste al digesto (`adaptacion.armar_datos.Maestro`).
Se reparte en partes y retoma lo que falte.

Uso (en un piloto con POR_TRABAJADOR=2: el 27B ocupa los dos tiles de una GPU):
    python -m nacional.generar documentos --entrada documentos.jsonl --salida DIR --parte 0 --partes 16
    python -m nacional.generar fragmentos --entrada fragmentos.jsonl --resumenes DIR_DOCS --salida DIR ...
"""
import argparse
import glob
import hashlib
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

SISTEMA = (
    'Sos especialista en normativa del sistema universitario argentino. Escribís en español de la Argentina, '
    'en registro formal y en lenguaje claro: oraciones directas, sin jerga jurídica innecesaria. Tratás a quien '
    'pregunta de usted o con construcciones impersonales. Nunca usás formas del español de España (vosotros, '
    'habéis, os, ordenador, móvil, vale, coger) y preferís el vocabulario argentino (computadora, celular, '
    'cursada, inscripción, nodocente, cátedra, expediente). Explicás normas que alcanzan a las universidades, '
    'pero no les atribuís un alcance que no tienen: si una norma es general (de toda la administración pública, '
    'de todos los trabajadores), lo decís así.'
)

FORMATO_PREGUNTAS = (
    'hasta {n} preguntas que haría alguien de la comunidad universitaria sin tener el texto a la vista, '
    'eligiendo para cada una la voz que mejor corresponda ("estudiante", "docente", "nodocente", "gestión") sin '
    'forzarla, y sin repetir preguntas con otras palabras. Cada respuesta nombra la norma (tipo, número y año, y el artículo cuando corresponda) y no agrega '
    'datos que no estén en el material. Si el material no da para {n} preguntas útiles, devolvé menos o ninguna.'
)

INSTRUCCION_DOCUMENTO = """Documento: {nombre}{sobre}.

Ficha de Infoleg:
{ficha}
{indice}{preambulo}{inicio}
Respondé solo con un objeto JSON con esta forma:
{{"resumen": "...", "preguntas": [{{"voz": "...", "pregunta": "...", "respuesta": "..."}}]}}

- "resumen": qué es y qué establece el documento, para quién y con qué alcance, en lenguaje claro, {largo_resumen}.{solo_ficha}
- "preguntas": """ + FORMATO_PREGUNTAS + """ Que sean preguntas sobre el documento en su conjunto (qué regula, a quién alcanza, qué crea, modifica o deroga, quién lo dictó y cuándo), no sobre detalles de un artículo."""

INSTRUCCION_FRAGMENTO = """Contexto (no es parte del fragmento): {documento}{titulo}.
{resumen}{ubicacion}
Fragmento:
<<<
{texto}
>>>

Respondé solo con un objeto JSON con esta forma:
{{"explicacion": "...", "preguntas": [{{"voz": "...", "pregunta": "...", "respuesta": "..."}}]}}

- "explicacion": qué establece el fragmento, en lenguaje claro, entre 100 y 200 palabras.
- "preguntas": """ + FORMATO_PREGUNTAS + """ Las preguntas y las respuestas se basan en el fragmento; el contexto sirve solo para ubicarlo. Cubrí distintas partes del fragmento."""

CARACTERES_POR_PREGUNTA = 700   # unos 180 tokens
PREGUNTAS_MIN, PREGUNTAS_MAX = 2, 10


def n_preguntas(texto):
    return max(PREGUNTAS_MIN, min(PREGUNTAS_MAX, round(len(texto) / CARACTERES_POR_PREGUNTA)))


def n_preguntas_documento(d):
    return max(2, min(6, round(len(d['texto']) / 8000) + 2)) if d['texto'] else 2


def recortar(t, n):
    t = (t or '').strip()
    return t if len(t) <= n else t[:n].rsplit(' ', 1)[0] + ' […]'


def mensaje_documento(d):
    tiene_texto = bool(d['texto'])
    sobre = f' (anexo de la norma titulada "{d["titulo"].strip().lower()}")' if d['es_anexo'] else ''
    indice = ('\nÍndice:\n' + recortar('\n'.join(d['indice']), 1500) + '\n') if d['indice'] else ''
    pre = ('\nPreámbulo (visto y considerandos):\n' + recortar(d['preambulo'], 2500) + '\n') if d['preambulo'] else ''
    resto = d['texto'].replace(d['preambulo'], '', 1) if d['preambulo'] else d['texto']
    inicio = ('\nComienzo del texto:\n<<<\n' + recortar(resto, 4000) + '\n>>>\n') if tiene_texto else ''
    solo = ('' if tiene_texto else ' No hay texto de la norma, solo la ficha: el resumen y las respuestas usan '
            'únicamente los datos de la ficha, sin agregar consecuencias, interpretaciones ni afirmaciones que no estén '
            'en ella.')
    return INSTRUCCION_DOCUMENTO.format(nombre=d['nombre'], sobre=sobre, ficha=recortar(d['ficha'], 2500),
                                        indice=indice, preambulo=pre, inicio=inicio, solo_ficha=solo,
                                        largo_resumen='entre 120 y 250 palabras' if tiene_texto else 'en una a tres oraciones (no más de 60 palabras)',
                                        n=n_preguntas_documento(d))


def mensaje_fragmento(f, resumenes):
    r = resumenes.get(f['id_doc'])
    resumen = f'Resumen del documento: {r}\n' if r else ''
    ub = ', '.join(x for x in (f['ubicacion'], f['articulos']) if x)
    return INSTRUCCION_FRAGMENTO.format(documento=f['documento'], titulo=f' ("{f["titulo"].strip().lower()}")',
                                        resumen=resumen, ubicacion=f'Ubicación del fragmento: {ub}\n' if ub else '',
                                        texto=f['texto'], n=n_preguntas(f['texto']))


def es_evaluacion(clave, proporcion=0.10):
    h = int(hashlib.sha1(clave.encode()).hexdigest()[:8], 16)
    return h / 0xFFFFFFFF < proporcion


def leer_json(texto):
    a, b = texto.find('{'), texto.rfind('}')
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(texto[a:b + 1])
    except json.JSONDecodeError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('etapa', choices=['documentos', 'fragmentos'])
    ap.add_argument('--entrada', required=True)
    ap.add_argument('--salida', required=True)
    ap.add_argument('--resumenes', help='carpeta con la salida de la etapa documentos (para la etapa fragmentos)')
    ap.add_argument('--parte', type=int, default=0)
    ap.add_argument('--partes', type=int, default=1)
    ap.add_argument('--modelo', default=os.path.expanduser('~/hf-models/Qwen3.5-27B'))
    ap.add_argument('--max-nuevos', type=int, default=3000)
    ap.add_argument('--lote', type=int, default=32, help='ítems que se mandan juntos al generador')
    ap.add_argument('--limite', type=int, default=0)
    a = ap.parse_args()

    clave = 'id_doc' if a.etapa == 'documentos' else 'id_fragmento'
    items = [json.loads(l) for l in open(a.entrada, encoding='utf-8')]
    items = [x for k, x in enumerate(items) if k % a.partes == a.parte]
    os.makedirs(a.salida, exist_ok=True)
    ruta = os.path.join(a.salida, f'parte_{a.parte}_de_{a.partes}.jsonl')
    hechos = {json.loads(l)[clave] for l in open(ruta, encoding='utf-8')} if os.path.exists(ruta) else set()
    pendientes = [x for x in items if x[clave] not in hechos]
    if a.limite:
        pendientes = pendientes[:a.limite]
    print(f'{a.etapa} parte {a.parte}/{a.partes}: {len(pendientes)} pendientes de {len(items)}', flush=True)
    if not pendientes:
        return
    resumenes = {}
    if a.etapa == 'fragmentos' and a.resumenes:   # una o varias carpetas, separadas por coma
        rutas = [r for c in a.resumenes.split(',') for r in glob.glob(os.path.join(c, 'parte_*.jsonl'))]
        for l in (l for r in rutas for l in open(r, encoding='utf-8')):
            r = json.loads(l)
            if r.get('datos') and r['datos'].get('resumen'):
                resumenes[r['id_doc']] = r['datos']['resumen']
        print(f'  {len(resumenes)} resúmenes de documentos como contexto', flush=True)

    from adaptacion.armar_datos import Maestro
    m = Maestro(a.modelo, a.max_nuevos)
    out = open(ruta, 'a', encoding='utf-8')
    for i in range(0, len(pendientes), a.lote):
        lote = pendientes[i:i + a.lote]
        textos = [mensaje_documento(x) if a.etapa == 'documentos' else mensaje_fragmento(x, resumenes) for x in lote]
        mensajes = [[{'role': 'system', 'content': SISTEMA}, {'role': 'user', 'content': t}] for t in textos]
        for x, (texto, termino, n_prompt) in zip(lote, m.generar(mensajes)):
            datos = leer_json(texto)
            fila = {clave: x[clave], 'id_doc': x['id_doc'], 'id': x['id'], 'norma': x['norma'],
                    'evaluacion': es_evaluacion(x[clave]), 'termino': termino, 'json_ok': datos is not None,
                    'datos': datos, 'crudo': None if datos else texto, 'tokens_prompt': n_prompt}
            out.write(json.dumps(fila, ensure_ascii=False) + '\n')
        out.flush()
        print(f'  {min(i + a.lote, len(pendientes))}/{len(pendientes)}', flush=True)
    print(f'LISTO {a.etapa} parte {a.parte}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(1)

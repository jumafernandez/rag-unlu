#!/usr/bin/env python3
"""Correcciones puntuales que salieron del control de datos del 16/11/2026. Se corre una vez por archivo y deja al lado
la copia del original (<archivo>.sin-depurar).

  - fragmentos.jsonl: fragmentos con la codificación rota en la fuente («ProducciÃ³n», «�IUHIBA�»);
  - aumentacion/nose/nose.jsonl: abstenciones que empiezan con un saludo de carta;
  - rag-entrenamiento.jsonl: no entran las respuestas que citan un acto que no está en el contexto ni las que traen un
    número largo (DNI, monto, expediente) que no figura en él.

    python -m nacional.depurar_sueltos --datos DIR [--ensayo]
"""
import argparse
import json
import os
import re
import shutil

MOJIBAKE = {'Ã¡': 'á', 'Ã©': 'é', 'Ã­': 'í', 'Ã³': 'ó', 'Ãº': 'ú', 'Ã±': 'ñ', 'Ã\x81': 'Á', 'Ã‰': 'É', 'Ã“': 'Ó', 'Ãš': 'Ú',
            'Ã‘': 'Ñ', 'Â°': '°', 'Âº': 'º', 'Âª': 'ª', 'â€œ': '“', 'â€\x9d': '”', 'â€“': '–'}
ROTO = re.compile('|'.join(map(re.escape, MOJIBAKE)) + '|�')
SALUDO = re.compile(r'^\W{0,4}estimad[oa]s?(/a)?\b[^,:\n]{0,40}[,:]\s*(le\s+informo\s+que\s+)?', re.I)
CITA = re.compile(r'\((?:[^()]*?)((?:Resoluci[oó]n|Disposici[oó]n|Ordenanza|Acta|Decreto|Ley)\s+[A-Z][A-Z\-]+\s+\d+/\d{4})')
NUMERO = re.compile(r'\d[\d.]{5,}\d')


def arreglar_codificacion(t):
    for mal, bien in MOJIBAKE.items():
        t = t.replace(mal, bien)
    # el carácter de reemplazo alrededor de una sigla era una comilla: «�IUHIBA�»
    t = re.sub(r'�([A-ZÁÉÍÓÚÑ0-9][^�\n]{0,60}?)�', r'“\1”', t)
    return t.replace('�', '')


def rag_sin_respaldo(r):
    """Por qué la respuesta no se sostiene en su contexto, o None."""
    ctx = ' '.join(m['content'] for m in r['mensajes'])
    fuera = [x for x in set(CITA.findall(r['respuesta'])) if x not in ctx]
    if fuera:
        return 'cita un acto que no está en el contexto: ' + ', '.join(sorted(fuera))
    plano = ctx.replace('.', '')
    nums = sorted(x for x in set(NUMERO.findall(r['respuesta'])) if x not in ctx and x.replace('.', '') not in plano)
    if nums:
        return 'número que no está en el contexto: ' + ', '.join(nums)
    return None


def reescribir(ruta, filas, ensayo):
    if ensayo:
        return
    if not os.path.exists(ruta + '.sin-depurar'):
        shutil.copy2(ruta, ruta + '.sin-depurar')
    with open(ruta + '.tmp', 'w', encoding='utf-8') as out:
        for r in filas:
            out.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(ruta + '.tmp', ruta)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True)
    ap.add_argument('--ensayo', action='store_true', help='muestra lo que cambiaría, sin escribir')
    a = ap.parse_args()
    leer = lambda p: [json.loads(l) for l in open(p, encoding='utf-8')]

    ruta = os.path.join(a.datos, 'fragmentos.jsonl')
    filas, n = leer(ruta), 0
    for r in filas:
        if ROTO.search(r.get('texto') or ''):
            nuevo = arreglar_codificacion(r['texto'])
            for m in ROTO.finditer(r['texto']):
                print(f'   fragmento {r.get("id_fragmento")}: {r["texto"][max(0, m.start() - 30):m.end() + 30]!r}')
            r['texto'], n = nuevo, n + 1
    print(f'fragmentos.jsonl: {n} fragmentos con la codificación corregida, de {len(filas)}')
    if n:
        reescribir(ruta, filas, a.ensayo)

    ruta = os.path.join(a.datos, 'aumentacion', 'nose', 'nose.jsonl')
    filas, n = leer(ruta), 0
    for r in filas:
        d = r.get('datos') or {}
        m = SALUDO.match(d.get('respuesta') or '') if isinstance(d.get('respuesta'), str) else None
        if m:
            print(f'   abstención: {d["respuesta"][:90]!r}')
            resto = d['respuesta'][m.end():]
            d['respuesta'], n = resto[:1].upper() + resto[1:], n + 1
            print(f'            -> {d["respuesta"][:70]!r}')
    print(f'nose.jsonl: {n} abstenciones sin el saludo, de {len(filas)}')
    if n:
        reescribir(ruta, filas, a.ensayo)

    ruta = os.path.join(a.datos, 'rag-entrenamiento.jsonl')
    filas, quedan, motivos = leer(ruta), [], []
    for r in filas:
        motivo = rag_sin_respaldo(r)
        if motivo:
            motivos.append(motivo)
        else:
            quedan.append(r)
    print(f'rag-entrenamiento.jsonl: {len(filas) - len(quedan)} respuestas afuera, quedan {len(quedan)}')
    for m in motivos[:60]:
        print('   ', m[:150])
    if len(quedan) < len(filas):
        reescribir(ruta, quedan, a.ensayo)


if __name__ == '__main__':
    main()

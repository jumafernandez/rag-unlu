#!/usr/bin/env python3
"""Control de los datos de entrenamiento de Liminar.

Arma los ejemplos con la misma función y las mismas opciones que el trabajo real (entrenar.armar_ejemplos), guarda el
texto de cada ejemplo con su fuente y corre controles sobre lo que de verdad entra al entrenamiento: vacíos, marcadores
sin reemplazar, restos de formato, problemas de codificación, textos cortados, duplicados, saludos y rótulos, español
peninsular, fragmentos cortados por el largo máximo y cercanía con las preguntas retenidas del benchmark.

Corre en CPU. Las opciones llegan por el entorno, como en entrenar.sbatch: DATOS, GENERADOS, ABIERTAS y EXTRA.
    python -m nacional.control_datos --salida DIR [--benchmark linea_base-preguntas-v2.jsonl] [--muestra 12]
"""
import argparse
import collections
import json
import os
import random
import re
import shlex
import statistics as st
import sys
import unicodedata

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

FUENTES = [('metadatos', None), ('fichas.jsonl', 'ficha'), ('/abiertas/', 'texto público'), ('documentos.jsonl', None),
           ('fragmentos.jsonl', 'fragmento de norma'), ('linea_base-preguntas', None), ('/alcance/', 'preguntas generadas'),
           ('versiones-t2', 'versiones (tanda 2)'), ('/versiones/', 'versiones (tanda 1)'), ('/nose/', 'abstención'),
           ('/identidad/', 'identidad'), ('replay', 'respuestas propias'), ('rag-entrenamiento', 'con documentos (RAG)')]

SALUDO = re.compile(r'^\W{0,4}(estimad[oa]s?(/a)?\b|de mi (mayor )?consideraci[oó]n|se[ñn]or(/a|a)?[,:])', re.I)
ROTULO = re.compile(r'^\W{0,4}(respuesta|versi[oó]n|par[aá]frasis|reescritura|texto\s+reescrito)\b[^\n]{0,60}:\W{0,4}$', re.I)
PREAMBULO = re.compile(r'^\W{0,4}(aqu[ií]\s+(tiene|est[aá]|va)|claro[,.!]|por\s+supuesto|con\s+gusto)', re.I)
DESPEDIDA = re.compile(r'(^|\n)\W{0,4}(atentamente|cordialmente|saludos\s+cordiales)\W{0,4}(\n|$)', re.I)
MARCADOR = re.compile(r'\{(NOMBRE|BASE|INSTITUCION|CORTE|[a-z_]{2,20})\}')
FORMATO = re.compile(r'```json|^\s*\{\s*"(texto|preguntas|respuesta)"|\\n\\n|\\"|<\|[a-z_]+\|>|</?(p|br|div|span|strong|em|li|ul|table|td|tr)\b[^>]{0,40}>|&(nbsp|amp|quot|lt|gt|[a-z]+acute|ntilde);', re.I | re.M)
CODIFICACION = re.compile(r'�|Ã[¡©­³º±‰“]|â€|Â[ºª°·]')
PENINSULAR = re.compile(r'\b(vosotr[oa]s|hab[eé]is|sois|ten[eé]is|pod[eé]is|quer[eé]is|vuestr[oa]s?|ordenador(es)?|os\s+(recomiendo|presento|explico|dejo))\b', re.I)
MODELO = re.compile(r'como\s+(modelo\s+de\s+lenguaje|inteligencia\s+artificial)\b|soy\s+un\s+modelo\s+de\s+lenguaje\s+entrenado\s+por|no\s+tengo\s+acceso\s+a\s+internet', re.I)
INGLES = re.compile(r'\b(the|and|of|is|are|this|that|with|for|which|have|has)\b', re.I)
FINAL = re.compile(r'[.!?…:;)\]»"”\'*`|>}\d%]\s*$')


def plano(s):
    s = unicodedata.normalize('NFD', (s or '').lower())
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9ñ ]+', ' ', ''.join(c for c in s if unicodedata.category(c) != 'Mn'))).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--salida', required=True)
    ap.add_argument('--benchmark', help='preguntas retenidas (linea_base-preguntas-v2.jsonl): cercanía con el entrenamiento')
    ap.add_argument('--muestra', type=int, default=12, help='ejemplos al azar por fuente en el archivo para leer')
    ap.add_argument('--sin-volcado', action='store_true')
    cfg = ap.parse_args()
    N = os.environ['DATOS']
    sys.argv = (['entrenar', '--datos', N, '--generados', os.environ['GENERADOS'], '--salida', '/dev/null'] +
                (['--abiertas', os.environ['ABIERTAS']] if os.environ.get('ABIERTAS') else []) + shlex.split(os.environ['EXTRA']))
    import nacional.entrenar as E
    a = E.args()
    estado = {'fuente': '?'}
    leer_original = E.leer

    def leer(ruta):
        for clave, fuente in FUENTES:
            if clave in ruta:
                estado['fuente'] = fuente or estado['fuente']
                break
        return leer_original(ruta)
    E.leer = leer

    class Registrador(E.Formato):
        """El formato real (mismos ids y mismos descartes), guardando además el texto y de dónde viene."""
        descartados = collections.Counter()

        def chat(self, pregunta, respuesta, max_tokens):
            x = super().chat(pregunta, respuesta, max_tokens)
            if x is None:
                self.descartados[estado['fuente']] += 1
                return None
            x['crudo'] = {'fuente': estado['fuente'], 'tipo': 'conversación', 'pregunta': pregunta, 'respuesta': respuesta}
            return x

        def chat_mensajes(self, mensajes, respuesta, max_tokens):
            x = super().chat_mensajes(mensajes, respuesta, max_tokens)
            if x is None:
                self.descartados[estado['fuente']] += 1
                return None
            x['crudo'] = {'fuente': estado['fuente'], 'tipo': 'conversación con documentos', 'mensajes': mensajes,
                          'respuesta': respuesta}
            return x

        def texto(self, t, max_tokens):
            x = super().texto(t, max_tokens)
            total = len(self.tok(t, add_special_tokens=False)['input_ids'])
            x['crudo'] = {'fuente': estado['fuente'], 'tipo': 'texto', 'texto': t, 'tokens_del_texto': total,
                          'cortado': total > max_tokens - 1 - len(self.bos)}
            return x

    from transformers import AutoTokenizer
    fmt = Registrador(AutoTokenizer.from_pretrained(a.modelo))
    tr, va, largos = E.armar_ejemplos(a, fmt)
    os.makedirs(cfg.salida, exist_ok=True)
    print(f'ejemplos: entrenamiento {len(tr)} ({sum(len(x["input_ids"]) for x in tr) / 1e6:.1f} M tokens) · validación '
          f'{len(va)} · conversaciones descartadas por largo {largos} {dict(fmt.descartados)}', flush=True)

    # ---------------------------------------------------------------- controles
    inf = collections.defaultdict(lambda: collections.defaultdict(int))
    ej = collections.defaultdict(lambda: collections.defaultdict(list))
    toks = collections.defaultdict(list)
    vistos = collections.defaultdict(collections.Counter)
    por_fuente = collections.defaultdict(list)

    def anotar(fuente, problema, texto):
        inf[fuente][problema] += 1
        if len(ej[fuente][problema]) < 3:
            ej[fuente][problema].append(texto.strip()[:220])

    for k, x in enumerate(tr):
        c = x['crudo']
        f = c['fuente']
        por_fuente[f].append(k)
        inf[f]['ejemplos'] += 1
        toks[f].append(len(x['input_ids']))
        if c['tipo'] == 'texto':
            cuerpo, pedido = c['texto'], ''
            if c['cortado']:
                inf[f]['cortados por el largo máximo'] += 1
                inf[f]['tokens que quedan afuera'] += c['tokens_del_texto'] - len(x['input_ids'])
        elif c['tipo'] == 'conversación':
            cuerpo, pedido = c['respuesta'], c['pregunta']
        else:
            cuerpo, pedido = c['respuesta'], ' '.join(m['content'] for m in c['mensajes'])
        vistos[f][(pedido, cuerpo)] += 1
        t = cuerpo.strip()
        if len(t) < 30:
            anotar(f, 'muy cortos (menos de 30 caracteres)', t or '(vacío)')
        if c['tipo'] != 'texto' and len(pedido.strip()) < 8:
            anotar(f, 'pedido vacío o mínimo', pedido)
        if MARCADOR.search(t) or MARCADOR.search(pedido):
            anotar(f, 'marcador sin reemplazar', (MARCADOR.search(t) or MARCADOR.search(pedido)).group(0) + ' … ' + t)
        if t.lower() in ('none', 'null', 'nan', 'n/a'):
            anotar(f, 'respuesta nula', t)
        if FORMATO.search(t):
            anotar(f, 'restos de formato (JSON, HTML, escapes)', FORMATO.search(t).group(0) + ' … ' + t[max(0, FORMATO.search(t).start() - 60):])
        if CODIFICACION.search(t) or CODIFICACION.search(pedido):
            anotar(f, 'problema de codificación', t[max(0, (CODIFICACION.search(t) or CODIFICACION.search(pedido)).start() - 60):])
        if c['tipo'] != 'texto':
            l1 = t.split('\n')[0][:160]
            carta = re.search(r'\b(carta|correo|e-?mail|nota|mensaje|comunicado|discurso|invitaci[oó]n|solicitud)\b', pedido, re.I)
            if SALUDO.search(l1) and not carta:
                anotar(f, 'empieza con saludo de carta', t)
            if ROTULO.search(l1):
                anotar(f, 'empieza con un rótulo', t)
            if PREAMBULO.search(l1) and f != 'respuestas propias':
                anotar(f, 'empieza con un preámbulo ("Aquí tiene", "Claro")', t)
            if DESPEDIDA.search(t[-300:]) and not carta:
                anotar(f, 'termina con despedida de carta', t[-200:])
            if MODELO.search(t) and f not in ('identidad',):
                anotar(f, 'habla de sí como modelo de lenguaje', t[max(0, MODELO.search(t).start() - 40):])
            if not FINAL.search(t):
                anotar(f, 'no termina en puntuación (posible corte)', t[-160:])
            if PENINSULAR.search(t) and not re.search(r'espa[ñn]a|peninsular|vosotros', pedido, re.I):
                anotar(f, 'formas del español de España', t[max(0, PENINSULAR.search(t).start() - 60):])
        pal = t.split()
        if f != 'respuestas propias' and len(pal) >= 30 and len(INGLES.findall(t)) / len(pal) > 0.12:
            anotar(f, 'texto mayormente en inglés', t)
    resumen = {}
    for f, ks in por_fuente.items():
        rep = sum(v - 1 for v in vistos[f].values() if v > 1)
        resumen[f] = {'ejemplos': len(ks), 'tokens: mediana': int(st.median(toks[f])), 'tokens: máximo': max(toks[f]),
                      'repeticiones exactas': rep, **{k: v for k, v in inf[f].items() if k != 'ejemplos'}}

    # ---------------------------------------------------------------- cercanía con el benchmark retenido
    fuga = {}
    if cfg.benchmark:
        bench = leer_original(cfg.benchmark)
        pb = {}
        for q in bench:
            pb.setdefault(q['id_pregunta'].split('-')[0], []).append((q['id_pregunta'], set(plano(q['pregunta']).split()), q['pregunta']))
        exactas = {plano(q['pregunta']) for q in bench}
        iguales, cerca, maximos = 0, [], collections.defaultdict(float)
        for x in tr:
            c = x['crudo']
            if c['tipo'] != 'conversación':
                continue
            p = plano(c['pregunta'])
            if p in exactas:
                iguales += 1
            for idp, sb, textob in pb.get(str(x.get('nid') or ''), []):
                sp = set(p.split())
                j = len(sp & sb) / max(len(sp | sb), 1)
                maximos[idp] = max(maximos[idp], j)
                if j >= 0.8:
                    cerca.append({'similitud': round(j, 2), 'benchmark': textob, 'entrenamiento': c['pregunta'], 'fuente': c['fuente']})
        ms = [maximos.get(q['id_pregunta'], 0.0) for q in bench]
        fuga = {'preguntas del benchmark': len(bench), 'preguntas de entrenamiento idénticas a una del benchmark': iguales,
                'pares con similitud de palabras >= 0,8 (misma norma)': len(cerca),
                'preguntas del benchmark con una de entrenamiento >= 0,8': sum(m >= 0.8 for m in ms),
                'similitud máxima con el entrenamiento: mediana': round(st.median(ms), 2),
                'ejemplos': sorted(cerca, key=lambda r: -r['similitud'])[:8]}

    json.dump({'modelo': a.modelo, 'ejemplos': len(tr), 'validacion': len(va), 'por_fuente': resumen,
               'ejemplos_de_problemas': {f: dict(v) for f, v in ej.items()}, 'benchmark': fuga},
              open(os.path.join(cfg.salida, 'control.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n== por fuente')
    for f, r in sorted(resumen.items(), key=lambda kv: -kv[1]['ejemplos']):
        print(f'  {f}: ' + ' · '.join(f'{k} {v}' for k, v in r.items()))
    if fuga:
        print('\n== benchmark retenido: ' + ' · '.join(f'{k} {v}' for k, v in fuga.items() if k != 'ejemplos'))
    # muestra para leer y volcado completo
    rng = random.Random(0)
    with open(os.path.join(cfg.salida, 'muestra.md'), 'w', encoding='utf-8') as out:
        for f, ks in sorted(por_fuente.items(), key=lambda kv: -len(kv[1])):
            out.write(f'\n# {f} ({len(ks)} ejemplos)\n')
            for k in rng.sample(ks, min(cfg.muestra, len(ks))):
                c = tr[k]['crudo']
                if c['tipo'] == 'texto':
                    out.write(f'\n---\n**Texto** ({len(tr[k]["input_ids"])} tokens{", CORTADO" if c["cortado"] else ""}):\n\n{c["texto"][:1800]}\n')
                elif c['tipo'] == 'conversación':
                    out.write(f'\n---\n**Pedido:** {c["pregunta"][:700]}\n\n**Respuesta:** {c["respuesta"][:1500]}\n')
                else:
                    out.write(f'\n---\n**Sistema y contexto:** {" ⏎ ".join(m["content"][:500] for m in c["mensajes"])}\n\n**Respuesta:** {c["respuesta"][:1500]}\n')
    if not cfg.sin_volcado:
        with open(os.path.join(cfg.salida, 'ejemplos.jsonl'), 'w', encoding='utf-8') as out:
            for x in tr:
                out.write(json.dumps({**x['crudo'], 'tokens': len(x['input_ids']), 'nid': x.get('nid')}, ensure_ascii=False) + '\n')
    print(f'\nescrito en {cfg.salida}: control.json, muestra.md' + ('' if cfg.sin_volcado else ', ejemplos.jsonl'))


if __name__ == '__main__':
    main()

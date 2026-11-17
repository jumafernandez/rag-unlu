#!/usr/bin/env python3
"""Evaluación general de Liminar contra su modelo base: lo que el modelo no debería perder al especializarse.

Dos conjuntos públicos en español, ajenos al entrenamiento:
  - MMMLU ES-LA (OpenAI, MIT): la traducción profesional de MMLU al español de Latinoamérica, opción múltiple en 57
    materias. Se toma una muestra estratificada (18 preguntas por materia, 1.026 en total) y se mide la exactitud
    con la probabilidad de cada letra en el primer token de la respuesta (sin generar ni interpretar texto).
  - m-ArenaHard ES (Cohere, Apache 2.0): consignas abiertas y difíciles (escritura, programación, razonamiento).
    Se toma una muestra de 200; cada modelo responde (greedy) y un juez que no compite (Llama 3.3 70B) compara la
    respuesta del modelo ajustado con la de su base, en los dos órdenes para descontar el sesgo de posición.

Pasos:
  preparar  Arma las dos muestras desde los archivos bajados de Hugging Face.
  opcion    Un modelo responde la muestra de MMMLU.
  opcion-texto  Lo mismo, pero el modelo escribe su respuesta y la letra se lee del texto (para los modelos que no
            responden solo con la letra: ahí la probabilidad del primer token no mide lo que el modelo contesta).
  abiertas  Un modelo responde la muestra de m-ArenaHard.
  duelo     El juez compara pares de respuestas (ajustado contra base) en los dos órdenes; retoma lo ya juzgado.
  resumen   Exactitud de MMMLU por modelo y área, y resultado de los duelos.

Uso:
    python -m nacional.generales preparar --mmmlu mmlu_ES-LA.csv --arena m-arenahard-es.parquet --salida DIR
    python -m nacional.generales opcion --modelo RUTA [--adaptador DIR] --nombre N --preguntas F --salida DIR
    python -m nacional.generales abiertas --modelo RUTA [--adaptador DIR] --nombre N --consignas F --salida DIR
    python -m nacional.generales duelo --respuestas DIR --pares base-x:liminar-x,... --consignas F --juez RUTA --salida DIR
    python -m nacional.generales resumen --opcion DIR --duelos DIR
"""
import argparse
import json
import os
import random
import re
import sys
from collections import defaultdict

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
from nacional.linea_base import cargar, es_mistral_common, generar, leer, plantilla  # noqa: E402

LETRAS = 'ABCD'
CONSIGNA_OPCION = ('Responda la siguiente pregunta de opción múltiple solo con la letra de la opción correcta '
                   '(A, B, C o D).\n\n{pregunta}\n\nA) {A}\nB) {B}\nC) {C}\nD) {D}')
# Áreas de MMLU (Hendrycks et al., 2021): para el resumen
AREAS = {
    'STEM': 'abstract_algebra anatomy astronomy college_biology college_chemistry college_computer_science '
            'college_mathematics college_physics computer_security conceptual_physics electrical_engineering '
            'elementary_mathematics high_school_biology high_school_chemistry high_school_computer_science '
            'high_school_mathematics high_school_physics high_school_statistics machine_learning',
    'Humanidades': 'formal_logic high_school_european_history high_school_us_history high_school_world_history '
                   'international_law jurisprudence logical_fallacies moral_disputes moral_scenarios philosophy '
                   'prehistory professional_law world_religions',
    'Ciencias sociales': 'econometrics high_school_geography high_school_government_and_politics '
                         'high_school_macroeconomics high_school_microeconomics high_school_psychology '
                         'human_sexuality professional_psychology public_relations security_studies sociology '
                         'us_foreign_policy',
}
AREA_DE = {m: area for area, ms in AREAS.items() for m in ms.split()}

JUEZ_DUELO = """Usted evalúa respuestas de asistentes de inteligencia artificial. Lea la consigna del usuario y las dos respuestas, y decida cuál responde mejor a la consigna: la que sea más correcta, útil, completa y clara. No tenga en cuenta el largo por sí mismo ni el orden en que aparecen.

[Consigna]
{consigna}

[Respuesta A]
{a}

[Respuesta B]
{b}

Responda solo con "A", "B" o "empate"."""


def escribir(ruta, filas):
    with open(ruta + '.tmp', 'w', encoding='utf-8') as out:
        for f in filas:
            out.write(json.dumps(f, ensure_ascii=False) + '\n')
    os.replace(ruta + '.tmp', ruta)


def preparar(a):
    import pandas as pd
    os.makedirs(a.salida, exist_ok=True)
    d = pd.read_csv(a.mmmlu)
    rnd = random.Random(a.semilla)
    filas = []
    for materia in sorted(d.Subject.unique()):
        idx = sorted(d.index[d.Subject == materia].tolist())
        for i in sorted(rnd.sample(idx, min(a.por_materia, len(idx)))):
            r = d.loc[i]
            filas.append({'id': f'mmmlu-{int(r.iloc[0])}', 'materia': materia, 'pregunta': str(r.Question),
                          **{x: str(r[x]) for x in LETRAS}, 'respuesta': str(r.Answer).strip()})
    escribir(os.path.join(a.salida, f'mmmlu-es_la-{len(filas)}.jsonl'), filas)
    print(f'MMMLU ES-LA: {len(filas)} preguntas de {d.Subject.nunique()} materias')
    ar = pd.read_parquet(a.arena)
    idx = sorted(rnd.sample(range(len(ar)), min(a.abiertas, len(ar))))
    consignas = [{'id': str(ar.iloc[i].question_id), 'grupo': str(ar.iloc[i].cluster), 'consigna': str(ar.iloc[i].prompt)}
                 for i in idx]
    escribir(os.path.join(a.salida, f'arenahard-es-{len(consignas)}.jsonl'), consignas)
    print(f'm-ArenaHard ES: {len(consignas)} consignas de {len(ar)}')


def ids_de_letras(tok):
    """Ids posibles de cada letra como primer token de la respuesta (con y sin espacio adelante)."""
    res = {}
    for x in LETRAS:
        ids = set()
        for v in (x, ' ' + x):
            e = tok.encode(v, add_special_tokens=False)
            if e:
                ids.add(e[0] if isinstance(e, list) else int(e[0]))
        res[x] = sorted(ids)
    return res


def opcion(a):
    import torch
    qs = leer(a.preguntas)
    os.makedirs(a.salida, exist_ok=True)
    ruta = os.path.join(a.salida, f'{a.nombre}.jsonl')
    tok, m = cargar(a.modelo, a.adaptador)
    letras = ids_de_letras(tok)
    disp = m.get_input_embeddings().weight.device
    textos = [plantilla(tok, CONSIGNA_OPCION.format(pregunta=q['pregunta'], **{x: q[x] for x in LETRAS}),
                        a.modelo) for q in qs]
    filas = []
    for i in range(0, len(textos), a.lote):
        parte = textos[i:i + a.lote]
        if isinstance(parte[0], list):   # mistral-common: ids ya armados, relleno a la izquierda
            L = max(map(len, parte))
            enc = {'input_ids': torch.tensor([[tok.pad_token_id] * (L - len(x)) + x for x in parte]),
                   'attention_mask': torch.tensor([[0] * (L - len(x)) + [1] * len(x) for x in parte])}
        else:
            enc = tok(parte, return_tensors='pt', padding=True, add_special_tokens=False)
        enc = {k: v.to(disp) for k, v in enc.items()}
        with torch.no_grad():
            ult = m(**enc, logits_to_keep=1).logits[:, -1, :].float()
        lp = torch.log_softmax(ult, dim=-1).cpu()
        for j, q in enumerate(qs[i:i + a.lote]):
            punt = {x: max(float(lp[j, t]) for t in letras[x]) for x in LETRAS}
            pred = max(punt, key=punt.get)
            filas.append({'id': q['id'], 'materia': q['materia'], 'respuesta': q['respuesta'], 'prediccion': pred,
                          'acierto': pred == q['respuesta'], 'logprob': {x: round(v, 3) for x, v in punt.items()}})
        print(f'  {min(i + a.lote, len(textos))}/{len(textos)}', flush=True)
    escribir(ruta, filas)
    print(f'{a.nombre}: exactitud {100 * sum(f["acierto"] for f in filas) / len(filas):.1f} % en {len(filas)} preguntas')


# Lectura de la letra en una respuesta escrita. Los patrones van del más explícito al más débil; se usa el primero que
# aparece. Una respuesta sin letra reconocible cuenta como error (como en simple-evals de OpenAI, el arnés con que se
# publicó MMMLU: extrae la letra con una expresión regular y lo que no se puede leer es incorrecto).
_L = r'\W{0,4}([ABCD])(?![A-Za-zÁÉÍÓÚáéíóúñÑ])'
PATRONES_LETRA = [
    # la respuesta es solo la letra, o empieza con la letra seguida de un cierre: "C", "**B**", "A) Alvéolos", "D."
    re.compile(r'^\W*([ABCD])(?:\s*[\)\.\:\*]|\s*$|\s*\n)'),
    # "la respuesta/opción/alternativa correcta es (la opción) X"
    re.compile(r'(?i:respuesta|opci[oó]n|alternativa)\s+(?i:correcta)\s+(?i:es|ser[ií]a)\s*:?\s*(?i:la\s+)?(?i:opci[oó]n\s+|letra\s+)?' + _L),
    # "la respuesta es X", "Respuesta: X", "la opción X"
    re.compile(r'(?i:respuesta|answer)\s*(?i:es|ser[ií]a|is)?\s*:?\s*(?i:la\s+)?(?i:opci[oó]n\s+|letra\s+)?' + _L),
    re.compile(r'(?i:la\s+opci[oó]n|la\s+alternativa|la\s+letra)\s+' + _L),
    # letra en negrita o entre paréntesis: "**D**", "**C) 3 N**", "(A)"
    re.compile(r'\*\*\(?([ABCD])\)?\s*[\)\*\.:]'),
    re.compile(r'\(([ABCD])\)'),
]


def _plano(s):
    """Texto en minúsculas, sin marcas de formato ni espacios repetidos (para comparar con el texto de una opción)."""
    return re.sub(r'\s+', ' ', re.sub(r'[*_`]', '', str(s or '')).replace('\xa0', ' ')).strip().lower()


def letra_del_texto(texto, opciones=None):
    """Letra (A-D) que el modelo da como respuesta en un texto, o None si no se puede saber. Si no nombra ninguna
    letra y se pasan las opciones ({'A': texto, ...}), vale la opción cuyo texto aparece en la respuesta, siempre que
    sea una sola («La relación es 3:1.» cuando la opción C es «3:1»)."""
    t = (texto or '').strip()
    for pat in PATRONES_LETRA:
        m = pat.search(t)
        if m:
            return m.group(1)
    if opciones:
        plano = _plano(t)
        hay = [x for x, o in opciones.items() if len(_plano(o)) >= 3 and _plano(o) in plano]
        if len(hay) == 1:
            return hay[0]
    return None


def opcion_texto(a):
    """Como opcion, pero generando: misma consigna, el modelo escribe (greedy, hasta --max-nuevos tokens) y la letra se
    lee del texto. Guarda el texto, así la lectura se puede rehacer sin volver a generar."""
    qs = leer(a.preguntas)
    os.makedirs(a.salida, exist_ok=True)
    ruta = os.path.join(a.salida, f'{a.nombre}.jsonl')
    if os.path.exists(ruta) and len(leer(ruta)) >= len(qs):
        print(f'{a.nombre}: ya estaba')
        return
    tok, m = cargar(a.modelo, a.adaptador)
    textos = [plantilla(tok, CONSIGNA_OPCION.format(pregunta=q['pregunta'], **{x: q[x] for x in LETRAS}),
                        a.modelo) for q in qs]
    resp, tokens, seg = generar(tok, m, textos, a.max_nuevos, a.lote)
    filas = []
    for q, r in zip(qs, resp):
        pred = letra_del_texto(r, {x: q[x] for x in LETRAS})
        filas.append({'id': q['id'], 'materia': q['materia'], 'respuesta': q['respuesta'], 'texto': r,
                      'prediccion': pred, 'acierto': pred == q['respuesta']})
    escribir(ruta, filas)
    sin = sum(f['prediccion'] is None for f in filas)
    print(f'{a.nombre}: exactitud {100 * sum(f["acierto"] for f in filas) / len(filas):.1f} % en {len(filas)} preguntas '
          f'(sin letra reconocible: {sin}); {tokens} tokens en {seg:.0f} s')


def leer_tolerante(ruta):
    """Como leer, pero salteando una última línea cortada (un corte del piloto a mitad de una escritura)."""
    filas = []
    if os.path.exists(ruta):
        for l in open(ruta, encoding='utf-8'):
            try:
                filas.append(json.loads(l))
            except json.JSONDecodeError:
                pass
    return filas


def abiertas(a):
    """Retoma: escribe cada dos lotes, así un piloto que termina a mitad de camino no pierde lo generado."""
    cs = leer(a.consignas)
    os.makedirs(a.salida, exist_ok=True)
    ruta = os.path.join(a.salida, f'{a.nombre}.jsonl')
    previas = leer_tolerante(ruta)
    hechas = {r['id'] for r in previas}
    faltan = [c for c in cs if c['id'] not in hechas]
    if not faltan:
        print(f'{a.nombre}: ya estaban las {len(cs)} respuestas')
        return
    escribir(ruta, previas)   # reescribe sin la línea cortada, si la había
    tok, m = cargar(a.modelo, a.adaptador)
    tokens = segundos = 0
    for i in range(0, len(faltan), 2 * a.lote):
        parte = faltan[i:i + 2 * a.lote]
        resp, tk, sg = generar(tok, m, [plantilla(tok, c['consigna'], a.modelo) for c in parte], a.max_nuevos, a.lote)
        tokens, segundos = tokens + tk, segundos + sg
        with open(ruta, 'a', encoding='utf-8') as out:
            out.write(''.join(json.dumps({'id': c['id'], 'modelo': a.nombre, 'respuesta': r}, ensure_ascii=False) + '\n'
                              for c, r in zip(parte, resp)))
        print(f'{a.nombre}: {len(hechas) + i + len(parte)}/{len(cs)} respuestas', flush=True)
    print(f'{a.nombre}: {len(faltan)} respuestas nuevas, {tokens} tokens en {segundos:.0f} s')


def duelo(a):
    cs = {c['id']: c for c in leer(a.consignas)}
    os.makedirs(a.salida, exist_ok=True)
    tok, m = None, None
    for par in a.pares.split(','):
        base, ajustado = par.split(':')
        rb = os.path.join(a.respuestas, f'{base}.jsonl')
        ra = os.path.join(a.respuestas, f'{ajustado}.jsonl')
        if not (os.path.exists(rb) and os.path.exists(ra)):
            print(f'{par}: faltan respuestas, se saltea', flush=True)
            continue
        B = {r['id']: r['respuesta'] for r in leer(rb)}
        A = {r['id']: r['respuesta'] for r in leer(ra)}
        destino = os.path.join(a.salida, f'{ajustado}__vs__{base}.jsonl')
        hechos = {(j['id'], j['orden']) for j in leer(destino)} if os.path.exists(destino) else set()
        pend = [(i, o) for i in cs if i in A and i in B for o in ('ajustado-primero', 'base-primero')
                if (i, o) not in hechos]
        if not pend:
            print(f'{par}: ya juzgado', flush=True)
            continue
        if tok is None:
            tok, m = cargar(a.juez)
        print(f'{par}: {len(pend)} juicios por hacer', flush=True)
        for k in range(0, len(pend), a.lote * 4):   # se escribe cada cuatro lotes: si el proceso cae, se retoma
            parte = pend[k:k + a.lote * 4]
            textos = []
            for i, o in parte:
                x, y = (A[i], B[i]) if o == 'ajustado-primero' else (B[i], A[i])
                textos.append(plantilla(tok, JUEZ_DUELO.format(consigna=cs[i]['consigna'], a=x[:6000], b=y[:6000]),
                                        a.juez))
            notas, _, _ = generar(tok, m, textos, 4, a.lote)
            with open(destino, 'a', encoding='utf-8') as out:
                for (i, o), n in zip(parte, notas):
                    t = n.strip().lower()
                    v = 'empate' if t.startswith('emp') else ('A' if t.startswith('a') else ('B' if t.startswith('b') else None))
                    gana = None if v is None else ('empate' if v == 'empate' else
                                                   ('ajustado' if (v == 'A') == (o == 'ajustado-primero') else 'base'))
                    out.write(json.dumps({'id': i, 'orden': o, 'veredicto': v, 'gana': gana, 'crudo': n},
                                         ensure_ascii=False) + '\n')
    print('LISTO duelos')


def resumen(a):
    if a.opcion and os.path.isdir(a.opcion):
        print('MMMLU ES-LA (exactitud, %)')
        print(f'{"modelo":38s} {"total":>6s} ' + ' '.join(f'{x:>17s}' for x in (*AREAS, 'Otras')))
        for ruta in sorted(f for f in os.listdir(a.opcion) if f.endswith('.jsonl')):
            fs = leer(os.path.join(a.opcion, ruta))
            por = defaultdict(list)
            for f in fs:
                por[AREA_DE.get(f['materia'], 'Otras')].append(f['acierto'])
            celdas = ' '.join(f'{100 * sum(por[x]) / len(por[x]):17.1f}' if por[x] else f'{"-":>17s}'
                              for x in (*AREAS, 'Otras'))
            print(f'{ruta[:-6]:38s} {100 * sum(f["acierto"] for f in fs) / len(fs):6.1f} {celdas}')
    if a.duelos and os.path.isdir(a.duelos):
        print('\nm-ArenaHard ES: ajustado contra base (juez Llama 3.3 70B, los dos órdenes)')
        print(f'{"duelo":60s} {"gana aj.":>9s} {"empate":>7s} {"gana base":>9s} {"sin leer":>8s} {"tasa aj.":>8s}')
        for ruta in sorted(f for f in os.listdir(a.duelos) if f.endswith('.jsonl')):
            js = leer(os.path.join(a.duelos, ruta))
            c = defaultdict(int)
            for j in js:
                c[j['gana']] += 1
            n = c['ajustado'] + c['empate'] + c['base']
            tasa = (c['ajustado'] + 0.5 * c['empate']) / n if n else float('nan')
            print(f'{ruta[:-6]:60s} {c["ajustado"]:9d} {c["empate"]:7d} {c["base"]:9d} {c[None]:8d} {100 * tasa:7.1f} %')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paso', choices=['preparar', 'opcion', 'opcion-texto', 'abiertas', 'duelo', 'resumen'])
    ap.add_argument('--mmmlu')
    ap.add_argument('--arena')
    ap.add_argument('--salida')
    ap.add_argument('--semilla', type=int, default=0)
    ap.add_argument('--por-materia', type=int, default=18)
    ap.add_argument('--abiertas', type=int, default=200)
    ap.add_argument('--modelo')
    ap.add_argument('--adaptador', help='adaptador LoRA entrenado sobre --modelo (se fusiona)')
    ap.add_argument('--nombre')
    ap.add_argument('--preguntas')
    ap.add_argument('--consignas')
    ap.add_argument('--respuestas')
    ap.add_argument('--pares', help='base:ajustado separados por coma (nombres de los archivos de respuestas)')
    ap.add_argument('--juez', default='/data/contrib/pci_146/hf-models/Llama-3.3-70B-Instruct')
    ap.add_argument('--opcion')
    ap.add_argument('--duelos')
    ap.add_argument('--max-nuevos', type=int, default=1024)
    ap.add_argument('--lote', type=int, default=16)
    a = ap.parse_args()
    {'preparar': preparar, 'opcion': opcion, 'opcion-texto': opcion_texto, 'abiertas': abiertas, 'duelo': duelo,
     'resumen': resumen}[a.paso](a)


if __name__ == '__main__':
    main()

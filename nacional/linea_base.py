#!/usr/bin/env python3
"""Línea de base de la capa nacional: el "antes" del benchmark. Cada modelo base (y después cada modelo
entrenado) responde las mismas preguntas apartadas, a libro cerrado; un juez califica todas las respuestas.

Pasos:
  preguntas  Arma el conjunto de evaluación con las preguntas de los documentos marcados como
             evaluación en `generar.py` (no se usan para entrenar). Cada una lleva la respuesta
             de referencia que escribió el 27B con el documento a la vista.
  responder  Un modelo contesta todas las preguntas sin contexto (greedy, en lotes) y se mide
             la velocidad de generación. Corre en un trabajador de piloto (2 tiles).
  juzgar     Un modelo que no compite (Llama 3.3 70B, descartado como base por su licencia)
             califica cada respuesta contra la de referencia: 0 incorrecta, 1 parcial,
             2 correcta.
  resumen    Tabla por modelo: calificación media, % correctas, marcas del español de España y
             tokens por segundo.

Uso:
    python -m nacional.linea_base preguntas --generados DIR,DIR --salida preguntas.jsonl [--n 300]
    python -m nacional.linea_base responder --modelo RUTA [--adaptador DIR] --nombre N --preguntas F --salida DIR
    python -m nacional.linea_base juzgar --respuestas DIR --preguntas F --juez RUTA --salida DIR
    python -m nacional.linea_base resumen --respuestas DIR --juicios DIR
"""
import argparse
import glob
import json
import os
import random
import re
import sys
import time

INSTRUCCION = ('Respondé en español, de forma breve y precisa, la siguiente pregunta sobre normativa del sistema '
               'universitario argentino. Si no sabés la respuesta, decilo.\n\nPregunta: {pregunta}')

PENINSULAR = re.compile(r'\b(vosotros|vosotras|habéis|tenéis|podéis|queréis|sois|estáis|os|vuestr[oa]s?|ordenador|'
                        r'móvil|vale|coger|conducir|aparcar)\b', re.I)

JUEZ = """Calificá la respuesta de un modelo a una pregunta sobre normativa universitaria argentina, comparándola con una respuesta de referencia escrita con la norma a la vista.

Pregunta: {pregunta}

Respuesta de referencia: {referencia}

Respuesta del modelo: {respuesta}

Criterio:
2 = correcta: coincide en lo sustancial con la referencia (puede decirlo con otras palabras o agregar datos correctos).
1 = parcial: acierta en algo importante pero omite o confunde parte de lo sustancial.
0 = incorrecta: contradice la referencia, inventa datos o no responde.

Respondé solo con un número: 0, 1 o 2."""


def leer(ruta):
    return [json.loads(l) for l in open(ruta, encoding='utf-8')] if os.path.exists(ruta) else []


# ---------------------------------------------------------------- preguntas
def preguntas(a):
    filas = []
    for ruta in (r for c in a.generados.split(',') for r in glob.glob(os.path.join(c, 'parte_*.jsonl'))):
        for r in leer(ruta):
            if not r.get('evaluacion') or not r.get('json_ok'):
                continue
            for k, q in enumerate((r['datos'] or {}).get('preguntas') or []):
                if isinstance(q, dict) and q.get('pregunta') and q.get('respuesta'):
                    clave = r.get('id_fragmento') or r['id_doc']
                    filas.append({'id_pregunta': f'{clave}-q{k}', 'id_doc': r['id_doc'], 'norma': r['norma'],
                                  'voz': q.get('voz'), 'pregunta': q['pregunta'], 'referencia': q['respuesta']})
    random.Random(0).shuffle(filas)
    filas = filas[:a.n] if a.n else filas
    with open(a.salida, 'w', encoding='utf-8') as out:
        for f in filas:
            out.write(json.dumps(f, ensure_ascii=False) + '\n')
    print(f'{len(filas)} preguntas en {a.salida}')


# ---------------------------------------------------------------- generación genérica
def es_mistral_common(tok):
    """Mistral Small 3.2 solo trae tekken.json: el tokenizador es el de mistral-common (hay que agregarlo al
    PYTHONPATH), arma la conversación directo a ids y no reconoce los tokens especiales dentro de un texto."""
    return type(tok).__name__.startswith('MistralCommon')


def cargar(ruta, adaptador=None):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(ruta)
    if not es_mistral_common(tok):
        tok.padding_side = 'left'
        if tok.pad_token_id is None:
            tok.pad_token = tok.eos_token
    if adaptador:   # el LoRA se entrenó sobre la clase de config.json: hay que cargar el base con esa clase
        import transformers
        clase = getattr(transformers, transformers.AutoConfig.from_pretrained(ruta).architectures[0])
        m = clase.from_pretrained(ruta, dtype=torch.bfloat16, device_map='balanced')
        from peft import PeftModel
        m = PeftModel.from_pretrained(m, adaptador).merge_and_unload()
        return tok, m.eval()
    try:
        m = AutoModelForCausalLM.from_pretrained(ruta, dtype=torch.bfloat16, device_map='balanced')
    except (ValueError, KeyError):
        from transformers import AutoModelForImageTextToText
        m = AutoModelForImageTextToText.from_pretrained(ruta, dtype=torch.bfloat16, device_map='balanced')
    return tok, m.eval()


def generar(tok, m, textos, max_nuevos, lote):
    """Greedy en lotes; devuelve (respuestas, tokens generados, segundos)."""
    import torch
    res, tokens, segundos = [], 0, 0.0
    disp = m.get_input_embeddings().weight.device
    for i in range(0, len(textos), lote):
        parte = textos[i:i + lote]
        if isinstance(parte[0], list):   # ids ya armados (mistral-common): relleno a la izquierda a mano
            L = max(map(len, parte))
            enc = {'input_ids': torch.tensor([[tok.pad_token_id] * (L - len(x)) + x for x in parte]),
                   'attention_mask': torch.tensor([[0] * (L - len(x)) + [1] * len(x) for x in parte])}
        else:
            enc = tok(parte, return_tensors='pt', padding=True, add_special_tokens=False)
        enc = {k: v.to(disp) for k, v in enc.items()}
        t0 = time.time()
        with torch.no_grad():
            out = m.generate(**enc, max_new_tokens=max_nuevos, do_sample=False, pad_token_id=tok.pad_token_id)
        torch.xpu.synchronize()
        segundos += time.time() - t0
        nuevos = out[:, enc['input_ids'].shape[1]:]
        for fila in nuevos:
            ids = [t for t in fila.tolist() if t != tok.pad_token_id]
            tokens += len(ids)
            res.append(tok.decode(ids, skip_special_tokens=True).strip())
        print(f'  {min(i + lote, len(textos))}/{len(textos)}', flush=True)
    return res, tokens, segundos


def plantilla(tok, contenido, ruta):
    msgs = [{'role': 'user', 'content': contenido}]
    if es_mistral_common(tok):
        r = tok.apply_chat_template(msgs, tokenize=True)
        return list(r['input_ids'] if hasattr(r, 'keys') else r)
    kw = {'enable_thinking': False} if 'qwen' in ruta.lower() else {}
    return tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, **kw)


def responder(a):
    qs = leer(a.preguntas)
    os.makedirs(a.salida, exist_ok=True)
    ruta = os.path.join(a.salida, f'{a.nombre}.jsonl')
    if os.path.exists(ruta) and len(leer(ruta)) >= len(qs):
        print(f'{a.nombre}: ya estaba')
        return
    tok, m = cargar(a.modelo, a.adaptador)
    textos = [plantilla(tok, INSTRUCCION.format(pregunta=q['pregunta']), a.modelo) for q in qs]
    resp, tokens, seg = generar(tok, m, textos, a.max_nuevos, a.lote)
    with open(ruta, 'w', encoding='utf-8') as out:
        for q, r in zip(qs, resp):
            out.write(json.dumps({'id_pregunta': q['id_pregunta'], 'modelo': a.nombre, 'respuesta': r},
                                 ensure_ascii=False) + '\n')
    json.dump({'modelo': a.nombre, 'ruta': a.modelo, 'preguntas': len(qs), 'tokens': tokens, 'segundos': round(seg, 1),
               'tokens_por_segundo': round(tokens / max(seg, 1e-6), 1), 'lote': a.lote},
              open(os.path.join(a.salida, f'{a.nombre}.velocidad.json'), 'w'), indent=1)
    print(f'LISTO {a.nombre}: {tokens} tokens en {seg:.0f} s')


def juzgar(a):
    qs = {q['id_pregunta']: q for q in leer(a.preguntas)}
    os.makedirs(a.salida, exist_ok=True)
    tok, m = cargar(a.juez)
    for ruta in sorted(glob.glob(os.path.join(a.respuestas, '*.jsonl'))):
        nombre = os.path.basename(ruta)[:-6]
        destino = os.path.join(a.salida, f'{nombre}.jsonl')
        if os.path.exists(destino):
            continue
        rs = leer(ruta)
        textos = [plantilla(tok, JUEZ.format(pregunta=qs[r['id_pregunta']]['pregunta'],
                                             referencia=qs[r['id_pregunta']]['referencia'],
                                             respuesta=r['respuesta'][:3000]), a.juez) for r in rs]
        notas, _, _ = generar(tok, m, textos, 4, a.lote)
        with open(destino, 'w', encoding='utf-8') as out:
            for r, n in zip(rs, notas):
                d = re.search(r'[012]', n)
                out.write(json.dumps({'id_pregunta': r['id_pregunta'], 'modelo': nombre,
                                      'nota': int(d.group()) if d else None, 'crudo': n}, ensure_ascii=False) + '\n')
        print(f'juzgado {nombre}', flush=True)
    print('LISTO juicios')


def resumen(a):
    filas = []
    for ruta in sorted(glob.glob(os.path.join(a.respuestas, '*.jsonl'))):
        nombre = os.path.basename(ruta)[:-6]
        rs = leer(ruta)
        js = [j for j in leer(os.path.join(a.juicios, f'{nombre}.jsonl')) if j['nota'] is not None] if a.juicios else []
        vel = json.load(open(ruta[:-6] + '.velocidad.json')) if os.path.exists(ruta[:-6] + '.velocidad.json') else {}
        pen = sum(1 for r in rs if PENINSULAR.search(r['respuesta']))
        filas.append((nombre, len(rs), sum(j['nota'] for j in js) / len(js) if js else None,
                      100 * sum(j['nota'] == 2 for j in js) / len(js) if js else None, 100 * pen / max(len(rs), 1),
                      vel.get('tokens_por_segundo')))
    print(f'{"modelo":32} {"n":>4} {"nota media":>10} {"% correctas":>12} {"% peninsular":>13} {"tok/s":>7}')
    def cel(x, d):
        return '-' if x is None else str(round(x, d))

    for n, k, nm, pc, pe, v in sorted(filas, key=lambda x: (-(x[2] or 0), x[0])):
        print(f'{n:32} {k:>4} {cel(nm, 2):>10} {cel(pc, 1):>12} {cel(pe, 1):>13} {cel(v, 1):>7}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paso', choices=['preguntas', 'responder', 'juzgar', 'resumen'])
    ap.add_argument('--generados')
    ap.add_argument('--preguntas')
    ap.add_argument('--salida')
    ap.add_argument('--modelo')
    ap.add_argument('--nombre')
    ap.add_argument('--juez')
    ap.add_argument('--adaptador', help='responder: adaptador LoRA entrenado sobre --modelo (se fusiona para generar)')
    ap.add_argument('--respuestas')
    ap.add_argument('--juicios')
    ap.add_argument('--n', type=int, default=300)
    ap.add_argument('--max-nuevos', type=int, default=400)
    ap.add_argument('--lote', type=int, default=16)
    a = ap.parse_args()
    {'preguntas': preguntas, 'responder': responder, 'juzgar': juzgar, 'resumen': resumen}[a.paso](a)


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(1)

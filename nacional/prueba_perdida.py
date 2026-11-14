#!/usr/bin/env python3
"""Control del paso hacia adelante de entrenamiento: pérdida del modelo sobre sus propias respuestas de la línea
de base (las generó con decodificación greedy, así que tiene que ser baja) y sobre las respuestas de referencia
(las escribió el Qwen 3.5 27B). Usa el mismo formato, lotes y pérdida que entrenar.py, sin FSDP (device_map).

Uso: python -m nacional.prueba_perdida RUTA_DEL_MODELO RESPUESTAS.jsonl PREGUNTAS.jsonl [--n 64]
"""
import argparse
import json
import sys

import torch
import transformers

from nacional.linea_base import INSTRUCCION
from nacional.entrenar import Formato

ap = argparse.ArgumentParser()
ap.add_argument('modelo')
ap.add_argument('respuestas')
ap.add_argument('preguntas')
ap.add_argument('--n', type=int, default=64)
a = ap.parse_args()

tok = transformers.AutoTokenizer.from_pretrained(a.modelo)
fmt = Formato(tok)
clase = getattr(transformers, transformers.AutoConfig.from_pretrained(a.modelo).architectures[0])
m = clase.from_pretrained(a.modelo, dtype=torch.bfloat16, device_map='balanced').eval()
disp = m.get_input_embeddings().weight.device
preguntas = {q['id_pregunta']: q for q in map(json.loads, open(a.preguntas, encoding='utf-8'))}
filas = [json.loads(l) for l in open(a.respuestas, encoding='utf-8')][:a.n]


@torch.no_grad()
def media(textos_respuesta):
    """Pérdida media por token de la respuesta, de a una secuencia (sin relleno). Los logits se calculan enteros:
    con el modelo partido entre tiles, pasar posiciones (logits_to_keep) las deja en el tile equivocado."""
    tot, n = 0.0, 0
    for r, resp in zip(filas, textos_respuesta):
        x = fmt.chat(INSTRUCCION.format(pregunta=preguntas[r['id_pregunta']]['pregunta']), resp, 4096)
        logits = m(input_ids=torch.tensor([x['input_ids']], device=disp)).logits[0, :-1].float()
        y = torch.tensor(x['labels'][1:], device=logits.device)
        k = y != -100
        tot += float(torch.nn.functional.cross_entropy(logits[k], y[k], reduction='sum'))
        n += int(k.sum())
    return tot / max(n, 1)


print(f'{a.modelo.rstrip("/").split("/")[-1]} · {len(filas)} preguntas', flush=True)
print(f'  pérdida sobre sus propias respuestas (greedy): {media([r["respuesta"] for r in filas]):.3f}', flush=True)
print(f'  pérdida sobre las respuestas de referencia (Qwen 27B): '
      f'{media([preguntas[r["id_pregunta"]]["referencia"] for r in filas]):.3f}', flush=True)
m.train()
print(f'  ídem en modo entrenamiento (dropout): {media([r["respuesta"] for r in filas]):.3f}', flush=True)

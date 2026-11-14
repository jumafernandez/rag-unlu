#!/usr/bin/env python3
"""Cuenta el conjunto de entrenamiento tal como lo arma entrenar.py para un tokenizador: ejemplos, tokens y pasos
por época con un presupuesto de tokens por lote y una cantidad de procesos. Corre en la CPU.

Uso: python -m nacional.contar_datos --datos DIR --generados D1,D2 [--abiertas DIR] --modelo RUTA [--procesos 8]
"""
import argparse
from types import SimpleNamespace

from transformers import AutoTokenizer

from nacional.entrenar import Formato, armar_ejemplos, lotes_por_tokens

ap = argparse.ArgumentParser()
ap.add_argument('--datos', required=True)
ap.add_argument('--generados', required=True)
ap.add_argument('--abiertas')
ap.add_argument('--modelo', required=True)
ap.add_argument('--procesos', type=int, default=8)
ap.add_argument('--max-tokens', type=int, default=3072)
a = ap.parse_args()
cfg = SimpleNamespace(datos=a.datos, generados=a.generados, abiertas=a.abiertas, max_tokens=a.max_tokens,
                      max_validacion=400, semilla=0)
fmt = Formato(AutoTokenizer.from_pretrained(a.modelo))
tr, va, largos = armar_ejemplos(cfg, fmt)
tokens = sum(len(x['input_ids']) for x in tr)
con_perdida = sum(sum(1 for l in x['labels'] if l != -100) for x in tr)
print(f'{a.modelo.rstrip("/").split("/")[-1]}: {len(tr)} ejemplos · {tokens / 1e6:.1f} M tokens '
      f'({con_perdida / 1e6:.1f} M con pérdida) · validación {len(va)} · descartados por largo {largos}')
for presupuesto, acum in ((4096, 1), (2048, 2)):
    lotes = len(lotes_por_tokens(tr, presupuesto, 0))
    print(f'  lotes de {presupuesto} × {acum}: {lotes // (a.procesos * acum)} pasos por época con {a.procesos} procesos')

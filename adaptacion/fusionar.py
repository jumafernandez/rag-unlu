#!/usr/bin/env python3
"""Guarda el 9B como modelo de Hugging Face, con el adaptador LoRA fusionado si se pasa uno.

Es el paso previo a convertirlo a GGUF para llama.cpp. El modelo sin adaptar pasa por el
mismo camino (cargar y guardar con la misma clase) para que las dos conversiones partan
de archivos equivalentes y la única diferencia entre ellas sea el ajuste. Corre en CPU.

Uso:
    python -m adaptacion.fusionar --salida DIR [--adaptador DIR] [--modelo DIR]
"""
import argparse
import os

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--modelo', default=os.path.expanduser('~/hf-models/Qwen3.5-9B'))
    ap.add_argument('--adaptador')
    ap.add_argument('--salida', required=True)
    a = ap.parse_args()
    m = AutoModelForCausalLM.from_pretrained(a.modelo, dtype=torch.bfloat16)
    if a.adaptador:
        from peft import PeftModel
        m = PeftModel.from_pretrained(m, a.adaptador).merge_and_unload()
    m.save_pretrained(a.salida, safe_serialization=True)
    AutoTokenizer.from_pretrained(a.modelo).save_pretrained(a.salida)
    print(f'guardado en {a.salida}: {type(m).__name__}', flush=True)


if __name__ == '__main__':
    main()

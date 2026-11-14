#!/usr/bin/env python3
"""Diagnóstico: ¿qué retiene en la CPU un modelo cargado con from_pretrained después de soltar sus pesos?

Con FSDP, el Gemma 4 31B seguía ocupando ~60 GB de la CPU por proceso después de repartirse entre los tiles
(el Qwen 3.5, con o sin LoRA, bajaba a ~1,5 GB). Acá se carga el modelo en la CPU, se pasan los pesos al
dispositivo 'meta' (se sueltan) y se mira cuánto queda y qué tensores siguen vivos, y quién los referencia.

Uso (en un nodo de CPU): python -m nacional.prueba_memoria RUTA_DEL_MODELO [--lora] [--sin-mmap]
"""
import ctypes
import gc
import sys

import torch
import transformers


def rss():
    campos = {l.split(':')[0]: int(l.split()[1]) / 2**20 for l in open('/proc/self/status')
              if l.startswith(('VmRSS', 'RssAnon', 'RssFile'))}
    return f"{campos['VmRSS']:.1f} GiB (anónima {campos['RssAnon']:.1f}, de archivo {campos['RssFile']:.1f})"


def liberar():
    gc.collect()
    ctypes.CDLL('libc.so.6').malloc_trim(0)


ruta = sys.argv[1]
clase = getattr(transformers, transformers.AutoConfig.from_pretrained(ruta).architectures[0])
m = clase.from_pretrained(ruta, dtype=torch.bfloat16, disable_mmap='--sin-mmap' in sys.argv)
liberar()
print(f'cargado: {rss()}', flush=True)
if '--lora' in sys.argv:   # como en entrenar.py: LoRA sobre las lineales del modelo de texto
    import re
    from peft import LoraConfig, get_peft_model
    no_texto = re.compile(r'visual|vision|audio|multi_modal_projector')
    lineales = [n for n, mod in m.named_modules() if isinstance(mod, torch.nn.Linear) and not no_texto.search(n)
                and not n.endswith('lm_head') and not n.endswith(('.mlp.gate', 'shared_expert_gate'))]
    m = get_peft_model(m, LoraConfig(r=64, lora_alpha=128, lora_dropout=0.05, target_modules=lineales,
                                     task_type='CAUSAL_LM'))
    m.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    liberar()
    print(f'con LoRA: {rss()}', flush=True)
with torch.no_grad():   # leer cada peso, como cuando FSDP lo copia al tile
    for p in m.parameters():
        p.data.float().sum()
liberar()
print(f'pesos leídos: {rss()}', flush=True)
m.to('meta')
liberar()
print(f'pesos soltados (meta): {rss()}', flush=True)
vivos = [o for o in gc.get_objects() if torch.is_tensor(o) and o.device.type == 'cpu']
print(f'{len(vivos)} tensores en la CPU, {sum(o.untyped_storage().nbytes() for o in vivos) / 2**30:.1f} GiB '
      f'(contando almacenamientos compartidos más de una vez)', flush=True)
for g in sorted(vivos, key=lambda o: -o.untyped_storage().nbytes())[:5]:
    refs = gc.get_referrers(g)
    desc = []
    for r in refs[:4]:
        if isinstance(r, dict):
            claves = [k for k, v in r.items() if v is g][:2]
            desc.append(f'dict{claves}')
        else:
            desc.append(type(r).__name__)
    print(f'  {tuple(g.shape)} {g.dtype} {g.untyped_storage().nbytes() / 2**20:.0f} MiB ← {desc}', flush=True)
del m
liberar()
print(f'sin el modelo: {rss()}', flush=True)

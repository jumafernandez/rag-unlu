#!/usr/bin/env python3
"""Diagnóstico: ¿alguna forma de lote rompe un núcleo de la GPU, sin comunicaciones de por medio?

Carga el modelo en un solo tile con LoRA (como entrenar.py, sin FSDP) y para cada forma BxT hace la pasada hacia
adelante y hacia atrás con la misma pérdida (logits solo donde hay etiqueta) y recálculo de activaciones,
sincronizando después de cada una. Si una forma se cae acá, la falla es del cómputo y no de oneCCL.

Uso: python -m nacional.prueba_formas RUTA_DEL_MODELO 45x91 39x103 15x258 ... [--chat]
"""
import sys
import time

import torch
import transformers

from adaptacion.entrenar_lora import perdida

ruta = sys.argv[1]
formas = [tuple(int(x) for x in f.split('x')) for f in sys.argv[2:] if 'x' in f]
chat = '--chat' in sys.argv   # etiquetas solo en el último cuarto (como una respuesta); si no, en todo (texto plano)
dev = torch.device('xpu', 0)
clase = getattr(transformers, transformers.AutoConfig.from_pretrained(ruta).architectures[0])
m = clase.from_pretrained(ruta, dtype=torch.bfloat16).to(dev)
from peft import LoraConfig, get_peft_model  # noqa: E402
lineales = [n for n, mod in m.named_modules() if isinstance(mod, torch.nn.Linear)
            and not any(x in n for x in ('visual', 'vision', 'audio', 'multi_modal_projector')) and not n.endswith('lm_head')]
m = get_peft_model(m, LoraConfig(r=16, lora_alpha=32, target_modules=lineales, task_type='CAUSAL_LM'))
m.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
m.train()
vocab = m.get_input_embeddings().weight.shape[0]
for b, t in formas:
    ids = torch.randint(10, vocab - 10, (b, t))
    lab = ids.clone()
    if chat:
        lab[:, : (3 * t) // 4] = -100
    att = torch.ones_like(ids)
    t0 = time.time()
    loss, n = perdida(m, ids, lab, att, dev)
    loss.backward()
    torch.xpu.synchronize(dev)
    m.zero_grad(set_to_none=True)
    print(f'{b}x{t}: ok · pérdida {float(loss):.2f} · {time.time() - t0:.1f} s · memoria máx '
          f'{torch.xpu.max_memory_allocated(dev) / 2**30:.1f} GiB', flush=True)
    torch.xpu.reset_peak_memory_stats(dev)
print('LISTO', flush=True)

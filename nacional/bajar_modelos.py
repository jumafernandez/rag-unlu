#!/usr/bin/env python3
"""Baja modelos de Hugging Face a /data/contrib/pci_146/hf-models/<nombre>, por el proxy de Clementina.

Sin Xet (por el proxy las descargas se traban) y con las variables del proxy también en mayúsculas.
Retoma lo que falte. Corre como spec de un piloto, no en el nodo de login.

Uso:
    python -m nacional.bajar_modelos google/gemma-4-E4B-it google/gemma-4-12B-it ...
"""
import os
import sys

os.environ['HF_HUB_OFFLINE'] = '0'
os.environ['TRANSFORMERS_OFFLINE'] = '0'
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
for v in ('https_proxy', 'http_proxy', 'no_proxy'):
    if os.environ.get(v) and not os.environ.get(v.upper()):
        os.environ[v.upper()] = os.environ[v]

from huggingface_hub import snapshot_download  # noqa: E402

DESTINO = '/data/contrib/pci_146/hf-models'

for repo in sys.argv[1:]:
    d = os.path.join(DESTINO, repo.split('/')[-1])
    print(f'bajando {repo} a {d}', flush=True)
    snapshot_download(repo, local_dir=d, max_workers=8)
    print(f'OK {d}', flush=True)

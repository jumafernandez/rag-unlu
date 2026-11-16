#!/usr/bin/env python3
"""Interpolación de pesos entre el modelo base y el ajustado (WiSE-FT, Wortsman et al., 2022; LM-Cocktail, Xiao et al.,
2023): θ = α·θ_ajustado + (1 − α)·θ_base. Con α = 1 queda el ajustado y con α = 0 el base; los valores intermedios
suelen recuperar parte de lo general que el ajuste hizo perder y conservar parte de lo ganado en el dominio, por eso se
miden las dos cosas para cada α.

Dos modos:
  - ajuste completo: recorre los tensores del ajustado y los mezcla con los del base (formato HF, se ignora el
    consolidated.safetensors de Mistral); copia del ajustado la configuración y el tokenizador;
  - adaptador LoRA: el delta del adaptador es B·A·escala, así que multiplicar lora_B por α equivale a interpolar el
    modelo fusionado con el base.

    python -m nacional.interpolar --base DIR_BASE --ajustado DIR_AJUSTADO --alfa 0.5 --salida DIR
    python -m nacional.interpolar --adaptador DIR_ADAPTADOR --alfa 0.5 --salida DIR
"""
import argparse
import glob
import json
import os
import shutil
import time


def archivos_pesos(d):
    """Mapa nombre de tensor → archivo, del índice HF si existe; si no, de los safetensors (sin consolidated)."""
    indice = os.path.join(d, 'model.safetensors.index.json')
    if os.path.exists(indice):
        mapa = json.load(open(indice))['weight_map']
        return {k: os.path.join(d, f) for k, f in mapa.items()}
    from safetensors import safe_open
    mapa = {}
    for f in sorted(glob.glob(os.path.join(d, '*.safetensors'))):
        if os.path.basename(f).startswith('consolidated'):
            continue
        with safe_open(f, 'pt') as h:
            mapa.update({k: f for k in h.keys()})
    return mapa


def copiar_resto(origen, destino, excluir):
    for f in os.listdir(origen):
        o = os.path.join(origen, f)
        if os.path.isfile(o) and not any(f.endswith(x) or f.startswith(x) for x in excluir):
            shutil.copy2(o, os.path.join(destino, f))


def completo(a):
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file
    mb, ma = archivos_pesos(a.base), archivos_pesos(a.ajustado)
    faltan = sorted(set(ma) - set(mb))
    if faltan:
        raise SystemExit(f'{len(faltan)} tensores del ajustado no están en el base (p. ej. {faltan[:3]})')
    abiertos = {}

    def tensor(ruta, k):
        if ruta not in abiertos:
            abiertos[ruta] = safe_open(ruta, 'pt')
        return abiertos[ruta].get_tensor(k)

    salida, iguales, t0 = {}, 0, time.time()
    for i, k in enumerate(sorted(ma)):
        x, y = tensor(ma[k], k), tensor(mb[k], k)
        if x.shape != y.shape:
            raise SystemExit(f'{k}: forma {tuple(x.shape)} en el ajustado y {tuple(y.shape)} en el base')
        if not x.is_floating_point() or torch.equal(x, y):
            iguales += int(torch.equal(x, y))
            salida[k] = x
        else:
            salida[k] = (a.alfa * x.float() + (1 - a.alfa) * y.float()).to(x.dtype)
        if (i + 1) % 100 == 0:
            print(f'  {i + 1}/{len(ma)} tensores ({time.time() - t0:.0f} s)', flush=True)
    os.makedirs(a.salida, exist_ok=True)
    save_file(salida, os.path.join(a.salida, 'model.safetensors'), metadata={'format': 'pt'})
    copiar_resto(a.ajustado, a.salida, ['.safetensors', 'model.safetensors.index.json'])
    json.dump({'base': a.base, 'ajustado': a.ajustado, 'alfa': a.alfa}, open(os.path.join(a.salida, 'interpolacion.json'), 'w'),
              indent=1)
    print(f'LISTO {a.salida}: {len(ma)} tensores, {iguales} iguales en base y ajustado (se copian), alfa {a.alfa}, '
          f'{time.time() - t0:.0f} s', flush=True)


def adaptador(a):
    from safetensors.torch import load_file, save_file
    conf = json.load(open(os.path.join(a.adaptador, 'adapter_config.json')))
    if conf.get('use_dora') or conf.get('modules_to_save'):
        raise SystemExit('el adaptador usa DoRA o modules_to_save: escalar lora_B no equivale a interpolar')
    pesos = load_file(os.path.join(a.adaptador, 'adapter_model.safetensors'))
    otros = [k for k in pesos if '.lora_A.' not in k and '.lora_B.' not in k]
    if otros:
        raise SystemExit(f'el adaptador tiene tensores que no son lora_A ni lora_B (p. ej. {otros[:3]})')
    nb = 0
    for k in pesos:
        if '.lora_B.' in k:
            pesos[k] = (a.alfa * pesos[k].float()).to(pesos[k].dtype)
            nb += 1
    if not nb:
        raise SystemExit('el adaptador no tiene tensores lora_B')
    os.makedirs(a.salida, exist_ok=True)
    save_file(pesos, os.path.join(a.salida, 'adapter_model.safetensors'), metadata={'format': 'pt'})
    copiar_resto(a.adaptador, a.salida, ['adapter_model.safetensors'])
    json.dump({'adaptador': a.adaptador, 'alfa': a.alfa}, open(os.path.join(a.salida, 'interpolacion.json'), 'w'), indent=1)
    print(f'LISTO {a.salida}: {nb} matrices lora_B escaladas por {a.alfa}', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', help='modelo base (ajuste completo)')
    ap.add_argument('--ajustado', help='modelo ajustado, mismo formato que el base (ajuste completo)')
    ap.add_argument('--adaptador', help='adaptador LoRA (en lugar de --base y --ajustado)')
    ap.add_argument('--alfa', type=float, required=True, help='peso del ajustado, entre 0 y 1')
    ap.add_argument('--salida', required=True)
    a = ap.parse_args()
    if not 0 <= a.alfa <= 1:
        raise SystemExit('--alfa va entre 0 y 1')
    # nunca escribir sobre un modelo existente: la salida es siempre una carpeta nueva
    for d in (a.base, a.ajustado, a.adaptador):
        if d and os.path.realpath(d) == os.path.realpath(a.salida):
            raise SystemExit('--salida no puede ser la carpeta de un modelo de entrada')
    if os.path.isdir(a.salida) and glob.glob(os.path.join(a.salida, '*.safetensors')):
        raise SystemExit(f'{a.salida} ya tiene pesos: no se sobrescribe')
    if a.adaptador:
        adaptador(a)
    elif a.base and a.ajustado:
        completo(a)
    else:
        raise SystemExit('hace falta --adaptador, o --base y --ajustado')


if __name__ == '__main__':
    main()

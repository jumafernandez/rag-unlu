#!/usr/bin/env python3
"""Convierte los resultados de la evaluación en los cuerpos de las tablas del paper.

No toca main.tex: imprime los renglones LaTeX (y una versión legible en texto) para
pegar a mano en las tablas correspondientes. La decisión es deliberada: los números
entran al paper pasando por los ojos de alguien, no por un script.

Uso:
    python -m evaluacion_automatica.tablas --resultados evaluacion_automatica/resultados.json \
                                --citas evaluacion_automatica/citas.json
"""
import argparse
import json
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fila(nombre, m):
    if not m:
        return f'{nombre} & -- & -- & -- \\\\'
    return (f"{nombre} & {m['recall@5']:.3f} & {m['mrr@10']:.3f} "
            f"& {m['ndcg@10']:.3f} \\\\")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--resultados', default=os.path.join(RAIZ, 'evaluacion', 'resultados.json'))
    p.add_argument('--citas', default=os.path.join(RAIZ, 'evaluacion', 'citas.json'))
    a = p.parse_args()

    r = json.load(open(a.resultados, encoding='utf-8'))['resumen']

    print('% ---------- tab:ablacion (señales, por tipo de consulta) ----------')
    nombres = {'denso': 'Denso solo', 'bm25': "L\\'exico solo",
               'hibrido': "H\\'ibrido (sistema)"}
    for config in ('denso', 'bm25', 'hibrido'):
        mi = r.get(config, {}).get('identificador')
        mc = r.get(config, {}).get('tematica')
        def c3(m):
            return (f"{m['recall@5']:.3f} & {m['mrr@10']:.3f} & {m['ndcg@10']:.3f}"
                    if m else '-- & -- & --')
        print(f'{nombres[config]} & {c3(mi)} & {c3(mc)} \\\\')

    print('\n% ---------- tab:procedencia (conversacionales) ----------')
    filas = [('sin_estado', 'Sin estado (turno 2 solo)'),
             ('sin_reescritura', '$-$ reescritura'),
             ('sin_entidad', '$-$ ranura entidad'),
             ('sin_actos', '$-$ ranura actos'),
             ('con_estado', 'Completa (estado inferido)'),
             ('estado_corregido', 'Estado corregido (peso usuario)')]
    for clave, nombre in filas:
        print(fila(nombre, r.get(clave, {}).get('conversacional')))

    print('\n% ---------- números sueltos ----------')
    for config in ('denso', 'bm25', 'hibrido', 'con_estado'):
        g = r.get(config, {}).get('global')
        if g:
            print(f"% {config}: n={g['n']}  R@1={g['recall@1']:.3f}  "
                  f"R@8={g['recall@8']:.3f}  sin_encontrar={g['sin_encontrar']}")

    if os.path.exists(a.citas):
        c = json.load(open(a.citas, encoding='utf-8'))['resumen']
        print('\n% ---------- tab:citas ----------')
        print(f"% respuestas evaluadas: {c['respuestas']} "
              f"(con al menos una cita: {c['con_citas']})")
        print(f"Actos citados & {c['actos_citados']} & -- \\\\")
        anclados = c['actos_citados'] - c['actos_sueltos']
        pct = 100 * anclados / c['actos_citados'] if c['actos_citados'] else 0
        print(f"Anclados en lo recuperado & {anclados} & {pct:.1f}\\% \\\\")
        print(f"Sueltos (sin fuente) & {c['actos_sueltos']} & "
              f"{100 - pct:.1f}\\% \\\\")
        if c['consultas_con_persona']:
            pa = 100 * c['atribucion_correcta'] / c['consultas_con_persona']
            print(f"Atribuci\\'on de persona correcta & "
                  f"{c['atribucion_correcta']}/{c['consultas_con_persona']} & {pa:.1f}\\% \\\\")


if __name__ == '__main__':
    main()

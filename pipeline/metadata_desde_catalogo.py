"""Genera la metadata que consume `chunkear.py`, directamente desde la recolección.

Reemplaza a `unir_metadata.py` para los documentos recolectados con el método nuevo. La
diferencia es que ya no hay nada que deducir: el CSV del portal trae, en cada fila, el
nombre del archivo que le corresponde. `unir_metadata.py` existía para reconstruir esa
correspondencia cuando se había perdido ---uniendo por posición y por código de acto, y
resolviendo desacuerdos---, y ese trabajo ya no hace falta.

Por eso todas las filas salen con confianza alta y vía `catalogo`: no es una estimación.

Uso:
    python -m pipeline.metadata_desde_catalogo \\
        --metadatos scrapers/metadatos.csv \\
        --pdfs data/portal-incremental \\
        --salida data/metadata-incremental.csv
"""
import argparse
import csv
import glob
import os


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--metadatos', required=True, help='CSV de la recolección del portal')
    p.add_argument('--pdfs', required=True, help='directorio con los PDF a incorporar')
    p.add_argument('--salida', required=True)
    a = p.parse_args()

    presentes = {os.path.basename(x) for x in glob.glob(os.path.join(a.pdfs, '*.pdf'))}

    filas, sin_pdf = [], 0
    with open(a.metadatos, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            archivo = (r.get('Archivo') or '').strip()
            if not archivo or archivo not in presentes:
                continue
            filas.append([
                # Con extensión: chunkear.py busca la clave como '{base}.pdf'. Sin ella la
                # unión no falla, simplemente no encuentra nada, y los fragmentos salen sin
                # título ni fecha sin que nada avise.
                archivo,
                'alta', 'catalogo',
                r.get('Numero', ''), r.get('Fecha', ''), r.get('Estado', ''),
                (r.get('Tipo de documento', '') or '').split(',')[0],
                r.get('Titulo', ''),
            ])

    vistos = {f[0] for f in filas}
    sin_pdf = len(presentes - vistos)

    with open(a.salida, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['archivo', 'confianza', 'via', 'numero', 'fecha', 'estado',
                    'tipo_documento', 'titulo'])
        w.writerows(filas)

    print(f'PDF en el directorio      : {len(presentes)}')
    print(f'con metadata del catálogo : {len(filas)}')
    print(f'sin correspondencia       : {sin_pdf}')
    print(f'-> {a.salida}')


if __name__ == '__main__':
    main()

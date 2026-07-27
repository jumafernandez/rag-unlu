#!/usr/bin/env python3
"""
Arma el archivo canónico final que piden los tutoriales: un solo .md con
front-matter YAML arriba y el cuerpo Markdown abajo.

    ---
    document_id: "..."
    ...
    ---

    # Disposición ...
    ## Visto
    ...

Hoy el pipeline deja dos archivos sueltos por documento (`X.md` y `X_canonico.yaml`),
y además el YAML embebe el markdown dentro del campo `content_markdown`. Eso obliga a
parsear YAML para leer el texto, y duplica el contenido. Para ingestar en un RAG conviene
el formato de arriba, que es texto plano con metadata al frente.

NO toca el extractor ni el post-procesador: lee lo que ellos ya generaron.

Uso:
    python construir_canonicos.py <carpeta_resultados> [carpeta_salida]

Por defecto la salida va a <carpeta_resultados>/canonicos/
"""

import os
import sys

import yaml


def _representar_str(dumper, data):
    """Los bloques multilínea salen legibles en vez de con \\n escapados."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, _representar_str, Dumper=yaml.SafeDumper)


def construir_uno(ruta_yaml, ruta_md, ruta_salida):
    """Devuelve (ok, detalle)."""
    with open(ruta_yaml, "r", encoding="utf-8") as f:
        meta = yaml.safe_load(f)

    if not isinstance(meta, dict):
        return False, "el YAML no es un mapping"

    # El cuerpo autoritativo es el .md. Si no está, caemos al content_markdown.
    cuerpo = None
    if os.path.exists(ruta_md):
        with open(ruta_md, "r", encoding="utf-8") as f:
            cuerpo = f.read().strip()
    if not cuerpo:
        cuerpo = (meta.get("content_markdown") or "").strip()
    if not cuerpo:
        return False, "sin cuerpo markdown (ni .md ni content_markdown)"

    # Sacamos content_markdown del front-matter: el cuerpo va abajo, no duplicado.
    meta.pop("content_markdown", None)

    front = yaml.safe_dump(
        meta, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10_000
    ).rstrip()

    with open(ruta_salida, "w", encoding="utf-8") as f:
        f.write(f"---\n{front}\n---\n\n{cuerpo}\n")

    return True, None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    carpeta = os.path.abspath(sys.argv[1])
    salida = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(carpeta, "canonicos")

    if not os.path.isdir(carpeta):
        print(f"ERROR: no existe la carpeta {carpeta}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(salida, exist_ok=True)

    yamls = sorted(f for f in os.listdir(carpeta) if f.endswith("_canonico.yaml"))
    if not yamls:
        print(f"ERROR: no encontré *_canonico.yaml en {carpeta}", file=sys.stderr)
        sys.exit(1)

    print(f">> {len(yamls)} documentos en {carpeta}")
    print(f">> salida: {salida}\n")

    ok = 0
    fallos = []
    for nombre in yamls:
        base = nombre[: -len("_canonico.yaml")]
        try:
            exito, detalle = construir_uno(
                os.path.join(carpeta, nombre),
                os.path.join(carpeta, f"{base}.md"),
                os.path.join(salida, f"{base}.md"),
            )
        except Exception as e:  # un doc roto no debe frenar los 19.959
            exito, detalle = False, f"{type(e).__name__}: {e}"

        if exito:
            ok += 1
        else:
            fallos.append((base, detalle))

    print(f">> canónicos generados: {ok}/{len(yamls)}")
    if fallos:
        print(f">> con problemas: {len(fallos)}")
        for base, detalle in fallos[:20]:
            print(f"   - {base}: {detalle}")
        if len(fallos) > 20:
            print(f"   ... y {len(fallos) - 20} más")
        sys.exit(1)


if __name__ == "__main__":
    main()

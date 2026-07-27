#!/usr/bin/env python3
"""
Verifica una corrida del extractor: cobertura, integridad y calidad de la metadata.

Pensado para revisar los 19.959 documentos de una sin abrirlos a mano. Responde:
  - ¿se procesaron todos los PDFs de origen?
  - ¿cada documento tiene sus 3 salidas (.md, .json, _canonico.yaml)?
  - ¿los YAML tienen los 21 campos que pide el tutorial de metadata?
  - ¿qué campos quedaron vacíos, sin normalizar o fuera de vocabulario?
  - ¿qué warnings dejó el extractor?

Uso:
    python verificar_resultados.py <carpeta_resultados> [carpeta_pdfs_origen]
"""

import collections
import json
import os
import sys

import yaml

CAMPOS_TUTORIAL = [
    "document_id", "source_pdf", "source_system", "document_type", "issuing_body",
    "institution", "document_code", "document_number", "date_issued", "year", "city",
    "has_annexes", "annex_count", "has_signature_page", "signature_mode", "signers",
    "referenced_entities", "normative_references", "auxiliary_codes",
    "publication_notes", "extraction_version",
]

# El tutorial fija este vocabulario para signature_mode.
SIGNATURE_MODE_OK = {"embedded", "separate_page"}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    res = os.path.abspath(sys.argv[1])
    origen = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else None

    if not os.path.isdir(res):
        print(f"ERROR: no existe {res}", file=sys.stderr)
        sys.exit(1)

    archivos = os.listdir(res)
    yamls = sorted(f for f in archivos if f.endswith("_canonico.yaml"))
    bases_yaml = {f[: -len("_canonico.yaml")] for f in yamls}
    bases_md = {f[:-3] for f in archivos if f.endswith(".md")}
    bases_json = {f[:-5] for f in archivos if f.endswith(".json")}

    print("=" * 60)
    print("COBERTURA")
    print("=" * 60)
    print(f"  .md              {len(bases_md)}")
    print(f"  .json            {len(bases_json)}")
    print(f"  _canonico.yaml   {len(bases_yaml)}")

    if origen and os.path.isdir(origen):
        pdfs = {f[:-4] for f in os.listdir(origen) if f.lower().endswith(".pdf")}
        print(f"  PDFs de origen   {len(pdfs)}")
        faltan = pdfs - bases_yaml
        print(f"\n  SIN procesar: {len(faltan)}")
        for b in sorted(faltan)[:15]:
            print(f"    - {b}.pdf")
        if len(faltan) > 15:
            print(f"    ... y {len(faltan) - 15} más")

    incompletos = (bases_md | bases_json | bases_yaml) - (bases_md & bases_json & bases_yaml)
    if incompletos:
        print(f"\n  Documentos con salidas INCOMPLETAS: {len(incompletos)}")
        for b in sorted(incompletos)[:15]:
            tiene = "".join(
                letra if b in conj else "-"
                for letra, conj in (("m", bases_md), ("j", bases_json), ("y", bases_yaml))
            )
            print(f"    - {b}  [{tiene}]")

    # ---- calidad de la metadata ----
    faltantes = collections.Counter()
    vacios = collections.Counter()
    sig_modes = collections.Counter()
    ciudades = collections.Counter()
    sin_fecha, fecha_rara, rotos = [], [], []
    total_signers = 0
    signers_unknown = 0
    docs_con_dup = 0

    for nombre in yamls:
        base = nombre[: -len("_canonico.yaml")]
        try:
            with open(os.path.join(res, nombre), "r", encoding="utf-8") as f:
                d = yaml.safe_load(f)
        except Exception as e:
            rotos.append((base, f"{type(e).__name__}: {e}"))
            continue

        if not isinstance(d, dict):
            rotos.append((base, "no es un mapping"))
            continue

        for c in CAMPOS_TUTORIAL:
            if c not in d:
                faltantes[c] += 1
            elif d[c] in (None, "", [], {}):
                vacios[c] += 1

        sig_modes[str(d.get("signature_mode"))] += 1
        if d.get("city"):
            ciudades[str(d["city"])] += 1

        fecha = d.get("date_issued")
        if not fecha:
            sin_fecha.append(base)
        elif not (isinstance(fecha, str) and len(fecha) == 10 and fecha[4] == "-" and fecha[7] == "-"):
            fecha_rara.append((base, fecha))

        firmantes = d.get("signers") or []
        if isinstance(firmantes, list):
            total_signers += len(firmantes)
            signers_unknown += sum(
                1 for s in firmantes if isinstance(s, dict) and s.get("role") in (None, "", "unknown")
            )
            nombres = [
                str(s.get("name", "")).strip().lower()
                for s in firmantes if isinstance(s, dict)
            ]
            if len(nombres) != len(set(nombres)):
                docs_con_dup += 1

    n = len(yamls)
    print()
    print("=" * 60)
    print(f"METADATA ({n} YAML analizados)")
    print("=" * 60)

    if rotos:
        print(f"\n  YAML ilegibles: {len(rotos)}")
        for b, e in rotos[:10]:
            print(f"    - {b}: {e}")

    print("\n  Campos del tutorial que FALTAN:")
    if faltantes:
        for c, k in faltantes.most_common():
            print(f"    {c:24s} falta en {k}/{n}")
    else:
        print("    (ninguno: los 21 campos están en todos)")

    print("\n  Campos presentes pero VACÍOS (top 10):")
    for c, k in vacios.most_common(10):
        print(f"    {c:24s} vacío en {k}/{n}  ({100*k//n}%)")

    print(f"\n  signature_mode (el tutorial admite {sorted(SIGNATURE_MODE_OK)}):")
    for v, k in sig_modes.most_common():
        marca = "  OK" if v in SIGNATURE_MODE_OK else "  <-- fuera de vocabulario"
        print(f"    {v:20s} {k}/{n}{marca}")

    print("\n  city (top 8) — el tutorial la quiere normalizada tipo 'Luján':")
    for v, k in ciudades.most_common(8):
        marca = "  <-- sin normalizar" if v.isupper() else ""
        print(f"    {v:20s} {k}{marca}")

    print("\n  date_issued:")
    print(f"    sin fecha           {len(sin_fecha)}/{n}")
    print(f"    fuera de ISO        {len(fecha_rara)}/{n}")
    for b, f_ in fecha_rara[:5]:
        print(f"      - {b}: {f_!r}")

    print("\n  signers:")
    print(f"    total               {total_signers}")
    print(f"    con role 'unknown'  {signers_unknown}"
          + (f" ({100*signers_unknown//total_signers}%)" if total_signers else ""))
    print(f"    docs con duplicados {docs_con_dup}/{n}")

    # ---- warnings del extractor ----
    warns = collections.Counter()
    for f_ in (x for x in archivos if x.endswith(".json")):
        try:
            with open(os.path.join(res, f_), "r", encoding="utf-8") as fh:
                for w in json.load(fh).get("warnings") or []:
                    warns[str(w)[:70]] += 1
        except Exception:
            pass

    print("\n" + "=" * 60)
    print("WARNINGS DEL EXTRACTOR")
    print("=" * 60)
    if warns:
        for w, k in warns.most_common(15):
            print(f"  {k:6d}  {w}")
    else:
        print("  (ninguno)")

    for reporte in ("docus_escaneos.txt", "docus_error.txt"):
        ruta = os.path.join(res, reporte)
        if os.path.exists(ruta):
            with open(ruta, "r", encoding="utf-8", errors="replace") as fh:
                líneas = [x for x in fh if x.strip()]
            print(f"\n  {reporte}: {len(líneas)} líneas")


if __name__ == "__main__":
    main()

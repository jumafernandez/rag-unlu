#!/usr/bin/env python3
"""
Auditoría del corpus parseado COMO INSUMO DE UN RAG.

No pregunta "¿el parser anduvo?" (para eso está verificar_resultados.py), sino
"¿esto sirve para recuperar normativa con precisión?". Mide cuatro ejes:

  1. CHUNKING      — ¿el Markdown tiene un esqueleto de headers que permita cortar
                     por unidad normativa (artículo, considerando, anexo)?
  2. FILTRADO      — ¿la metadata permite filtrar por tipo/órgano/año/vigencia sin
                     mentir? (fechas incoherentes rompen el filtrado temporal)
  3. INTEGRIDAD    — ¿hay documentos mudos (escaneos), texto sospechosamente corto,
                     encoding roto, boilerplate que contamina embeddings?
  4. ENLACES       — ¿se capturan las citas entre normas (X modifica a Y)?

Uso:
    python auditoria_rag.py <carpeta_resultados> [--muestra N]
"""

import collections
import json
import os
import re
import statistics
import sys

import yaml

# Encabezados que el tutorial define como unidades semánticas.
H_ARTICULO = re.compile(r"^###\s+Art[íi]culo\b", re.MULTILINE | re.IGNORECASE)
H_CUALQUIERA = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
# Fórmula institucional que NO debería ser un header (abre la parte resolutiva).
H_FORMULA = re.compile(r"^##\s+(EL|LA|LOS|LAS)\s+[A-ZÁÉÍÓÚÑ\s\.]{10,}$", re.MULTILINE)
# Membrete institucional colándose dentro del cuerpo.
MEMBRETE = re.compile(r"Universidad Nacional de Luj[áa]n\s+Departamento de", re.IGNORECASE)
# Leyenda de validez web (boilerplate que no debería ir al índice).
DISCLAIMER = re.compile(r"no tendr[áa] validez|sin autenticaci[óo]n|sitio Web de la Universidad", re.IGNORECASE)
# Cierre ritual, presente en casi todos: ruido para embeddings si queda suelto.
CIERRE = re.compile(r"Reg[íi]strese,?\s+(comun[íi]quese|notif[íi]quese)", re.IGNORECASE)
# Citas a otras normas.
CITA_NORMA = re.compile(
    r"\b(RESHCS|RESPRHCS|DISPCD|DISPPCD|RESREC|DISP|RES|ACTDB|RESOL)[A-Z\-]*\s*[:\-]?\s*"
    r"\d{1,7}\s*[/\-]\s*\d{2,4}", re.IGNORECASE)


def analizar_md(texto):
    """Métricas de chunkabilidad de un Markdown."""
    headers = H_CUALQUIERA.findall(texto)
    niveles = collections.Counter(len(h[0]) for h in headers)
    arts = len(H_ARTICULO.findall(texto))

    # Texto antes del primer header = huérfano (no cae en ningún chunk titulado).
    m = re.search(r"^#{1,3}\s+", texto, re.MULTILINE)
    huerfano = len(texto[: m.start()].strip()) if m else len(texto.strip())

    # Tamaño de las secciones: bloques enormes no entran en un chunk útil.
    cortes = [mm.start() for mm in H_CUALQUIERA.finditer(texto)] + [len(texto)]
    secciones = [cortes[i + 1] - cortes[i] for i in range(len(cortes) - 1)]

    return {
        "headers": len(headers),
        "h1": niveles.get(1, 0), "h2": niveles.get(2, 0), "h3": niveles.get(3, 0),
        "articulos": arts,
        "titulos": [h[1].strip() for h in headers],
        "huerfano": huerfano,
        "sec_max": max(secciones) if secciones else 0,
        "chars": len(texto),
        "formula_como_header": len(H_FORMULA.findall(texto)),
        "membrete_en_cuerpo": len(MEMBRETE.findall(texto)),
        "disclaimer": len(DISCLAIMER.findall(texto)),
        "cierre_ritual": len(CIERRE.findall(texto)),
        "citas": len(set(CITA_NORMA.findall(texto))),
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    res = os.path.abspath(sys.argv[1])

    archivos = os.listdir(res)
    yamls = sorted(f for f in archivos if f.endswith("_canonico.yaml"))
    if not yamls:
        print(f"ERROR: no hay *_canonico.yaml en {res}", file=sys.stderr)
        sys.exit(1)

    n = len(yamls)
    ids = collections.Counter()
    docs, tipos, organos, años = [], collections.Counter(), collections.Counter(), collections.Counter()
    fecha_incoherente, sin_refs, cortos = [], 0, []
    total_arts = 0

    for nombre in yamls:
        base = nombre[: -len("_canonico.yaml")]
        try:
            with open(os.path.join(res, nombre), encoding="utf-8") as f:
                meta = yaml.safe_load(f) or {}
        except Exception:
            continue

        ruta_md = os.path.join(res, f"{base}.md")
        texto = ""
        if os.path.exists(ruta_md):
            with open(ruta_md, encoding="utf-8") as f:
                texto = f.read()
        elif meta.get("content_markdown"):
            texto = meta["content_markdown"]

        a = analizar_md(texto)
        a["base"] = base
        a["meta"] = meta
        docs.append(a)
        total_arts += a["articulos"]

        ids[str(meta.get("document_id"))] += 1
        tipos[str(meta.get("document_type"))] += 1
        organos[str(meta.get("issuing_body"))[:60]] += 1

        # Coherencia temporal: el año del número de acto (ej "103/2025") vs date_issued.
        año_meta = meta.get("year")
        años[str(año_meta)] += 1
        num = str(meta.get("document_number") or "")
        mnum = re.search(r"/\s*(\d{2,4})\s*$", num)
        if mnum and isinstance(año_meta, int):
            yy = int(mnum.group(1))
            yy = 2000 + yy if yy < 100 else yy
            if abs(yy - año_meta) > 0:
                fecha_incoherente.append((base, num, año_meta))

        if not (meta.get("normative_references") or []):
            sin_refs += 1
        if a["chars"] < 400:
            cortos.append((base, a["chars"]))

    def pct(k):
        return f"{100*k//n}%"

    print("=" * 66)
    print(f"AUDITORÍA RAG — {n} documentos")
    print("=" * 66)

    # ---------- 1. CHUNKING ----------
    print("\n1. CHUNKING (¿se puede cortar por unidad normativa?)")
    sin_h = sum(1 for d in docs if d["headers"] == 0)
    sin_art = sum(1 for d in docs if d["articulos"] == 0)
    print(f"   docs sin NINGÚN header      {sin_h}/{n} ({pct(sin_h)})   <- irrecuperables por sección")
    print(f"   docs sin '### Artículo'     {sin_art}/{n} ({pct(sin_art)})")
    print(f"   artículos totales           {total_arts}  (media {total_arts/n:.1f}/doc)")
    hs = [d["headers"] for d in docs]
    print(f"   headers por doc             mediana {statistics.median(hs):.0f}, min {min(hs)}, max {max(hs)}")
    huer = [d["huerfano"] for d in docs]
    graves = sum(1 for h in huer if h > 300)
    print(f"   texto huérfano (pre-header) mediana {statistics.median(huer):.0f} chars; "
          f">300 chars en {graves}/{n} ({pct(graves)})")
    smax = [d["sec_max"] for d in docs]
    gigantes = sum(1 for s in smax if s > 4000)
    print(f"   sección más larga           mediana {statistics.median(smax):.0f} chars; "
          f">4000 en {gigantes}/{n}  <- chunk difícil de indexar")

    print("\n   Convención de títulos (top 12) — la inconsistencia rompe el routing:")
    tit = collections.Counter(t for d in docs for t in d["titulos"])
    for t, k in tit.most_common(12):
        print(f"     {k:5d}  {t[:56]}")

    variantes = {
        "parte resolutiva/dispositiva": [t for t in tit if re.match(r"parte (resolutiva|dispositiva)", t, re.I)],
        "firmas": [t for t in tit if re.search(r"firmas", t, re.I)],
        "anexo": [t for t in tit if re.match(r"anexo", t, re.I)],
    }
    print("\n   Variantes del MISMO concepto (deberían ser una sola):")
    for concepto, vs in variantes.items():
        u = collections.Counter(vs)
        if len(u) > 1:
            print(f"     {concepto}: {len(u)} formas -> {', '.join(list(u)[:4])}")
        else:
            print(f"     {concepto}: OK ({list(u)[0] if u else 'ausente'})")

    # ---------- 2. FILTRADO ----------
    print("\n2. FILTRADO / ROUTING (¿la metadata permite acotar la búsqueda?)")
    dups = {i: k for i, k in ids.items() if k > 1}
    print(f"   document_id únicos          {len(ids)}/{n}")
    if dups:
        print(f"   *** COLISIONES: {len(dups)} ids repetidos (un doc pisa a otro en el índice)")
        for i, k in list(dups.items())[:5]:
            print(f"       {i} x{k}")
    print(f"   document_type               {dict(tipos.most_common(5))}")
    print(f"   issuing_body distintos      {len(organos)}")
    print(f"   años cubiertos              {sorted(a for a in años if a.isdigit())[:3]} ... "
          f"{sorted(a for a in años if a.isdigit())[-3:]}")
    print(f"   *** fecha INCOHERENTE con el nº de acto: {len(fecha_incoherente)}/{n} "
          f"({pct(len(fecha_incoherente))})  <- rompe filtro temporal")
    for b, num, y in fecha_incoherente[:6]:
        print(f"       {b[:44]:44s} nº {num:12s} year={y}")

    # ---------- 3. INTEGRIDAD ----------
    print("\n3. INTEGRIDAD DE CONTENIDO")
    ch = [d["chars"] for d in docs]
    print(f"   longitud del texto          mediana {statistics.median(ch):.0f} chars, min {min(ch)}, max {max(ch)}")
    print(f"   docs < 400 chars            {len(cortos)}/{n}  <- casi sin contenido (¿escaneo?)")
    for b, c in cortos[:5]:
        print(f"       {b[:50]:50s} {c} chars")
    ruido = {
        "membrete dentro del cuerpo": sum(1 for d in docs if d["membrete_en_cuerpo"]),
        "leyenda de validez web": sum(1 for d in docs if d["disclaimer"]),
        "fórmula como header": sum(1 for d in docs if d["formula_como_header"]),
        "cierre ritual (Regístrese…)": sum(1 for d in docs if d["cierre_ritual"]),
    }
    print("   Ruido que contamina embeddings:")
    for k, v in ruido.items():
        print(f"     {k:32s} {v}/{n} ({pct(v)})")

    for reporte in ("docus_escaneos.txt", "docus_error.txt"):
        p = os.path.join(res, reporte)
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                líneas = [x for x in f if x.strip()]
            print(f"   {reporte:24s} {len(líneas)} líneas")

    # ---------- 4. ENLACES ----------
    print("\n4. ENLACES ENTRE NORMAS (preguntas del tipo '¿qué modificó a X?')")
    citas_txt = sum(d["citas"] for d in docs)
    con_cita = sum(1 for d in docs if d["citas"])
    print(f"   docs que citan otra norma en el texto   {con_cita}/{n} ({pct(con_cita)})")
    print(f"   citas detectadas en el texto            {citas_txt}")
    print(f"   docs con normative_references vacío     {sin_refs}/{n} ({pct(sin_refs)})")
    perdidas = sum(1 for d in docs if d["citas"] and not (d["meta"].get("normative_references") or []))
    print(f"   *** citan en el texto pero NO lo capturan en metadata: {perdidas}/{n} ({pct(perdidas)})")


if __name__ == "__main__":
    main()

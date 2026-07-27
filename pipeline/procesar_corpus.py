#!/usr/bin/env python3
"""
Runner de corpus para cluster. Reemplaza a procesador_masivo.py en corridas grandes.

Por qué existe: el orquestador del repo del extractor funciona bien en una carpeta chica,
pero en Clementina se colgó a los 686 de 19.959 documentos sin dejar rastro de dónde. Con
~140.000 documentos por delante (portal + digesto) hacía falta algo que:

  - **escriba en disco local del nodo** y copie al final, en vez de martillar el filesystem
    compartido con miles de archivos chicos (principal sospechoso del cuelgue);
  - **reparta la salida en subdirectorios**, para no tener 420.000 archivos en un solo dir;
  - **deje un log por documento con timestamps**, para que un cuelgue sea diagnosticable en
    vez de un proceso mudo;
  - **se reanude** sin reprocesar, y **se pueda shardear** entre varios nodos (job array).

No modifica el extractor ni el post-procesador: los invoca como subprocesos, igual que antes.

Uso:
    python procesar_corpus.py --pdfs DIR --salida DIR [opciones]

    --shard i/N       procesar solo la parte i de N (para job arrays de SLURM)
    --scratch DIR     directorio de trabajo local del nodo (default: $TMPDIR)
    --workers N       procesos en paralelo (default: cores asignados por SLURM)
    --timeout SEG     máximo por documento (default: 300)
    --sin-scratch     escribir directo a --salida, sin disco local intermedio
"""

import argparse
import concurrent.futures as futures
import json
import os
import shutil
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))


def cores_disponibles():
    n = os.environ.get("SLURM_CPUS_PER_TASK")
    if n and n.isdigit():
        return max(1, int(n))
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:
        return max(1, os.cpu_count() or 1)


def subdir_de(nombre):
    """Reparte los documentos en 256 subdirectorios estables por nombre.

    Un solo directorio con cientos de miles de archivos degrada mucho en filesystems
    paralelos (contención de metadatos) y hace inusable hasta un `ls`.
    """
    h = 0
    for c in nombre.encode("utf-8"):
        h = (h * 131 + c) & 0xFFFFFFFF
    return f"{h % 256:02x}"


def procesar_uno(tarea):
    pdf, dir_pdfs, dir_trabajo, extractor, post, timeout = tarea
    base = os.path.splitext(pdf)[0]
    destino = os.path.join(dir_trabajo, subdir_de(base))
    os.makedirs(destino, exist_ok=True)

    t0 = time.time()
    registro = {"archivo": pdf, "inicio": t0}

    def fin(estado, detalle=""):
        registro.update(estado=estado, detalle=detalle[:300], segundos=round(time.time() - t0, 2))
        return registro

    try:
        r = subprocess.run(
            [sys.executable, extractor, os.path.join(dir_pdfs, pdf)],
            cwd=destino, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return fin("TIMEOUT_EXTRACTOR")
    except Exception as e:
        return fin("ERROR_EXTRACTOR", f"{type(e).__name__}: {e}")

    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if r.returncode == 2 or "FILTRADO" in err.upper() or "ESCANEO" in err.upper():
            return fin("FILTRADO", err or "escaneo puro")
        return fin("ERROR_EXTRACTOR", err)

    md = os.path.join(destino, f"{base}.md")
    js = os.path.join(destino, f"{base}.json")
    if not (os.path.exists(md) and os.path.exists(js)):
        return fin("SIN_SALIDA", "el extractor no generó .md/.json")

    try:
        r2 = subprocess.run(
            [sys.executable, post, f"{base}.json", f"{base}.md"],
            cwd=destino, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return fin("TIMEOUT_POST")
    except Exception as e:
        return fin("ERROR_POST", f"{type(e).__name__}: {e}")

    if r2.returncode != 0:
        return fin("ERROR_POST", (r2.stderr or "").strip())
    if not os.path.exists(os.path.join(destino, f"{base}_canonico.yaml")):
        return fin("SIN_YAML")
    return fin("OK")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pdfs", required=True)
    p.add_argument("--salida", required=True)
    p.add_argument("--extractor", default=None, help="ruta a script_extractor.py")
    p.add_argument("--post", default=None, help="ruta a post_procesador.py")
    p.add_argument("--shard", default=None, help="i/N")
    p.add_argument("--scratch", default=None)
    p.add_argument("--sin-scratch", action="store_true")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--timeout", type=int, default=300)
    a = p.parse_args()

    repo = os.path.dirname(AQUI)
    extractor = os.path.abspath(a.extractor or os.path.join(repo, "extractor", "script_extractor.py"))
    post = os.path.abspath(a.post or os.path.join(repo, "extractor", "post_procesador.py"))
    for ruta, nombre in ((extractor, "script_extractor.py"), (post, "post_procesador.py")):
        if not os.path.exists(ruta):
            sys.exit(f"ERROR: no encuentro {nombre} en {ruta} (pasalo con --extractor/--post)")

    dir_pdfs = os.path.abspath(a.pdfs)
    salida = os.path.abspath(a.salida)
    os.makedirs(salida, exist_ok=True)

    archivos = sorted(f for f in os.listdir(dir_pdfs) if f.lower().endswith(".pdf"))
    etiqueta = "todo"
    if a.shard:
        i, n = (int(x) for x in a.shard.split("/"))
        archivos = [f for k, f in enumerate(archivos) if k % n == i]
        etiqueta = f"shard{i:03d}de{n}"

    # Reanudación: saltear lo que ya tenga su YAML en la salida definitiva.
    ya = set()
    for raiz, _, files in os.walk(salida):
        ya.update(f[: -len("_canonico.yaml")] for f in files if f.endswith("_canonico.yaml"))
    pendientes = [f for f in archivos if os.path.splitext(f)[0] not in ya]
    omitidos = len(archivos) - len(pendientes)

    workers = a.workers or max(1, cores_disponibles() - 1)

    if a.sin_scratch:
        dir_trabajo = salida
    else:
        base_scratch = a.scratch or os.environ.get("TMPDIR") or "/tmp"
        dir_trabajo = os.path.join(base_scratch, f"ragunlu_{etiqueta}_{os.getpid()}")
        os.makedirs(dir_trabajo, exist_ok=True)

    log_path = os.path.join(salida, f"proceso_{etiqueta}.jsonl")

    print(f"documentos   : {len(archivos)} ({etiqueta})", flush=True)
    print(f"ya procesados: {omitidos}  -> pendientes: {len(pendientes)}", flush=True)
    print(f"workers      : {workers}   timeout: {a.timeout}s", flush=True)
    print(f"trabajo en   : {dir_trabajo}", flush=True)
    print(f"salida       : {salida}", flush=True)
    print(f"log          : {log_path}", flush=True)
    if not pendientes:
        print("nada por hacer", flush=True)
        return

    tareas = [(f, dir_pdfs, dir_trabajo, extractor, post, a.timeout) for f in pendientes]
    conteo = {}
    t0 = time.time()
    hecho = 0

    with open(log_path, "a", encoding="utf-8") as log:
        with futures.ProcessPoolExecutor(max_workers=workers) as ex:
            for reg in ex.map(procesar_uno, tareas, chunksize=8):
                hecho += 1
                conteo[reg["estado"]] = conteo.get(reg["estado"], 0) + 1
                log.write(json.dumps(reg, ensure_ascii=False) + "\n")
                # Latido cada 50: si se cuelga, el último timestamp dice dónde y con qué archivo.
                if hecho % 50 == 0:
                    log.flush()
                    os.fsync(log.fileno())
                    vel = hecho / max(1e-6, time.time() - t0)
                    resta = (len(pendientes) - hecho) / max(1e-6, vel)
                    print(f"[{hecho}/{len(pendientes)}] {vel:.1f} doc/s  "
                          f"faltan ~{resta/60:.0f} min  {conteo}", flush=True)

    if dir_trabajo != salida:
        print("copiando resultados del disco local a la salida definitiva...", flush=True)
        movidos = 0
        for sub in sorted(os.listdir(dir_trabajo)):
            orig, dest = os.path.join(dir_trabajo, sub), os.path.join(salida, sub)
            if not os.path.isdir(orig):
                continue
            os.makedirs(dest, exist_ok=True)
            for f in os.listdir(orig):
                shutil.move(os.path.join(orig, f), os.path.join(dest, f))
                movidos += 1
        shutil.rmtree(dir_trabajo, ignore_errors=True)
        print(f"  {movidos} archivos copiados", flush=True)

    dur = time.time() - t0
    print(f"\n=== {etiqueta}: {hecho} documentos en {dur/60:.1f} min "
          f"({hecho/max(1e-6,dur):.1f} doc/s) ===", flush=True)
    for k in sorted(conteo):
        print(f"  {k:20s} {conteo[k]}", flush=True)


if __name__ == "__main__":
    main()

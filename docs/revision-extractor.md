# Extractor_pdf en Clementina — revisión, parche y despliegue

Repo de los becarios: https://github.com/SebasUNLu/Extractor_pdf (commit 7a3bf97, 14-jul-2026)

## Veredicto

**El parser funciona y la salida es buena.** Probado sobre PDFs reales del digesto: extrae,
jerarquiza el Markdown según el tutorial 1, y produce JSON + YAML conformes al tutorial 2.
Medido: **0,7 s/PDF** → los 19.959 son ~4 h de CPU en un core, **~7 min con 32 workers** (1 nodo).

> **Reproceso más rápido (a futuro, si hace falta):** se puede repartir en varios nodos con un
> SLURM job array — cada tarea procesa un shard DISJUNTO (ej. `índice % N == SLURM_ARRAY_TASK_ID`),
> escribiendo a la misma carpeta sin pisarse. Ojo: shards disjuntos sí o sí (dos tareas sobre el
> mismo PDF se pisan). El speedup NO es lineal: el cuello no es parsear sino escribir ~60.000
> archivos chicos en el filesystem compartido (contención de metadatos). Realista: ~7 min → ~2-3 min
> con 4 nodos. Para una corrida única de 7 min no vale la pena; sí cuando reprocesar sea rutina.

Hay **dos parches** (separados a propósito):

- [extractor_pdf_clementina.patch](extractor_pdf_clementina.patch) — **portabilidad y robustez**
  (2 archivos, +66/−7). Es lo que hace falta para que corra en el cluster. **Obligatorio.**
- [extractor_pdf_calidad.patch](extractor_pdf_calidad.patch) — **calidad de la metadata**
  (solo `post_procesador.py`, +79/−8). Mejora la conformidad con el tutorial. **Recomendado**,
  pero podés correr sin él.

`script_extractor.py` quedó **intacto** en ambos casos.

---

## Qué arregla el parche (todo verificado corriendo, no en teoría)

### 1. `["python", ...]` hardcodeado → `sys.executable`
`procesador_masivo.py` lanzaba los subprocesos con el literal `"python"`. En Linux moderno y
en macOS solo existe `python3`.

- **Antes**: 20 PDFs reales → **20/20 fallidos**, `[Errno 2] No such file or directory: 'python'`
- **Después**: mismos 20, sin venv activado → **20/20 exitosos en 7 s**

### 2. `os.cpu_count() - 3` → cores reales asignados
Ahora lee `SLURM_CPUS_PER_TASK`, y si no, `os.sched_getaffinity(0)`. Antes veía todos los cores
del nodo y sobresuscribía. Override manual: `EXTRACTOR_WORKERS=N`.

### 3. Timeout por archivo
`_ejecutar_comando()` hacía polling sin límite: un PDF que colgara al extractor bloqueaba al
worker para siempre. Ahora corta a los `EXTRACTOR_TIMEOUT` segundos (default 300).

- **Verificado** con `EXTRACTOR_TIMEOUT=0`: corta, registra `TIMEOUT: superó 0s y fue terminado`
  y termina en 1 s en vez de colgarse.

### 4. Reanudación
Omite los PDFs que ya tienen su `_canonico.yaml`. Si el job se corta, lo re-encolás y sigue.
Desactivable con `EXTRACTOR_REANUDAR=0`.

- **Verificado**: segunda corrida → `Reanudación: 20 ya procesados, se omiten`

### 5. `requirements.txt` corregido
El original decía `fitz==0.0.1.dev2`, que **no es PyMuPDF** (en PyPI `fitz` es otro paquete,
un stub abandonado). Además faltaban `Levenshtein` y `tabulate`.

El correcto (cada uno verificado en el código):

| Paquete | Por qué |
|---|---|
| `PyMuPDF` | provee `import fitz` |
| `pandas` | `script_extractor.py:765` `to_pandas()` — PyMuPDF importa pandas adentro; sin él fallan los PDFs **con tablas** |
| `tabulate` | `script_extractor.py:776` `df.to_markdown()` — pandas lo exige; sin él: `ImportError: Import tabulate failed` |
| `PyYAML` | escribe el YAML canónico |
| `Levenshtein` | `post_procesador.py:558` |

---

## Qué arregla el parche de CALIDAD (medido sobre 300 documentos reales)

| Métrica | Antes | Después |
|---|---|---|
| `signature_mode` en vocabulario del tutorial | 0/300 (usaba `digital`) | **300/300** (`separate_page`/`embedded`) |
| `city` normalizada | `LUJÁN` | **`Luján`** |
| Documentos con firmantes duplicados | 76/300 | **0/300** |
| Firmantes con `role: unknown` | 726 (67%) | **389 (53%)** |
| Fecha incoherente con el nº de acto | 30/300 (10%) | **0/300** |

- **`signature_mode`**: usaba `digital`, que no está en el vocabulario `embedded`\|`separate_page`
  del tutorial (y era redundante: que sea electrónico ya lo dice `source_system`).
- **`city`**: ahora capitaliza bien conservando acentos (`San Miguel`, `Luján`).
- **`signers`**: el mismo firmante aparecía dos veces —desde el pie del acto (con rol) y desde la
  hoja de firmas de sudocu (sin rol, en mayúsculas)—. Ahora se fusionan por tokens del nombre y se
  prioriza la variante que trae rol. No se descarta a nadie.

Los 389 `role: unknown` que quedan son firmantes que el extractor detecta pero no logra asociar a
un rol: eso ya es de la detección (script_extractor.py), no del post-proceso.

## Auditoría del corpus COMO INSUMO DE RAG

`auditoria_rag.py` no pregunta "¿el parser anduvo?" sino "¿esto sirve para recuperar normativa
con precisión?". Medido sobre 300 documentos reales:

### ✅ Lo que está bien
| Eje | Resultado |
|---|---|
| Chunkabilidad | **0/300 sin headers** — todos cortables por sección |
| Artículos marcados | 924 artículos (3,1/doc); solo 3% sin `### Artículo` |
| Texto huérfano | mediana 0 chars (casi nada fuera de sección) |
| `document_id` únicos | **300/300** — sin colisiones en el índice |
| Documentos mudos | 1/300 con <400 chars |
| Fechas coherentes | **300/300** tras el fix (antes 10% mal) |

### ⚠️ Lo que hay que tener en cuenta al indexar
| Problema | Alcance | Impacto en el RAG |
|---|---|---|
| **Membrete dentro del cuerpo** | 38% | "Universidad Nacional de Luján / Departamento de…" se cuela **dentro del texto del Artículo 1** y contamina el embedding de ese chunk |
| **Citas no capturadas en metadata** | 39% | 84% de los docs citan otra norma en el texto, pero `normative_references` queda vacío en 54% → no se puede responder "¿qué modificó a X?" por metadata |
| **Títulos inconsistentes** | — | `Parte dispositiva` (246) vs `Parte resolutiva` (44); `Firmas` (535) vs `Hoja de firmas` (300); **11 formas distintas de "ANEXO"** → routing por header poco fiable |
| **Fórmula como header** | 10% | `## LA DIRECCIÓN GENERAL DE…` abre una sección espuria |
| **Cierre ritual** | 91% | "Regístrese, comuníquese, archívese" en casi todos: chunk de bajo valor, conviene filtrarlo del índice |
| **Secciones gigantes** | 14/300 >4000 chars | anexos (programas de asignatura) que hay que sub-chunkear |

**Recomendación para el ingester**: cortar por `##`/`###`, descartar chunks que sean solo cierre
ritual o firmas, sub-chunkear secciones >4000 chars, y normalizar los títulos variantes a una
forma canónica al indexar (mapear `Parte dispositiva`→`Parte resolutiva`, `Hoja de firmas`→`Firmas`).

## Herramientas nuevas (mías, no tocan el repo de los becarios)

- **`construir_canonicos.py`** — arma el archivo final único que pide el tutorial 18: un `.md` con
  front-matter YAML arriba y el cuerpo Markdown abajo (hoy quedaban sueltos y el md iba embebido
  en el YAML). El job de SLURM ya lo corre al final. Salida en `resultados_extractor/canonicos/`.
- **`verificar_resultados.py`** — auditor para revisar los 19.959 sin abrirlos a mano: cobertura
  (¿se procesaron todos?), integridad (¿las 3 salidas por doc?), y calidad de la metadata (campos
  vacíos, fuera de vocabulario, fechas no-ISO, firmantes unknown, warnings). Correlo después del job:
  `python verificar_resultados.py work/resultados_extractor data/portal`

### Fecha de emisión — arreglado

El post-procesador hacía `date_candidates[0]` a ciegas, y el primer candidato suele ser una fecha
de vigencia o del sello digital. Caso real: el PDF dice `LUJAN, 1º DE ABRIL DE 2025`, los
candidatos eran `['2026-03-31', '2025-04-01']` → tomaba 2026, **un año de más**.

Ahora `elegir_fecha_emision()` usa el año del número de acto (`101/2025` → 2025) como señal
principal, y como respaldo la fecha del encabezado del documento. Resultado: **10% → 0%** de
fechas incoherentes, y el rango de años del corpus pasó de un imposible `2022–2030` a `2024–2026`.

> Nota: el arreglo elige mejor entre los candidatos que el extractor **ya detecta**. Si el
> extractor no detectó ninguna fecha del año correcto, no hay magia posible: eso sí requeriría
> tocar `script_extractor.py`.

## Lo que NO toqué (queda como observación para los becarios)

- **Escrituras concurrentes sin lock**: `registrar_escaneo()` hace read + append desde varios
  workers. Puede duplicar o perder líneas en `docus_escaneos.txt`.
- **Todo en una carpeta**: ~60.000 archivos (3 por documento) en un solo directorio.
- **Hay que pararse en el directorio de los `.py`** (se resuelven relativos al CWD).

### Brechas de calidad vs. los tutoriales

| Qué | Ejemplo real | Qué pide el tutorial |
|---|---|---|
| Convención inestable | `## Parte dispositiva` vs `## Parte resolutiva`; `## Firmas` vs `## Hoja de firmas` | "una sola convención estable" |
| Ordinal partido | `### Artículo 1` y el cuerpo arranca `°.- APROBAR...` | el `1°.-` no debería cortarse |
| Membrete sin limpiar | "Universidad Nacional de Luján / Departamento de Ciencias Básicas" **dentro** del Artículo 1 | quitar repeticiones del membrete |
| `city` sin normalizar | `city: LUJÁN` | `city: "Luján"` |
| `signature_mode` fuera de vocabulario | `digital` | `embedded` \| `separate_page` |
| Firmantes sin consolidar | mismo firmante 2 veces + quien lo cargó en sudocu, `role: unknown` | "salida razonablemente consolidada" |
| `auxiliary_codes` con basura | `'Fecha: 29/12'` | solo códigos |
| Fórmula como encabezado | `## LA DIRECCIÓN GENERAL DE ASUNTOS ACADÉMICOS` | esa fórmula abre `## Parte resolutiva` |
| Concatenación final | `.md` y `_canonico.yaml` sueltos; el md va embebido como `content_markdown` | un archivo: front-matter YAML + cuerpo Markdown |

---

## Cómo correrlo

### Paso 0 · Reconocimiento — ✅ hecho
Clementina: **Python 3.9.18**, partición de CPU **`cpunode`** (default `batch`; `testing` = 30 min).
El job ya viene con `--partition=cpunode`.

### Paso 1 · Wheels — ✅ ya bajadas
El bundle **`wheels-py3.9/`** (11 wheels, 58 MB, cp39/manylinux) ya está en esta carpeta y
verifiqué que es autocontenido (instala sin internet). **Podés saltear este paso.**
Solo re-generalo si cambian las dependencias:
```bash
cd rag-unlu/extractor
./01_bajar_wheels.sh 3.9
```

> **Verificado en Python 3.9 real**: bajé un CPython 3.9 con `uv` y corrí el pipeline parcheado
> sobre 15 PDFs del corpus → 15/15 OK, 15/15 canónicos. El código NO usa sintaxis de 3.10+.
> Versiones que resuelven para 3.9: PyMuPDF 1.26, pandas 2.3.2 (la 3.0 no soporta 3.9),
> numpy 2.0.2, Levenshtein 0.27.1 + rapidfuzz, PyYAML 6.0.3, tabulate 0.9.

### Paso 2 · Subir
```bash
cd rag-unlu/extractor
rsync -avh repo-patched wheels-py3.9 construir_canonicos.py verificar_resultados.py \
      requirements-clementina.txt 02_instalar_en_clementina.sh run_extractor.slurm \
      clementina:rag-unlu/extractor/
```

### Paso 3 · Instalar (sin internet)
```bash
ssh clementina
cd ~/rag-unlu/extractor && ./02_instalar_en_clementina.sh wheels-py3.9
```

### Paso 4 · Work dir
```bash
mkdir -p ~/rag-unlu/extractor/work
cp ~/rag-unlu/extractor/repo-patched/*.py ~/rag-unlu/extractor/work/
```
(`repo-patched/` ya trae los dos parches aplicados. `construir_canonicos.py` y
`verificar_resultados.py` viven en `~/rag-unlu/extractor/` y el job los llama desde ahí.)

### Paso 5 · Canario (200 PDFs) — no encoles los 19.959 de una
```bash
cd ~/rag-unlu/extractor/work
source ../venv/bin/activate
mkdir -p /tmp/canario
find ~/rag-unlu/data/portal -maxdepth 1 -name '*.pdf' | head -200 | xargs -I{} cp {} /tmp/canario/
# en un nodo de cómputo (evitá cargar el login node):
srun --partition=testing --cpus-per-task=8 --time=00:20:00 python procesador_masivo.py /tmp/canario
```
Tiene que decir `Exitosos: 200 / Fallidos: 0`. Revisá a ojo un `.md` y un `_canonico.yaml`.
Después limpiá: `rm -rf /tmp/canario resultados_extractor` (para que la corrida real arranque limpia).

### Paso 6 · La corrida completa
```bash
cd ~/rag-unlu/extractor
# editá antes la línea #SBATCH --partition
sbatch run_extractor.slurm
squeue -u $USER
```
Salidas en `~/rag-unlu/extractor/work/resultados_extractor/`. Es reanudable: si se corta,
`sbatch` de nuevo y sigue donde quedó.

---

## Para los becarios

[extractor_pdf_clementina.patch](extractor_pdf_clementina.patch) se aplica sobre el repo limpio:
```bash
git apply extractor_pdf_clementina.patch
```
Son cambios de portabilidad y robustez, no de la lógica de extracción.

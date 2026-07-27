# Trazabilidad del pipeline

Este documento describe cómo se rastrea una respuesta del asistente hasta el documento
oficial que la sustenta, y cómo se reproduce cada etapa. Está pensado para auditoría.

## Cadena de custodia del dato

```
Sistema fuente (portal / digesto UNLu)
  │
  ├─ scrapers/                PDF + metadatos.csv (fecha, título, número, estado)
  │                           mapeo_renombres.csv (nombre original -> nombre en disco)
  │
  ├─ ingesta/                 copia verificada a Clementina
  │                           verify_upload.sh compara nombre y tamaño de los 19.959
  │
  ├─ extractor/               PDF -> .md (estructura) + .json (señales)
  │                           procesar_corpus.py registra CADA documento en JSONL
  │
  ├─ pipeline/unir_metadata   asocia cada PDF con su fila del sistema fuente
  │                           marca la confianza del vínculo (alta / media / conflicto)
  │
  ├─ pipeline/chunkear        corta por unidad normativa y adjunta la cita
  │
  ├─ pipeline/embeddings      vectores denso + léxico por chunk
  │
  └─ backend/                 respuesta con las fuentes que la sustentan
```

## Cómo se audita una respuesta

Cada respuesta de `/consultar` incluye, por fuente:

| Campo | Para qué sirve |
|---|---|
| `cita` | identificación legible del acto y su artículo |
| `documento` | identificador interno del documento |
| `source_pdf` | nombre del PDF original |
| `seccion` | de qué parte del acto salió el texto |
| `texto` | el fragmento exacto que se le pasó al modelo |
| `metadata_confianza` | si la metadata está verificada contra el sistema origen |
| `ranking` | posición en cada señal de recuperación (densa y léxica) |

`GET /documento/{documento}` devuelve todas las secciones de un documento, para
contrastar la cita contra el acto completo.

El texto que recibe el modelo es exactamente el campo `texto` de las fuentes: no hay
reescritura intermedia. El modelo tiene instrucción explícita de no usar conocimiento
propio y de declarar cuándo el contexto no alcanza.

## Confianza de la metadata

La metadata de cada documento se obtiene por dos caminos independientes:

- **posicional**: nombre del archivo → `mapeo_renombres.csv` → fila del sistema fuente
- **por código**: el código impreso en el acto (`DISPCD-CB : 528 / 2025`) contra el
  campo `Numero` del sistema fuente

| Confianza | Significado |
|---|---|
| `alta` | los dos caminos coinciden |
| `media` | resolvió uno solo |
| `conflicto` | los dos resolvieron y apuntan a actos distintos → **no se usa** |
| `sin_metadata` | ninguno resolvió → queda la inferida del texto, marcada como tal |

Esta doble vía no es redundancia: el scraper numeró los archivos con un identificador que
un renombrado posterior descartó, y **1.780 de 19.959 archivos** quedaron con un número que
ya no corresponde a su fila. Unir por un solo camino daba metadata de otro documento en
~9% de los casos, sin ninguna señal de error. Las respuestas marcan explícitamente cuándo
alguna fuente tiene metadata sin verificar.

## Reproducibilidad

Toda etapa se regenera desde el repositorio, sin pasos manuales:

```bash
# 1. corpus (requiere rclone configurado)
ingesta/download_portal_rclone.sh && ingesta/upload_portal.sh && ingesta/verify_upload.sh

# 2. parseo (Clementina, job array)
sbatch pipeline/run_corpus.slurm

# 3. metadata autoritativa
python pipeline/unir_metadata.py --scrapers scrapers --pdfs data/portal \
    --yaml resultados/portal --salida resultados/join_metadata.csv

# 4. chunks
python pipeline/chunkear.py --resultados resultados/portal \
    --metadata resultados/join_metadata.csv --salida resultados/chunks.jsonl

# 5. índice
python pipeline/embeddings.py --chunks resultados/chunks.jsonl --salida indice/

# 6. API
uvicorn backend.api:app --port 8000
```

Verificación en cada etapa:

| Etapa | Comando | Qué comprueba |
|---|---|---|
| ingesta | `ingesta/verify_upload.sh` | nombre y tamaño de cada archivo, local vs cluster |
| parseo | `pipeline/verificar_resultados.py` | cobertura, integridad, campos de la metadata |
| parseo | `resultados/portal/proceso_*.jsonl` | estado y duración de cada documento |
| calidad | `pipeline/auditoria_rag.py` | aptitud del corpus como insumo de recuperación |
| índice | `GET /salud` | modelo, dimensión y cantidad de chunks indexados |

## Dependencias externas

| Componente | Dónde corre | Qué sale de la institución |
|---|---|---|
| Extractor, chunking, embeddings | Clementina (sin internet) | nada |
| Índice y recuperación | servidor propio | nada |
| Generación | API externa (configurable) | la consulta y los fragmentos recuperados |

La generación está aislada en una única función (`backend/api.py::generar`). Reemplazarla
por un modelo alojado en infraestructura de la Universidad no afecta al resto del sistema.
Mientras se use una API externa, salen de la institución la consulta del usuario y los
fragmentos de normativa recuperados; el digesto es documentación pública.

## Autoría del código

`CREDITS.md` detalla la autoría. El extractor y los scrapers se incorporaron con
`git subtree` conservando su historial: `git log` y `git blame` mantienen la atribución
original de cada línea.

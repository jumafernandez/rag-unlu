# rag-unlu

Pipeline para construir un sistema RAG sobre la **normativa de la Universidad Nacional de Luján**:
disposiciones, resoluciones y órdenes de compra que van del PDF publicado al texto estructurado
con metadata, listo para indexar.

El corpus sale de **dos sistemas distintos**, con documentos de fisonomía diferente
(ver [docs/fuentes.md](docs/fuentes.md)):

| Fuente | Período | Documentos | Estado |
|---|---|---|---|
| **portal** | desde abril/2024 | 19.959 | en Clementina, parseándose |
| **digesto** (legacy) | hasta abril/2024 | ~120.000 | scrapeado, pendiente de incorporar |

El cómputo pesado corre en **Clementina XXI** (supercomputadora nacional, SLURM).

## El recorrido de un documento

```
scrapers/     portal y digesto -> PDFs + metadatos.csv (fecha, título, estado del sistema fuente)
  └─ ingesta/         rclone: Drive -> disco -> Clementina (rsync)
       └─ extractor/  PDF -> .md jerarquizado + .json de señales
            └─ pipeline/  .json + .md -> _canonico.yaml -> archivo canónico único
                 └─ (front-matter YAML + cuerpo Markdown, listo para chunkear)
```

## Estructura

| Carpeta | Qué hay |
|---|---|
| `scrapers/` | Scraping de ambos sistemas + `metadatos.csv` (subtree, autoría de Fran) |
| `ingesta/` | Bajar los PDFs del Drive y subirlos a Clementina. Ver [docs/ingesta-corpus.md](docs/ingesta-corpus.md) |
| `extractor/` | Parser PDF→Markdown+JSON de los becarios (subtree, ver [CREDITS.md](CREDITS.md)) |
| `pipeline/` | Lo que rodea al extractor: instalación offline, job de SLURM, armado del canónico, auditoría |
| `patches/` | Nuestras mejoras al extractor, aisladas para proponer upstream |
| `docs/` | Las dos fuentes, revisión del extractor, auditoría del corpus como insumo de RAG |

## Estado

| Etapa | Estado |
|---|---|
| Corpus **portal** en Clementina | ✅ 19.959 PDFs (8,56 GiB), verificados uno por uno |
| Extractor revisado y parcheado | ✅ portabilidad + calidad (ver `patches/`) |
| Instalación en Clementina | ✅ venv con deps offline, verificado |
| Corrida completa de los 19.959 | ⚠️ se cuelga a ~686 documentos — en diagnóstico |
| Metadata del scraper en el pipeline | ⏳ `metadatos.csv` trae fecha/título/estado del sistema fuente, sin usar todavía |
| Corpus **digesto** (~120k) | ⏳ scrapeado, sin incorporar; el parser nunca vio documentos `legacy` |
| Indexado / RAG | ⏳ pendiente |

## Arranque rápido

```bash
# 1. traer el corpus (necesita rclone configurado, ver ingesta/SETUP_RCLONE.md)
cd ingesta && ./download_portal_rclone.sh && ./upload_portal.sh && ./verify_upload.sh

# 2. preparar Clementina (sin internet: wheels precompiladas)
cd ../pipeline && ./01_bajar_wheels.sh 3.9
# ... subir a Clementina y allá:
./02_instalar_en_clementina.sh wheels-py3.9

# 3. procesar
sbatch run_extractor.slurm

# 4. auditar el resultado
python pipeline/verificar_resultados.py <resultados> <pdfs>   # cobertura e integridad
python pipeline/auditoria_rag.py <resultados>                 # calidad como insumo de RAG
```

## Qué mide la auditoría

`auditoria_rag.py` no pregunta "¿el parser anduvo?" sino "¿esto sirve para recuperar normativa
con precisión?": chunkabilidad (¿hay esqueleto de headers?), filtrado (¿la metadata permite
acotar por tipo/órgano/año sin mentir?), integridad (¿documentos mudos, ruido que contamina
embeddings?) y enlaces entre normas. Resultados sobre 300 documentos en
[docs/revision-extractor.md](docs/revision-extractor.md).

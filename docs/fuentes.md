# Las dos fuentes documentales

La normativa de la UNLu no sale de un solo sistema. Hay **dos**, con documentos de fisonomía
distinta, y eso atraviesa todo el pipeline: scraper distinto, y muy probablemente reglas de
parseo distintas.

| | **portal** | **digesto** |
|---|---|---|
| Vigencia | desde abril/2024 | hasta abril/2024 (fuera de producción) |
| Volumen | 19.959 documentos | ~120.000 documentos |
| Estado | ✅ scrapeado, en Clementina, parseándose | ✅ scrapeado, **pendiente de incorporar** |
| `source_system` | `electronic` | `legacy` |
| Firmas | hoja de firmas separada, digital | embebidas al final del texto |
| Códigos | `DISPCD-CB : 528 / 2025` | `RESPRHCS-LUJ:0000042-24`, + auxiliares tipo `ACTDB-LUJ: 0000038/2010` |
| Particularidad | — | leyenda de validez web al pie |

## Por qué importa

El parser (`extractor/`) fue desarrollado y probado **contra documentos del portal**. En la
muestra de 300 documentos verificada, **300/300 son `electronic`**: o sea, la rama `legacy` del
código nunca se ejercitó a escala.

Cuando entren los ~120.000 del digesto, el corpus pasa de 20k a 140k documentos (**7×**), con un
formato que el parser prácticamente no vio. Hay dos frentes a resolver antes:

1. **Fidelidad de parseo sobre `legacy`** — las expresiones regulares de encabezado, la detección
   de firmas y la de anexos asumen el formato nuevo.
2. **Arquitectura de salida** — hoy son 3 archivos por documento en un único directorio. Con 140k
   documentos son ~420.000 archivos en un directorio de filesystem paralelo. La corrida actual en
   Clementina ya se colgó a los 686 documentos con 29 workers escribiendo al mismo directorio
   (ningún PDF individual cuelga: se probaron 3.000, cero colgados).

## Metadata autoritativa del scraper

`scrapers/metadatos.csv` trae, para cada documento del portal, metadata **tomada del sistema
fuente**, no inferida del texto del PDF:

| Campo | Para qué sirve |
|---|---|
| `Fecha` | fecha real, sin heurística sobre el texto |
| `Titulo` | título descriptivo — el pipeline hoy **no lo tiene** y es muy valioso para recuperación |
| `Numero` | código y número del acto |
| `Estado` | vigencia (en el portal todos `Autorizado`; en el digesto puede haber derogadas) |
| `Tipo de documento` | tipo y órgano emisor, más granular que lo que infiere el parser |

La clave de join con los PDFs es **(carpeta, `ID PDF`)**: el `ID PDF` se reinicia en 1 por
carpeta y el nombre del archivo termina en `_<N>.pdf` con ese mismo número.

Aprovechar esta metadata es probablemente la mejora de mayor impacto disponible: convierte
campos inferidos —y a veces equivocados— en campos tomados de la fuente.

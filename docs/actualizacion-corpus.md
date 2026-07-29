# Actualización del corpus

Estado del corpus, qué se hizo en julio de 2026 y cómo repetirlo.

## Punto de partida

El corpus se recolectó entre el **7 y el 13 de abril de 2026**: 19.959 documentos. El
percentil 99 de las fechas de esos actos es el **10 de abril**, que es la cobertura real.

El índice declaraba `normativa_hasta: 06/07/2026`, tres meses más tarde. Ese valor salía del
**máximo** de las fechas, y unos pocos actos con fecha atípica lo corrían. La interfaz
mostraba ese dato, de modo que anunciaba una actualización que el corpus no tenía. Conviene
calcularlo con un percentil alto en lugar del máximo.

## Qué se agregó

Cada acto del portal tiene una URL permanente a su PDF publicado (ver
[recoleccion.md](recoleccion.md)). Se incorporó a cada fragmento del índice:

| | |
|---|---|
| Fragmentos con enlace al PDF oficial | 140.825 de 140.902 (99,95 %) |
| Documentos sin enlace | 13 de 19.959 |
| Matriz de embeddings | sin cambios |

El enlace **no** obligó a rehacer los embeddings: lo que se embebe es `título | cita` más el
fragmento, y ninguno de los campos agregados —URL, identificadores, fecha del acto— entra en
ese texto. Se verificó que `densos.npy` siguiera alineado con `chunks.jsonl` fragmento por
fragmento.

### Cómo se emparejó cada fragmento con su acto

Por identidad —código, número y año—, con dos salvedades que aparecieron al hacerlo:

**El código se escribe distinto en cada fuente.** El portal antepone el tipo de acto
(`DISP DGAA`) y el documento lleva solo el organismo (`DGAA`). Contemplar ambas formas
recuperó 23.322 fragmentos que no encontraban su acto aunque el acto estuviera recolectado.

**Hay actos sin identidad reconstruible.** Los de Órdenes de Compra quedaron con código y
número `unknown` porque el parser no pudo extraerlos del PDF; los de Ciencias Sociales
guardan el código abreviado `CS` mientras el portal distingue `DISPCD-CS`, `DISPPCD-CS` y
`DISPDD-CS`. Para esos se usa el **título dentro de la misma sección**, y solo cuando
corresponde a un único acto: con más de un candidato no se enlaza, porque llevar a alguien
al documento equivocado es peor que no ofrecer enlace.

## Actos que faltan

La recolección de julio encontró **21.344 actos** en el portal contra los 19.959 del corpus.
La diferencia son **3.766 actos que no tenemos**, casi todos posteriores al 10 de abril:

| Mes del acto | Faltantes |
|---|---|
| abril 2026 (posterior al 10) | 761 |
| mayo 2026 | 1.048 |
| junio 2026 | 995 |
| julio 2026 | 617 |
| anteriores | ~345 |

Mientras esos actos no estén indexados, una consulta sobre normativa de mayo o junio va a
responder que no encontró nada, sin distinguir entre "no existe" y "no está cargado".

## Cómo actualizar

### 1. Recolectar los metadatos

Por API, sin navegador. Es lo más rápido y no depende de que la interfaz renderice:

```bash
cd scrapers
../.venv/bin/python recolectar_api.py --todas --salida metadatos.csv --paciencia 25
```

`--paciencia` es la cantidad de respuestas vacías seguidas que se toleran antes de dar una
carpeta por terminada. El servidor responde en falso de manera intermitente, así que un
valor bajo corta la recolección a mitad de camino: con 6, la carpeta de Secretarías de
Rectorado se cortó en 90 documentos; con 25 entregó 5.613.

### 2. Bajar los PDF nuevos

```bash
cd scrapers
../.venv/bin/python bajar_pdfs.py \
    --destino ../data/portal-incremental \
    --log ../data/descargas.jsonl \
    --desde 10/04/2026
```

Es reanudable: un archivo ya presente no se vuelve a pedir. Cada descarga deja una línea en
el log con la URL, el código de respuesta, el tamaño, el SHA-256 y la duración. Escribe con
nombre temporal y renombra al final, para que un corte no deje un PDF truncado con nombre
definitivo que la reanudación daría por bueno.

Rinde alrededor de dos documentos por segundo.

### 3. Actualizar la metadata del índice

```bash
python pipeline/actualizar_metadata.py --metadatos scrapers/metadatos.csv
python pipeline/actualizar_metadata.py --metadatos scrapers/metadatos.csv --aplicar
```

La primera corrida solo informa. Interesa el conteo de **títulos que cambian**: el título se
embebe, así que un título distinto significa que ese fragmento quedó con un vector viejo.
Si el número es alto, conviene revisar antes de aplicar. La comparación colapsa espacios
repetidos, porque la recolección por navegador leía el texto ya renderizado —que los junta—
y la API devuelve el valor crudo.

### 4. Indexar lo nuevo

Los PDF nuevos hay que parsearlos, fragmentarlos y embeberlos con el pipeline habitual. Este
paso sí necesita GPU o varias horas de CPU, y es el único que no se resuelve en el escritorio.

## Un aviso operativo

El servidor y el túnel corren en la notebook. Si se queda sin batería, macOS **hiberna**: los
procesos sobreviven y retoman al enchufarla, pero durante ese lapso no responde nada. El 29
de julio estuvo caída entre las 07:53 y las 12:30 por esa razón. `caffeinate -dims` evita la
suspensión por inactividad, pero no la falta de energía.

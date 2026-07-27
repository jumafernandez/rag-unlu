# PDF to Markdown Extractor

Este proyecto convierte documentos PDF complejos en texto estructurado en Markdown y en metadatos normalizados en YAML.

## 🔧 Scripts principales

### 1. script_extractor.py
Responsable de la extracción inicial de cada PDF.

Funciona en dos pasos internos:
- Aplanado y limpieza del PDF para reconstruir el contenido en memoria.
- Extracción de texto, tablas, firmas y metadatos auxiliares.

Genera como salida:
- un archivo `.md` con el contenido estructurado,
- un archivo `.json` con los metadatos detectados,
- y registra documentos filtrados o parciales en `docus_escaneos.txt` cuando el PDF parece ser un escaneo puro o un caso parcialmente escaneado.

### 2. post_procesador.py
Toma los archivos generados por `script_extractor.py` y construye un YAML canónico.

Su rol es:
- leer el `.json` y el `.md`,
- normalizar los metadatos principales,
- validar campos básicos como `document_id`, `document_code`, `date_issued`, `city`, entre otros,
- y escribir un archivo `_canonico.yaml` con la información consolidada.

Además, si detecta campos incompletos o inconsistencias, registra la incidencia en `docus_error.txt`.

### 3. procesador_masivo.py
Orquesta el procesamiento en lote de una carpeta de PDFs.

Ejecuta, para cada archivo:
1. `script_extractor.py`,
2. `post_procesador.py` si se generaron los archivos intermedios,
3. y guarda los resultados en la carpeta `resultados_extractor` dentro del directorio de trabajo.

También administra los reportes de escaneos y errores.

### 4. analizador_txt.py
Herramienta utilizada en desarrollo para visualizar el tipo y cantidad de errores de un procesamiento, a traves de su `docus_error.txt`.

## 🛠️ Requisitos e instalación

Asegúrate de tener Python 3.x instalado y luego instala las dependencias:

```bash
pip install pymupdf pandas tabulate pyyaml
```

## ▶️ Uso

### Procesamiento individual de un PDF

#### Paso 1: extracción
```bash
py script_extractor.py ruta/al/archivo.pdf
```

Esto genera:
- `archivo.md`
- `archivo.json`

#### Paso 2: post-procesamiento
```bash
py post_procesador.py archivo.json archivo.md
```

Esto genera:
- `archivo_canonico.yaml`

### Procesamiento masivo de una carpeta

```bash
py procesador_masivo.py <ruta_de_la_carpeta_con_pdfs> [cantidad_de_archivos]
```

Ejemplos:
```bash
py procesador_masivo.py .\testing\docus2
py procesador_masivo.py "D:\Documentos\PDFs" 500
```

El segundo parámetro es opcional y permite procesar solo una cantidad limitada de archivos.

## 📁 Archivos y salidas

- `script_extractor.py`: genera `.md` y `.json` intermedios.
- `post_procesador.py`: genera `_canonico.yaml`.
- `procesador_masivo.py`: procesa carpetas completas y guarda los resultados en `resultados_extractor`.
- `docus_escaneos.txt`: lista los PDFs marcados como escaneos puros o parciales.
- `docus_error.txt`: registra problemas de metadata o campos incompletos.

## 📂 Estructura de prueba

La carpeta `Pruebas/` contiene ejemplos y documentos usados para comparar resultados del extractor con los PDFs originales.
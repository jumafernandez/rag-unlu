# Método anterior de recolección (conservado, no usar)

Estos scripts son la primera generación de la recolección, con navegador (Selenium).
Se conservan porque cuentan cómo se construyó el corpus inicial y porque documentan el
problema que motivó el método actual; no se usan más.

| Archivo | Qué era |
|---|---|
| `Scraping.py` + `funciones.py` + `opciones.py` | Recorrido del portal con Chrome: clic en el ícono de descarga de cada fila, unión de metadatos POR POSICIÓN |
| `recolectar.py` | Segunda iteración: leía el objeto Angular de cada fila (ya sin unión posicional), pero seguía necesitando navegador |
| `renombrar_archivos.py` + `mapeo_renombres.csv` | Renombrado posterior de lo descargado; el mapeo es el registro de esa cirugía |

Por qué se reemplazó: la unión posicional se rompió en silencio (1.780 archivos
renumerados por fecha de creación) y la descarga por navegador dependía de que la
interfaz mostrara el botón —el portal de la UNSL, sin ir más lejos, no lo muestra—.
El método actual (`../recolectar_api.py`) habla con la API del portal: identidad
desglosada por acto, URL permanente al PDF, criterio de completitud contra el total
declarado, y cero navegador. Ver `docs/recoleccion.md`.

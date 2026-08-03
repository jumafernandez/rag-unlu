# Qué hubo que adaptar para la UNSL — la lista que define lo configurable

Este espejo existe para contestar una pregunta con evidencia: ¿qué hay que tocar para
montar el asistente en otra universidad con portal SUDOCU? La regla del experimento: el
código de la instancia UNLu no se toca; cada diferencia encontrada se anota acá y se
convierte en requisito de configuración. Esta lista ES el diseño de esa configuración.

## Lo que NO hubo que tocar (ya era configurable o portable)

- **URL del portal**: `SUDOCU_PORTAL_URL` en el entorno. Única seña de identidad de la
  fuente de datos.
- **Carpetas**: el recolector las descubre en el portal (`/mpd/contenedores/`). La UNSL
  publica por facultad (15 carpetas) y la UNLu por tipo de acto (11): ninguna
  configuración, el descubrimiento absorbe la diferencia.
- **Criterio de completitud** (`total` por documento), **URL permanente del PDF** (el
  identificador dos veces, ruta y query) y **endpoint público sin autenticación**:
  idénticos en las dos instalaciones. La sonda (`probar_portal.py`) lo verifica en tres
  pedidos antes de comprometerse.
- **Identidad, marca y textos de la interfaz**: panel de Personalización (nombre, sigla,
  denominación del cuerpo normativo, logo, colores, sugerencias, aviso legal).
- **Estructura de los actos**: Visto / Considerando / Parte resolutiva / artículos se
  detectan igual en los PDF sanluiseños (20/20 en el piloto). La forma del acto
  administrativo es nacional, no de cada casa.

## Diferencias absorbidas por el código común (mismo código en todas)

El criterio del proyecto: si una diferencia se puede absorber sin que ninguna
instalación se entere, va al código común; la configuración queda solo para lo
genuinamente distinto.

1. **La clave del código de acto en el objeto `nro`** del listado.
   UNLu: `nro.tipo = "DISPCD-CB"`. UNSL: `nro.codigo_tipo_documento = "R17"` (y `tipo`
   no existe). Sin esto el archivo pierde identidad (`24_2024.pdf` en vez de
   `R17_24_2024.pdf`) y colisiona entre carpetas.
   → *Absorbido*: `catalogo_comun.py` prueba las claves en orden. Corre igual en las dos.

2. **El nombre de la carpeta con `--carpeta` suelto.**
   El recolector etiquetaba con el mapa estático de la UNLu al listar una carpeta por id.
   → *Absorbido*: se pregunta siempre al portal; el mapa queda como respaldo.

## Lo que SÍ pide configuración por universidad

1. **Membrete y lemas del año en los PDF.**
   Los actos de la UNSL abren con lemas ("Año de la Defensa de la Vida…", "A 30 años de
   la Consagración Constitucional…") que el extractor parsea como títulos espurios
   (`##`), ensuciando los fragmentos tipo "otra". No rompe la estructura, pero mete
   ruido en el índice.
   → *Configurable*: patrones de limpieza de membrete por institución (la decisión de JM:
   parser configurable por universidad, un YAML con lo de la UNLu como default). Tres
   superficies previstas: limpieza de encabezados/lemas, sinónimos de marcadores
   estructurales, detección de firmas. El piloto solo encontró necesidad de la primera.

## Pendiente de observar con el corpus completo

- Tipos de acto y numeración de la UNSL a lo largo de todas las carpetas (¿aparecen más
  variantes de `nro`?).
- Calidad de la fragmentación sobre ordenanzas y resoluciones conjuntas (documentos
  potencialmente más largos que los de DECOM).
- Fechas: `fecha` vs `fecha_autorizacion` se comportan igual que en la UNLu en el piloto;
  confirmar en el corpus completo.

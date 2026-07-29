# Recolección desde el portal SUDOCU

Cómo se obtienen los actos administrativos y por qué el procedimiento cambió.

## El problema del método anterior

La primera recolección (`scrapers/Scraping.py` con `funciones.py`) recorría el portal con
un navegador, hacía clic en el ícono de descarga de cada fila y dejaba que el navegador
guardara el PDF. Después, `renombrar_archivos.py` renombraba lo descargado y el CSV de
metadatos se unía a los archivos **por posición**: la fila *n* del CSV correspondía al
archivo *n* de la carpeta.

Ese supuesto se rompió. Durante la verificación se detectaron **1.780 archivos renumerados
por fecha de creación**, lo que desplazó la correspondencia. El problema no es que haya
fallado: es que falló **en silencio**. Un sistema construido sobre esa unión habría citado
fechas, números y títulos equivocados con total aplomo, que en normativa es la peor forma
posible de estar mal.

`pipeline/unir_metadata.py` recuperó la situación uniendo por el código de acto impreso en
el propio documento en vez de por posición. Pero seguía siendo una reparación sobre un
vínculo que nunca debió ser posicional.

## Qué se encontró

La interfaz del portal descarga los PDF mediante una URL temporal de tipo `blob:`, que
existe solo mientras la pestaña está abierta. Eso hacía parecer que no había forma de
enlazar un acto de manera permanente.

El `blob` es un artefacto del navegador. Detrás hay una petición HTTP común. En el bundle
de la aplicación (`mpd.min.js`):

```js
descargaDocumento: function (a, o) {
  var i = _.isUndefined(t.UNGSXTuser)
        ? n.apiUrl + "/archivos/publico/" + a
        : n.apiUrl + "/archivos/" + a + "/documento";
  return e.get(i, { responseType: "arraybuffer",
                    params: { id_archivo: a, id_documento: o } })
```

Angular pide el archivo como `arraybuffer` y recién después lo envuelve en un `Blob` para
disparar la descarga. La URL de origen es estable:

```
https://portal.unlu.edu.ar/sudocu/api/archivos/publico/{id_archivo}
    ?id_archivo={id_archivo}&id_documento={id_documento}
```

**El identificador viaja dos veces**, en la ruta y en la query, junto con el del documento.
Con la ruta sola el servidor responde 500 con `error_descargar_missing_data`, cuyo mensaje
sugiere que el documento no tiene PDF generado. Es engañoso: lo que falta es la forma de
pedirlo, no el archivo.

El endpoint **no requiere autenticación**. Se verificó que el PDF obtenido por esa URL es
byte a byte idéntico al descargado por el navegador: mismo SHA-256, mismo tamaño.

Los identificadores están en el objeto que la aplicación mantiene asociado a cada fila:

| Campo del objeto | Significado |
|---|---|
| `documento` | `id_archivo` |
| `id` | `id_documento` |

## Cómo funciona ahora

`scrapers/recolectar.py` recorre el portal para **leer**, no para descargar. Por cada fila
toma el objeto completo y arma la URL. El PDF se baja por HTTP directo.

Las consecuencias:

- **No hay unión posicional.** El CSV trae, en cada fila, el nombre del archivo que le
  corresponde. El nombre se deriva de la identidad del acto (`DISPCD-CB_528_2025.pdf`), no
  de su orden de llegada. Ante colisión se desempata con el identificador del archivo,
  nunca con un contador.
- **Cada acto queda enlazado.** La URL permanente permite ir de una afirmación del
  asistente al documento oficial publicado.
- **Los metadatos vienen desglosados.** El objeto trae tipo, número, año y organismo por
  separado, en lugar de una cadena armada para mostrar en pantalla que había que volver a
  parsear.
- **Se registra el SHA-256** de cada archivo descargado.
- **Es reanudable.** Una corrida sobre el portal completo lleva horas; si se corta, las
  secciones ya recolectadas no se repiten.

### Dos fechas distintas

El portal muestra en su tabla la **fecha de autorización** (cuándo se firmó el acto), que
no siempre coincide con la **fecha del acto** (la impresa en el documento). Para
DISPCD-CB 528/2025: el documento dice *"LUJÁN, 29 DE DICIEMBRE DE 2025"* y la tabla del
portal muestra 30/12/2025.

Ambas se conservan, en columnas separadas: `Fecha` mantiene el significado anterior
—autorización— para no alterar en silencio lo que ya consumía el pipeline, y `Fecha acto`
agrega la del documento.

Nada de esto es específico de la UNLu. SUDOCU es un sistema de alcance nacional y el
Módulo de Publicación Documental es un componente estándar suyo: los selectores que se usan
son clases de Angular Material del propio sistema, no del portal de una institución.

### Verificación pendiente sobre otra instalación

La Universidad Nacional de San Luis tiene su propio módulo de publicación documental sobre
SUDOCU. Está previsto correr esta misma recolección contra ese portal una vez terminada la
de la UNLu, con dos objetivos:

- **Comprobar que el procedimiento es repetible**: que cambiando la URL y la lista de
  carpetas se obtiene un catálogo verificado contra los totales que declara ese portal,
  sin tocar código.
- **Reportarlo en el trabajo**: la diferencia entre afirmar que el sistema es adaptable y
  mostrar que se adaptó es la que un revisor puede comprobar.

Conviene acordarlo previamente con quien corresponda en esa universidad: aunque el portal
es público y la recolección es de solo lectura, sigue siendo tráfico sobre un sistema
institucional ajeno.

## Portabilidad

La superficie de adaptación son dos valores en `scrapers/conf.py`:

```python
PORTAL_URL = os.environ.get("SUDOCU_PORTAL_URL", "...")
SECCIONES  = [...]
```

## Lo que queda anotado

El objeto de cada fila incluye `atributos.contenido`: **el texto completo del acto en
HTML**. Comparado con lo que hoy se extrae del PDF, es más limpio —la extracción de PDF
intercala el encabezado de página en medio de las oraciones y parte los rótulos de los
artículos—, pero **no incluye los anexos**, que en muchos actos son la parte sustantiva.

No es un reemplazo de la extracción del PDF. Sirve para dos cosas todavía sin implementar:
tomar el cuerpo del acto de la fuente más limpia, y usar el HTML como control de calidad
automático de la extracción, comparando ambos textos y señalando los documentos donde
difieren demasiado.

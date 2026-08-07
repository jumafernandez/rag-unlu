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

La superficie de adaptación es UN valor en `scrapers/conf.py`:

```python
PORTAL_URL = os.environ.get("SUDOCU_PORTAL_URL", "...")
```

Las carpetas ya no se configuran: el recolector se las pregunta al portal
(`carpetas_del_portal()`, el mismo endpoint con el que la portada se dibuja), con un mapa
estático como respaldo si ese pedido falla.

Antes de comprometerse con otra universidad, la sonda hace las tres comprobaciones de las
que depende el pipeline —carpetas públicas, campos del listado con su criterio de
completitud, y PDF accesible por URL permanente— en tres pedidos de solo lectura:

```bash
python probar_portal.py --portal https://<portal>/sudocu/mpd/
```

Contra el portal de la UNLu responde `VEREDICTO: compatible`. Queda pendiente correrla
contra el MPD de la UNSL cuando tengamos su URL.

## El total del portal puede venir inflado

El criterio de completitud compara lo recolectado contra el `total` que el portal declara
en cada documento. Ese total puede contar de más: el portal a veces repite una fila entre
dos páginas y la cuenta dos veces (en la carpeta del Departamento de Tecnología: 1808
filas declaradas, 1807 actos distintos). Por eso una carpeta que termina "a uno" del
total, con el listado ya agotado, no está incompleta: está completa y el total miente.
La traza JSONL permite verificarlo: los ids vienen registrados página por página.

## Actualización incremental

Recolectar el catálogo completo cuesta horas: son veintiún mil actos leídos de a quince por
página. Hacer eso todas las semanas para encontrar los pocos actos publicados desde la
semana anterior no escala, y en una universidad con cien mil actos deja de ser viable.

El portal entrega cada carpeta con lo más reciente al principio, así que lo que cambió está
en las primeras páginas. La lectura incremental aprovecha eso: pide páginas desde el
comienzo y se detiene cuando **tres páginas seguidas** no traen ningún documento que el
catálogo no tenga ya. Una actualización semanal pasa de recorrer veintiún mil filas a leer
unas tres páginas por carpeta: dos minutos para el portal entero.

Tres páginas y no una porque un acto puede publicarse con retraso —fechado en marzo,
cargado hoy— y entra al listado por su fecha, o sea sepultado bajo los más nuevos. Frenar
con el primer conocido lo dejaría afuera para siempre.

### Por qué la lectura incremental no alcanza sola

Leer la punta encuentra lo recién publicado, pero es **ciega a los agujeros del medio**: un
acto que se perdió en una corrida cortada hace meses está enterrado entre miles de
conocidos y ninguna lectura de la punta lo va a ver. Al medirlo, seis carpetas tenían 49
actos faltantes de ese tipo, y en todas ellas la punta daba cero novedades.

Por eso hay una segunda comprobación, aritmética: **lo que el catálogo ya tiene de esa
carpeta, más lo que la lectura acaba de encontrar, tiene que dar lo que el portal declara
para ella**. Si no da, la carpeta se lista completa. Es la única forma de que un faltante
viejo se vuelva visible.

### El orden no es el que parece

La lectura incremental depende de que lo nuevo esté al principio, así que eso se verifica
en cada corrida en vez de suponerse. La verificación **no** es que cada fila sea más vieja
que la anterior: se midió contra el portal de la UNLu y no se cumple. En Resoluciones del
H. Consejo Superior aparece un acto del 12 de diciembre después de uno del 11. El listado
viene ordenado por algo que correlaciona con la fecha pero admite inversiones locales.

Lo que sí vale, y es lo único que la lectura necesita, es que ninguna página posterior
traiga un documento más nuevo que el más nuevo de la primera. Si eso deja de cumplirse
—otra versión de SUDOCU, otra configuración, otra universidad— la carpeta se lista completa
en vez de confiar.

### El total se recuerda

El criterio de completitud necesita el total que declara el portal. El portal lo declara
casi siempre, pero devuelve cuerpo vacío de manera intermitente, y no parejo: las dos
carpetas más grandes fallan mucho más seguido que el resto. Una consulta fallida no
significa que el total no exista, así que se guarda en `scrapers/totales.json` cada vez que
se consigue y se reusa cuando no viene. Sin esa memoria, justo las carpetas que más importa
vigilar quedaban sin verificar.

El total no se pide aparte: viaja dentro de cada documento de la primera página, que ya se
pide para leer las novedades.

### Rehacer una carpeta no borra nada

Marcar una carpeta para rehacerla era destructivo: primero se sacaban sus filas del CSV y
después se salía a listarla de nuevo. Entre esas dos cosas puede pasar cualquier cosa, y
pasó: Secretarías de Rectorado quedó con 255 filas de 5.668. El faltante no lo causó la
interrupción sino el borrado previo.

Ahora lo que se saca se conserva, y al terminar se reponen las filas cuyo documento no haya
vuelto a aparecer. La carpeta puede quedar incompleta —y el resumen lo dice—, pero el
catálogo no pierde lo que ya tenía. Reponer de más es imposible: se compara por
identificador de documento.

### Cuándo se lista la carpeta entera

- La verificación de orden falla.
- El portal no entrega ni una sola página después de insistir.
- La punta no converge: hay tanto desconocido que ya no es "lo nuevo" sino una carpeta a
  medio construir.
- La cuenta no cierra contra el total declarado.
- Se pide explícitamente con `--completo`.

Fuera de esos casos, la carpeta se da por al día y se dice por qué.

## Lo que queda anotado

El objeto de cada fila incluye `atributos.contenido`: **el texto completo del acto en
HTML**. Comparado con lo que hoy se extrae del PDF, es más limpio —la extracción de PDF
intercala el encabezado de página en medio de las oraciones y parte los rótulos de los
artículos—, pero **no incluye los anexos**, que en muchos actos son la parte sustantiva.

No es un reemplazo de la extracción del PDF. Sirve para dos cosas todavía sin implementar:
tomar el cuerpo del acto de la fuente más limpia, y usar el HTML como control de calidad
automático de la extracción, comparando ambos textos y señalando los documentos donde
difieren demasiado.

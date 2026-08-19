# Guía del panel de administración

Para quien administra el asistente día a día. Todo lo de acá se hace desde la interfaz:
la tuerca **Administración** del panel lateral, visible solo para administradores. El
permiso se verifica en el servidor en cada pedido; el botón escondido es comodidad, no
seguridad.

## Quién es administrador

- El o los correos de `RAG_ADMINS` (en el entorno del servidor) son administradores
  **fijos**: existen desde la instalación y no se pueden quitar desde el panel, para que
  el sistema nunca quede sin dueño.
- El resto se da de alta y de baja en la pestaña **Administradores**, con el correo de
  Google con el que la persona inicia sesión. Queda registrado quién dio de alta a quién.

## Estado

El monitor: cuántos documentos y fragmentos se están sirviendo, hasta qué fecha llega la
normativa, el estado de los artefactos del índice en disco con su fecha, el espacio
libre, el modelo de generación configurado y el uso acumulado (usuarios, conversaciones,
mensajes). Solo lectura: si algo se ve mal acá, la acción está en Ejecuciones.

## Ejecuciones

Los pasos del pipeline de datos, en el orden en el que corren:

1. **Catálogo** — pregunta al portal SUDOCU qué actos existen y arma la lista con la
   identidad y el enlace permanente de cada uno. No descarga documentos.
2. **Descarga de documentos** — baja los PDF del catálogo que faltan en disco. Lo ya
   descargado no se vuelve a pedir.
3. **Vectorización** — extrae el texto de lo descargado que aún no está en el índice,
   lo parte por artículo y calcula los vectores. Es el paso pesado.
4. **Indexación** — reconstruye los artefactos de búsqueda y recarga el índice **sin
   cortar el servicio**.

**Actualización completa** encadena los cuatro; es lo que corre la rutina programada.

### Verificar la instalación

**0 · Verificación de la instalación** comprueba que cada paso pueda correr en esta
máquina antes de que alguien lo necesite: las dependencias que importa cada script, los
directorios donde el pipeline escribe, la salida al portal y al modelo de lenguaje, y el
espacio libre. No modifica nada ni gasta dinero.

Conviene correrla después de instalar, y cada vez que cambia algo del entorno. La razón
es concreta: el sistema se desarrolla en una máquina y se despliega en otra, y las dos
difieren en cosas que ningún paso declara. En la primera instalación de la UNLu faltaban
tres dependencias que solo usa el extractor, y eso apareció recién al vectorizar, como 96
documentos fallando con `ERROR_EXTRACTOR` y nada más. La verificación lo dice en una
línea, junto con todo lo demás que falte.

Las dependencias no están anotadas en una lista: se leen de los propios archivos de cada
paso, así que no envejecen cuando el código cambia.

### Programarla

Arriba de los botones hay una tarjeta para que la actualización completa corra sola:
diaria, semanal por día, o mensual por número de día, siempre a una hora en punto. Si el
día elegido no existe en algún mes ---el 31 en febrero--- corre el último día de ese mes,
porque saltearlo sería un silencio que nadie nota hasta que la normativa está vieja.

Configurarla no la ejecuta: al guardar, la última ocurrencia ya pasada se da por hecha.
Activarla a las cinco de la tarde con la hora puesta al mediodía programa la de mañana, no
dispara una en ese momento.

La corrida programada pasa por el mismo registro que el botón, con su log y su regla de
que no puede haber dos operaciones a la vez: si a la hora programada hay algo corriendo,
se saltea y queda anotado. Y si el servicio estuvo caído en ese momento, al volver corre
igual, siempre que no hayan pasado más de doce horas.

Conviene programarla en lugar de usar cron. Una corrida lanzada por cron no pasa por el
registro, así que no aparece en el panel y ---más importante--- puede arrancar justo
cuando alguien apretó Ejecutar, con dos procesos escribiendo el mismo catálogo.

Reglas de la casa:

- **Corre una sola ejecución a la vez.** Comparten archivos; dos juntas se pisan. Si un
  botón dice "Hay una en curso", es eso.
- Cada ejecución queda en el **registro**: cuándo, quién la lanzó, cómo salió, y su log
  completo, que se puede mirar en vivo mientras corre.
- **Cancelar** interrumpe el proceso y lo deja registrado como cancelado.
- Si el servidor se reinicia en medio de una ejecución, el proceso sobrevive y el
  registro lo re-adopta. El único dato que se pierde es el código de salida final
  (queda como "Terminó (código desconocido)"); el log dice cómo terminó de verdad.
- Cada paso es un script que también se puede correr desde una terminal —el panel no
  tiene lógica propia—, así que todo lo de acá es reproducible afuera
  (`python -m pipeline.actualizar --help`).

## Documentos

El corpus visto por sección del portal y por tipo de acto: cuántos documentos y
fragmentos hay de cada uno. Para responder "¿está cargado tal cuerpo de normativa?" sin
revisar archivos.

## Personalización

Todo lo que ata la aplicación a la institución, editable sin recompilar:

- **Identidad**: nombre, sigla, nombre del asistente, la bajada del panel, la
  denominación del cuerpo normativo ("Digesto", "Boletín Oficial"…), el texto del aviso
  al pie, el enlace a la fuente oficial y el del portal SUDOCU.
- **Sugerencias de la pantalla inicial**: los botones que ven quienes llegan por primera
  vez. Una por línea, hasta ocho. Son la primera impresión: conviene que reflejen lo que
  la gente de la institución realmente busca.
- **Glosario de la institución**: cómo dice la gente lo que los actos escriben de otra
  forma, una equivalencia por línea con la forma «como se dice → como se escribe». Se
  aplica al interpretar cada consulta: la búsqueda agrega el término administrativo entre
  paréntesis, sin reemplazar las palabras de quien pregunta, así que el término original
  sigue pesando en la señal semántica y el del corpus entra por la léxica.

  Viene con equivalencias del castellano administrativo que sirven en cualquier
  universidad. Se agregaron porque se midió que la pregunta natural no encontraba el acto:
  «¿Qué carreras tiene la UNLu?» no encontraba nada, y la lista completa estaba en una
  tabla dentro de un acto titulado OFERTA ACADÉMICA. La palabra "carrera" aparece en 8.172
  actos; "oferta académica, sede de dictado" apunta a los tres que traen la tabla.

  Conviene una lista corta y segura: cada línea suma palabras que la persona no dijo, y eso
  solo mejora si la equivalencia es certera. La fuente para ampliarla son las consultas
  reales, que quedan registradas en **Uso**.
- **Logo**: PNG, JPEG o GIF de hasta 2 MB. Se valida el contenido del archivo, no su
  nombre. El favicon de la pestaña lo sigue. "Volver al original" restaura el del build.
- **Colores**: cuatro roles de los que deriva toda la interfaz. La vista previa aplica
  en vivo sobre la aplicación entera; nada queda guardado hasta tocar **Guardar**.

El prompt del sistema toma de acá el nombre de la institución y la denominación del
cuerpo normativo: cambiarlos cambia también cómo se presenta el asistente.

## Generación

El modelo de lenguaje que redacta las respuestas:

- **Modelo** y **endpoint**: cualquier servidor compatible con la API de OpenAI sirve
  (la nube, un vLLM institucional, un Ollama). Endpoint vacío = OpenAI.
- **Temperatura**: 0 por omisión, y conviene dejarla ahí: en normativa se busca que la
  misma pregunta dé la misma respuesta.
- **La clave de API no se maneja acá**: vive en el entorno del servidor y el panel solo
  informa si está configurada. Una credencial no es un ajuste.
- **Probar el modelo** hace una llamada real de ida y vuelta con lo guardado, para
  enterarse de un problema antes de que lo descubra un usuario.

## Uso

Qué se le pregunta al sistema y si las respuestas sirven: total de respuestas, cuántas
fueron valoradas útiles y no útiles (los pulgares), las conversaciones recientes de
todos los usuarios, y cada conversación completa con sus valoraciones.

Es la única realimentación real que hay sobre la calidad del sistema. Dos cosas a tener
presentes: es una vista de administrador sobre conversaciones ajenas —usarla para
mejorar el sistema, no para otra cosa— y lo que diga la política de privacidad de la
instalación tiene que reflejar que existe (ver [privacidad.md](privacidad.md)).

## Ajustes de los usuarios comunes

Cada usuario con sesión tiene su propia tuerca al lado de su nombre, con el **tono** de
las respuestas ("explicámelo simple", "tratame de usted"…). Afecta la redacción, nunca
el contenido ni las citas. No es administrable: cada quien maneja el suyo.

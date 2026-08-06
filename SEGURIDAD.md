# Seguridad del sistema

Este documento describe qué hace el sistema con la información que maneja, qué controles
aplica y qué riesgos quedan abiertos. Está dirigido a quien tenga que decidir si lo instala
en su universidad, y a quien tenga que operarlo después.

No es una auditoría certificada ni una promesa de invulnerabilidad. Es la declaración de
quienes lo desarrollan, con las verificaciones que efectivamente se hicieron y su fecha.
Última revisión: 6 de agosto de 2026, sobre la instalación de la Universidad Nacional de
Luján.

## Qué es y qué expone

El sistema responde consultas en lenguaje natural sobre los actos administrativos que la
universidad publica en el módulo de publicación documental de SUDOCU. Todo el material que
indexa es de acceso público: no ingresa expedientes reservados, legajos ni ningún dato que
no esté ya publicado por la institución.

Hacia afuera expone una sola cosa: una aplicación web, servida por el servidor web
institucional, sobre HTTPS. El servicio de la aplicación escucha únicamente en la interfaz
local de la máquina y no es alcanzable desde la red.

Hacia adentro consume tres servicios externos: el proveedor del modelo de lenguaje que
redacta las respuestas, los servidores de Google para validar el inicio de sesión, y el
portal SUDOCU de la propia universidad para recuperar los documentos originales.

## Identidad y permisos

El sistema **no maneja contraseñas**. Quien inicia sesión lo hace con su cuenta de Google:
el navegador obtiene un testimonio firmado por Google y el servidor lo valida contra las
claves públicas de Google, comprobando firma, emisor, destinatario y vencimiento. La
contraseña de la persona nunca pasa por el sistema ni podría hacerlo.

Validado ese testimonio, el sistema emite una sesión propia, firmada con HMAC-SHA256 y con
vencimiento incluido en la firma. Cada petición la verifica con comparación en tiempo
constante. En la instalación de referencia la sesión dura cuarenta y ocho horas.

Iniciar sesión es **opcional**: sin cuenta el sistema responde igual, y lo único que cambia
es que no queda historial. Las consultas de quien no inicia sesión no se guardan en ningún
lado.

Hay dos niveles: usuario y administrador. La condición de administrador se define por
dirección de correo, en la configuración del despliegue o desde el propio panel. **Toda ruta
de administración verifica el permiso en el servidor**, no en la interfaz: esconder un botón
no es un control de acceso.

## Datos personales

De quien inicia sesión se guarda el identificador estable de su cuenta de Google, su
dirección de correo y su nombre, junto con las conversaciones que mantenga. No se registran
direcciones IP.

El panel de administración **no muestra el contenido de las consultas**. Informa agregados:
volumen por día, personas distintas, proporción de respuestas que no citaron material y
normativa más citada. La decisión tiene un motivo concreto: la condición de administrador se
otorga con facilidad, y quien administra no necesita leer lo que preguntó otra persona para
saber si el sistema funciona. Leer una conversación exige acceso a la máquina donde vive la
base, que es un acto deliberado y con rastro.

Esto importa más de lo que sugiere el hecho de que el corpus sea público: el sistema se
consulta sobre licencias, sumarios y derechos laborales, y **qué consultó una persona puede
ser sensible aunque la norma consultada sea pública**.

La retención de las conversaciones y el procedimiento de acceso a la base son decisiones que
corresponden a cada institución. El sistema no impone plazo.

## Secretos

Las credenciales ---clave del proveedor de lenguaje, secreto de firma de sesiones,
identificador del cliente OAuth--- viven en el archivo de entorno del servicio, con permisos
de lectura restringidos a su dueño. No están en el repositorio ni lo estuvieron: la historia
del proyecto se revisó para confirmarlo.

La clave del proveedor puede configurarse desde el panel, en cuyo caso queda guardada en la
base local. Es de solo escritura: se puede cambiar, nunca leer, y ninguna respuesta de la
interfaz la devuelve.

## Despliegue

El servicio corre bajo un **usuario de sistema dedicado, sin intérprete de comandos ni
permisos de administración**, cuyo único propósito es ejecutar la aplicación. La unidad de
systemd aplica un aislamiento estricto: el sistema de archivos es de solo lectura salvo tres
directorios ---índice, datos y caché del modelo---, no hay acceso a los directorios de
usuarios, se prohíbe la elevación de privilegios, y se restringen dispositivos, espacios de
nombres y familias de sockets.

La aplicación se publica detrás del servidor web institucional, que aporta TLS, cabeceras de
seguridad ---política de contenido, tipo de contenido, política de referencia, prohibición
de enmarcado--- y límite de tasa por dirección de origen sobre las rutas de consulta y de
inicio de sesión, que son las que cuestan cómputo y dinero.

Las versiones de todas las dependencias están fijadas en un archivo específico de
despliegue. Instalar con versiones libres expone a que una publicación defectuosa o
comprometida aguas arriba entre sin aviso.

## Verificaciones realizadas

**Revisión del código.** Se revisaron los puntos de entrada, el manejo de sesiones, la
autorización, la subida de archivos, la ejecución de procesos del pipeline y el acceso a la
base. Las consultas SQL están parametrizadas; la subida de logotipo valida la firma binaria
del archivo y no su extensión; los comandos del pipeline provienen de un catálogo cerrado y
se ejecutan como lista de argumentos, sin intérprete de comandos; el reenvío de documentos
acepta únicamente identificadores con formato definido y toma la dirección de origen del
índice y no del parámetro.

**Dependencias.** Auditoría de las sesenta y siete dependencias del entorno de producción
contra bases de vulnerabilidades conocidas: sin coincidencias.

**Prueba dinámica sobre el sistema en operación.** Veinte comprobaciones: acceso a cada ruta
de administración sin sesión, con firma inventada, con sesión vencida y con credencial
malformada; escritura de configuración y lanzamiento de operaciones sin credencial; intentos
de recorrido de directorios; cuerpos malformados y desmedidos; credencial de Google
inventada. Ninguna obtuvo un resultado inesperado.

**Inyección de instrucciones.** Ocho intentos de sacar al modelo de su papel: anular su
consigna, revelar sus instrucciones, inventar un acto inexistente, responder sin respaldo,
cambiar de rol, invocar una autoridad falsa, revelar la configuración y exponer datos de
otras personas. Cinco fueron rechazados en la primera prueba; tres tuvieron éxito parcial y
se corrigieron reforzando las instrucciones del sistema, con verificación posterior. El
sistema no fabricó actos inexistentes en ningún caso.

## Limitaciones y riesgos asumidos

**La custodia de la máquina es de quien la aloja.** Quien administra la infraestructura
tiene acceso por consola, independientemente de lo que se configure dentro. Esto no es una
falla: es la condición de alojarse en infraestructura institucional, y conviene que esté
dicho antes que descubierto.

**Las sesiones no se pueden revocar de a una.** El testimonio es autónomo: una sesión
filtrada es válida hasta su vencimiento, y la única forma de anularla es rotar el secreto de
firma, lo que cierra las sesiones de todos. La duración corta es la mitigación.

**La inyección de instrucciones se mitiga, no se elimina.** Las instrucciones del sistema
indican tratar el texto de la persona como consulta y nunca como orden, y esto se verificó.
Pero ningún modelo de lenguaje garantiza obediencia. El daño posible está acotado por
diseño: el modelo redacta texto y no ejecuta acciones, no accede a datos de otras personas
ni a la configuración, y toda afirmación normativa se contrasta con lo recuperado. Un riesgo
que crece si el corpus dejara de provenir exclusivamente de publicaciones institucionales.

**Las respuestas pueden equivocarse.** El sistema recupera y redacta; no dictamina. Cada
respuesta cita los actos en los que se apoya y enlaza al documento oficial, y la interfaz
advierte que la fuente que da fe es el digesto de la institución. Es una herramienta de
búsqueda, no un canal de asesoramiento.

**No hubo auditoría externa.** Todo lo anterior lo verificó el propio equipo de desarrollo.

## Guía de despliegue seguro

Para quien instale su propia instancia, el mínimo razonable:

Ejecutar el servicio con un usuario de sistema dedicado, sin intérprete de comandos ni
permisos de administración, y con la unidad de systemd que acompaña al proyecto, que ya trae
el aislamiento configurado. Nunca con una cuenta que tenga privilegios de administración.

Publicarlo detrás del servidor web institucional con TLS, incorporando las cabeceras de
seguridad y el límite de tasa que se distribuyen con el proyecto. El servicio debe escuchar
solo en la interfaz local.

Restringir a su dueño los permisos del archivo de entorno y de las bases de datos, e instalar
con las versiones fijadas del archivo de despliegue.

Definir, antes de abrir el sistema a la comunidad, el plazo de conservación de las
conversaciones y quién puede acceder a la base, y comunicarlo a quienes lo usen.

Configurar un tope de gasto en la cuenta del proveedor del modelo. El límite de tasa acota
el peor caso, pero no lo lleva a cero.

Revisar quién tiene la condición de administrador. Ese permiso habilita a cambiar la
configuración de generación y a lanzar operaciones sobre el corpus.

## Reporte de vulnerabilidades

Si encontrás un problema de seguridad, escribinos antes de publicarlo, para que podamos
corregirlo y avisar a las instalaciones afectadas. El contacto está en el archivo principal
del proyecto.

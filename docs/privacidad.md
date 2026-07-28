# Privacidad del asistente

Qué guarda y qué no guarda el sistema. Está escrito para que se pueda **verificar en el
código y en la base de datos**, no solo creer.

## Qué se guarda

Iniciar sesión es **opcional**. Sin cuenta el asistente responde igual, y no queda ningún
registro. Con cuenta, se guardan las conversaciones para poder recuperarlas después.

| Dato | ¿Se guarda? | Dónde |
|---|---|---|
| Identificador de la cuenta de Google | Sí | tabla `usuario` |
| Correo y nombre de la cuenta | Sí | tabla `usuario` |
| Conversaciones y mensajes | Sí, vinculados al usuario | tablas `conversacion`, `mensaje` |
| Fuentes citadas en cada respuesta | Sí | columna `mensaje.fuentes` |
| Valoración de las respuestas | Sí, si la persona la marca | columna `mensaje.util` |
| Contraseña | **No** | nunca llega a este sistema |
| Dirección IP | **No** | — |
| Consultas de quien no inicia sesión | **No** | no se registran |

Todo vive en un único archivo SQLite (`datos/chatdigesto.sqlite` por defecto), en la
infraestructura donde se despliegue el sistema.

## Decisión de diseño: la identidad se guarda

Se evaluó un esquema sin identidad —autenticar solo para validar el acceso y guardar el
historial en el navegador— que permitiría afirmar que el sistema no puede saber quién
consultó qué. Se descartó por una razón práctica: **historial entre dispositivos y
no-vinculabilidad son incompatibles**. Para que alguien vea desde su computadora lo que
consultó desde el teléfono, algo tiene que unir persona e historial.

Se eligió el historial. En consecuencia, **el vínculo entre usuario y consultas existe** y
quien administre la base puede verlo. Decirlo es parte del diseño: un sistema que promete
anonimato y guarda identidad es peor que uno que guarda identidad y lo dice.

Mitigaciones que sí están:

- **Sin contraseñas.** La autenticación la resuelve Google; acá solo llega un token firmado
  que se valida contra las claves públicas de Google. El sistema nunca ve una contraseña.
- **Aislamiento entre usuarios**, verificado: cada operación sobre una conversación
  comprueba que pertenezca a quien la pide. Un usuario no puede leer, renombrar, escribir
  ni borrar conversaciones de otro.
- **Uso opcional.** Quien no quiera dejar registro, consulta sin iniciar sesión.
- **Borrado a demanda.** Cada persona puede borrar sus conversaciones desde la interfaz.

## Qué queda pendiente de definir por la Universidad

Son decisiones institucionales, no técnicas:

- **Retención.** Por cuánto tiempo se conservan las conversaciones.
- **Acceso.** Quién puede acceder a la base y con qué procedimiento.
- **Aviso a la comunidad.** Que quien use el sistema sepa qué queda registrado.

Estas definiciones importan porque el asistente se consulta sobre licencias, sumarios y
derechos laborales: saber qué consultó una persona puede ser sensible aunque el contenido
del digesto sea público.

## Cifrado del historial

Si se quisiera que ni quien administra la base pueda leer las conversaciones, la salida es
cifrarlas del lado del cliente: el servidor guardaría texto cifrado con una clave que nunca
sale del navegador. Funciona, pero si la persona pierde la clave pierde el historial. No
está implementado; se puede agregar sin cambiar el resto del sistema.

## Cómo verificarlo

```bash
# Esquema completo: qué columnas existen realmente
sqlite3 datos/chatdigesto.sqlite ".schema"

# Las conversaciones de un usuario no son accesibles desde otro:
grep -n "usuario_id" backend/historial.py

# La contraseña nunca llega: solo se valida un token firmado por Google
grep -n "verify_oauth2_token" backend/sesion.py
```

## Dependencias externas

| Componente | Dónde corre | Qué sale de la institución |
|---|---|---|
| Extractor, chunking, embeddings | Clementina (sin internet) | nada |
| Índice y recuperación | servidor propio | nada |
| Autenticación | Google | el inicio de sesión de la persona |
| Generación | API externa (configurable) | la consulta y los fragmentos recuperados |

La generación está aislada en una única función (`backend/api.py::generar`). Reemplazarla
por un modelo alojado en infraestructura de la Universidad no afecta al resto del sistema.
Mientras se use una API externa, salen de la institución la consulta y los fragmentos de
normativa recuperados; el digesto es documentación pública.

## Sobre el contenido del digesto

Los actos administrativos incluyen nombres y legajos. Eso ya es público por tratarse de
normativa publicada: el sistema no agrega exposición, pero sí **facilita** encontrar y
reunir esa información. Es una decisión institucional si eso amerita restringir el acceso.

## Marco normativo

Ley 25.326 de Protección de Datos Personales. El diseño busca minimizar lo que se recoge
(no se guarda IP, ni las consultas de quien no inicia sesión) y limitar la finalidad
(el historial existe para que cada persona recupere sus consultas). Corresponde a la
Universidad definir retención y acceso, que es lo que falta arriba.

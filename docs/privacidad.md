# Privacidad del asistente

Este documento describe qué guarda y qué no guarda el sistema. Está pensado para que
cualquiera —la Dirección General de Sistemas, o cualquier integrante de la comunidad
universitaria— pueda **verificarlo en el código y en la base de datos**, no solo creerlo.

## El problema

Un asistente sobre normativa se consulta por licencias, sumarios, concursos, derechos
laborales. Si quien lo usa sospecha que la institución puede ver qué consultó, no lo usa. Y
la sospecha sería razonable: en la mayoría de los sistemas con inicio de sesión, el vínculo
entre persona e historial existe aunque nadie lo mire.

Acá ese vínculo **no existe**.

## Qué se guarda

| Dato | ¿Se guarda? | Dónde |
|---|---|---|
| Correo institucional | **No** | se usa en memoria para validar el dominio y se descarta |
| Identificador de persona | **No** | no hay tabla de usuarios |
| Dirección IP | **No** | — |
| Historial de conversaciones | **No en el servidor** | en el navegador de cada persona |
| Texto de las consultas | Sí, **sin vincular a nadie** | `datos/consultas.sqlite` |
| Fuentes recuperadas por consulta | Sí, sin vincular a nadie | idem |
| Valoración (sirvió / no sirvió) | Sí, sin vincular a nadie | idem |

## Cómo se sostiene técnicamente

**Autenticación sin identidad** (`backend/sesion.py`). El inicio de sesión valida contra la
cuenta institucional que la persona pertenece al dominio autorizado, emite un token firmado
que dice únicamente *"esta sesión pertenece a alguien del dominio"*, y descarta el correo. El
token lleva un identificador aleatorio distinto en cada inicio de sesión: dos sesiones de la
misma persona no se pueden vincular entre sí.

**Historial del lado del usuario.** Las conversaciones se guardan en el navegador. El
servidor no las recibe ni las almacena. Por eso el historial no se comparte entre
dispositivos: es el precio de que la afirmación anterior sea literalmente cierta.

**Registro de consultas desacoplado** (`backend/registro.py`). Para medir si el sistema
recupera bien hace falta saber qué se preguntó y qué se devolvió; no hace falta saber quién
preguntó. La tabla `consulta` no tiene ninguna columna que apunte a una persona: ni correo,
ni usuario, ni sesión, ni IP. Cada consulta es una fila suelta.

Además, **el momento se redondea a la hora**. Con marcas de tiempo al milisegundo se podrían
reagrupar por cercanía las consultas de una misma persona; la hora alcanza para analizar uso
y no permite esa reconstrucción.

## Cómo verificarlo

```bash
# 1. No hay correos ni identificadores de persona en el esquema
sqlite3 datos/consultas.sqlite ".schema"

# 2. Ni un solo correo en los datos
sqlite3 datos/consultas.sqlite "SELECT count(*) FROM consulta WHERE pregunta LIKE '%@%'"

# 3. En el código, el correo se usa una sola vez y no se persiste
grep -rn "correo\|email" backend/
```

## Lo que este diseño NO resuelve

Conviene decirlo con todas las letras:

- **Las consultas quedan guardadas.** Sin vínculo a la persona, pero una consulta muy
  específica podría insinuar de quién viene (por ejemplo, si menciona un legajo concreto).
  No se puede eliminar ese riesgo sin dejar de registrar, que es lo que se necesita para
  evaluar el sistema.
- **El proveedor de generación ve la consulta.** Mientras se use una API externa, la
  consulta y los fragmentos recuperados salen de la institución. Está aislado en una sola
  función (`backend/api.py::generar`) para poder reemplazarlo por un modelo propio.
- **El contenido del digesto es público, pero contiene datos personales.** Los actos
  administrativos incluyen nombres y legajos. Eso ya es público por ser normativa publicada;
  el sistema no agrega exposición, pero sí **facilita** encontrar y agregar esa información.
  Es una decisión institucional si eso amerita restringir el acceso al sistema.

## Si en el futuro se pide historial entre dispositivos

Historial sincronizado y no-vinculabilidad son incompatibles: algo tiene que unir persona e
historial. La salida honesta es cifrado del lado del cliente —el servidor guarda texto
cifrado con una clave que nunca sale del navegador—. Funciona, pero si la persona pierde la
clave pierde el historial. No se implementó porque nadie lo pidió todavía.

## Marco normativo

Ley 25.326 de Protección de Datos Personales. El diseño busca cumplir por construcción:
minimización (no se recoge lo que no se necesita), limitación de finalidad (el registro se
usa para evaluar el sistema) y ausencia de datos personales en el registro. Corresponde a la
Universidad definir la política de retención de `datos/consultas.sqlite` y quién accede.

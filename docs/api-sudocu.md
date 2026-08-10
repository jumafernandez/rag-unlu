# Observaciones técnicas sobre el API del Módulo de Publicación Documental

Notas de implementación surgidas de construir un sistema de consulta sobre el MPD de
SUDOCU, contrastadas entre **dos instalaciones independientes**: la Universidad Nacional
de Luján (21.452 actos publicados) y la Universidad Nacional de San Luis. Todo lo que
sigue se midió sobre los portales públicos, con pedidos de solo lectura.

El objetivo es doble: dejar constancia de lo que un integrador necesita saber, y reportar
un comportamiento del servidor que afecta a la propia interfaz del portal.

---

## 1. El listado corta las consultas pesadas a los 20 segundos

Es la observación más importante de este documento.

Los contenedores con miles de documentos devuelven, de manera intermitente, **HTTP 200 con
cuerpo vacío exactamente a los 20,0 segundos**. No es un error 5xx ni un timeout del
cliente: es una respuesta exitosa, vacía, a los veinte segundos justos.

Medido sobre la UNLu:

| contenedor | documentos | respuesta |
|---|---|---|
| 13 · Resoluciones Presidente HCS | 420 | 200, 158.907 bytes, **3,2 s** |
| 29 · Direcciones Administrativas | 3.656 | 200, **0 bytes**, **20,1 s** |
| 30 · Secretarías de Rectorado | 5.671 | 200, **0 bytes**, **20,1 s** |

Se reprodujo desde dos redes distintas —una VM detrás de `proxy.unlu.edu.ar` y una
máquina con salida directa— y con siete variantes de parámetros: sin `dir`, sin `orden`,
`dir=ASC`, `limit=5`, `limit=1`, `orden=fecha` y `offset=5000`. El resultado es el mismo
en todas.

**Esto explica un síntoma visible para los usuarios finales:** la propia interfaz del MPD
muestra "No se encontraron documentos" en esos contenedores. No es que estén vacíos; es
que la consulta no termina a tiempo y el front recibe una respuesta vacía indistinguible
de un resultado sin coincidencias.

El comportamiento es intermitente y depende de la carga: el mismo contenedor que falla a
una hora responde correctamente a otra.

Dos sugerencias, en orden de utilidad para quien integra:

- Que una consulta que excede el tiempo devuelva un error explícito en lugar de un 200
  vacío. Un integrador no puede distinguir "no hay documentos" de "no pude responder", y
  esa ambigüedad se propaga: en nuestro caso llevó a que un recolector diera por vacía una
  carpeta con 5.668 actos.
- Que la interfaz distinga ambos casos al informar al usuario.

## 2. El mismo dato en claves distintas según la instalación

Los 29 campos del documento son idénticos en ambas instalaciones: ninguno exclusivo de una.
Pero el **código del acto** —la sigla con la que se lo cita— viaja en claves diferentes:

| | UNLu | UNSL |
|---|---|---|
| clave | `nro.tipo` | `nro.codigo_tipo_documento` |
| valor | `"RR"` | `"DR"` |
| `nro.numero_asignado` | `" RR :  588 /  2024 "` | `" DR -  250 /  2023 "` |

Un integrador que resuelva el código por una sola de las dos claves obtiene actos sin
código en la otra universidad, y sin ningún error de por medio. `numero_asignado` está en
las dos, pero con separador distinto (`:` contra `-`), así que tampoco sirve como fuente
única sin un análisis tolerante.

`id_tipo` y `tipo` (el nombre del tipo documental) sí están en ambas y con el mismo rol,
aunque con convenciones de redacción distintas: mayúsculas sostenidas y nombres largos en
una, capitalización normal y nombres cortos en la otra.

## 3. Campos presentes pero no poblados

Existir no es estar poblado, y la diferencia importa al planificar sobre el API:

| campo | UNLu | UNSL |
|---|---|---|
| `atributos.contenido` | 45/45 | 30/30 |
| `palabras_clave` | 8/45 | 9/30 |
| `relaciones_documentos` | 0/45 | 3/30 |
| `atributos.copiado_de` | 42/45 | 0/30 |

`relaciones_documentos` es el caso que más nos interesaba: sería el lugar natural para
registrar qué norma modifica o deroga a cuál. Está prácticamente vacío en ambas
instalaciones, de modo que **no existe una fuente estructurada de vigencia normativa**, y
cualquier sistema que la afirme la está infiriendo.

`titulo` está poblado en ambas, pero con convenciones locales. En la UNLu sigue una
estructura constante —`EXP 1296/2024  INICIO SUMARIO`: expediente, punto, asunto en
mayúsculas—; en UNSL alterna oraciones descriptivas, prefijos de expediente y mayúsculas
sostenidas. Es utilizable como descripción; no como campo estructurado.

## 4. Orden del listado

El parámetro `orden` no altera el resultado: `fecha`, `ts` y `nombre_plural` devuelven la
misma secuencia. El listado viene aproximadamente de más reciente a más antiguo, pero **no
de forma estricta**: en el contenedor 12 de la UNLu aparece un acto del 23 de diciembre
después de uno del 16.

Quien construya una lectura incremental sobre el supuesto de orden por recencia debería
verificarlo en ejecución, no asumirlo.

## 5. El total declarado puede exceder los documentos distintos

Cada documento trae un campo `total` con la cantidad que el contenedor declara. Ese número
puede ser mayor que la cantidad de identificadores distintos que el listado entrega,
porque el portal repite filas entre páginas: en el contenedor 26 se midieron 1.808 filas
declaradas contra 1.807 actos distintos.

Es una diferencia chica y no impide usar el total como criterio de completitud, pero
obliga a una tolerancia.

## 6. Enlace permanente al PDF

La interfaz descarga los documentos mediante una URL `blob:`, que existe solo mientras la
pestaña está abierta. Eso da la impresión de que no hay forma de enlazar un acto de manera
estable. Detrás hay un pedido HTTP común, visible en el bundle de la aplicación:

```
GET /sudocu/api/archivos/publico/{id_archivo}?id_archivo={id_archivo}&id_documento={id_documento}
```

Es la referencia permanente que un sistema externo necesita para citar un acto y llevar al
usuario al documento oficial. Vale la pena documentarla: sin ella, un integrador termina
guardando copias en lugar de enlazar al original.

## 7. No hay endpoint público por documento

`guest/documento/{id}` existe pero responde 401. Las variantes con parámetros
(`?id=`, `?documento=`) responden 500. La única vía pública es paginar el contenedor.

Para recuperar un documento puntual eso implica recorrer hasta miles de registros. Un
endpoint público de lectura por identificador simplificaría bastante a quien integra, y no
expondría nada que el listado no exponga ya.

---

## Qué usamos

Del contrato de 29 campos, el sistema consume la identidad completa (`id`, `documento`,
`nro` con su desglose, `tipo`, `id_tipo`), el estado, las dos fechas —la del acto y la de
autorización, que son distintas—, el título, y la URL permanente del PDF. El cuerpo en
HTML (`atributos.contenido`) se usa como respaldo para los actos cuyo PDF es un escaneo
sin texto legible.

Todas las mediciones de este documento son reproducibles con
`scrapers/probar_portal.py --portal <URL>`, que hace las comprobaciones básicas de
compatibilidad en tres pedidos de solo lectura.

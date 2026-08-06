# Plan: etiquetado de las partes del acto

## Por qué

Cada fragmento del índice lleva una etiqueta de qué parte del acto es ---visto,
considerando, artículo, anexo---, y esa etiqueta viaja en la cita que el modelo lee. De
esa etiqueta depende que el sistema distinga lo que la norma **establece** de lo que la
norma **argumenta**, que es la diferencia entre citar un artículo y citar un considerando.

Hoy 47.870 fragmentos de 151.696 ---el 31% del corpus--- están sin clasificar. Con eso el
sistema recupera partes del acto de forma indistinta.

## Qué se diagnosticó

El fragmentador clasifica mirando el título de cada sección, así que solo reconoce lo que
se anuncia: `Visto`, `Considerando`, `Artículo N`, `Parte dispositiva`, `Anexo`. Todo lo
demás cae en una bolsa común.

Los archivos de origen muestran por qué eso deja afuera a los anexos:

```
# Disposicion DISPCD-CB - 208/2025
## Visto
## Considerando
## Parte dispositiva
###   Artículo 1, 2, 3
## Firmas                  <- termina el acto
## PROGRAMA OFICIAL        <- empieza el anexo, sin anunciarse
## DESCRIPTORES
## CONTENIDOS Unidad 1
## METODOLOGÍA
## Hoja de firmas
```

Las secciones del anexo están al **mismo nivel** que el visto y el considerando: son
hermanas, no hijas. Por eso no alcanza con heredar de un ancestro, ni con heredar del
último tipo conocido ---esto último, medido, etiquetaría 24.604 fragmentos de anexo como
articulado, que es peor que dejarlos sin etiqueta---.

Sobre una muestra de 3.000 actos:

| Observación | Valor |
|---|---|
| Actos con contenido real después de las firmas | 9% |
| Actos con encabezado `ANEXO` explícito | 5% |
| Secciones de anexo en la muestra | 5.173 |

La frontera es el bloque de firmas del acto, no la jerarquía.

## Regla propuesta

Dentro de cada documento, en orden:

1. Antes del primer bloque de firmas, la clasificación actual por título.
2. Después del primer bloque de firmas, toda sección que no sea a su vez de firmas es
   **anexo**.
3. Se mantiene aparte una lista corta de ruido ---nombre de la institución como encabezado
   de página, "Fecha:", numeración de páginas, restos de digitalización--- que no es ni
   norma ni anexo y no debería competir en la recuperación.

Opcional, a evaluar en la fase 2: registrar **qué artículo aprueba cada anexo**, extrayendo
del articulado la fórmula "apruébase ... que como Anexo ... forma parte de la presente".
Con eso la cita puede decir "Anexo I, aprobado por el Artículo 3", que es la forma correcta
de citarlo.

## Principio de diseño

El anexo **no es contexto de segundo orden**: es contenido aprobado por un artículo y forma
parte del acto. Cuando alguien pregunta por los contenidos de una asignatura, el texto de
un reglamento o quiénes integran un jurado, la respuesta está en el anexo y tiene la
autoridad que le da el artículo que lo aprueba. El objetivo del etiquetado es
**distinguir** las partes, no jerarquizarlas.

## Fases

**1. Reglas y validación sobre muestra.** Implementar la regla en el fragmentador y
verificarla contra un conjunto de actos revisados a mano, cubriendo los casos que se sabe
distintos: acto sin anexo, acto con anexo anunciado, acto con anexo implícito, y orden de
compra ---que tiene una estructura propia y mucho ruido de digitalización---.

**2. Reconstrucción en Clementina.** Re-fragmentar desde los canónicos, re-vectorizar con
GPU y construir el índice. Los scripts de trabajo ya existen. El índice en producción no se
toca durante este paso.

**3. Validación con métricas.** Correr la evaluación automática sobre el índice nuevo y
compararla con la del índice actual, con las mismas consultas. La reconstrucción tiene que
mejorar o empatar en recuperación. Si empeora, se sabe antes de desplegar.

**4. Despliegue.** Reemplazo del índice en la instalación con el intercambio atómico que ya
tiene el sistema, y recarga en caliente. Sin corte de servicio.

## Qué queda habilitado después

El articulado bien delimitado es la base para extraer los vínculos entre actos ---"derógase
el artículo tal de", "modifícase la resolución cual"---, que hoy se mezclan con las
menciones de los considerandos. Un vínculo que aparece en un considerando es contexto; uno
que aparece en el articulado es un efecto jurídico. Esa distinción es lo que permite armar
la cronología de una norma en lugar de una lista de coincidencias textuales.

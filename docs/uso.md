# Guía de uso

Asistente de consulta sobre el digesto normativo de la Universidad Nacional de Luján.
Este documento explica qué hace, qué no hace y cómo sacarle provecho.

## Qué es

Un buscador conversacional sobre la normativa institucional publicada en el portal de la
UNLu. Se le pregunta en lenguaje natural y responde citando los actos administrativos en
los que se apoya, con un enlace desde cada afirmación al fragmento que la respalda.

No reemplaza al digesto oficial. Sirve para **encontrar**; la fuente que da fe sigue
siendo <http://digesto.unlu.edu.ar/>.

## Qué hay adentro

| | |
|---|---|
| Documentos | 19.959 |
| Fragmentos indexados | 140.902 |
| Origen | Portal SUDOCU de la UNLu |
| Período | Desde abril de 2024 |

El corpus anterior a abril de 2024 —el digesto histórico, unos 120.000 documentos— todavía
no está incorporado. Si una consulta no encuentra algo viejo, puede ser por eso y no
porque no exista.

## Cómo preguntar

**Por tema.** "¿Qué dice la normativa sobre licencias del personal docente?"

**Por acto.** "¿Qué establece la RESHCS 893/2025?" Reconoce los códigos tal como figuran
en el documento: `RESHCS`, `DISPCD-CB`, `DISPSEACAD`, `DSECEXT` y demás.

**Por persona u órgano.** "¿Qué cargos tiene Carina Duna?", "¿Qué resolvió el Departamento
de Ciencias Básicas sobre el plan de estudios?"

**Repreguntando.** La conversación mantiene el hilo: se puede preguntar "¿y qué dice el
artículo 2?" sin repetir de qué acto se habla.

Conviene ser concreto. "Información sobre docentes" trae poco; "¿Qué disposiciones
designan docentes en la División Matemática?" trae bastante.

## La barra de contexto

Arriba del campo de escritura aparece **qué está siguiendo el sistema**: el sujeto de la
conversación y los actos que se vinieron mencionando.

No es solo informativo, es editable:

- **Tocar el sujeto** permite corregirlo. Si el sistema entendió que se habla de una
  comisión cuando en realidad se habla de una persona, se escribe el nombre correcto.
- **Tocar un acto** lo descarta; vuelve a tocarlo y se reincorpora. Los descartados quedan
  a la vista, tachados, para saber qué se dejó afuera.
- **La ✕** olvida el sujeto actual.

Lo que se corrige a mano **pesa más** que lo que dedujo el sistema, y el sistema deja de
sobrescribirlo. Es la forma más rápida de reencauzar una conversación que se desvió.

## Alcance de la búsqueda

Debajo del campo de escritura: **Preciso**, **Equilibrado** o **Exhaustivo**. Cambia
cuántos fragmentos se consultan antes de responder. Preciso es más rápido y más acotado;
Exhaustivo mira más documentos y conviene para preguntas amplias.

## Ver razonamiento

Un enlace discreto abajo a la derecha. Al activarlo, cada respuesta muestra con qué
consulta se buscó realmente, qué sujeto se estaba siguiendo y cuántos fragmentos entraron
por peso de contexto. Sirve para entender por qué una respuesta salió como salió.

## Iniciar sesión

Es opcional. Sin sesión el asistente funciona igual, pero no guarda nada. Con sesión de
Google quedan las conversaciones anteriores, que se pueden renombrar —doble clic sobre el
título— y borrar.

Cada persona ve únicamente sus propias conversaciones.

## Límites que conviene conocer

**No sabe qué está vigente.** Es la limitación más importante. La UNLu no lleva registro
de qué norma deroga o modifica a cuál: ese dato no existe en ningún sistema. El asistente
puede citar con total precisión una resolución que fue derogada después, y no tiene manera
de advertirlo. Ante cualquier uso que dependa de la vigencia, hay que verificar en la
fuente oficial.

**Cita lo que encuentra, no lo que existe.** Si un documento no está en el corpus, para el
asistente no existe. Una respuesta negativa significa "no lo encontré", no "no está".

**Puede equivocarse.** Es un modelo de lenguaje leyendo fragmentos. Las citas enlazan al
texto original justamente para poder comprobar cada afirmación en dos clics.

## Qué reportar

Si algo sale mal, lo más útil es:

1. La pregunta exacta, y las anteriores si venía de una conversación.
2. Qué se esperaba y qué respondió.
3. Si el acto correcto se conoce, su código y número.

Los casos más valiosos son los que responde con seguridad y está equivocado, más que los
que dice no saber.

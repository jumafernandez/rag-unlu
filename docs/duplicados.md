# Actos duplicados: por qué existen y cómo se tratan

## El hecho

El portal publica **el mismo acto en más de una carpeta**. No es un error de la
recolección ni del sistema: una disposición que afecta a dos departamentos aparece
publicada en las secciones de los dos. Al recolectar por carpeta, ese acto se descarga
dos veces, con dos nombres de archivo distintos y dos identificadores de documento
distintos, pero es un solo acto.

La magnitud, medida sobre el corpus de la UNLu en agosto de 2026: **2.274 actos
aparecen repetidos y ocupan 4.645 documentos**. Hay casos extremos, como
`DISPSEACAD 1270/2025`, publicada ocho veces.

## Por qué importa

Un acto duplicado no es un problema estético. La misma disposición aparece varias veces
entre las fuentes de una respuesta, ocupando lugares que deberían ir a normativa
distinta, y quien consulta cree que encontró varias normas cuando encontró una sola
repetida.

Además cuesta plata y tiempo: vectorizar diecisiete mil fragmentos que son copia de
otros es cómputo tirado en cada reconstrucción.

## Cómo se resuelve

Con `pipeline/depurar_indice.py`, que conserva **una copia por identidad de acto**
---código y número--- con una regla determinista: se prefiere la que tiene URL al
portal; después la de metadata con más confianza; después la que tiene más fragmentos; y
como último desempate, el nombre de documento más nuevo. Correrlo dos veces da lo mismo.

El mismo script repara las citas degradadas de los actos cuya identidad no se conocía al
fragmentarlos.

## La trampa en la que caímos, para no repetirla

En julio de 2026 se depuró el índice y quedó limpio. En agosto se reconstruyó el índice
completo desde los canónicos ---por un cambio en el fragmentador--- y **los duplicados
volvieron**, porque los canónicos son el corpus tal como vino del portal.

Al comparar el índice reconstruido con el que estaba sirviendo, la diferencia se leyó
primero como pérdida de documentos: el nuevo tenía 19.959 y el viejo 21.364. La lectura
correcta era otra: son conjuntos que se solapan parcialmente. Al reconstruido le faltan
los actos incorporados por las actualizaciones incrementales posteriores al volcado
original, y le sobran los duplicados que la depuración de julio había quitado.

**La conclusión operativa**: una reconstrucción total no reemplaza a la depuración ni a
las actualizaciones incrementales. La secuencia completa es reconstruir, depurar y
actualizar. Si alguna vez un índice reconstruido tiene menos documentos que el que está
sirviendo, la pregunta no es "qué se perdió" sino "qué trae cada uno que el otro no".

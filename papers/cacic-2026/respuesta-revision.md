# Respuesta punto a punto a la revisión

**Paper:** *Un RAG portable sobre SUDOCU: consulta conversacional de normativa universitaria mediante estados de diálogo con procedencia*

Agradecemos la revisión, cuyo dictamen y observaciones mejoraron sustancialmente la claridad del manuscrito. Se detallan las decisiones tomadas sobre cada punto.

---

## 1. Explicación causal de las consultas conversacionales — ADOPTADO CON PRECISIÓN

Se adoptó el encuadre propuesto, ajustado a la mecánica exacta del sistema: la detección de entidad y actos y la reescritura de la repregunta son salidas de una misma pasada del modelo sobre los últimos intercambios; los valores detectados pueblan los *slots* y reponen la información omitida en la consulta autocontenida. La Sección 3.4 ahora lo dice explícitamente, la frase "los slots no desplazan estas métricas" se reformuló ("los *slots*, cuyos valores ya están repuestos en la consulta reescrita, conservan su función de corrección…"), y la composición de cada configuración de la Tabla 1 se explicita en la prosa posterior a la tabla, incluida la cadena reescritura → anclaje que explica el resultado perfecto de la población anclada en un acto.

**Criterio:** la evaluación mide reescritura con detección en contexto (el arnés no persiste estado entre corridas), por lo que el texto describe la detección y la reescritura como coproductos, sin afirmar que la variable persistida alimente la reescritura ni que ambos mecanismos sean independientes.

## 2. Centralidad del estado visible y editable — ADOPTADO

- La Sección 3.4 abre el bloque de interfaz declarando que los *slots* se muestran desde la primera recuperación y permanecen visibles.
- Se incorporó la formulación de las tres funciones del estado (conserva referentes, expone la interpretación del sistema, admite corrección directa; la procedencia determina peso y persistencia).
- La contribución (ii) de la introducción se reescribió alrededor de esas tres funciones.
- En lugar de una captura de interfaz (costo de espacio), se añadió una nota al pie con el enlace a la instancia de demostración (URL institucional en trámite).

## 3. Visibilidad de la evaluación de procedencia — ADOPTADO

Los resultados ahora reportan la evaluación de procedencia con números exactos y localizables: la condición de *estado corregido* sostiene los valores de la configuración completa (R@5 de 0,93; 0,86 y 1,00 por población) y la remoción individual de cada *slot* no los altera. El cierre explicita el criterio interpretativo: más allá de las métricas de recuperación, la ventaja de la procedencia es habilitar la corrección humana con prioridad y persistencia garantizadas.

**Criterio:** se optó por prosa rotulada dentro de la discusión en lugar de una subsección o tabla adicional, para preservar la narrativa de la sección y el límite de páginas (una tabla con cuatro filas idénticas no aporta información).

## 4. Afirmaciones sobre fidelidad — ADOPTADO

Las conclusiones ahora dicen "correspondencia verificada entre las citas y el material recuperado", en coincidencia exacta con el protocolo aplicado (anclaje de actos citados y atribución de personas, no verificación semántica de cada afirmación). Los porcentajes se acompañan de conteos absolutos (23 de 24 atribuciones).

**Criterio adicional:** se evitó reutilizar el término "anclaje" para esta verificación, reservándolo para el mecanismo de identificadores de la recuperación (un término, una acepción).

## 5. Alcance de la portabilidad — ADOPTADO

Los resultados califican la evidencia como "portabilidad técnica del pipeline". El párrafo ya delimitaba el alcance (evaluación sobre la instalación UNLu por conocimiento del corpus y acceso a evaluadores; descubrimiento de 15 carpetas; ingesta escalonada; instancia sobre tres carpetas) y el trabajo futuro ya preveía evaluación con jueces de cada institución. Las cifras (72.816 declarados, 26.282 procesados) están verificadas contra la corrida final.

## 6. Benchmark sintético y amenazas a la validez — ADOPTADO CON FORMATO PROPIO

- La nota al pie declara que tanto la generación de consultas como la de respuestas usó `gpt-4o-mini` a temperatura 0.
- El control de calidad se describe en el protocolo (segundo pase que juzga si el fragmento responde la consulta; 34,5 % descartado).
- Las limitaciones se declaran en las conclusiones en una única oración: consultas sintéticas sin revisión manual, relevancia referida al acto de origen, evidencia UNSL limitada a portabilidad técnica.

**Criterio:** el manuscrito evita encabezados de subsubsección por decisión editorial; las amenazas a la validez se integran como limitaciones en las conclusiones, con el mismo contenido.

## 7. Consistencia de cifras, métricas y nombres — ADOPTADO

- Cifra única del corpus: 21.344 en resumen, cuerpo, figura y conclusiones.
- El resultado perfecto por identificador se atribuye explícitamente al anclaje que integran las configuraciones híbridas, con la explicación del mecanismo en la prosa posterior a la Tabla 1.
- Nombres de configuraciones simplificados ("Híbrido" / "Híbrido + estado"); el contraste es autoevidente.
- Los encabezados de la Tabla 1 mantienen "MRR" y "nDCG" abreviados por restricción de ancho; el corte @10 queda definido en el protocolo, junto a las citas de ambas métricas.

## 8. Referencias y detalles editoriales — ADOPTADO

Se eliminaron las anotaciones internas de las referencias, se uniformó el criterio de fecha de consulta en URLs, y todas las siglas (RAG, RRF, FAISS, FTS5, MPD, SUDOCU) se presentan en su primera aparición. El título se ajustó a "…mediante estados de diálogo con procedencia".

## 9. Alineación resumen–contribuciones–conclusiones — ADOPTADO

La misma secuencia (detección → *slots* → reescritura → recuperación, con la procedencia gobernando peso y persistencia) se describe en resumen, introducción, metodología, resultados y conclusiones. La expresión "con ablación de cada mecanismo" se retiró de las contribuciones. El trabajo futuro se limita a tres líneas: expansión institucional, corpus heredado y relaciones normativas estructuradas.

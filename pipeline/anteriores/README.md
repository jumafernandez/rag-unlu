# Piezas reemplazadas del pipeline (conservadas, no usar)

| Archivo | Qué era | Reemplazado por |
|---|---|---|
| `unir_metadata.py` | Reconstrucción de la correspondencia PDF↔metadatos cuando la unión era posicional y se rompió: unía por el código de acto impreso en el documento, resolviendo desacuerdos | `metadata_desde_catalogo.py` — con la recolección por API la correspondencia viene explícita en el catálogo y no hay nada que reconstruir |

Su trabajo ya está hecho: el corpus que reparó quedó incorporado, y las corridas nuevas
no lo necesitan.

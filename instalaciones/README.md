# Instalaciones

Una carpeta por universidad. El código es UNO —el de este repo—; acá vive **solo lo que
cambia** entre instalaciones: su `.env` (portal, bases, puerto), sus notas de adaptación
y, cuando haga falta, la configuración del parser (patrones de membrete propios).

La regla del proyecto: si una diferencia se puede absorber en el código común sin que
ninguna instalación se entere, se absorbe; la configuración queda para lo genuinamente
distinto.

## Cómo corre una instancia

Cada instancia usa SU carpeta como directorio de trabajo: sus datos (`datos/`, `data/`,
`indice/`) quedan acá adentro, ignorados por git. El código y el front compilado salen
del repo.

```bash
cd instalaciones/unsl
cp .env.ejemplo .env    # completar credenciales
OMP_NUM_THREADS=1 PYTHONPATH=../.. ../../.venv/bin/python -m uvicorn backend.api:app --port 8001
```

El pipeline, igual: `PYTHONPATH=../.. ../../.venv/bin/python -m pipeline.actualizar`.
O desde el panel de la propia instancia (pestaña Ejecuciones), que es lo mismo.

La instancia histórica de la UNLu corre con la raíz del repo como directorio de trabajo:
es, en la práctica, la instalación "por defecto".

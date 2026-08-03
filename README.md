# rag-unlu — ChatDigesto

Asistente conversacional sobre la **normativa de una universidad nacional** publicada en
su portal SUDOCU. Responde en lenguaje natural, cita cada afirmación con su fragmento
normativo y enlaza al PDF oficial. Nació para el Digesto de la UNLu y corre sin cambios
de código en cualquier universidad con ese portal: hay una segunda instancia funcionando
contra el Boletín Oficial de la UNSL (`instalaciones/unsl/`).

- **Recuperación híbrida**: embeddings BGE-m3 + BM25 propio (conserva identificadores
  como `RESHCS 893/2025`) fusionados con RRF, más anclaje exacto cuando la consulta
  nombra un acto.
- **Estado de diálogo con procedencia**: el sistema sigue la entidad y los actos en
  juego; lo que infiere pesa poco, lo que el usuario fija pesa más y no se sobrescribe.
  El estado es visible y editable en la interfaz.
- **Skills**: `/lista`, `/ficha` (con "quién lo menciona"), `/comparar`, `/novedades`.
- **Panel de administración**: personalización completa de la institución (nombre, logo,
  colores, textos), configuración del LLM, registro de ejecuciones del pipeline con log
  en vivo, vista de uso.
- **Un límite dicho de frente**: la Universidad no registra derogaciones, así que el
  sistema jamás afirma vigencia.

## Arranque

Guía completa en [docs/despliegue.md](docs/despliegue.md). En corto:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd frontend && npm install && npx vite build && cd ..
cp .env.ejemplo .env   # completar credenciales
OMP_NUM_THREADS=1 .venv/bin/python -m uvicorn backend.api:app --port 8000
```

¿Otra universidad? Primero la sonda (3 pedidos de solo lectura):

```bash
.venv/bin/python scrapers/probar_portal.py --portal https://<portal>/sudocu/mpd/
```

y después una carpeta en `instalaciones/` con su `.env` — el código no se toca
(ver [instalaciones/README.md](instalaciones/README.md)).

## El pipeline de datos

Cuatro pasos, ejecutables desde el panel (pestaña Ejecuciones) o por terminal — son los
mismos scripts:

| Paso | Script | Qué hace |
|---|---|---|
| 1 · Catálogo | `scrapers/recolectar_api.py` | Lista los actos contra la API del portal; carpetas autodescubiertas; completitud contra el total declarado |
| 2 · Descarga | `scrapers/bajar_pdfs.py` | Baja los PDF que falten (paralela, reanudable, SHA-256 registrado) |
| 3 · Vectorización | `pipeline/actualizar.py --sin-recolectar --sin-descargar --sin-indexar` | Extrae, chunkea por unidad normativa, embebe y fusiona lo nuevo |
| 4 · Indexación | `pipeline/actualizar.py --solo-indexar` | Reconstruye SQLite+FAISS con swap atómico y recarga la API sin cortar el servicio |

`python -m pipeline.actualizar` corre los cuatro seguidos: es la rutina que se programa
(cron semanal, ver la guía). El catálogo de actos y su ciclo de vida viven en
`pipeline/catalogo.py`; la depuración de duplicados en `pipeline/depurar_indice.py`.

## Estructura

| Carpeta | Qué hay |
|---|---|
| `backend/` | API FastAPI: recuperación, generación, sesiones, panel |
| `frontend/` | Interfaz React (build servido por la propia API) |
| `scrapers/` | Recolección por API del portal + sonda de portabilidad |
| `pipeline/` | Chunking, embeddings, índice, catálogo, orquestador |
| `extractor/` | Parser PDF→Markdown de los becarios (subtree, ver CREDITS.md) |
| `evaluacion/` | Consultas sintéticas, ablación, fidelidad de citas, cuestionario humano |
| `instalaciones/` | Una carpeta por universidad: solo configuración |
| `papers/` | CACIC 2026 |
| `docs/` | Despliegue, administración, uso, recolección, privacidad |
| `*/anteriores/` | Métodos reemplazados, conservados con su historia |

El corpus histórico previo al portal (~120.000 documentos) aún no está incorporado; el
camino para volúmenes así es Clementina XXI (`pipeline/*.slurm`).

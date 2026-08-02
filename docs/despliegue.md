# Despliegue desde cero

Cómo levantar el asistente completo en una máquina nueva: la API, la interfaz, la
autenticación y el pipeline de datos. Está escrito para que lo pueda seguir otra
universidad con su propio portal SUDOCU, no solo la UNLu.

## Requisitos

- Python 3.12 o posterior, Node 20 o posterior
- ~4 GB de disco para el índice del corpus del portal (crece con el corpus)
- Una clave de OpenAI **o** cualquier endpoint compatible (vLLM, Ollama) para la
  redacción de respuestas; sin ella el sistema funciona devolviendo fuentes sin redactar
- Un *client id* de Google OAuth para el inicio de sesión (opcional: sin sesión el
  asistente responde igual, solo que no guarda conversaciones)

## 1. Código y dependencias

```bash
git clone <repo> rag-unlu && cd rag-unlu
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && npx vite build && cd ..
```

La compilación del front queda en `frontend/dist` y **la sirve la propia API**: un solo
proceso, un solo origen, sin CORS ni segundo servidor.

## 2. Configuración (`.env` en la raíz)

```bash
# --- datos ---
RAG_BD=datos/chatdigesto.sqlite      # usuarios, conversaciones, ajustes del panel
RAG_CATALOGO=datos/catalogo.sqlite   # ciclo de vida de cada acto del corpus
RAG_CORRIDAS=datos/corridas          # logs de las ejecuciones del pipeline

# --- generación ---
OPENAI_API_KEY=...                   # la clave NUNCA se configura desde el panel
# RAG_MODELO_GEN=gpt-4o-mini         # arranque; después se cambia desde el panel
# RAG_LLM_BASE=                      # endpoint compatible propio, si lo hay

# --- sesión ---
RAG_SESION_SECRETO=<64 hex al azar>  # si falta, cada reinicio cierra todas las sesiones
GOOGLE_CLIENT_ID=...                 # el del proyecto OAuth de la institución
# RAG_DOMINIOS=unlu.edu.ar           # si se quiere restringir quién puede entrar

# --- administración ---
RAG_ADMINS=correo@institucion.edu.ar # el primer administrador; el resto, desde el panel

# --- fuente de datos ---
SUDOCU_PORTAL_URL=https://portal.unlu.edu.ar/sudocu/mpd/#!/mpd/portada
```

`RAG_ADMINS` existe para que un sistema recién instalado tenga dueño sin tocar la base;
esa persona da de alta a las demás desde el panel, y no puede ser eliminada desde ahí.

## 3. ¿Sirve mi portal? (otra universidad)

Antes de nada, la sonda de portabilidad. Tres pedidos de solo lectura:

```bash
.venv/bin/python scrapers/probar_portal.py --portal https://<portal>/sudocu/mpd/
```

Si termina en `VEREDICTO: compatible`, el resto de esta guía aplica sin cambios: las
carpetas del portal se descubren solas. Si señala fallas, eso es exactamente lo que hay
que adaptar. Es cortesía elemental avisarle al área de sistemas de esa universidad antes
de correr la recolección completa.

## 4. Primer corpus

El camino completo, del portal al índice servible:

```bash
OMP_NUM_THREADS=1 .venv/bin/python -m pipeline.actualizar
```

Encadena: catálogo → descarga → extracción → chunking → vectores → índice. La primera
vez descarga y procesa TODO el corpus, así que tarda horas y conviene una GPU para el
paso de vectores (ver `pipeline/embeddings.py --dispositivo`). Las siguientes veces solo
procesa lo nuevo: minutos.

Si el cómputo pesado corre en otra máquina (un cluster, como Clementina en nuestro
caso), los artefactos que hay que traer de vuelta son `indice/chunks.jsonl` y
`indice/densos.npy`; después `python -m pipeline.actualizar --solo-indexar` arma el
resto acá.

## 5. Levantar el servicio

```bash
OMP_NUM_THREADS=1 .venv/bin/python -m uvicorn backend.api:app --host 0.0.0.0 --port 8000
```

`OMP_NUM_THREADS=1` no es opcional en macOS: FAISS y PyTorch traen cada uno su OpenMP y
sin esa variable el proceso muere en el arranque.

La API no acepta tráfico hasta tener el índice y el modelo de embeddings cargados
(decenas de segundos). `GET /salud` cuenta la verdad: cuántos fragmentos se están
sirviendo y hasta qué fecha llega la normativa.

## 6. Después del primer arranque

Todo lo demás se hace desde el panel (entrar con el correo de `RAG_ADMINS` y tocar la
tuerca **Administración**):

- **Personalización**: nombre, sigla, logo, colores, denominación del cuerpo normativo,
  sugerencias de la pantalla inicial, aviso legal. Nada de esto pide recompilar.
- **Generación**: modelo, endpoint y temperatura del LLM, con botón de prueba.
- **Ejecuciones**: los pasos del pipeline, con registro y log en vivo.
- **Administradores**: altas y bajas.

Ver [administracion.md](administracion.md).

## 7. Actualización periódica

La rutina completa es idempotente y se programa tal cual. Con cron:

```
15 3 * * 0  cd /ruta/rag-unlu && OMP_NUM_THREADS=1 .venv/bin/python -m pipeline.actualizar >> datos/corridas/cron.log 2>&1
```

Termina recargando el índice de la API en caliente (sin cortar el servicio) si la API
está corriendo con la misma `RAG_CLAVE_INTERNA` del entorno; si no, el índice nuevo se
toma en el próximo reinicio. También se puede lanzar desde el panel: es el botón
**Actualización completa**.

## Exponerlo afuera

Para una prueba, un túnel alcanza (`ngrok http 8000`). Para producción, lo habitual: un
proxy con TLS (nginx/caddy) delante del puerto de uvicorn. La aplicación no asume ningún
dominio: el front usa rutas relativas y funciona detrás de cualquier nombre.

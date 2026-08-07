# Despliegue en licdia.unlu.edu.ar/rag-unlu

Requisitos de la VM: ~4 GB de RAM libres (modelo de embeddings + índice), Python 3.12+,
nginx ya sirviendo el dominio (verificado), ~6 GB de disco para código + índice.

## Pasos (desde la Mac de JM, con acceso SSH a la VM)

1. Copiar código e índice (sin datos crudos, sin venv):

       rsync -avz --exclude .venv --exclude data --exclude node_modules \
             --exclude .git --exclude papers --exclude instalaciones \
             /Users/devlaptop/Documents/GitHub/rag-unlu/ VM:/opt/rag-unlu/
       rsync -avz /Users/devlaptop/Documents/GitHub/rag-unlu/indice/ VM:/opt/rag-unlu/indice/
       rsync -avz /Users/devlaptop/Documents/GitHub/rag-unlu/datos/ VM:/opt/rag-unlu/datos/

2. En la VM: el front de la subruta reemplaza al de raíz:

       # (el build de frontend/dist usa rutas relativas: sirve tal cual bajo la subruta)
       python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

3. Crear `/opt/rag-unlu/.env` (copiar de `.env.ejemplo` y completar claves;
   en `RAG_ADMINS` va el correo institucional de quien vaya a administrar,
   separando con comas si es más de uno).

4. Servicio y proxy:

       sudo cp despliegue/licdia/rag-unlu.service /etc/systemd/system/
       sudo useradd -r -d /opt/rag-unlu ragunlu && sudo chown -R ragunlu: /opt/rag-unlu
       sudo systemctl enable --now rag-unlu
       # nginx: incluir despliegue/licdia/nginx-rag-unlu.conf en el server{} del sitio
       sudo nginx -t && sudo systemctl reload nginx

5. Verificar: `curl -s https://licdia.unlu.edu.ar/rag-unlu/salud` debe devolver (etapa 2)
   `"estado": "ok"` con 21.344+ documentos. El arranque tarda ~1 minuto
   (carga índice y modelo antes de aceptar tráfico).

6. JM: agregar `https://licdia.unlu.edu.ar` a los orígenes autorizados del
   cliente OAuth de Google (para el login y el panel).

Nota: el primer arranque descarga BGE-m3 (~2,3 GB) desde Hugging Face si no se
copió la caché; alternativa: rsync de `~/.cache/huggingface` desde la Mac.

## Actualizaciones posteriores

Lo de arriba es la instalación. Para publicar cambios de código se usa `desplegar-rag`,
que vive en `/usr/local/sbin`, es de root y está habilitado en sudoers sin contraseña. No
toma argumentos: los orígenes y destinos son fijos, así que el permiso sirve para desplegar
esta aplicación y para nada más.

1. Preparar el origen desde la Mac. Las tres primeras partes son código puro; `scrapers` y
   `pipeline` van filtradas a `*.py` porque en la VM esas carpetas son también donde el
   panel **escribe** el catálogo y la traza:

       D=VM:/home/administrador/deploy-pendiente
       rsync -az --delete --exclude='__pycache__' backend/ $D/backend/
       rsync -az --delete frontend/dist/ $D/frontend/dist/
       rsync -az --delete docs/ $D/docs/
       rsync -az --include='*/' --include='*.py' --exclude='*' scrapers/ $D/scrapers/
       rsync -az --include='*/' --include='*.py' --include='*.slurm' --exclude='*' \
             pipeline/ $D/pipeline/

2. **Verificar que no haya una operación del panel en curso**, inmediatamente antes de
   desplegar. `desplegar-rag` reinicia el servicio, y las operaciones corren como procesos
   hijos suyos: reiniciar las mata. Ya pasó, con una recolección de catálogo de dos horas.
   Chequear diez minutos antes no sirve; el chequeo tiene que ser parte del mismo comando:

       ssh VM 'ps -eo cmd | grep -E "recolectar_api|bajar_pdfs|pipeline\.actualizar" \
               | grep -v grep && echo "HAY ALGO CORRIENDO" && exit 1; \
               sudo -n /usr/local/sbin/desplegar-rag'

3. Verificar. El arranque tarda unos dos minutos en cargar índice y modelo:

       ssh VM 'curl -s http://127.0.0.1:8000/salud'

El índice, los datos, el entorno virtual y el `.env` no se tocan nunca desde el despliegue:
son estado, no código. Las configuraciones de systemd y de nginx tampoco, porque cambian el
entorno de ejecución y merecen una decisión humana.

Si hay que cambiar el propio `desplegar-rag`, conviene dejar copia de la versión instalada
antes de reemplazarla: es lo único del despliegue que no se puede revertir volviendo a
correr el despliegue.

## El proxy

La VM no sale a internet directamente: usa `http://proxy.unlu.edu.ar:8080`, declarado en la
unidad de systemd. Las operaciones del panel lo heredan porque corren como hijas del
servicio. Un script lanzado a mano por SSH **no** lo hereda y muere en un timeout largo;
si hay que correr algo así, hay que exportar `HTTPS_PROXY`, `HTTP_PROXY` y `NO_PROXY`
primero.

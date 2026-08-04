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
   `RAG_ADMINS=jumfernandez@gmail.com`).

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

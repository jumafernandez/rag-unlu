# RAG digesto UNLu — traer los PDFs a Clementina

La carpeta Drive **"Archivos portal"** tiene **19.959 PDFs = 8,56 GiB** (bastante más de
lo que aparentaba el vistazo público). Objetivo: dejarlos en Clementina en
`rag-unlu/data/portal`. Todo lo corrés vos.

> **Importante:** tu Mac tiene ~5,5 GiB libres, así que **no entran los 8,5 GiB** para
> stagear local. Por eso la vía recomendada es **streaming directo Drive → Clementina**
> (no toca el disco de la Mac).

```
rag-unlu/
├── SETUP_RCLONE.md          # config de rclone (ya la hiciste: remote "unludrive")
└── scripts/
    ├── stream_to_clementina.sh     # RECOMENDADO: Drive -> Clementina, sin staging
    ├── download_portal_rclone.sh   # opcional: Drive -> Mac (necesita ~8,5 GiB libres)
    ├── download_portal.sh          # opcional: Drive -> Mac vía curl+manifest.tsv
    └── upload_portal.sh            # Mac -> Clementina (si bajaste local)
```

---

## Vía recomendada — streaming Drive → Clementina

No baja nada a la Mac: rclone lee de Drive y sube por SFTP a Clementina al mismo tiempo.
Requiere la **VPN levantada**.

```bash
nc -z 172.29.3.3 22 && echo "VPN OK"     # chequeo de alcance
cd rag-unlu/scripts
./stream_to_clementina.sh
```

- Es **resumible**: cortá con Ctrl-C y volvé a correr; con `--size-only` saltea lo ya subido.
- Antes conviene chequear que Clementina tenga lugar para 8,5 GiB:
  `ssh clementina 'df -h ~ ; quota -s 2>/dev/null | tail -2'`
- Si la llave `~/.ssh/clementina` tiene passphrase: `ssh-add ~/.ssh/clementina` y después
  `CLEM_USE_AGENT=1 ./stream_to_clementina.sh` (el script igual detecta el agente solo).

Cuánto pesa / cuántos son, antes de arrancar:
```bash
rclone size unludrive: --drive-root-folder-id 1F8yOUefIDSny6ByPvtpHCt3_FjlW_hnQ
```

---

## Vías alternativas (solo si conseguís espacio local para 8,5 GiB)

**A) rclone a la Mac** y después subir:
```bash
cd rag-unlu/scripts
./download_portal_rclone.sh      # Drive -> ../data/portal (necesita ~8,5 GiB libres)
./upload_portal.sh               # ../data/portal -> Clementina (VPN arriba)
```

**B) curl + manifest.tsv** (sin instalar nada, pero con 20k archivos es poco práctico):
`./download_portal.sh` lee `manifest.tsv` (id⇥tamaño⇥título) y baja con curl. El manifiesto
no llegó a generarse completo (son ~200 páginas del Drive); quedó como último recurso.

---

## Notas

- Los PDFs son "cualquiera con el link"; el remote `unludrive` los ve porque están
  compartidos con tu cuenta.
- rclone avisa que su `client_id` compartido se retira durante 2026; funciona igual por
  ahora. Si en algún momento falla, se crea un client_id propio (https://rclone.org/drive/#making-your-own-client-id).
- La carpeta se modificó 23-jul 01:53 — si frantamasi sigue cargando, volvé a correr el
  streaming y trae solo lo nuevo.
- Alias `clementina` de tu `~/.ssh/config` (172.29.3.3, user jfernandez). Otro destino:
  `DESTDIR=otra/ruta ./stream_to_clementina.sh`.

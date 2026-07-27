# Configurar rclone para bajar "Archivos portal" (una sola vez)

Solo hace falta si vas por la vía rclone (`download_portal_rclone.sh`). Si usás la
vía simple (`download_portal.sh` con `manifest.tsv`), no necesitás nada de esto.

## 1) Instalar

```bash
brew install rclone
```

## 2) Crear el remote

```bash
rclone config
```

Respuestas:

- `n` (New remote)
- name: **unludrive**  *(si ponés otro, usalo con `RCLONE_REMOTE=... ./download_portal_rclone.sh`)*
- Storage: **drive** (Google Drive) — buscá el número que diga "Google Drive"
- client_id: *(enter, vacío)*
- client_secret: *(enter, vacío)*
- scope: **2** (`drive.readonly` — solo lectura, es lo único que necesitamos)
- root_folder_id: *(enter, vacío — la carpeta se pasa por flag en el script)*
- service_account_file: *(enter, vacío)*
- Edit advanced config: **n**
- Use auto config: **y** → se abre el navegador. **Iniciá sesión con la cuenta que
  tiene compartida la carpeta** (tu `jumfernandez@gmail.com`) y autorizá.
- Configure as a team drive: **n**
- Confirmá con **y**, y salí con **q**.

## 3) Probar

```bash
rclone lsd unludrive: --drive-root-folder-id 1F8yOUefIDSny6ByPvtpHCt3_FjlW_hnQ
rclone size unludrive: --drive-root-folder-id 1F8yOUefIDSny6ByPvtpHCt3_FjlW_hnQ
```

`size` te dice cuántos archivos y cuántos GB son. Si eso anda, corré:

```bash
cd rag-unlu/scripts
./download_portal_rclone.sh
```

> Nota: la carpeta está *compartida* con vos (no es tuya). Apuntar por
> `--drive-root-folder-id` funciona igual porque el id es accesible directamente.
> Si en algún caso `rclone` no la viera, agregá `--drive-shared-with-me` al comando.

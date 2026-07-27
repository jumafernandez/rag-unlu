# Client_id propio de Google (arregla el throttling de rclone)

El remote `unludrive` usa el **client_id compartido** de rclone, que está muy limitado
por cuota (lo usan miles) → listar/bajar 20k archivos se vuelve lentísimo o se traba con
errores `userRateLimitExceeded`. Un client_id propio sube el límite muchísimo. Es gratis
y se hace una vez (~10 min).

## 1) Crear el client_id en Google Cloud

1. Entrá a https://console.cloud.google.com/ con tu cuenta `jumfernandez@gmail.com`.
2. Creá un proyecto (arriba, "Select a project" → "New project"), p.ej. `rclone-unlu`.
3. **Habilitá la API:** APIs & Services → Library → buscá **"Google Drive API"** → Enable.
4. **Pantalla de consentimiento:** APIs & Services → OAuth consent screen →
   - User type: **External** → Create.
   - Completá nombre de app (p.ej. `rclone`) y tu email donde lo pida. Save/Continue.
   - En **Test users** agregá tu propio mail `jumfernandez@gmail.com`. Save.
   - (No hace falta publicar; con vos como test user alcanza.)
5. **Credenciales:** APIs & Services → Credentials → Create Credentials →
   **OAuth client ID** → Application type: **Desktop app** → Create.
   Te da un **Client ID** y un **Client secret**: copialos.

## 2) Cargarlos en el remote y re-autorizar

Actualizá el remote existente (no perdés nada) y volvé a hacer el login:

```bash
rclone config update unludrive client_id "TU_CLIENT_ID" client_secret "TU_CLIENT_SECRET"
rclone config reconnect unludrive:          # abre el navegador para re-autorizar
```

## 3) Probar

```bash
rclone size unludrive: --drive-root-folder-id 1F8yOUefIDSny6ByPvtpHCt3_FjlW_hnQ
```

Debería listar rápido (sin frenarse). Después volvés a correr el download o el streaming;
son resumibles, así que continúan lo que ya haya.

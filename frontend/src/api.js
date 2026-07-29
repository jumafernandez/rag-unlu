/**
 * Cliente de la API del asistente.
 *
 * La URL sale de VITE_API_URL para que el mismo build sirva en desarrollo y en el
 * despliegue, sin recompilar. Ver frontend/.env.example.
 *
 * La sesión es OPCIONAL: si hay token se manda, y el servidor guarda la conversación.
 * Sin token todo funciona igual, solo que no queda historial.
 */

// En desarrollo el front corre en Vite y la API en otro puerto. En una compilación de
// producción el backend sirve estos mismos archivos, así que la API está en el mismo
// origen y alcanza con rutas relativas: eso hace que funcione igual en localhost, detrás
// de un túnel o en un despliegue, sin recompilar. VITE_API_URL sigue mandando si está.
const BASE = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "http://localhost:8000" : "");
const CLAVE_TOKEN = "chatdigesto_sesion";

export function guardarSesion(datos) {
  localStorage.setItem(CLAVE_TOKEN, JSON.stringify(datos));
}

export function leerSesion() {
  try {
    return JSON.parse(localStorage.getItem(CLAVE_TOKEN) || "null");
  } catch {
    return null;
  }
}

export function cerrarSesion() {
  localStorage.removeItem(CLAVE_TOKEN);
}

function cabeceras() {
  const s = leerSesion();
  return {
    "Content-Type": "application/json",
    ...(s?.token ? { Authorization: `Bearer ${s.token}` } : {})
  };
}

async function pedir(ruta, opciones = {}) {
  const r = await fetch(`${BASE}${ruta}`, { headers: cabeceras(), ...opciones });
  if (r.status === 401) {
    // La sesión venció: se limpia para que el front vuelva al estado sin cuenta.
    cerrarSesion();
    throw new Error("Tu sesión venció. Volvé a iniciar sesión.");
  }
  if (!r.ok) {
    const detalle = await r.text().catch(() => "");
    throw new Error(`${r.status}. ${detalle.slice(0, 200)}`);
  }
  return r.json();
}

export async function iniciarSesionGoogle(credencial) {
  const r = await fetch(`${BASE}/sesion`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ credencial })
  });
  if (!r.ok) throw new Error("No se pudo iniciar sesión.");
  const datos = await r.json();
  guardarSesion(datos);
  return datos;
}

export const consultar = ({ pregunta, k = 8, conversacionId = null, generar = true }) =>
  pedir("/consultar", {
    method: "POST",
    body: JSON.stringify({ pregunta, k, conversacion_id: conversacionId, generar })
  });

/**
 * Consulta con respuesta en flujo (server-sent events).
 *
 * Llama a `alRecibir` con cada novedad a medida que llega: primero las fuentes, después
 * los fragmentos de texto, y al final los identificadores. La respuesta tarda lo mismo,
 * pero se empieza a leer enseguida.
 */
export async function consultarEnFlujo(
  { pregunta, k = 8, conversacionId = null, historial = [], estado = null },
  alRecibir
) {
  const r = await fetch(`${BASE}/consultar/flujo`, {
    method: "POST",
    headers: cabeceras(),
    body: JSON.stringify({ pregunta, k, conversacion_id: conversacionId, historial, estado })
  });
  if (r.status === 401) { cerrarSesion(); throw new Error("Tu sesión venció."); }
  if (!r.ok) throw new Error(`La consulta falló (${r.status}).`);

  const lector = r.body.getReader();
  const decodificador = new TextDecoder();
  let resto = "";

  while (true) {
    const { done, value } = await lector.read();
    if (done) break;
    resto += decodificador.decode(value, { stream: true });

    // Los eventos vienen separados por una línea en blanco. Se procesan los completos
    // y lo que quede a medio llegar espera al próximo trozo.
    const bloques = resto.split("\n\n");
    resto = bloques.pop() || "";

    for (const bloque of bloques) {
      let evento = "message", datos = "";
      for (const linea of bloque.split("\n")) {
        if (linea.startsWith("event:")) evento = linea.slice(6).trim();
        else if (linea.startsWith("data:")) datos += linea.slice(5).trim();
      }
      if (!datos) continue;
      try { alRecibir(evento, JSON.parse(datos)); } catch { /* bloque incompleto */ }
    }
  }
}

/** Guarda en el historial una conversación que se venía teniendo sin sesión iniciada. */
export const adoptarConversacion = (mensajes) =>
  pedir("/conversaciones", { method: "POST", body: JSON.stringify({ mensajes }) });

export const listarConversaciones = () => pedir("/conversaciones");
export const leerConversacion = (id) => pedir(`/conversaciones/${id}`);
export const renombrarConversacion = (id, titulo) =>
  pedir(`/conversaciones/${id}`, { method: "PATCH", body: JSON.stringify({ titulo }) });
export const borrarConversacion = (id) => pedir(`/conversaciones/${id}`, { method: "DELETE" });
export const valorarMensaje = (id, util) =>
  pedir(`/mensajes/${id}/valoracion`, { method: "POST", body: JSON.stringify({ util }) });
export const salud = () => pedir("/salud");

// ---------------------------------------------------------------- panel
export const adminSoy = () => pedir("/admin/soy");
export const adminEstado = () => pedir("/admin/estado");
export const adminDocumentos = () => pedir("/admin/documentos");
export const adminAdmins = () => pedir("/admin/admins");
export const adminAgregarAdmin = (correo) =>
  pedir("/admin/admins", { method: "POST", body: JSON.stringify({ correo }) });
export const adminQuitarAdmin = (correo) =>
  pedir(`/admin/admins/${encodeURIComponent(correo)}`, { method: "DELETE" });

/** El tema se lee sin sesión: la interfaz necesita los colores para pintarse antes de que
 *  nadie inicie sesión, y son públicos por naturaleza. Guardarlos sí requiere ser admin. */
export const leerTema = () => fetch(`${BASE}/admin/tema`).then((r) => r.json());
export const guardarTema = (colores) =>
  pedir("/admin/tema", { method: "PUT", body: JSON.stringify({ colores }) });

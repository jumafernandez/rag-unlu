/**
 * Cliente de la API del asistente.
 *
 * La URL sale de VITE_API_URL para que el mismo build sirva en desarrollo y en el
 * despliegue, sin recompilar. Ver frontend/.env.example.
 *
 * La sesión es OPCIONAL: si hay token se manda, y el servidor guarda la conversación.
 * Sin token todo funciona igual, solo que no queda historial.
 */

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
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
  { pregunta, k = 8, conversacionId = null },
  alRecibir
) {
  const r = await fetch(`${BASE}/consultar/flujo`, {
    method: "POST",
    headers: cabeceras(),
    body: JSON.stringify({ pregunta, k, conversacion_id: conversacionId })
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

export const listarConversaciones = () => pedir("/conversaciones");
export const leerConversacion = (id) => pedir(`/conversaciones/${id}`);
export const renombrarConversacion = (id, titulo) =>
  pedir(`/conversaciones/${id}`, { method: "PATCH", body: JSON.stringify({ titulo }) });
export const borrarConversacion = (id) => pedir(`/conversaciones/${id}`, { method: "DELETE" });
export const valorarMensaje = (id, util) =>
  pedir(`/mensajes/${id}/valoracion`, { method: "POST", body: JSON.stringify({ util }) });
export const salud = () => pedir("/salud");

/**
 * Cliente de la API del asistente.
 *
 * La URL sale de VITE_API_URL para que el mismo build sirva en desarrollo y en el
 * despliegue, sin recompilar. Ver frontend/.env.example.
 */

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function consultar({ pregunta, k = 8, anio = null, tipo = null, soloArticulos = false }) {
  const r = await fetch(`${BASE}/consultar`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pregunta,
      k,
      anio,
      tipo,
      solo_articulos: soloArticulos,
      generar: true
    })
  });

  if (!r.ok) {
    const detalle = await r.text().catch(() => "");
    throw new Error(`La consulta falló (${r.status}). ${detalle.slice(0, 200)}`);
  }
  return r.json();
}

export async function salud() {
  const r = await fetch(`${BASE}/salud`);
  if (!r.ok) throw new Error(`Sin respuesta del servidor (${r.status})`);
  return r.json();
}

export async function documento(id) {
  const r = await fetch(`${BASE}/documento/${encodeURIComponent(id)}`);
  if (!r.ok) throw new Error(`No se encontró el documento (${r.status})`);
  return r.json();
}

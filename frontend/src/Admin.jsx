/**
 * Panel de administración.
 *
 * Muestra el estado del sistema y guarda ajustes. NO ejecuta el pipeline: recolectar,
 * descargar, vectorizar e indexar son operaciones con sus propios scripts, y el panel las
 * va a lanzar a través del registro de corridas. La razón es concreta: si la lógica de una
 * operación vive en el panel, en un mes hay cosas que solo se pueden hacer desde el panel y
 * el sistema deja de poder explicarse desde una terminal.
 *
 * El permiso se verifica en el servidor. Que la entrada esté escondida para el resto es
 * comodidad, no seguridad.
 */
import { useEffect, useState } from "react";
import {
  adminEstado, adminDocumentos, adminAdmins, adminAgregarAdmin, adminQuitarAdmin,
  leerTema, guardarTema
} from "./api";

const SECCIONES = [
  ["estado", "Estado"],
  ["docs", "Documentos"],
  ["template", "Apariencia"],
  ["admins", "Administradores"],
];

const NOMBRES_COLOR = {
  "marca": "Color institucional",
  "marca-oscura": "Títulos y textos destacados",
  "fondo-marca": "Fondo del panel lateral",
  "realce": "Realces y estados activos",
};

const bytes = (n) =>
  n == null ? "—" : n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`;

const fecha = (seg) =>
  seg ? new Date(seg * 1000).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" }) : "—";

/** Aplica los colores a las variables reales de la hoja de estilos.
 *
 *  Es lo que hace que la vista previa sea la aplicación entera y no un recuadro de muestra:
 *  al pisar las variables en :root, cada componente que las usa se repinta solo. */
export function aplicarTema(colores) {
  const raiz = document.documentElement;
  Object.entries(colores || {}).forEach(([k, v]) => {
    if (v) raiz.style.setProperty(`--${k}`, v);
  });
}

/** '#2f6b2f' -> 'rgb(47, 107, 47)' */
function aRgb(hex) {
  const h = (hex || "").replace("#", "");
  if (h.length !== 6) return "";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return `rgb(${r}, ${g}, ${b})`;
}

export default function Admin({ alSalir }) {
  const [seccion, setSeccion] = useState("estado");
  const [error, setError] = useState(null);

  return (
    <div className="admin">
      <div className="admin-cabecera">
        <div>
          <h2>Administración</h2>
          <p>Estado del sistema y configuración</p>
        </div>
        <button className="sidebar-action admin-volver" onClick={alSalir}>
          Volver al asistente
        </button>
      </div>

      <nav className="admin-tabs">
        {SECCIONES.map(([id, etiqueta]) => (
          <button key={id}
                  className={`admin-tab ${seccion === id ? "activa" : ""}`}
                  onClick={() => { setSeccion(id); setError(null); }}>
            {etiqueta}
          </button>
        ))}
      </nav>

      {error && <div className="admin-error">{error}</div>}

      {seccion === "estado" && <Estado alFallar={setError} />}
      {seccion === "docs" && <Documentos alFallar={setError} />}
      {seccion === "template" && <Apariencia alFallar={setError} />}
      {seccion === "admins" && <Administradores alFallar={setError} />}
    </div>
  );
}

function Estado({ alFallar }) {
  const [d, setD] = useState(null);
  useEffect(() => { adminEstado().then(setD).catch((e) => alFallar(e.message)); }, [alFallar]);
  if (!d) return <p className="admin-cargando">Cargando…</p>;

  const c = d.corpus || {};
  return (
    <>
      <div className="admin-tarjetas">
        <Tarjeta titulo="Documentos" valor={c.documentos?.toLocaleString("es-AR") ?? "—"}
                 pie="actos indexados" />
        <Tarjeta titulo="Fragmentos" valor={c.fragmentos?.toLocaleString("es-AR") ?? "—"}
                 pie="unidades recuperables" />
        <Tarjeta titulo="Normativa hasta" valor={c.normativa_hasta ?? "—"}
                 pie="cobertura del corpus" />
        <Tarjeta titulo="Disco libre" valor={bytes(d.disco?.libre)}
                 pie={`de ${bytes(d.disco?.total)}`} />
      </div>

      <h3 className="admin-subtitulo">Generación</h3>
      <table className="admin-tabla">
        <tbody>
          <tr><th>Modelo</th><td>{d.generacion?.modelo}</td></tr>
          <tr><th>Clave configurada</th>
              <td>{d.generacion?.clave_configurada ? "sí" : "no"}</td></tr>
          <tr><th>Almacén de búsqueda</th><td>{c.almacen ?? "—"}</td></tr>
        </tbody>
      </table>

      <h3 className="admin-subtitulo">Artefactos del índice</h3>
      <p className="admin-nota">
        La fecha de cada archivo permite ver si el índice que se está sirviendo corresponde a
        la última reconstrucción o quedó atrás.
      </p>
      <table className="admin-tabla">
        <thead><tr><th>Archivo</th><th>Tamaño</th><th>Modificado</th></tr></thead>
        <tbody>
          {Object.entries(d.artefactos || {}).map(([nombre, a]) => (
            <tr key={nombre}>
              <td>{nombre}</td><td>{bytes(a.bytes)}</td><td>{fecha(a.modificado)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="admin-subtitulo">Uso</h3>
      <table className="admin-tabla">
        <tbody>
          <tr><th>Usuarios</th><td>{d.uso?.usuarios ?? "—"}</td></tr>
          <tr><th>Conversaciones</th><td>{d.uso?.conversaciones ?? "—"}</td></tr>
          <tr><th>Mensajes</th><td>{d.uso?.mensajes ?? "—"}</td></tr>
        </tbody>
      </table>
    </>
  );
}

function Tarjeta({ titulo, valor, pie }) {
  return (
    <div className="admin-tarjeta">
      <div className="admin-tarjeta-titulo">{titulo}</div>
      <div className="admin-tarjeta-valor">{valor}</div>
      <div className="admin-tarjeta-pie">{pie}</div>
    </div>
  );
}

function Documentos({ alFallar }) {
  const [secciones, setSecciones] = useState(null);
  useEffect(() => {
    adminDocumentos().then((r) => setSecciones(r.secciones)).catch((e) => alFallar(e.message));
  }, [alFallar]);
  if (!secciones) return <p className="admin-cargando">Cargando…</p>;

  const totalDocs = secciones.reduce((a, s) => a + s.documentos, 0);
  const maximo = Math.max(...secciones.map((s) => s.documentos), 1);
  return (
    <>
      <p className="admin-nota">
        Lo que está efectivamente indexado, que es lo que el asistente puede responder — no
        lo que alguna vez se recolectó. {totalDocs.toLocaleString("es-AR")} documentos.
      </p>
      <table className="admin-tabla">
        <thead><tr><th>Sección</th><th>Documentos</th><th>Fragmentos</th><th></th></tr></thead>
        <tbody>
          {secciones.map((s) => (
            <tr key={s.seccion}>
              <td>{s.seccion.replaceAll("_", " ")}</td>
              <td>{s.documentos.toLocaleString("es-AR")}</td>
              <td>{s.fragmentos.toLocaleString("es-AR")}</td>
              <td className="admin-barra-celda">
                <span className="admin-barra"
                      style={{ width: `${(s.documentos / maximo) * 100}%` }} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Apariencia({ alFallar }) {
  const [tema, setTema] = useState(null);
  const [omision, setOmision] = useState(null);
  const [guardado, setGuardado] = useState(null);
  const [guardando, setGuardando] = useState(false);

  useEffect(() => {
    leerTema().then((r) => { setTema(r.tema); setOmision(r.por_omision); setGuardado(r.tema); })
              .catch((e) => alFallar(e.message));
  }, [alFallar]);

  // La vista previa se aplica en el momento a las variables reales, así el cambio se ve en
  // toda la aplicación y no en un recuadro. Al salir sin guardar se restaura lo guardado.
  useEffect(() => { if (tema) aplicarTema(tema); }, [tema]);
  useEffect(() => () => { if (guardado) aplicarTema(guardado); }, [guardado]);

  if (!tema) return <p className="admin-cargando">Cargando…</p>;

  const cambiar = (clave, valor) => setTema((t) => ({ ...t, [clave]: valor }));
  const sinGuardar = JSON.stringify(tema) !== JSON.stringify(guardado);

  return (
    <>
      <p className="admin-nota">
        Cuatro colores definen la identidad visual: el resto de la interfaz deriva de ellos.
        Los cambios se ven al instante en toda la aplicación; recién se conservan al guardar.
      </p>

      <div className="admin-colores">
        {Object.keys(omision).map((clave) => (
          <div className="admin-color" key={clave}>
            <label htmlFor={`c-${clave}`}>{NOMBRES_COLOR[clave] || clave}</label>
            <div className="admin-color-fila">
              <input id={`c-${clave}`} type="color" value={tema[clave]}
                     onChange={(e) => cambiar(clave, e.target.value)} />
              <span className="admin-color-muestra" style={{ background: tema[clave] }} />
              <code className="admin-color-codigo">
                {tema[clave]}
                <em>{aRgb(tema[clave])}</em>
              </code>
              {tema[clave] !== omision[clave] && (
                <button className="admin-color-reset"
                        title={`Volver a ${omision[clave]}`}
                        onClick={() => cambiar(clave, omision[clave])}>restaurar</button>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="admin-acciones">
        <button className="sidebar-action primary-side admin-guardar"
                disabled={!sinGuardar || guardando}
                onClick={async () => {
                  setGuardando(true);
                  try {
                    const r = await guardarTema(tema);
                    setGuardado(r.tema);
                  } catch (e) { alFallar(e.message); }
                  setGuardando(false);
                }}>
          {guardando ? "Guardando…" : sinGuardar ? "Guardar" : "Sin cambios"}
        </button>
        {sinGuardar && (
          <button className="sidebar-action" onClick={() => setTema(guardado)}>
            Descartar
          </button>
        )}
      </div>
    </>
  );
}

function Administradores({ alFallar }) {
  const [lista, setLista] = useState(null);
  const [correo, setCorreo] = useState("");

  const recargar = () =>
    adminAdmins().then((r) => setLista(r.admins)).catch((e) => alFallar(e.message));
  useEffect(() => { recargar(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!lista) return <p className="admin-cargando">Cargando…</p>;
  return (
    <>
      <p className="admin-nota">
        Solo se administran los administradores: el resto de las personas entra con su cuenta
        de Google y no hay nada que dar de alta. Los que vienen del entorno no se pueden
        quitar desde acá, para que el sistema no pueda quedarse sin ninguno.
      </p>

      <table className="admin-tabla">
        <thead><tr><th>Correo</th><th>Origen</th><th></th></tr></thead>
        <tbody>
          {lista.map((a) => (
            <tr key={a.correo}>
              <td>{a.correo}</td>
              <td>{a.fijo ? "entorno" : `alta por ${a.por || "—"}`}</td>
              <td>
                {!a.fijo && (
                  <button className="admin-quitar"
                          onClick={async () => {
                            try { await adminQuitarAdmin(a.correo); recargar(); }
                            catch (e) { alFallar(e.message); }
                          }}>quitar</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form className="admin-acciones"
            onSubmit={async (e) => {
              e.preventDefault();
              if (!correo.trim()) return;
              try { await adminAgregarAdmin(correo.trim()); setCorreo(""); recargar(); }
              catch (err) { alFallar(err.message); }
            }}>
        <input className="admin-entrada" type="email" placeholder="correo@ejemplo.com"
               value={correo} onChange={(e) => setCorreo(e.target.value)} />
        <button className="sidebar-action primary-side admin-guardar" type="submit">
          Agregar
        </button>
      </form>
    </>
  );
}

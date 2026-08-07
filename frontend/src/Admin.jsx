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
import { useEffect, useRef, useState } from "react";
import {
  adminEstado, adminDocumentos, adminAdmins, adminAgregarAdmin, adminQuitarAdmin,
  leerTema, guardarTema, leerInstitucion, guardarInstitucion, subirLogo, quitarLogo, URL_LOGO,
  adminCorridas, adminCorrida, adminLanzarCorrida, adminCancelarCorrida, adminRecargarIndice,
  adminGuardarProgramacion,
  adminGeneracion, adminGuardarGeneracion, adminProbarGeneracion, adminUso
} from "./api";
import { LOGO_POR_OMISION } from "./config";

const SECCIONES = [
  ["estado", "Estado"],
  ["corridas", "Ejecuciones"],
  ["docs", "Documentos"],
  ["template", "Personalización"],
  ["llm", "Generación"],
  ["uso", "Uso"],
  ["admins", "Administradores"],
];

const CAMPOS_INSTITUCION = [
  ["nombre", "Nombre de la Universidad", "text"],
  ["sigla", "Sigla", "text"],
  ["producto", "Nombre del asistente", "text"],
  ["descripcion", "Bajada del panel lateral", "text"],
  ["denominacion", "Denominación del cuerpo normativo (Digesto, Boletín Oficial…)", "text"],
  ["aviso", "Aviso al pie de cada respuesta", "text"],
  ["digesto_oficial", "Fuente oficial (enlace del aviso al pie)", "url"],
  ["portal_sudocu", "Portal SUDOCU de publicación documental", "url"],
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

  // Los NEUTROS derivan de la paleta: grises de texto, bordes y fondos salen mezclando
  // los cuatro colores configurables con grises de referencia. Sin esto quedaban fijos
  // con el matiz verdoso de la UNLu y otra universidad los heredaba (se notaba en un
  // tema azul). Las proporciones reproducen los valores históricos de la UNLu con
  // diferencias imperceptibles, así que para ella nada cambia.
  const c = colores || {};
  if (c["marca"] && c["marca-oscura"] && c["fondo-marca"] && c["realce"]) {
    const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    const mez = (a, b, p) => {
      const [ar, ag, ab] = rgb(a), [br, bg, bb] = rgb(b);
      const h = (x, y) => Math.round(x * p + y * (1 - p)).toString(16).padStart(2, "0");
      return `#${h(ar, br)}${h(ag, bg)}${h(ab, bb)}`;
    };
    const set = (n, v) => raiz.style.setProperty(`--${n}`, v);
    set("texto", mez(c["marca-oscura"], "#1c1c1c", 0.25));
    set("texto-suave", mez(c["marca-oscura"], "#777777", 0.35));
    set("texto-tenue", mez(c["marca-oscura"], "#8a8a8a", 0.28));
    set("marca-media", mez(c["marca-oscura"], "#777777", 0.55));
    set("marca-clara", mez(c["marca"], "#ffffff", 0.6));
    set("fondo", mez(c["fondo-marca"], "#fbfbfb", 0.35));
    set("borde", mez(c["realce"], c["marca"], 0.82));
    set("borde-fuerte", mez(c["realce"], c["marca"], 0.62));
  }
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
    // El scroll vive en .admin, a todo el ancho, y el contenido se centra adentro. Si el
    // ancho máximo y el scroll estuvieran en el mismo elemento, la barra quedaría metida
    // dentro de la columna de texto en vez de al borde de la ventana.
    <div className="admin">
     <div className="admin-interior">
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
      {seccion === "corridas" && <Corridas alFallar={setError} />}
      {seccion === "docs" && <Documentos alFallar={setError} />}
      {seccion === "template" && <Apariencia alFallar={setError} />}
      {seccion === "llm" && <Generacion alFallar={setError} />}
      {seccion === "uso" && <Uso alFallar={setError} />}
      {seccion === "admins" && <Administradores alFallar={setError} />}
     </div>
    </div>
  );
}

const ESTADOS_CORRIDA = {
  en_curso: "En curso",
  ok: "Terminó bien",
  error: "Falló",
  cancelada: "Cancelada",
  interrumpida: "Interrumpida",
  terminada: "Terminó (código desconocido: la API se reinició en el medio)",
};

function duracion(inicio, fin) {
  if (!inicio) return "—";
  const seg = (fin || Math.floor(Date.now() / 1000)) - inicio;
  if (seg < 90) return `${seg}s`;
  if (seg < 5400) return `${Math.round(seg / 60)} min`;
  return `${(seg / 3600).toFixed(1)} h`;
}

const DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"];

/** Configuración de la actualización automática.
 *
 * Vive acá y no en una pantalla aparte porque es lo mismo que hacen los botones de abajo,
 * solo que sin que nadie los apriete: la corrida programada aparece en el mismo registro,
 * con su log, y respeta la misma regla de que no puede haber dos a la vez.
 */
function Programacion({ inicial, texto, alGuardar, alFallar, fallo }) {
  const [cfg, setCfg] = useState(inicial);
  const [guardando, setGuardando] = useState(false);
  const [descripcion, setDescripcion] = useState(texto);
  const cambiado = JSON.stringify(cfg) !== JSON.stringify(inicial);

  const guardar = async () => {
    setGuardando(true);
    try {
      const r = await adminGuardarProgramacion(cfg);
      setDescripcion(r.descripcion);
      alGuardar(r.programacion);
    } catch (e) { alFallar(e.message); }
    setGuardando(false);
  };

  return (
    <div className="programacion">
      <div className="programacion-titulo">
        <label className="programacion-switch">
          <input type="checkbox" checked={!!cfg.activa}
                 onChange={(e) => setCfg({ ...cfg, activa: e.target.checked })} />
          <strong>Actualización automática</strong>
        </label>
        {cambiado && (
          <button className="sidebar-action primary-side admin-guardar"
                  disabled={guardando} onClick={guardar}>
            {guardando ? "Guardando…" : "Guardar"}
          </button>
        )}
      </div>

      {cfg.activa && (
        <div className="programacion-campos">
          <label>Cada
            <select value={cfg.cadencia}
                    onChange={(e) => setCfg({ ...cfg, cadencia: e.target.value })}>
              <option value="diaria">día</option>
              <option value="semanal">semana</option>
              <option value="mensual">mes</option>
            </select>
          </label>

          {cfg.cadencia === "semanal" && (
            <label>el
              <select value={cfg.dia_semana}
                      onChange={(e) => setCfg({ ...cfg, dia_semana: +e.target.value })}>
                {DIAS_SEMANA.map((d, i) => <option key={d} value={i}>{d}</option>)}
              </select>
            </label>
          )}

          {cfg.cadencia === "mensual" && (
            <label>el día
              <input type="number" min="1" max="31" value={cfg.dia_mes}
                     onChange={(e) => setCfg({ ...cfg, dia_mes: +e.target.value })} />
            </label>
          )}

          <label>a las
            <select value={cfg.hora}
                    onChange={(e) => setCfg({ ...cfg, hora: +e.target.value })}>
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>
              ))}
            </select>
          </label>
        </div>
      )}

      <p className="admin-nota">{descripcion}</p>
      {cfg.activa && (
        <p className="admin-nota-chica">
          Corre unos minutos después de la hora elegida. El corrimiento es propio de esta
          instalación y siempre el mismo: evita que todas las universidades salgan a
          consultar el portal en el mismo minuto.
        </p>
      )}
      {cfg.cadencia === "mensual" && cfg.activa && cfg.dia_mes > 28 && (
        <p className="admin-nota-chica">
          Los meses que no llegan a ese día se actualizan el último día del mes.
        </p>
      )}
      {fallo && (
        <p className="programacion-fallo">
          La última actualización automática falló (#{fallo.id}). Mirá su log más abajo.
        </p>
      )}
    </div>
  );
}

const POR_PAGINA = 8;

function Corridas({ alFallar }) {
  const [datos, setDatos] = useState(null);
  const [abierta, setAbierta] = useState(null);      // id de la corrida cuyo log se mira
  const [detalle, setDetalle] = useState(null);
  const [lanzando, setLanzando] = useState(false);
  const [pagina, setPagina] = useState(0);

  const refrescar = () => adminCorridas().then(setDatos).catch((e) => alFallar(e.message));
  useEffect(() => { refrescar(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  // Si hay una ejecución en curso, su log se abre solo al entrar: es lo que se vino a
  // mirar. Una sola vez, para no reabrirlo si el usuario lo cierra a propósito.
  const autoAbierta = useRef(false);
  useEffect(() => {
    if (!autoAbierta.current && datos?.en_curso && abierta == null) {
      autoAbierta.current = true;
      abrir(datos.en_curso.id);
    }
  });   // eslint-disable-line react-hooks/exhaustive-deps

  // Mientras haya una corrida en curso, la lista y el log abierto se refrescan solos.
  useEffect(() => {
    if (!datos?.en_curso && !(detalle && detalle.estado === "en_curso")) return;
    const timer = setInterval(() => {
      refrescar();
      if (abierta != null) adminCorrida(abierta).then(setDetalle).catch(() => {});
    }, 6000);
    return () => clearInterval(timer);
  });   // sin dependencias a propósito: se rearma en cada render y usa el estado fresco

  const abrir = (id) => {
    setAbierta(id);
    setDetalle(null);
    adminCorrida(id).then(setDetalle).catch((e) => alFallar(e.message));
  };

  // Detener lo que está corriendo. La función existía en el servidor desde el principio
  // ---mata el grupo de procesos, así que se lleva también a los hijos que el script haya
  // lanzado--- pero no había por dónde pedirla: una recolección de dos horas lanzada por
  // error no se podía parar salvo reiniciando el servicio.
  //
  // Se confirma porque del otro lado puede haber horas de trabajo, y se aclara que lo ya
  // hecho queda: los pasos escriben a medida que avanzan, no al final.
  const detener = async (id) => {
    if (!window.confirm(
      "¿Detener la operación en curso?\n\n" +
      "Lo que ya se recolectó, descargó o vectorizó se conserva: al volver a ejecutar " +
      "el paso, retoma desde donde quedó.")) return;
    try {
      await adminCancelarCorrida(id);
      await refrescar();
      if (abierta === id) adminCorrida(id).then(setDetalle).catch(() => {});
    } catch (e) { alFallar(e.message); }
  };

  const lanzar = async (clave) => {
    setLanzando(true);
    try {
      const r = await adminLanzarCorrida(clave);
      await refrescar();
      abrir(r.id);
    } catch (e) { alFallar(e.message); }
    setLanzando(false);
  };

  if (!datos) return <p className="admin-cargando">Cargando…</p>;
  const hayActiva = !!datos.en_curso;

  // El registro crece con cada ejecución y la consola vive abajo de la tabla: sin paginar,
  // llegar al log de lo que se acaba de lanzar es scrollear todo el historial. Se muestran
  // de a POR_PAGINA, con la más reciente primero, que es la que se viene a mirar.
  const paginas = Math.max(1, Math.ceil(datos.corridas.length / POR_PAGINA));
  const actual = Math.min(pagina, paginas - 1);
  const visibles = datos.corridas.slice(actual * POR_PAGINA, (actual + 1) * POR_PAGINA);

  return (
    <>
      <Programacion inicial={datos.programacion} texto={datos.programacion_texto}
                    alGuardar={(p) => setDatos({ ...datos, programacion: p })}
                    alFallar={alFallar}
                    fallo={datos.corridas.find((c) => c.por === "programada" &&
                                                     c.estado !== "en_curso")?.estado === "error"
                           ? datos.corridas.find((c) => c.por === "programada" &&
                                                        c.estado !== "en_curso")
                           : null} />

      <div className="corrida-operaciones">
        {datos.operaciones.map((op) => (
          <div className="corrida-operacion" key={op.clave}>
            <div>
              <strong>{op.titulo}</strong>
              <p>{op.descripcion}</p>
            </div>
            {/* El paso que se está ejecutando muestra su propio estado y cómo cortarlo.
                Los demás quedan deshabilitados y nada más: antes los siete botones
                cambiaban a "Hay una en curso" a la vez, y no se distinguía el que uno
                acababa de apretar de los que estaban bloqueados por él. */}
            {datos.en_curso?.operacion === op.clave ? (
              <div className="corrida-operacion-activa">
                <span className="corrida-estado en_curso">En curso</span>
                <button className="sidebar-action admin-guardar corrida-detener"
                        onClick={() => detener(datos.en_curso.id)}>Detener</button>
              </div>
            ) : (
              <button className="sidebar-action primary-side admin-guardar"
                      disabled={hayActiva || lanzando}
                      title={hayActiva ? "Esperá a que termine la que está en curso" : ""}
                      onClick={() => lanzar(op.clave)}>
                Ejecutar
              </button>
            )}
          </div>
        ))}
      </div>

      <h3 className="admin-subtitulo">Registro</h3>
      {datos.corridas.length === 0 && (
        <p className="admin-nota">Todavía no se ejecutó ningún paso.</p>
      )}
      {datos.corridas.length > 0 && (
        <div className="admin-tabla-envoltura">
          <table className="admin-tabla">
            <thead>
              <tr><th>#</th><th>Paso</th><th>Estado</th><th>Inicio</th>
                  <th>Duración</th><th>Ejecutado por</th><th></th></tr>
            </thead>
            <tbody>
              {visibles.map((c) => (
                <tr key={c.id} className={abierta === c.id ? "fila-activa" : ""}>
                  <td>{c.id}</td>
                  <td>{c.operacion}</td>
                  <td><span className={`corrida-estado ${c.estado}`}>
                    {ESTADOS_CORRIDA[c.estado] || c.estado}</span></td>
                  <td>{fecha(c.inicio)}</td>
                  <td>{duracion(c.inicio, c.fin)}</td>
                  <td>{c.por}</td>
                  <td className="corrida-acciones">
                    <button className="history-accion" onClick={() => abrir(c.id)}>
                      ver log</button>
                    {c.estado === "en_curso" && (
                      <button className="history-accion corrida-detener"
                              onClick={() => detener(c.id)}>detener</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {paginas > 1 && (
        <div className="admin-paginador">
          <button className="history-accion" disabled={actual === 0}
                  onClick={() => setPagina(actual - 1)}>anterior</button>
          <span>página {actual + 1} de {paginas} · {datos.corridas.length} ejecuciones</span>
          <button className="history-accion" disabled={actual >= paginas - 1}
                  onClick={() => setPagina(actual + 1)}>siguiente</button>
        </div>
      )}

      {abierta != null && (
        <div className="corrida-log">
          <div className="corrida-log-cabecera">
            <strong>Log de la ejecución #{abierta}</strong>
            <span>
              {detalle?.estado === "en_curso" && (
                <button className="history-accion"
                        onClick={() => adminCancelarCorrida(abierta)
                          .then(refrescar).catch((e) => alFallar(e.message))}>
                  Cancelar ejecución
                </button>
              )}
              <button className="history-accion" onClick={() => setAbierta(null)}>cerrar</button>
            </span>
          </div>
          <pre>{detalle ? (detalle.log_cola || []).join("\n") || "(log vacío)" : "Cargando…"}</pre>
        </div>
      )}
    </>
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
  const [d, setD] = useState(null);
  const [vista, setVista] = useState("secciones");
  useEffect(() => {
    adminDocumentos().then(setD).catch((e) => alFallar(e.message));
  }, [alFallar]);
  if (!d) return <p className="admin-cargando">Cargando…</p>;

  const filas = vista === "secciones"
    ? d.secciones.map((s) => ({ clave: s.seccion, etiqueta: s.seccion.replaceAll("_", " "),
                                docs: s.documentos, frags: s.fragmentos }))
    : d.tipos.map((s) => ({ clave: s.tipo, etiqueta: s.tipo,
                            docs: s.documentos, frags: s.fragmentos }));
  const maximo = Math.max(...filas.map((f) => f.docs), 1);

  return (
    <>
      <div className="admin-tarjetas">
        <Tarjeta titulo="Documentos indexados" valor={d.documentos.toLocaleString("es-AR")}
                 pie="lo que el asistente puede responder" />
        {d.sin_seccion > 0 && (
          <Tarjeta titulo="Sin sección asignada"
                   valor={d.sin_seccion.toLocaleString("es-AR")}
                   pie="no se sabe de qué carpeta del portal vienen" />
        )}
      </div>

      <div className="admin-alternar">
        <button className={vista === "secciones" ? "activa" : ""}
                onClick={() => setVista("secciones")}>Por sección del portal</button>
        <button className={vista === "tipos" ? "activa" : ""}
                onClick={() => setVista("tipos")}>Por tipo de acto</button>
      </div>

      <table className="admin-tabla">
        <thead>
          <tr>
            <th>{vista === "secciones" ? "Sección" : "Tipo de acto"}</th>
            <th>Documentos</th><th>Fragmentos</th><th></th>
          </tr>
        </thead>
        <tbody>
          {filas.map((f) => (
            <tr key={f.clave}>
              <td>{f.etiqueta}</td>
              <td>{f.docs.toLocaleString("es-AR")}</td>
              <td>{f.frags.toLocaleString("es-AR")}</td>
              <td className="admin-barra-celda">
                <span className="admin-barra" style={{ width: `${(f.docs / maximo) * 100}%` }} />
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

  const [inst, setInst] = useState(null);
  const [instGuardada, setInstGuardada] = useState(null);
  const [hayLogo, setHayLogo] = useState(false);
  const [versionLogo, setVersionLogo] = useState(0);

  useEffect(() => {
    leerTema().then((r) => { setTema(r.tema); setOmision(r.por_omision); setGuardado(r.tema); })
              .catch((e) => alFallar(e.message));
    leerInstitucion().then((r) => {
      setInst(r.institucion); setInstGuardada(r.institucion); setHayLogo(!!r.institucion.logo);
    }).catch((e) => alFallar(e.message));
  }, [alFallar]);

  // La vista previa se aplica en el momento a las variables reales, así el cambio se ve en
  // toda la aplicación y no en un recuadro. Al salir sin guardar se restaura lo guardado.
  useEffect(() => { if (tema) aplicarTema(tema); }, [tema]);
  useEffect(() => () => { if (guardado) aplicarTema(guardado); }, [guardado]);

  if (!tema || !inst) return <p className="admin-cargando">Cargando…</p>;

  const cambiar = (clave, valor) => setTema((t) => ({ ...t, [clave]: valor }));
  const cambiarInst = (clave, valor) => setInst((i) => ({ ...i, [clave]: valor }));
  const temaCambio = JSON.stringify(tema) !== JSON.stringify(guardado);
  const instCambio = JSON.stringify(inst) !== JSON.stringify(instGuardada);
  const sinGuardar = temaCambio || instCambio;

  const guardarTodo = async () => {
    setGuardando(true);
    try {
      if (instCambio) {
        const r = await guardarInstitucion(inst);
        setInst(r.institucion); setInstGuardada(r.institucion);
      }
      if (temaCambio) {
        const r = await guardarTema(tema);
        setGuardado(r.tema);
      }
    } catch (e) { alFallar(e.message); }
    setGuardando(false);
  };

  return (
    <>
      <h3 className="admin-subtitulo">Identidad</h3>
      <div className="admin-campos">
        {CAMPOS_INSTITUCION.map(([clave, etiqueta, tipo]) => (
          <div className="admin-campo" key={clave}>
            <label htmlFor={`i-${clave}`}>{etiqueta}</label>
            <input id={`i-${clave}`} type={tipo} className="admin-entrada"
                   value={inst[clave] || ""}
                   onChange={(e) => cambiarInst(clave, e.target.value)} />
          </div>
        ))}
      </div>

      <p className="admin-nota admin-nota-chica">
        Si el aviso contiene «fuentes oficiales», esas palabras enlazan a la fuente
        oficial; si no, el enlace se agrega al final.
      </p>

      <h3 className="admin-subtitulo">Sugerencias de la pantalla inicial</h3>
      <p className="admin-nota">
        Los botones que se ofrecen antes de la primera consulta. Una por línea, hasta 8.
      </p>
      <textarea className="admin-entrada admin-sugerencias" rows={6}
                value={(inst.sugerencias || []).join("\n")}
                onChange={(e) => cambiarInst("sugerencias", e.target.value.split("\n"))} />

      <h3 className="admin-subtitulo">Logo</h3>
      <p className="admin-nota">
        PNG, JPEG o GIF, hasta 2 MB. Se valida la firma del archivo y no su extensión. Sin
        logo propio se usa el que viene con la aplicación.
      </p>
      <div className="admin-logo">
        <img src={hayLogo ? `${URL_LOGO}?v=${versionLogo}` : LOGO_POR_OMISION}
             alt="Logo actual" className="admin-logo-muestra" />
        <div className="admin-logo-acciones">
          <label className="sidebar-action admin-guardar admin-subir">
            Elegir archivo
            <input type="file" accept="image/png,image/jpeg,image/gif" hidden
                   onChange={async (e) => {
                     const f = e.target.files?.[0];
                     if (!f) return;
                     try {
                       await subirLogo(f);
                       setHayLogo(true); setVersionLogo(Date.now());
                     } catch (err) { alFallar(err.message); }
                     e.target.value = "";
                   }} />
          </label>
          {hayLogo && (
            <button className="sidebar-action"
                    onClick={async () => {
                      try { await quitarLogo(); setHayLogo(false); setVersionLogo(Date.now()); }
                      catch (e) { alFallar(e.message); }
                    }}>Volver al original</button>
          )}
        </div>
      </div>
      <p className="admin-nota admin-nota-chica">
        El logo se guarda y se quita en el momento; no espera al botón de guardar.
      </p>

      <h3 className="admin-subtitulo">Colores</h3>
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
                disabled={!sinGuardar || guardando} onClick={guardarTodo}>
          {guardando ? "Guardando…" : sinGuardar ? "Guardar" : "Sin cambios"}
        </button>
        {sinGuardar && (
          <button className="sidebar-action"
                  onClick={() => { setTema(guardado); setInst(instGuardada); }}>
            Descartar
          </button>
        )}
      </div>
    </>
  );
}

function Generacion({ alFallar }) {
  const [datos, setDatos] = useState(null);
  const [valores, setValores] = useState(null);
  const [guardando, setGuardando] = useState(false);
  const [prueba, setPrueba] = useState(null);
  const [probando, setProbando] = useState(false);

  useEffect(() => {
    adminGeneracion().then((r) => { setDatos(r); setValores(r.generacion); })
                     .catch((e) => alFallar(e.message));
  }, [alFallar]);

  if (!valores) return <p className="admin-cargando">Cargando…</p>;

  const cambio = JSON.stringify(valores) !== JSON.stringify(datos.generacion);

  const guardar = async () => {
    setGuardando(true);
    try {
      const r = await adminGuardarGeneracion(valores);
      setDatos((d) => ({ ...d, generacion: r.generacion }));
      setValores(r.generacion);
      setPrueba(null);
    } catch (e) { alFallar(e.message); }
    setGuardando(false);
  };

  const probar = async () => {
    setProbando(true);
    setPrueba(null);
    try { setPrueba(await adminProbarGeneracion()); }
    catch (e) { alFallar(e.message); }
    setProbando(false);
  };

  return (
    <>
      <p className="admin-nota admin-nota-ancha">
        LLM para la generación: Acepta cualquier endpoint compatible con la API de
        OpenAI (OpenAI, vLLM, Ollama, etc).
      </p>

      <div className="admin-campos">
        <div className="admin-campo">
          <label htmlFor="g-modelo">Modelo</label>
          <input id="g-modelo" type="text" className="admin-entrada"
                 value={valores.modelo}
                 onChange={(e) => setValores((v) => ({ ...v, modelo: e.target.value }))} />
        </div>
        <div className="admin-campo">
          <label htmlFor="g-base">Endpoint (vacío = OpenAI)</label>
          <input id="g-base" type="url" className="admin-entrada"
                 placeholder="https://servidor-propio/v1"
                 value={valores.base_url || ""}
                 onChange={(e) => setValores((v) => ({ ...v, base_url: e.target.value }))} />
        </div>
        <div className="admin-campo">
          <label htmlFor="g-proxy"
                 title="Si la institución sale a Internet por un proxy, la dirección va acá; vacío = conexión directa">
            Proxy de salida (opcional)</label>
          <input id="g-proxy" type="url" className="admin-entrada"
                 placeholder="http://proxy.universidad.edu.ar:8080"
                 value={valores.proxy || ""}
                 onChange={(e) => setValores((v) => ({ ...v, proxy: e.target.value }))} />
        </div>
        <div className="admin-campo">
          <label htmlFor="g-temp"
                 title="0 = respuestas reproducibles, recomendado en normativa">Temperatura</label>
          <input id="g-temp" type="number" min="0" max="2" step="0.1" className="admin-entrada"
                 value={valores.temperatura}
                 onChange={(e) => setValores((v) => ({ ...v, temperatura: e.target.value }))} />
        </div>
        <div className="admin-campo">
          <label htmlFor="g-clave">Clave de API</label>
          <input id="g-clave" type="password" className="admin-entrada"
                 autoComplete="new-password"
                 placeholder={datos.clave_configurada ? "••••••••  (configurada)" : "sin configurar"}
                 value={valores.clave || ""}
                 onChange={(e) => setValores((v) => ({ ...v, clave: e.target.value }))} />
        </div>
      </div>

      <div className="admin-acciones">
        <button className="sidebar-action primary-side admin-guardar"
                disabled={!cambio || guardando} onClick={guardar}>
          {guardando ? "Guardando…" : cambio ? "Guardar" : "Sin cambios"}
        </button>
        <button className="sidebar-action" disabled={probando || cambio} onClick={probar}
                title={cambio ? "Guardá antes de probar: la prueba usa lo guardado" : undefined}>
          {probando ? "Probando…" : "Probar el modelo"}
        </button>
        {cambio && (
          <button className="sidebar-action" onClick={() => setValores(datos.generacion)}>
            Descartar
          </button>
        )}
      </div>

      {prueba && (
        <p className={`admin-prueba ${prueba.ok ? "ok" : "error"}`}>
          {prueba.ok
            ? `Respondió "${prueba.respuesta}" en ${prueba.segundos}s (${prueba.modelo}).`
            : `Falló en ${prueba.segundos}s: ${prueba.error}`}
        </p>
      )}
    </>
  );
}

function Uso({ alFallar }) {
  const [datos, setDatos] = useState(null);

  useEffect(() => { adminUso().then(setDatos).catch((e) => alFallar(e.message)); }, [alFallar]);
  if (!datos) return <p className="admin-cargando">Cargando…</p>;

  const r = datos.resumen;
  const m = datos.metricas;
  const pico = Math.max(1, ...m.por_dia.map((d) => d.consultas));
  const sinMaterial = m.consultas ? Math.round((m.sin_fuentes / m.consultas) * 100) : 0;

  return (
    <>
      <div className="admin-tarjetas">
        <Tarjeta titulo="Consultas" valor={m.consultas.toLocaleString("es-AR")}
                 pie={`en los últimos ${m.dias} días`} />
        <Tarjeta titulo="Personas" valor={m.usuarios.toLocaleString("es-AR")}
                 pie="con sesión iniciada" />
        <Tarjeta titulo="Sin material" valor={`${sinMaterial}%`}
                 pie="respuestas que no citaron normativa" />
        <Tarjeta titulo="Respuestas dadas" valor={r.respuestas.toLocaleString("es-AR")}
                 pie="desde el primer día" />
        <Tarjeta titulo="Valoradas útiles" valor={r.utiles.toLocaleString("es-AR")}
                 pie="pulgar arriba" />
        <Tarjeta titulo="Valoradas no útiles" valor={r.no_utiles.toLocaleString("es-AR")}
                 pie="pulgar abajo" />
      </div>

      <h3 className="admin-subtitulo">Consultas por día</h3>
      <div className="uso-serie">
        {m.por_dia.map((d) => (
          <div key={d.dia} className="uso-barra-envoltura" title={`${d.dia}: ${d.consultas}`}>
            <div className="uso-barra" style={{ height: `${(d.consultas / pico) * 100}%` }} />
            <span className="uso-barra-dia">{d.dia.slice(8)}</span>
          </div>
        ))}
      </div>

      <h3 className="admin-subtitulo">Normativa más citada</h3>
      {m.actos_citados.length === 0 ? (
        <p className="admin-nota">Todavía no hay respuestas con fuentes citadas.</p>
      ) : (
        <div className="admin-tabla-envoltura">
          <table className="admin-tabla">
            <thead><tr><th>Acto</th><th>Veces citado</th></tr></thead>
            <tbody>
              {m.actos_citados.map((a) => (
                <tr key={a.acto}><td>{a.acto}</td><td>{a.veces}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
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

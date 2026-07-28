import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./styles.css";
import Login from "./Login";
import Markdown from "./Markdown";
import {
  consultarEnFlujo, leerSesion, cerrarSesion, listarConversaciones,
  leerConversacion, renombrarConversacion, borrarConversacion, valorarMensaje, salud
} from "./api";

function PencilIcon() {
  return (
    <svg viewBox="0 0 24 24" className="icon" aria-hidden="true">
      <path d="M4 20h4l10-10-4-4L4 16v4zm12.7-13.3 1.6-1.6a1 1 0 0 1 1.4 0l1.2 1.2a1 1 0 0 1 0 1.4l-1.6 1.6-2.6-2.6z" fill="currentColor"/>
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" className="icon" aria-hidden="true">
      <path d="M10.5 4a6.5 6.5 0 1 0 4.03 11.6l4.43 4.43 1.41-1.41-4.43-4.43A6.5 6.5 0 0 0 10.5 4zm0 2a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9z" fill="currentColor"/>
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" className="icon" aria-hidden="true">
      <path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" fill="currentColor"/>
    </svg>
  );
}

// El atajo funciona con Ctrl y con ⌘ en cualquier sistema; solo cambia cómo se nombra.
// En la UNLu la mayoría usa Windows, así que mostrar "⌘K" a todos confunde.
const TECLA_ATAJO = /Mac|iPhone|iPad/i.test(
  (typeof navigator !== "undefined" && (navigator.userAgentData?.platform || navigator.platform || navigator.userAgent)) || ""
) ? "⌘K" : "Ctrl+K";

export default function App() {
  const [view, setView] = useState("chat");
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [searchChats, setSearchChats] = useState("");
  const [cargando, setCargando] = useState(false);

  const [sesion, setSesion] = useState(() => leerSesion());
  const [history, setHistory] = useState([]);
  const [convId, setConvId] = useState(null);
  const [editando, setEditando] = useState(null);   // id de la conversación en edición
  const [tituloEdit, setTituloEdit] = useState("");
  const [panelAbierto, setPanelAbierto] = useState(false);
  const [porBorrar, setPorBorrar] = useState(null);  // id con confirmación pendiente
  const [alcance, setAlcance] = useState(null);
  // Cuánta normativa traer por consulta. Se expone en lenguaje de usuario, no como un
  // número: a quien consulta le importa "rápido y preciso" o "traeme todo", no el valor
  // de k. Se recuerda entre visitas.
  const [amplitud, setAmplitud] = useState(
    () => localStorage.getItem("chatdigesto_amplitud") || "equilibrado"
  );
  useEffect(() => { localStorage.setItem("chatdigesto_amplitud", amplitud); }, [amplitud]);
  const [copiado, setCopiado] = useState(null);
  const [fuenteMarcada, setFuenteMarcada] = useState(null);   // "mensaje-fuente"
  const finDelChat = useRef(null);
  const cajaTexto = useRef(null);
  const buscador = useRef(null);
  const [anchoPanel, setAnchoPanel] = useState(
    () => Number(localStorage.getItem("chatdigesto_ancho")) || 320
  );

  // Alcance del corpus: cuántos documentos hay y hasta cuándo llega la normativa. Para
  // quien consulta un digesto eso decide si una respuesta vacía significa "no existe" o
  // "todavía no está cargado".
  useEffect(() => { salud().then(setAlcance).catch(() => setAlcance(null)); }, []);

  // Al llegar una respuesta se baja al final: si no, en conversaciones largas la
  // respuesta nueva queda fuera de la vista y parece que no pasó nada.
  useEffect(() => {
    finDelChat.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, cargando]);

  // El campo crece con el texto en vez de mostrar una sola línea con scroll interno.
  const ajustarAlto = (el) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  };
  useEffect(() => { ajustarAlto(cajaTexto.current); }, [input]);

  // El ancho del panel se recuerda entre visitas.
  useEffect(() => {
    document.documentElement.style.setProperty("--sidebar-width", `${anchoPanel}px`);
    localStorage.setItem("chatdigesto_ancho", String(anchoPanel));
  }, [anchoPanel]);

  const arrastrar = (ev) => {
    ev.preventDefault();
    const mover = (e) => setAnchoPanel(Math.min(520, Math.max(240, e.clientX)));
    const soltar = () => {
      window.removeEventListener("mousemove", mover);
      window.removeEventListener("mouseup", soltar);
      document.body.style.userSelect = "";
    };
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", mover);
    window.addEventListener("mouseup", soltar);
  };

  // Varias secciones del mismo acto llegan como fragmentos separados. Mostrarlas sueltas
  // hace parecer que hay más normativa de la que hay: se agrupan por documento,
  // conservando el índice original de cada fragmento para que las citas sigan saltando.
  const agruparPorDocumento = (fuentes = []) => {
    const grupos = new Map();
    fuentes.forEach((f, indice) => {
      const clave = f.documento || f.cita;
      if (!grupos.has(clave)) {
        grupos.set(clave, {
          clave,
          // Se muestra el código tal como se lo nombra en la UNLu ("DISPCD-T 204/2024"),
          // sin anteponer el tipo de acto: es la forma instalada y evita depender de
          // cómo esté escrito "Disposición"/"Resolución" en la metadata.
          encabezado: (f.cita || "").split("—")[0].trim()
            .replace(/^(Disposici[oó]n|Resoluci[oó]n)\s+/i, ""),
          fecha: f.date_issued,
          titulo: f.titulo,
          confianza: f.metadata_confianza,
          pdf: f.source_pdf,
          partes: []
        });
      }
      grupos.get(clave).partes.push({ ...f, indice });
    });
    return [...grupos.values()];
  };

  const irALaFuente = (indiceMensaje, indiceFuente) => {
    const clave = `${indiceMensaje}-${indiceFuente}`;
    const caja = document.getElementById(`fuentes-${indiceMensaje}`);
    if (caja && !caja.open) caja.open = true;   // desplegar si estaba cerrado
    setFuenteMarcada(clave);
    // Se espera al despliegue antes de desplazar, si no la posición está mal calculada.
    requestAnimationFrame(() => {
      document.getElementById(`fuente-${clave}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    setTimeout(() => setFuenteMarcada((v) => (v === clave ? null : v)), 2600);
  };

  const copiar = async (texto, id) => {
    try {
      await navigator.clipboard.writeText(texto);
      setCopiado(id);
      setTimeout(() => setCopiado((v) => (v === id ? null : v)), 1600);
    } catch (e) { console.error(e); }
  };

  // Atajos: ⌘K / Ctrl+K busca en las conversaciones, Escape cierra el cajón.
  useEffect(() => {
    const alTeclear = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPanelAbierto(true);
        buscador.current?.focus();
      }
      if (e.key === "Escape") { setPanelAbierto(false); setPorBorrar(null); }
    };
    window.addEventListener("keydown", alTeclear);
    return () => window.removeEventListener("keydown", alTeclear);
  }, []);

  // Si la ventana se agranda, el cajón deja de tener sentido: se cierra solo para que
  // el estado no quede pegado al volver a escritorio.
  useEffect(() => {
    const alAjustar = () => { if (window.innerWidth > 980) setPanelAbierto(false); };
    window.addEventListener("resize", alAjustar);
    return () => window.removeEventListener("resize", alAjustar);
  }, []);

  // El historial existe solo con sesión iniciada. Sin cuenta el asistente funciona
  // igual, pero no queda registro de las consultas.
  const refrescarHistorial = useCallback(async () => {
    if (!leerSesion()?.token) { setHistory([]); return; }
    try {
      const r = await listarConversaciones();
      setHistory(r.conversaciones || []);
    } catch { setHistory([]); }
  }, []);

  useEffect(() => { refrescarHistorial(); }, [refrescarHistorial, sesion]);

  const abrirConversacion = async (id) => {
    try {
      const c = await leerConversacion(id);
      setConvId(c.id);
      setView("chat");
      setPanelAbierto(false);
      setMessages(c.mensajes.map((m) => ({
        role: m.rol, content: m.texto, fuentes: m.fuentes || [],
        mensajeId: m.id, util: m.util
      })));
    } catch (e) { console.error(e); }
  };

  const empezarEdicion = (id, actual) => {
    setEditando(id);
    setTituloEdit(actual || "");
  };

  const confirmarEdicion = async () => {
    const id = editando, t = tituloEdit.trim();
    setEditando(null);
    if (!t || !id) return;
    // Se actualiza en pantalla de inmediato; si el servidor falla, se recarga la lista
    // real y el título vuelve a lo que estaba.
    setHistory((prev) => prev.map((c) => (c.id === id ? { ...c, titulo: t } : c)));
    try { await renombrarConversacion(id, t); } catch (e) { console.error(e); }
    refrescarHistorial();
  };

  const confirmarBorrado = async (id) => {
    setPorBorrar(null);
    // Se saca de la lista al instante; si el servidor falla, refrescar la devuelve.
    setHistory((prev) => prev.filter((c) => c.id !== id));
    if (convId === id) { setConvId(null); setMessages([]); }
    try { await borrarConversacion(id); } catch (e) { console.error(e); }
    refrescarHistorial();
  };

  const valorar = async (mensajeId, util, indice) => {
    if (!mensajeId) return;
    const nuevo = util;
    setMessages((prev) => prev.map((m, i) => (i === indice ? { ...m, util: nuevo } : m)));
    try { await valorarMensaje(mensajeId, nuevo); } catch (e) { console.error(e); }
  };

  const [formData, setFormData] = useState({
    tipo: "",
    tema: "",
    area: "",
    objetivo: "",
    contexto: ""
  });

  const filteredHistory = useMemo(() => {
    if (!searchChats.trim()) return history;
    const q = searchChats.toLowerCase();
    return history.filter((item) => (item.titulo || "").toLowerCase().includes(q));
  }, [history, searchChats]);

  const sendMessage = async () => {
    if (!input.trim() || cargando) return;
    const content = input.trim();
    setMessages((prev) => [...prev, { role: "user", content }]);
    setInput("");
    setCargando(true);

    // Se agrega la burbuja de respuesta vacía y se va llenando a medida que llega el
    // texto. Así la lectura empieza enseguida en vez de esperar la respuesta completa.
    let indiceRespuesta = -1;
    setMessages((prev) => {
      indiceRespuesta = prev.length;
      return [...prev, { role: "assistant", content: "", fuentes: [], enCurso: true }];
    });

    const actualizar = (cambios) =>
      setMessages((prev) =>
        prev.map((m, i) => (i === indiceRespuesta ? { ...m, ...cambios } : m))
      );

    try {
      let texto = "";
      // Se envían los últimos intercambios: sin esto cada consulta llega aislada y una
      // repregunta como "¿me resumís qué sabés de ella?" no tiene a qué referirse.
      const historial = messages
        .filter((m) => m.content && !m.error)
        .slice(-6)
        .map((m) => ({ rol: m.role === "user" ? "user" : "assistant", texto: m.content }));

      const K = { preciso: 5, equilibrado: 8, exhaustivo: 16 }[amplitud] ?? 8;

      await consultarEnFlujo({ pregunta: content, k: K, conversacionId: convId, historial }, (evento, datos) => {
        if (evento === "fuentes") {
          actualizar({ fuentes: datos.fuentes || [] });
        } else if (evento === "texto") {
          texto += datos.t;
          actualizar({ content: texto });
        } else if (evento === "aviso") {
          actualizar({ advertencia: datos.mensaje });
        } else if (evento === "fin") {
          if (datos.conversacion_id && datos.conversacion_id !== convId) {
            setConvId(datos.conversacion_id);
          }
          actualizar({
            mensajeId: datos.mensaje_id,
            segundos: datos.segundos,
            enCurso: false
          });
        }
      });

      // Sin texto generado pero con normativa recuperada: se explica en vez de dejar
      // la burbuja vacía.
      setMessages((prev) =>
        prev.map((m, i) => {
          if (i !== indiceRespuesta || m.content) return m;
          return {
            ...m,
            content: m.fuentes?.length
              ? `Encontré ${m.fuentes.length} fragmento${m.fuentes.length > 1 ? "s" : ""} de normativa relacionada. Abrí las fuentes para leer el texto de los actos.`
              : "No encontré normativa que responda esa consulta.",
            enCurso: false
          };
        })
      );
    } catch (e) {
      actualizar({ content: `No pude consultar el Digesto. ${e.message}`, error: true, enCurso: false });
    } finally {
      setCargando(false);
      refrescarHistorial();
    }
  };

  const handleSuggestion = (text) => {
    setInput(text);
  };

  const handleNewChat = () => {
    setMessages([]);
    setInput("");
    setConvId(null);
    setView("chat");
    setPanelAbierto(false);
  };

  const startDraft = () => {
    console.log("Parámetros redacción:", formData);
  };

  return (
    <div className="app-shell">
      <button
        className="boton-menu"
        onClick={() => setPanelAbierto((v) => !v)}
        aria-label={panelAbierto ? "Cerrar el panel" : "Abrir el panel"}
        aria-expanded={panelAbierto}
      >
        {panelAbierto ? "✕" : "☰"}
      </button>

      {panelAbierto && (
        <button className="velo" aria-label="Cerrar el panel"
                onClick={() => setPanelAbierto(false)} />
      )}

      <div className="tirador" onMouseDown={arrastrar}
           onDoubleClick={() => setAnchoPanel(320)}
           title="Arrastrá para ajustar. Doble clic para volver al ancho original."
           role="separator" aria-label="Ajustar ancho del panel" />

      <aside className={`sidebar ${panelAbierto ? "abierto" : ""}`}>
        <div className="sidebar-top">
          <div className="sidebar-brand">
            <img src="/logo-unlu-96.png" className="logo" alt="Logo UNLu" />
            <div>
              <h1>ChatDigesto</h1>
              <p>Consulta de normativa institucional de acceso público</p>
            </div>
          </div>

          <button className="sidebar-action primary-side" onClick={handleNewChat}>
            <PlusIcon />
            <span>Nuevo chat</span>
          </button>

          <button className="sidebar-action bloqueada" disabled
                  title="En preparación">
            <PencilIcon />
            <span>Redacción asistida</span>
            <em className="proximamente">Próximamente</em>
          </button>

          <div className="search-box">
            <SearchIcon />
            <input
              ref={buscador}
              type="text"
              placeholder={`Buscar chats  (${TECLA_ATAJO})`}
              value={searchChats}
              onChange={(e) => setSearchChats(e.target.value)}
            />
          </div>
        </div>

        <div className="sidebar-bottom">
          <div>
            <div className="sidebar-section-title">Consultas previas</div>
            <div className="history-list">
              {!sesion && (
                <p className="history-vacio">
                  Iniciá sesión para guardar tus consultas.
                </p>
              )}
              {sesion && filteredHistory.length === 0 && (
                <p className="history-vacio">Todavía no tenés consultas guardadas.</p>
              )}
              {filteredHistory.map((item) => (
                <div key={item.id} className={`history-row ${convId === item.id ? "activa" : ""}`}>
                  {editando === item.id ? (
                    <input
                      className="history-edit"
                      value={tituloEdit}
                      autoFocus
                      onChange={(e) => setTituloEdit(e.target.value)}
                      onBlur={confirmarEdicion}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") { e.preventDefault(); confirmarEdicion(); }
                        if (e.key === "Escape") setEditando(null);
                      }}
                    />
                  ) : (
                    <>
                      <button
                        className="history-item"
                        onClick={() => abrirConversacion(item.id)}
                        onDoubleClick={() => empezarEdicion(item.id, item.titulo)}
                        title={item.titulo}
                      >
                        {item.titulo}
                      </button>
                      {porBorrar === item.id ? (
                        <span className="confirmar-borrado">
                          <button className="confirmar-si" title="Confirmar"
                                  onClick={() => confirmarBorrado(item.id)}>Borrar</button>
                          <button className="confirmar-no" title="Cancelar"
                                  onClick={() => setPorBorrar(null)}>✕</button>
                        </span>
                      ) : (
                        <>
                          <button className="history-accion" title="Renombrar"
                                  onClick={() => empezarEdicion(item.id, item.titulo)}>✎</button>
                          <button className="history-accion" title="Borrar"
                                  onClick={() => setPorBorrar(item.id)}>🗑</button>
                        </>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="sesion-caja">
            {sesion ? (
              <div className="sesion-activa">
                <span className="sesion-nombre" title={sesion.correo}>{sesion.nombre || sesion.correo}</span>
                <button className="sesion-salir" onClick={() => { cerrarSesion(); setSesion(null); setHistory([]); setConvId(null); }}>
                  Salir
                </button>
              </div>
            ) : (
              <>
                <p className="sesion-invitacion">Entrá para guardar tus consultas</p>
                <Login onEntrar={(d) => setSesion(d)} />
              </>
            )}
          </div>

          {alcance?.documentos && (
            <dl className="alcance" title={
              alcance.indice_generado
                ? `Índice generado el ${alcance.indice_generado}`
                : undefined
            }>
              <div>
                <dt>Documentos</dt>
                <dd>{alcance.documentos.toLocaleString("es-AR")}</dd>
              </div>
              {alcance.normativa_hasta && (
                <div>
                  <dt>Actualizado</dt>
                  <dd>{new Date(alcance.normativa_hasta + "T00:00:00").toLocaleDateString(
                    "es-AR", { day: "2-digit", month: "short", year: "numeric" })}</dd>
                </div>
              )}
            </dl>
          )}

          <a
            className="licdia-signature"
            href="https://licdia.unlu.edu.ar/"
            target="_blank"
            rel="noreferrer"
          >
            <img src="/logo-licdia-96.png" alt="Logo LICDIA" className="licdia-logo" />
            <span>Desarrollado por LICDIA</span>
          </a>
        </div>
      </aside>

      <main className="main-panel">
        {view === "chat" && (
          <div className="chat-page">
            <div className="chat-content">
              {messages.length === 0 ? (
                <section className="empty-state">
                  <div className="empty-card">
                    <h2>¿En qué puedo ayudarte?</h2>
                    <p>Consultá normativa, resoluciones o disposiciones del Digesto.</p>

                    <div className="suggestions">
                      <button className="suggestion-chip" onClick={() => handleSuggestion("Normativa sobre concursos docentes")}>
                        Normativa sobre concursos docentes
                      </button>
                      <button className="suggestion-chip" onClick={() => handleSuggestion("Normativa sobre becas y viajes curriculares")}>
                        Normativa sobre becas y viajes curriculares
                      </button>
                      <button className="suggestion-chip" onClick={() => handleSuggestion("Acuerdos de la Paritaria Particular del Sector Nodocente")}>
                        Acuerdos de la Paritaria Particular del Sector Nodocente
                      </button>
                      <button className="suggestion-chip" onClick={() => handleSuggestion("Reglamentos académicos")}>
                        Reglamentos académicos
                      </button>
                      <button className="suggestion-chip" onClick={() => handleSuggestion("Planes de Estudios y Carreras")}>
                        Planes de Estudios y Carreras
                      </button>
                    </div>
                  </div>
                </section>
              ) : (
                <section className="messages">
                  {messages.map((msg, i) => (
                    <div key={i} className={`message-row ${msg.role}`}>
                      <div className={`message-bubble ${msg.role} ${msg.error ? "error" : ""}`}>
                        {msg.role === "assistant" && !msg.error
                          ? <Markdown texto={msg.content} fuentes={msg.fuentes}
                                      alTocarCita={(j) => irALaFuente(i, j)} />
                          : msg.content}
                        {msg.enCurso && <span className="cursor-escribiendo" />}

                        {msg.advertencia && (
                          <div className="aviso-metadata">{msg.advertencia}</div>
                        )}

                        {msg.role === "assistant" && !msg.enCurso && msg.content && (
                          <div className="acciones-respuesta">
                            <button className="copiar" title="Copiar la respuesta"
                                    onClick={() => copiar(msg.content, `r${i}`)}>
                              {copiado === `r${i}` ? "✓ copiado" : "⧉ copiar"}
                            </button>

                            {msg.mensajeId && (
                              <span className="valoracion">
                                <span>¿Te sirvió?</span>
                                <button className={msg.util === 1 ? "elegido" : ""}
                                        onClick={() => valorar(msg.mensajeId, true, i)}
                                        aria-label="Sí, sirvió">👍</button>
                                <button className={msg.util === 0 ? "elegido" : ""}
                                        onClick={() => valorar(msg.mensajeId, false, i)}
                                        aria-label="No sirvió">👎</button>
                              </span>
                            )}
                          </div>
                        )}

                        {msg.fuentes?.length > 0 && !msg.enCurso && (
                          <details className="fuentes" id={`fuentes-${i}`}>
                            {(() => {
                              const grupos = agruparPorDocumento(msg.fuentes);
                              return (
                                <>
                                  <summary>
                                    {grupos.length} documento{grupos.length > 1 ? "s" : ""} del Digesto
                                    {msg.fuentes.length !== grupos.length &&
                                      ` · ${msg.fuentes.length} fragmentos`}
                                    {msg.segundos ? ` · ${msg.segundos}s` : ""}
                                  </summary>
                                  <ul>
                                    {grupos.map((g) => (
                                      <li key={g.clave} className="fuente-doc">
                                        <div className="fuente-cita">
                                          <span>{g.encabezado}</span>
                                          <span className="fuente-derecha">
                                            {g.fecha && <span className="fuente-fecha">{g.fecha}</span>}
                                            <button className="copiar-cita" title="Copiar la referencia"
                                                    onClick={() => copiar(g.encabezado, `g${i}-${g.clave}`)}>
                                              {copiado === `g${i}-${g.clave}` ? "✓" : "⧉"}
                                            </button>
                                          </span>
                                        </div>

                                        {g.titulo && <div className="fuente-titulo">{g.titulo}</div>}

                                        {g.partes.map((f) => (
                                          <div key={f.indice} id={`fuente-${i}-${f.indice}`}
                                               className={`fuente-parte ${
                                                 fuenteMarcada === `${i}-${f.indice}` ? "marcada" : ""}`}>
                                            <div className="fuente-seccion">
                                              {(f.cita || "").split("—")[1]?.trim() || f.seccion || "Texto"}
                                            </div>
                                            <div className="fuente-texto">{f.texto}</div>
                                          </div>
                                        ))}

                                        {g.confianza && g.confianza !== "alta" && (
                                          <div className="fuente-pie">
                                            <span className="fuente-confianza">
                                              metadata sin verificar contra el sistema origen
                                            </span>
                                          </div>
                                        )}
                                      </li>
                                    ))}
                                  </ul>
                                </>
                              );
                            })()}
                          </details>
                        )}
                      </div>
                    </div>
                  ))}


                </section>
              )}
              <div ref={finDelChat} />
            </div>

            <div className="composer-outer">
              <div className="input-area">
                <textarea
                  ref={cajaTexto}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    // Enter envía; Shift+Enter hace salto de línea. Se ignora mientras
                    // el navegador está componiendo caracteres (acentos, teclados IME),
                    // porque ahí el Enter confirma la composición, no la consulta.
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      sendMessage();
                    }
                  }}
                  placeholder="¿Qué querés saber del Digesto UNLu?"
                  rows={1}
                />
                <button onClick={sendMessage}>Enviar</button>
              </div>
              <div className="amplitud">
                <span>Alcance de la búsqueda</span>
                {[
                  ["preciso", "Preciso", "Menos fuentes, más directo"],
                  ["equilibrado", "Equilibrado", "El punto medio"],
                  ["exhaustivo", "Exhaustivo", "Para relevar todo lo que existe sobre un tema"]
                ].map(([valor, etiqueta, ayuda]) => (
                  <button
                    key={valor}
                    className={amplitud === valor ? "elegida" : ""}
                    onClick={() => setAmplitud(valor)}
                    title={ayuda}
                    aria-pressed={amplitud === valor}
                  >
                    {etiqueta}
                  </button>
                ))}
              </div>

              <div className="notice">
                Las respuestas pueden contener errores. Verificá siempre la información en las{" "}
                <a href="http://digesto.unlu.edu.ar/" target="_blank" rel="noreferrer">
                  fuentes oficiales
                </a>.
              </div>
            </div>
          </div>
        )}

        {view === "draft" && (
          <main className="draft-container">
            <div className="draft-header">
              <button className="back" onClick={() => setView("chat")}>
                ← Volver a consulta
              </button>

              <div>
                <h2>Redacción asistida</h2>
                <p>Definí los parámetros del documento y agregá contexto libre.</p>
              </div>
            </div>

            <div className="draft-panel">
              <div className="form-grid">
                <select
                  value={formData.tipo}
                  onChange={(e) => setFormData({ ...formData, tipo: e.target.value })}
                >
                  <option value="">Tipo de documento</option>
                  <option value="resolucion">Proyecto de resolución</option>
                  <option value="disposicion">Disposición</option>
                  <option value="nota">Nota formal</option>
                </select>

                <input
                  type="text"
                  placeholder="Tema"
                  value={formData.tema}
                  onChange={(e) => setFormData({ ...formData, tema: e.target.value })}
                />

                <input
                  type="text"
                  placeholder="Área o dependencia"
                  value={formData.area}
                  onChange={(e) => setFormData({ ...formData, area: e.target.value })}
                />

                <textarea
                  placeholder="Objetivo del documento"
                  value={formData.objetivo}
                  onChange={(e) => setFormData({ ...formData, objetivo: e.target.value })}
                />
              </div>

              <div className="context-box">
                <label>Contexto adicional</label>
                <textarea
                  className="big-textarea"
                  placeholder="Agregá antecedentes, detalles del caso o redactá libremente la idea inicial..."
                  value={formData.contexto}
                  onChange={(e) => setFormData({ ...formData, contexto: e.target.value })}
                />
              </div>

              <div className="draft-actions">
                <div className="draft-disclaimer">
                  El borrador se genera a partir de información indexada en el sistema y debe ser revisado antes de su utilización. La tramitación es responsabilidad de la persona que formaliza su utilización.
                </div>
                <button className="primary" onClick={startDraft}>
                  Generar borrador
                </button>
              </div>
            </div>
          </main>
        )}
      </main>
    </div>
  );
}

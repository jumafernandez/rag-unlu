import { useCallback, useEffect, useMemo, useState } from "react";
import "./styles.css";
import Login from "./Login";
import {
  consultar, leerSesion, cerrarSesion, listarConversaciones,
  leerConversacion, renombrarConversacion, borrarConversacion, valorarMensaje
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

  const borrar = async (id) => {
    if (!window.confirm("¿Borrar esta conversación?")) return;
    try {
      await borrarConversacion(id);
      if (convId === id) { setConvId(null); setMessages([]); }
      refrescarHistorial();
    } catch (e) { console.error(e); }
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

    try {
      const r = await consultar({ pregunta: content, conversacionId: convId });
      if (r.conversacion_id && r.conversacion_id !== convId) setConvId(r.conversacion_id);
      const fuentes = r.fuentes || [];
      // Tres situaciones distintas, que antes se mezclaban en un mismo mensaje:
      // hay respuesta redactada; hay normativa pero sin redactar (p. ej. sin clave de
      // generación); o directamente no se encontró nada.
      const texto = r.respuesta
        ? r.respuesta
        : fuentes.length
          ? `Encontré ${fuentes.length} fragmento${fuentes.length > 1 ? "s" : ""} de normativa relacionada. Abrí las fuentes para leer el texto de los actos.`
          : "No encontré normativa que responda esa consulta.";

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: texto,
          mensajeId: r.mensaje_id,
          sinGenerar: !r.respuesta && fuentes.length > 0,
          fuentes,
          advertencia: r.advertencia,
          segundos: r.segundos
        }
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `No pude consultar el Digesto. ${e.message}`, error: true }
      ]);
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
  };

  const startDraft = () => {
    console.log("Parámetros redacción:", formData);
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="sidebar-brand">
            <img src="/logo-unlu.png" className="logo" alt="Logo UNLu" />
            <div>
              <h1>ChatDigesto</h1>
              <p>Consulta de normativa institucional de acceso público</p>
            </div>
          </div>

          <button className="sidebar-action primary-side" onClick={handleNewChat}>
            <PlusIcon />
            <span>Nuevo chat</span>
          </button>

          <button className="sidebar-action" onClick={() => setView("draft")}>
            <PencilIcon />
            <span>Redacción asistida</span>
          </button>

          <div className="search-box">
            <SearchIcon />
            <input
              type="text"
              placeholder="Buscar chats"
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
                      <button className="history-accion" title="Renombrar"
                              onClick={() => empezarEdicion(item.id, item.titulo)}>✎</button>
                      <button className="history-accion" title="Borrar"
                              onClick={() => borrar(item.id)}>🗑</button>
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

          <a
            className="licdia-signature"
            href="https://licdia.unlu.edu.ar/"
            target="_blank"
            rel="noreferrer"
          >
            <img src="/logo-licdia.png" alt="Logo LICDIA" className="licdia-logo" />
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
                        {msg.content}

                        {msg.advertencia && (
                          <div className="aviso-metadata">{msg.advertencia}</div>
                        )}

                        {msg.role === "assistant" && msg.mensajeId && (
                          <div className="valoracion">
                            <span>¿Te sirvió?</span>
                            <button className={msg.util === 1 ? "elegido" : ""}
                                    onClick={() => valorar(msg.mensajeId, true, i)}
                                    aria-label="Sí, sirvió">👍</button>
                            <button className={msg.util === 0 ? "elegido" : ""}
                                    onClick={() => valorar(msg.mensajeId, false, i)}
                                    aria-label="No sirvió">👎</button>
                          </div>
                        )}

                        {msg.fuentes?.length > 0 && (
                          <details className="fuentes">
                            <summary>
                              {msg.fuentes.length} fuente{msg.fuentes.length > 1 ? "s" : ""} del Digesto
                              {msg.segundos ? ` · ${msg.segundos}s` : ""}
                            </summary>
                            <ul>
                              {msg.fuentes.map((f, j) => (
                                <li key={j}>
                                  <div className="fuente-cita">
                                    {f.cita}
                                    {f.date_issued && <span className="fuente-fecha">{f.date_issued}</span>}
                                  </div>
                                  <div className="fuente-texto">{f.texto}</div>
                                  <div className="fuente-pie">
                                    {f.source_pdf}
                                    {f.metadata_confianza && f.metadata_confianza !== "alta" && (
                                      <span className="fuente-confianza"> · metadata {f.metadata_confianza}</span>
                                    )}
                                  </div>
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </div>
                    </div>
                  ))}

                  {cargando && (
                    <div className="message-row assistant">
                      <div className="message-bubble assistant pensando">
                        Buscando en el Digesto…
                      </div>
                    </div>
                  )}
                </section>
              )}
            </div>

            <div className="composer-outer">
              <div className="input-area">
                <textarea
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
              <div className="notice">
                Las respuestas pueden contener errores. Verificá siempre la información en las fuentes oficiales.
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

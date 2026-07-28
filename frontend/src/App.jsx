import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./styles.css";
import Login from "./Login";
import Markdown from "./Markdown";
import { INSTITUCION, TEXTOS } from "./config";
import {
  consultarEnFlujo, leerSesion, cerrarSesion, listarConversaciones, adoptarConversacion,
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
  // Estado de la conversación: el sujeto del que se viene hablando y los actos que se
  // mencionaron. Se mantiene entre turnos y es EDITABLE — si el sistema lo interpretó
  // mal, corregirlo arregla la conversación en vez de obligar a reescribir cada pregunta.
  // Y lo que el usuario fija pesa más en la búsqueda que lo que el sistema infirió: su
  // decisión vale más que la inferencia.
  const [estado, setEstado] = useState(null);
  const [verRazonamiento, setVerRazonamiento] = useState(
    () => localStorage.getItem("chatdigesto_razonamiento") === "1"
  );
  const [editandoFoco, setEditandoFoco] = useState(false);
  const [focoEdit, setFocoEdit] = useState("");

  // Al fijar la entidad a mano queda marcada como del usuario, y eso es lo que hace que
  // el modelo deje de reemplazarla y que pese más al recuperar.
  const fijarEntidad = (valor) =>
    setEstado((e) => ({
      ...(e || { actos: [] }),
      entidad: valor || null,
      entidad_origen: valor ? "usuario" : "descartado",
    }));

  // Descartar no borra: el acto queda a la vista con peso cero, para que se siga viendo
  // qué venía siguiendo el sistema y se pueda volver atrás.
  const cambiarActo = (codigo, numero, origen) =>
    setEstado((e) => !e ? e : {
      ...e,
      actos: (e.actos || []).map((a) =>
        a.codigo === codigo && a.numero === numero ? { ...a, origen } : a
      ),
    });
  useEffect(() => {
    localStorage.setItem("chatdigesto_razonamiento", verRazonamiento ? "1" : "0");
  }, [verRazonamiento]);
  const finDelChat = useRef(null);
  const areaChat = useRef(null);
  const pegadoAbajo = useRef(true);

  // Espejo de lo que hay en pantalla. `entrar` necesita leerlo, pero no puede DEPENDER de
  // ello: si dependiera, volvería a ser una función distinta en cada render y con eso
  // volvería el titileo del botón de Google.
  const mensajesRef = useRef([]);
  const convIdRef = useRef(null);
  const cajaTexto = useRef(null);
  const buscador = useRef(null);
  const [anchoPanel, setAnchoPanel] = useState(
    () => Number(localStorage.getItem("chatdigesto_ancho")) || 320
  );

  // Alcance del corpus: cuántos documentos hay y hasta cuándo llega la normativa. Para
  // quien consulta un digesto eso decide si una respuesta vacía significa "no existe" o
  // "todavía no está cargado".
  useEffect(() => { salud().then(setAlcance).catch(() => setAlcance(null)); }, []);

  // La conversación se mantiene pegada abajo mientras el usuario esté mirando el final.
  // Si subió a leer algo —muy común mientras la respuesta se está escribiendo— no se lo
  // arrastra de vuelta: se deja de seguir hasta que vuelva a bajar.
  const alScrollear = () => {
    const el = areaChat.current;
    if (!el) return;
    pegadoAbajo.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  useEffect(() => {
    if (!pegadoAbajo.current) return;
    const el = areaChat.current;
    // Salto directo y no suave: durante el streaming esto corre con cada fragmento, y
    // una animación por fragmento se pisa a sí misma y tiembla.
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, cargando]);

  // Al cambiar de conversación se vuelve a seguir el final, aunque en la anterior el
  // usuario hubiera quedado leyendo más arriba.
  useEffect(() => { pegadoAbajo.current = true; }, [convId]);

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

  useEffect(() => { mensajesRef.current = messages; }, [messages]);
  useEffect(() => { convIdRef.current = convId; }, [convId]);

  // Al iniciar sesión con una conversación en curso, esa conversación se adopta: se
  // guarda en el historial del usuario que acaba de entrar, con sus fuentes.
  //
  // Quien consulta sin cuenta y después entra lo hace, casi siempre, porque quiere
  // conservar lo que está viendo. Y antes quedaba algo peor que perderlo: la conversación
  // seguía en pantalla pero no en la base, así que la consulta siguiente guardaba una
  // respuesta apoyada en turnos que no quedaban registrados en ningún lado.
  const entrar = useCallback(async (datos) => {
    setSesion(datos);
    const previos = mensajesRef.current;
    if (convIdRef.current || !previos?.length) return;
    try {
      const r = await adoptarConversacion(
        previos
          .filter((m) => m.content && !m.enCurso)
          .map((m) => ({
            rol: m.role === "user" ? "user" : "assistant",
            texto: m.content,
            fuentes: m.fuentes?.length ? m.fuentes : null,
          }))
      );
      setConvId(r.conversacion_id);
      refrescarHistorial();
    } catch {
      // Si falla, la conversación sigue en pantalla: no se pierde nada, solo no se guarda.
    }
  }, [refrescarHistorial]);

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

  // La lista se muestra de a tandas: con muchas conversaciones el panel se volvía una
  // tira interminable, y las viejas casi nunca se buscan bajando sino por el buscador.
  // El tamaño de la tanda es el que entra en el panel sin que aparezca la barra de
  // scroll: si hubiera que scrollear para llegar al "Ver más", no serviría de nada.
  const TANDA = 8;
  const [visibles, setVisibles] = useState(TANDA);
  // Cada búsqueda arranca desde arriba: si no, se hereda el "ver más" de la anterior.
  useEffect(() => { setVisibles(TANDA); }, [searchChats]);
  const historialVisible = filteredHistory.slice(0, visibles);
  const ocultas = filteredHistory.length - historialVisible.length;

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

      await consultarEnFlujo({ pregunta: content, k: K, conversacionId: convId, historial, estado }, (evento, datos) => {
        if (evento === "fuentes") {
          // El estado que devuelve el servidor ya trae lo que el usuario había fijado,
          // así que se reemplaza entero en vez de fusionarlo acá.
          if (datos.estado) setEstado(datos.estado);
          actualizar({
            fuentes: datos.fuentes || [],
            consultaEfectiva: datos.consulta_efectiva,
            estadoUsado: datos.estado
          });
        } else if (evento === "estado") {
          // Llega al final: los actos que la respuesta acaba de citar se suman al estado.
          if (datos.estado) {
            setEstado(datos.estado);
            actualizar({ estadoUsado: datos.estado });
          }
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
    setEstado(null);
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
            <img src={INSTITUCION.logo} className="logo" alt={`Logo ${INSTITUCION.sigla}`} />
            <div>
              <h1>{INSTITUCION.producto}</h1>
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
          <div className="historial-bloque">
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
              {historialVisible.map((item) => (
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
              {(ocultas > 0 || visibles > TANDA) && (
                <div className="ver-mas-fila">
                  {ocultas > 0 && (
                    <button className="ver-mas" onClick={() => setVisibles((n) => n + TANDA)}>
                      Ver más ({ocultas})
                    </button>
                  )}
                  {visibles > TANDA && (
                    <button className="ver-mas" onClick={() => setVisibles(TANDA)}>
                      Ver menos
                    </button>
                  )}
                </div>
              )}
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
                <Login onEntrar={entrar} />
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
            {/* El scroll vive acá y no en la página: así el campo de escritura queda
                siempre a la vista y la conversación se mantiene pegada abajo, en vez de
                que la respuesta nueva empuje todo y haya que perseguirla. */}
            <div className="chat-scroll" ref={areaChat} onScroll={alScrollear}>
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

                        {verRazonamiento && !msg.enCurso && msg.consultaEfectiva && (
                          <div className="razonamiento">
                            <div>
                              <span>Buscó</span>
                              <code>{msg.consultaEfectiva}</code>
                            </div>
                            {msg.estadoUsado?.entidad && (
                              <div>
                                <span>Entidad</span>
                                <code>{msg.estadoUsado.entidad}
                                  {msg.estadoUsado.tipo ? ` · ${msg.estadoUsado.tipo}` : ""}
                                  {msg.estadoUsado.entidad_origen === "usuario" ? " · fijada" : ""}</code>
                              </div>
                            )}
                            {msg.estadoUsado?.actos?.some((a) => a.origen !== "descartado") && (
                              <div>
                                <span>Actos</span>
                                <code>
                                  {msg.estadoUsado.actos
                                    .filter((a) => a.origen !== "descartado")
                                    .map((a) => `${a.codigo} ${a.numero}`)
                                    .join(" · ")}
                                </code>
                              </div>
                            )}
                            {msg.fuentes?.some((f) => f.ranking?.foco || f.ranking?.continuidad) && (
                              <div>
                                <span>Pesadas</span>
                                <code>
                                  {msg.fuentes.filter((f) => f.ranking?.foco).length} por entidad ·{" "}
                                  {msg.fuentes.filter((f) => f.ranking?.continuidad).length} por acto
                                </code>
                              </div>
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
            </div>

            <div className="composer-outer">
              {(estado?.entidad || estado?.actos?.length > 0 || verRazonamiento) && (
                <div className="foco-barra">
                  <span className="foco-etiqueta">Consultando sobre</span>
                  {editandoFoco ? (
                    <input
                      className="foco-edit"
                      value={focoEdit}
                      autoFocus
                      onChange={(e) => setFocoEdit(e.target.value)}
                      onBlur={() => {
                        setEditandoFoco(false);
                        fijarEntidad(focoEdit.trim());
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") e.currentTarget.blur();
                        if (e.key === "Escape") setEditandoFoco(false);
                      }}
                    />
                  ) : (
                    <button className={`foco-valor${estado?.entidad_origen === "usuario" ? " fijado" : ""}`}
                            title={estado?.entidad_origen === "usuario"
                              ? "Lo fijaste vos: el sistema no lo va a cambiar y pesa más al buscar"
                              : "Corregir el sujeto de la conversación"}
                            onClick={() => { setFocoEdit(estado?.entidad || ""); setEditandoFoco(true); }}>
                      {estado?.entidad || "sin definir"}
                      {estado?.tipo && <em className="foco-tipo">{estado.tipo}</em>}
                    </button>
                  )}
                  {estado?.entidad && (
                    <button className="foco-limpiar" title="Olvidar el sujeto actual"
                            onClick={() => fijarEntidad(null)}>✕</button>
                  )}

                  {/* El sistema fue contra lo que fijó el usuario en un caso donde podría
                      haber aplicado. Se dice, en lugar de resolverlo en silencio. */}
                  {estado?.discrepancia && (
                    <span className="foco-discrepancia"
                          title="Podés reformular la pregunta si querés que se use igual">
                      Esta pregunta no parecía ser sobre {estado.discrepancia}
                    </span>
                  )}

                  {/* Actos que la conversación viene tocando. Se pueden apagar y volver a
                      encender: apagado no es borrado, sigue visible para que se entienda
                      qué venía siguiendo el sistema. */}
                  {estado?.actos?.length > 0 && (
                    <span className="actos-seguidos">
                      {estado.actos.map((a) => (
                        <button
                          key={`${a.codigo}-${a.numero}`}
                          className={`acto-chip${a.origen === "descartado" ? " apagado" : ""}${a.origen === "usuario" ? " fijado" : ""}`}
                          title={a.origen === "descartado"
                            ? "Descartado — tocá para volver a tenerlo en cuenta"
                            : a.origen === "usuario"
                              ? "Lo fijaste vos: pesa más al buscar. Tocá para descartarlo"
                              : "Lo está siguiendo el sistema. Tocá para descartarlo"}
                          onClick={() => cambiarActo(a.codigo, a.numero,
                            a.origen === "descartado" ? "usuario" : "descartado")}>
                          {a.codigo} {a.numero}
                        </button>
                      ))}
                    </span>
                  )}
                </div>
              )}
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
                  placeholder={TEXTOS.placeholder}
                  rows={1}
                />
                <button onClick={sendMessage}>Enviar</button>
              </div>
              <div className="amplitud">
                <span>
                  Alcance de la búsqueda
                  <span className="amplitud-ayuda" tabIndex={0} role="button"
                        aria-label="Qué significa el alcance de la búsqueda">
                    ?
                    <span className="amplitud-globo">
                      Cuánta normativa se consulta para armar cada respuesta.{" "}
                      <b>Preciso</b> mira pocos documentos: sirve cuando buscás algo puntual
                      y querés la respuesta rápido. <b>Equilibrado</b> es el punto medio y
                      cubre bien la mayoría de las consultas. <b>Exhaustivo</b> mira muchos
                      más, para cuando necesitás relevar todo lo que existe sobre un tema.
                      No cambia qué hay en el Digesto, solo cuánto se revisa por consulta.
                    </span>
                  </span>
                  :
                </span>

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

                <button className={`razonamiento-toggle ${verRazonamiento ? "activo" : ""}`}
                        onClick={() => setVerRazonamiento((v) => !v)}
                        title="Mostrar con qué consulta se buscó y qué sujeto se está siguiendo">
                  Ver razonamiento
                </button>
              </div>

              <div className="notice">
                Las respuestas pueden contener errores. Verificá siempre la información en las{" "}
                <a href={INSTITUCION.digestoOficial} target="_blank" rel="noreferrer">
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

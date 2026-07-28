/**
 * Renderizador mínimo de Markdown para las respuestas.
 *
 * El modelo escribe un subconjunto muy acotado —negritas, itálicas, listas y párrafos—
 * así que no hace falta una librería completa. Y hay una razón más fuerte para no usarla:
 * el texto viene de un modelo, y un renderizador general puede interpretar HTML crudo.
 * Acá NUNCA se inserta HTML: se construyen elementos de React, así que no hay forma de
 * que el contenido inyecte marcado.
 *
 * Soporta: **negrita**, *itálica*, `código`, listas con - o *, listas numeradas,
 * y párrafos separados por línea en blanco. Todo lo demás se muestra tal cual.
 */

const RE_INLINE = /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`\n]+`)/g;

// Citas del tipo "(Disposición DISPCD-CB 450/2025 — Artículo 1)". Se detectan para poder
// enlazarlas con la fuente correspondiente: así la respuesta deja de ser un párrafo suelto
// y se puede ir de la afirmación al acto que la respalda.
const RE_CITA = /\(((?:Disposici[oó]n|Resoluci[oó]n)[^)]{4,120})\)/g;

const sinTildes = (t) =>
  t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/\s+/g, " ").trim();

const RE_NUMERO_ACTO = /(\d{1,6})\s*\/\s*(\d{2,4})/;

/** Índice de la fuente que corresponde a una cita, o -1.
 *
 * El emparejamiento se apoya en el NÚMERO de acto, no en el código: el modelo a veces
 * transcribe mal la sigla ("DISPPCD-CB" por "DISPCD-CB"), pero el número casi nunca. Con
 * ese número y la sección alcanza para identificar la fuente sin ambigüedad dentro de un
 * puñado de resultados.
 */
function buscarFuente(cita, fuentes) {
  if (!fuentes?.length) return -1;
  const objetivo = sinTildes(cita);

  const exacta = fuentes.findIndex((f) => sinTildes(f.cita) === objetivo);
  if (exacta >= 0) return exacta;

  const num = cita.match(RE_NUMERO_ACTO);
  if (num) {
    const clave = `${num[1]}/${num[2]}`;
    const candidatos = fuentes
      .map((f, i) => ({ f, i }))
      .filter(({ f }) => (f.cita || "").replace(/\s+/g, "").includes(clave));

    if (candidatos.length === 1) return candidatos[0].i;
    if (candidatos.length > 1) {
      // Varias secciones del mismo acto: se elige la que la cita menciona.
      const seccion = sinTildes((cita.split("—")[1] || "").trim());
      if (seccion) {
        const conSeccion = candidatos.find(({ f }) =>
          sinTildes((f.cita.split("—")[1] || "").trim()) === seccion
        );
        if (conSeccion) return conSeccion.i;
      }
      return candidatos[0].i;
    }
  }

  return fuentes.findIndex((f) => {
    const c = sinTildes(f.cita);
    return c.includes(objetivo) || objetivo.includes(c);
  });
}

function conFormato(texto, clave, fuentes, alTocarCita) {
  const partes = texto.split(RE_INLINE).filter(Boolean);
  return partes.map((parte, i) => {
    const k = `${clave}-${i}`;
    if (parte.startsWith("**") && parte.endsWith("**") && parte.length > 4) {
      return <strong key={k}>{parte.slice(2, -2)}</strong>;
    }
    if (parte.startsWith("*") && parte.endsWith("*") && parte.length > 2) {
      return <em key={k}>{parte.slice(1, -1)}</em>;
    }
    if (parte.startsWith("`") && parte.endsWith("`") && parte.length > 2) {
      return <code key={k}>{parte.slice(1, -1)}</code>;
    }
    return <span key={k}>{conCitas(parte, k, fuentes, alTocarCita)}</span>;
  });
}

/** Convierte las citas del texto en botones que llevan a su fuente. */
function conCitas(texto, clave, fuentes, alTocarCita) {
  if (!fuentes?.length) return texto;
  const trozos = [];
  let ultimo = 0;
  for (const m of texto.matchAll(RE_CITA)) {
    if (m.index > ultimo) trozos.push(texto.slice(ultimo, m.index));

    // El modelo suele agrupar varias citas en un mismo paréntesis separadas por ";".
    // Cada una tiene que poder tocarse por separado, no el bloque entero.
    const sueltas = m[1].split(";").map((x) => x.trim()).filter(Boolean);
    const enlazadas = [];
    sueltas.forEach((cita, n) => {
      const idx = buscarFuente(cita, fuentes);
      if (n > 0) enlazadas.push(<span key={`${clave}-s${m.index}-${n}`}>; </span>);
      enlazadas.push(
        idx >= 0 ? (
          // Va como <span> y no como <button> a propósito. Un botón es una caja atómica:
          // el navegador no puede partir su texto, así que ante una cita larga la baja
          // entera al renglón siguiente y deja el paréntesis de apertura colgando solo al
          // final de la línea. Un span fluye como texto y se parte donde corresponde.
          <span
            key={`${clave}-c${m.index}-${n}`}
            className="cita-enlace"
            role="button"
            tabIndex={0}
            onClick={() => alTocarCita?.(idx)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                alTocarCita?.(idx);
              }
            }}
            title="Ver la fuente que respalda esta afirmación"
          >
            {/* Se muestra la cita tal como figura en la fuente, no como la escribió el
                modelo: así una sigla mal transcrita no llega al usuario. */}
            {fuentes[idx].cita}
          </span>
        ) : (
          <span key={`${clave}-t${m.index}-${n}`}>{cita}</span>
        )
      );
    });
    trozos.push(<span key={`${clave}-g${m.index}`}>({enlazadas})</span>);
    ultimo = m.index + m[0].length;
  }
  if (!trozos.length) return texto;
  if (ultimo < texto.length) trozos.push(texto.slice(ultimo));
  return trozos;
}

export default function Markdown({ texto, fuentes, alTocarCita }) {
  if (!texto) return null;

  const bloques = [];
  let lista = null;   // { ordenada: bool, items: [] }

  const cerrarLista = () => {
    if (!lista) return;
    const Etiqueta = lista.ordenada ? "ol" : "ul";
    bloques.push(
      <Etiqueta key={`l${bloques.length}`} className="md-lista"
                start={lista.ordenada ? lista.desde : undefined}>
        {lista.items.map((it, i) => (
          <li key={i}>{conFormato(it, `li${bloques.length}-${i}`, fuentes, alTocarCita)}</li>
        ))}
      </Etiqueta>
    );
    lista = null;
  };

  for (const linea of texto.split("\n")) {
    const limpia = linea.trim();

    if (!limpia) continue;   // una línea en blanco entre ítems no corta la lista

    const numerada = limpia.match(/^(\d+)[.)]\s+(.*)$/);
    const conGuion = limpia.match(/^[-*•]\s+(.*)$/);

    if (numerada || conGuion) {
      const ordenada = Boolean(numerada);
      if (!lista || lista.ordenada !== ordenada) {
        cerrarLista();
        // Se conserva el número con el que arranca: el modelo separa los ítems con
        // líneas en blanco, y sin esto cada uno abría una lista nueva desde 1.
        lista = { ordenada, items: [], desde: numerada ? Number(numerada[1]) : undefined };
      }
      lista.items.push(numerada ? numerada[2] : conGuion[1]);
      continue;
    }

    cerrarLista();
    bloques.push(
      <p key={`p${bloques.length}`}>{conFormato(limpia, `p${bloques.length}`, fuentes, alTocarCita)}</p>
    );
  }
  cerrarLista();

  return <div className="md">{bloques}</div>;
}

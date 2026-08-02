/**
 * Valores de arranque de la institución.
 *
 * La fuente de verdad es el panel de administración (Personalización), que los guarda en
 * el servidor: así otra universidad se adapta sin recompilar. Este archivo es lo que se
 * dibuja en el primer pintado, antes de que llegue la respuesta del servidor, y lo que
 * queda si el servidor no contesta. Las claves son las mismas que las del backend.
 */

export const INSTITUCION = {
  nombre: "Universidad Nacional de Luján",
  sigla: "UNLu",
  producto: "ChatDigesto",
  descripcion: "Consulta de normativa institucional de acceso público",
  denominacion: "Digesto",
  digesto_oficial: "http://digesto.unlu.edu.ar/",
  portal_sudocu: "https://portal.unlu.edu.ar/sudocu/mpd/#!/mpd/portada",
  aviso: "Las respuestas pueden contener errores. Verificá siempre la información en las fuentes oficiales.",
  sugerencias: [],

  // Logo de reserva, en public/. El que configura el admin se sirve desde /marca/logo.
  logo: null,
};

export const LOGO_POR_OMISION = "/logo-unlu-96.png";

/** Textos que nombran a la institución, derivados de los campos configurables. */
export const textos = (inst) => ({
  tituloPagina: `${inst.producto} \u00b7 ${inst.denominacion} ${inst.sigla}`,
  descripcion: `Consulta de la normativa institucional de la ${inst.nombre}.`,
  placeholder: `\u00bfQu\u00e9 quer\u00e9s saber del ${inst.denominacion} ${inst.sigla}?`,
});

/**
 * Configuración de la institución.
 *
 * Todo lo que ata este asistente a una universidad concreta vive acá. El resto del
 * código no nombra a ninguna: SUDOCU es un sistema nacional y el portal de publicación
 * es un módulo estándar suyo, así que la misma aplicación sirve para cualquier
 * universidad que lo tenga desplegado. Adaptarla es editar este archivo y reemplazar
 * los logos de public/.
 *
 * Los valores del backend —URL del portal, secciones a recolectar— están en la
 * configuración del scraper; este archivo es solo lo que se ve.
 */

export const INSTITUCION = {
  nombre: "Universidad Nacional de Luján",
  sigla: "UNLu",

  // Nombre del asistente y de la pestaña del navegador.
  producto: "ChatDigesto",

  // Digesto oficial. Es la fuente a la que se remite para verificar, y por eso aparece
  // al pie: el asistente ayuda a encontrar, la fuente oficial es la que da fe.
  digestoOficial: "http://digesto.unlu.edu.ar/",

  // Archivos en public/. El de 96 px es el que se muestra; el grande queda para pantallas
  // de alta densidad y para reemplazarlo sin regenerar el chico.
  logo: "/logo-unlu-96.png",

  // Segundo logo, si el desarrollo tiene un laboratorio o unidad responsable. null lo oculta.
  logoSecundario: "/logo-licdia-96.png",
};

/** Textos que nombran a la institución. Separados para poder traducirlos o ajustarlos
 *  sin tocar los componentes. */
export const TEXTOS = {
  tituloPagina: `${INSTITUCION.producto} · Digesto ${INSTITUCION.sigla}`,
  descripcion: `Consulta de la normativa institucional de la ${INSTITUCION.nombre}.`,
  placeholder: `¿Qué querés saber del Digesto ${INSTITUCION.sigla}?`,
};

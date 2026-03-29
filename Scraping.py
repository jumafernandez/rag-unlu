import opciones as op
import conf
from funciones import descargar_Documento

url = "https://portal.unlu.edu.ar/sudocu/mpd/#!/mpd/portada"

for seccion in conf.SECCIONES:
    opciones = op.options(seccion)
    descargar_Documento(opciones, url)
    
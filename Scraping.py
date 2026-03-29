import opciones as op
import conf
from funciones import encontrar_Carpeta

url = "https://portal.unlu.edu.ar/sudocu/mpd/#!/mpd/portada"

for seccion in conf.SECCIONES:
    opciones = op.options(seccion)
    encontrar_Carpeta(opciones, url, seccion)
    

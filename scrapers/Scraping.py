import opciones as op
import conf
from funciones import encontrar_Carpeta

# La URL del portal sale de la configuración: ver PORTAL_URL en conf.py, que es —junto
# con SECCIONES— lo único que hay que cambiar para apuntar a otra universidad.
url = conf.PORTAL_URL

for seccion in conf.SECCIONES:
    opciones = op.options(seccion)
    encontrar_Carpeta(opciones, url, seccion)
    
        
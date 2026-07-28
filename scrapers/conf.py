import os
#RUTAS Y DIRECTORIOS
#Obtenemos la ruta absoluta de la carpeta donde esta el archivo conf.py
_RUTA_DEL_SCRIPT = os.path.abspath(__file__)
DIRECTORIO_BASE = os.path.dirname(_RUTA_DEL_SCRIPT)

#Carpeta donde se guardan los PDF
DIRECTORIO_DESCARGAS = os.path.join(DIRECTORIO_BASE, "Descargas")

#Rutas completas a los archivos de datos
RUTA_METADATOS = os.path.join(DIRECTORIO_BASE, "metadatos.csv")
RUTA_PROGRESO = os.path.join(DIRECTORIO_BASE, "Indice.json")
#
PAUSA = 4

# --- Instalación de SUDOCU a la que se apunta -----------------------------------------
# Estas dos variables son TODO lo que ata este scraper a una universidad. SUDOCU es un
# sistema nacional y el Módulo de Publicación Documental (mpd) es un módulo estándar
# suyo, así que el resto del código vale para cualquier instalación: lo que se busca en
# la página son clases de Angular Material del propio SUDOCU, no del portal de una
# institución en particular.
#
# Para apuntar a otra universidad alcanza con cambiar la URL y listar sus secciones.
PORTAL_URL = os.environ.get(
    "SUDOCU_PORTAL_URL",
    "https://portal.unlu.edu.ar/sudocu/mpd/#!/mpd/portada",
)

# Secciones a recolectar. Estas son las del portal de la UNLu; cada instalación tiene las
# suyas y se leen del menú del propio portal.
SECCIONES = [
    "DEPARTAMENTO DE CIENCIAS BÁSICAS",
    "RESOLUCIONES ASAMBLEA UNIVERSITARIA",
    "RESOLUCIONES RECTOR",
    "DEPARTAMENTO DE TECNOLOGIA",
    "ORDENES DE COMPRA",
    "DEPARTAMENTO DE CIENCIAS SOCIALES",
    "DEPARTAMENTO DE EDUCACION",
    "SECRETARIAS DE RECTORADO",
    "RESOLUCIONES H. CONSEJO SUPERIOR",
    "RESOLUCIONES PRESIDENTE H. CONSEJO SUPERIOR",
    "DIRECCIONES ADMINISTRATIVAS",
]
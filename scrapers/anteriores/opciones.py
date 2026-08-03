import os 
from selenium.webdriver.chrome.options import Options   #libreria que permite configurar el comportamiento del buscador
from fake_useragent import UserAgent
import conf
import pandas as pd
import time
import glob
import json
import sys
import re
import unicodedata
#os.environ['no_proxy'] = 'localhost,127.0.0.1'

def nuevaCarpeta(seccion):
    ruta_final = os.path.join(conf.DIRECTORIO_DESCARGAS, seccion)
    os.makedirs(ruta_final, exist_ok=True)

def options(seccion):
    opciones = Options()
    opciones.add_argument("--window-size=1920,1080")  #Establecer tamaño de ventana
    opciones.add_argument("--headless")  #Ejecucion sin interfaz visual
    opciones.add_argument("--start-maximized")  #Maximiza la ventana al abrir
    opciones.add_argument("--disable-extensions")  #Deshabilita las extensiones
    opciones.add_argument("--blink-settings=imagesEnabled=false")  #Desactiva la carga de imagenes
    opciones.add_argument("--disable-autofill")  #Desactiva el autocompletado
    opciones.add_argument("--disable-password-manager-reauthentication")  #Desactiva el guardado de claves
    opciones.add_argument("--lang=en")  #es español, en ingles
    
    opciones.add_argument("--no-sandbox")   # Necesario en Linux/Docker
    opciones.add_argument("--disable-dev-shm-usage") # Evita errores de memoria en Linux
    
    opciones.add_argument("--disable-blink-features=AutomationControlled")
    opciones.add_experimental_option("excludeSwitches", ["enable-automation"])
    opciones.add_experimental_option('useAutomationExtension', False)
    
    opciones.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    nuevaCarpeta(seccion)
    ruta = os.path.join(conf.DIRECTORIO_DESCARGAS, seccion)
    
    ua = UserAgent()
    prefs = {"download.default_directory": ruta,        #Establece el directorio de descargas
            "download.prompt_for_download": False,        #no preguntar antes de descargar
            "directory_upgrade": True,                    #actualizar carpeta si ya existe
            "plugins.always_open_pdf_externally": True,   #descargar PDF en lugar de abrirlo en Chrome
            "safebrowsing.enabled": True }                #evitar advertencias de seguridad
    opciones.add_argument(f'user-agent={ua.random}')    #UserAgent aleatorio
    opciones.add_experimental_option("prefs", prefs)
    opciones.add_argument("--disable-pdf-viewer")  #Desactiva el visor de PDFs en Chrome
    opciones.add_experimental_option("excludeSwitches", ["enable-automation"])
    opciones.add_experimental_option("useAutomationExtension", False)
    
    return opciones

########################################################################################################################

def pandasDataframe(ARCHIVO_METADOS):    
    
    if os.path.exists(ARCHIVO_METADOS):
        return pd.read_csv(ARCHIVO_METADOS)
    else:   #crea el dataframe
        dataframe = pd.DataFrame(columns=["Tipo de documento", "Numero", "Estado", "Fecha", "Titulo", "ID PDF"])
        dataframe.to_csv(ARCHIVO_METADOS, index=False)
        return dataframe
    
def normalizar_prefijo_archivo(nombre):
    sin_tildes = unicodedata.normalize("NFKD", nombre)
    sin_tildes = "".join(letra for letra in sin_tildes if not unicodedata.combining(letra))

    caracteres = []
    for letra in sin_tildes.upper():
        if letra.isalnum():
            caracteres.append(letra)
        else:
            caracteres.append("_")

    return "_".join(parte for parte in "".join(caracteres).split("_") if parte)


def nombre_archivo_pdf(seccion, id_pdf):
    prefijo = normalizar_prefijo_archivo(seccion)
    return f"{prefijo}_{id_pdf}.pdf"


def patron_nombre_pdf(seccion):
    prefijo = normalizar_prefijo_archivo(seccion)
    return re.compile(rf"^{re.escape(prefijo)}_(\d+)\.pdf$", re.IGNORECASE)


def es_nombre_pdf_definitivo(nombre_archivo, seccion):
    return patron_nombre_pdf(seccion).match(nombre_archivo) is not None


def renombrarArchivo(ruta, id_pdf):
    time.sleep(2) 
    seccion = os.path.basename(os.path.normpath(ruta))
    patron_archivos = os.path.join(ruta, "*.pdf")
    archivos = [
        archivo
        for archivo in glob.glob(patron_archivos)
        if not es_nombre_pdf_definitivo(os.path.basename(archivo), seccion)
    ]
    if not archivos:
        return #si no lo encuentra, corta
    
    archivo_reciente = max(archivos, key=os.path.getctime)  #busca el archivo mas reciente de la ruta
    ruta_final = os.path.join(ruta, nombre_archivo_pdf(seccion, id_pdf))
    try:
        os.rename(archivo_reciente, ruta_final)
        return ruta_final
    except Exception as e:
        print(f"Error al renombrar: {e}")
        return None



########################################################################################################################

def cargarindice(seccion):  
    if os.path.exists(conf.RUTA_PROGRESO):
        try:
            with open(conf.RUTA_PROGRESO, 'r') as f:
                datos = json.load(f)
                return datos.get(seccion, 0)    #lee el json y retorna el indice de la seccion especifica. si no se encontro la seccion, devuelve "0"
        except:
            return 0
    return 0

def guardarindice(seccion, nuevo_indice):
    datos = {}
    if os.path.exists(conf.RUTA_PROGRESO):
        try:
            with open(conf.RUTA_PROGRESO, 'r') as f:  #lee los datos 
                datos = json.load(f)
        except:
            datos = {}
    
    datos[seccion] = nuevo_indice   #en la seccion especifica, actualiza el progreso
    with open(conf.RUTA_PROGRESO, 'w') as f:
        json.dump(datos, f)

def calcular_ID(seccion):
    
    """
    Lee la carpeta de la seccion y devuelve el numero secuencial mas alto.
    Retorna 1 si la carpeta no existe o está vacía.
    """
    ruta_carpeta = os.path.join(conf.DIRECTORIO_DESCARGAS, seccion)
    
    # si la carpeta de esta seccion todavia no existe, arrancamos desde cero
    if not os.path.exists(ruta_carpeta):
        print(f"no existe la carpeta {seccion} en la carpeta de descargas")
        return 1
        
    archivos = os.listdir(ruta_carpeta)
    numeros_existentes = []
    patron_nombre_actual = patron_nombre_pdf(seccion)
    
    #recorremos lo que hay en la carpeta
    for archivo in archivos:
        if archivo.lower().endswith(".pdf"):
            # le sacamos el ".pdf" para quedarnos solo con el numero
            nombre_sin_extension = os.path.splitext(archivo)[0]
        
            if nombre_sin_extension.isdigit():
                numeros_existentes.append(int(nombre_sin_extension))
                continue

            coincidencia = patron_nombre_actual.match(archivo)
            if coincidencia:
                numeros_existentes.append(int(coincidencia.group(1)))
                
    #calculamos el mas grande
    if numeros_existentes:
        ultimo_numero = max(numeros_existentes)
        #sumamos 1 para que sea el siguiente id a descargar
        ultimo_numero += 1
        
        return ultimo_numero
    else:
        # Si la carpeta existe pero no hay PDFs válidos adentro
        return 1
########################################################################################################################

def pausa_preventiva_parada(segundos=5):
    """
    Crea una cuenta regresiva en la consola para permitir al usuario
    detener el script manualmente antes de una acción crítica.
    """
    print(f"\n--- ATENCIÓN: Acción de descarga inminente ---")
    for i in range(segundos, 0, -1):
        # El \r al principio y end="" hacen que el contador se actualice en la misma línea
        sys.stdout.write(f"\rTenés {i} segundos para parar el script (Ctrl+C o Stop)... ")
        sys.stdout.flush()
        time.sleep(1)
    
    # Limpiamos la línea al terminar la cuenta
    sys.stdout.write("\rContinuando con la ejecución...                         \n")
    sys.stdout.flush()

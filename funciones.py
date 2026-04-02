import os
from selenium import webdriver  
import time
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from random import randint
import opciones as op
import conf
import uuid
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def encontrar_Carpeta(opciones, url, seccion):
    
    driver = webdriver.Chrome(options=opciones)
    driver.implicitly_wait(12)
    driver.get(url)
    time.sleep(randint(1,5))
    
    botones = driver.find_elements(By.CSS_SELECTOR, ".md-button.ng-scope.md-ink-ripple")    #buscamos todos los botones
    #por cada boton, buscamos el que sirve para ver todas las secciones
    for boton in botones:
        if "VER TODOS" in boton.text.upper():
            try:
                boton.click()
                break
            except Exception:
                print("No se pudo acceder al boton para acceder a todas las secciones...\n")

    time.sleep(randint(1,5)) 
    
    try:
        seccion_por_recorrer = f"//*[contains(@class, 'pointer') and contains(., '{seccion}')]"
        
        carpeta_buscada = driver.find_element(By.XPATH, seccion_por_recorrer)
        carpeta_buscada.click()
        time.sleep(randint(1,5))
        
        #iniciamos la descarga de documentos
        recorrer_pagina(driver, seccion)
        
    except NoSuchElementException:
        print(f"Error: no se pudo encontrar ninguna carpeta que coincida con: '{seccion}'...\n")
    
    except Exception as e:
        print(f"Ocurrio un error inesperado al descargar los documentos de la carpeta: '{seccion}'...\n Error {e}")
                   
    
    
def recorrer_pagina(driver, seccion):
    print(f"Iniciando la descarga de los documentos de {seccion}\n")
    time.sleep(randint(1,3))
    primera_vuelta = True
    #calcular pagina
    if ultima_pagina_recorrida(seccion, driver):
    
        while(True):
            #recopilar elementos
            lista_filas = driver.find_elements(By.CSS_SELECTOR, "tr.md-row.ng-scope")
            cantidad_filas = len(lista_filas)
            #accedemos a cada uno
            for i in range(cantidad_filas):
                try:
                    lista_filas = driver.find_elements(By.CSS_SELECTOR, "tr.md-row.ng-scope")
                    elemento = lista_filas[i]
                    
                    id_pdf = str(uuid.uuid4())[:8]  #genera un id en hexa de 8 caracteres para que no se repitan los nombres de los pdf
                    metadatos = recopilar_metadatos(elemento, id_pdf)
                    descargar = False
                    
                    if primera_vuelta:
                        if not comparar_si_metadatos_existen(metadatos):
                            descargar = True        
                    else:
                        descargar = True
                    
                    if descargar:
                        if descargar_Documento(elemento, driver):
                            
                            #agregamos los metadatos al pandas
                            agregar_metadatos_pandas(metadatos)
                                    
                            #le cambiamos el nombre al archivo descargado
                            ruta_descarga = os.path.join(conf.DIRECTORIO_DESCARGAS, seccion)
                            op.renombrarArchivo(ruta_descarga, id_pdf)
                            ruta_absoluta = os.path.join(conf.DIRECTORIO_DESCARGAS, f"{id_pdf}.pdf")
                            print(f"PDF descargado en {ruta_absoluta}\n")
                            
                except Exception as e:
                    print(f"Error procesando la fila {i+1}: {e}\n")    
                       
            
            primera_vuelta = False        
                
            #avanzamos de pagina
            try:
                click_avanzar = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Next']")
        
                if click_avanzar.get_attribute("disabled"): #no hay mas paginas disponibles para recorrer
                    op.guardarindice(seccion, nuevo_indice=(op.cargarindice(seccion) + 1))
                    print(f"No hay mas paginas disponibles para recorrer dentro de la seccion {seccion}...\n")
                    break 
        
                click_avanzar.click()
                print("Siguiente pagina...\n")
                
                #actualizar archivo de pagina
                op.guardarindice(seccion, nuevo_indice=(op.cargarindice(seccion) + 1))
                time.sleep(5)

            except NoSuchElementException:  #no encontro el boton para seguir
                print(f"No hay mas paginas en la seccion {seccion}...\n")
                break
    
    else:
        print(f"No hay paginas nuevas para recorrer en la seccion {seccion}...\n")    
   

def descargar_Documento(elemento, driver): #True si pudo descargar el documeto
    
        
    #descargamos pdf  
    try:
        #accedemos donde se encuentra el documento
        click_PDF = elemento.find_element(By.CSS_SELECTOR, "i.fas.fa-arrow-circle-down.icon-button")
        #iniciamos la descarga
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", click_PDF)
        time.sleep(0.5)
        
        click_PDF.send_keys(Keys.ENTER)
        time.sleep(randint(2,5))
        return True
    
    except NoSuchElementException:
        print("No se encontro un PDF para descargar...\n")
        return False
    
    except Exception as e:
        print(f"Error real al intentar clickear el PDF: {e}\n")
        return False


def recopilar_metadatos(elemento, id_pdf):
    
    
    #recopilamos todos los td para poder extraer el texto por partes
    metadatos = elemento.find_elements(By.TAG_NAME, "td")
    
    tipo_documento = metadatos[0].text
    num_disposicion = metadatos[1].text
    estado = metadatos[2].text
    fecha = metadatos[3].text
    titulo = metadatos[5].text
    
    print(f"tipo de documento: {tipo_documento} / numero: {num_disposicion} / estado: {estado} / fecha: {fecha} / titulo: {titulo} / ID PDF : {id_pdf}\n")
    
    fila_completa = {
        "Tipo de documento": tipo_documento, 
        "Numero": num_disposicion,
        "Estado" : estado,
        "Fecha" : fecha,
        "Titulo" : titulo,
        "ID PDF" : id_pdf
        }
    
    return fila_completa

def agregar_metadatos_pandas(metadatos):
    #agregamos una nueva fila en nuestro .csv
    dataframe = op.pandasDataframe(conf.RUTA_METADATOS)
    dataframe.loc[len(dataframe)] = metadatos
    dataframe.to_csv(conf.RUTA_METADATOS, index=False, encoding="utf-8-sig")    #guarda las modificaciones 


def comparar_si_metadatos_existen(metadatos):   #True: si los datos existen el archivo
    dataframe = op.pandasDataframe(conf.RUTA_METADATOS)
    
    #agarra el diccionario y comparara, titulo, numero, fecha
    condicion = (
        (dataframe['Titulo'] == metadatos['Titulo']) &
        (dataframe['Numero'] == metadatos['Numero']) &
        (dataframe['Fecha'] == metadatos['Fecha'])
    )
    #retorna true si estan todos los datos dentro del archivo
    if not dataframe[condicion].empty:
        print(f"El documento {metadatos['Numero']} no se va a descargar porque ya fue descargado previamente\n")
        return True
    #en caso contrario false
    return False    
    
def ultima_pagina_recorrida(seccion, driver):
    
    #recuperar la ultima pagina que recorrio (archivo)
    
    valor = op.cargarindice(seccion)
    
    if valor == 0:  #no existen paginas recorridas de esta seccion
        op.guardarindice(seccion,1) #actualizamos el indice a 1
        print("Iniciando el scraping desde primer pagina\n")
        return True
    
    valor_string = str(valor)
    
    try:
        #abrir todas las opciones de paginas
        paginas = driver.find_element(By.CSS_SELECTOR, ".page-select.ng-scope")
        paginas.click()
        time.sleep(1)
        
        #selecciona el menu de opciones de paginas para poder scrollear sobre el
        menu_paginas = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".md-select-menu-container.md-active md-content[role='listbox']"))
        )
        time.sleep(0.5)
        
        #scrollea hacia abajo en el menu de las paginas para revelar todas las paginas disponibles
        for i in range(50):
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", menu_paginas)
            time.sleep(0.2)
        
        #obtenemos todos las opciones de paginas
        lista = driver.find_elements(By.CSS_SELECTOR, "md-option[ng-repeat='page in $pageSelect.pages']")
        #accedemos a la ultima pagina disponible
        ultima_pagina = lista[-1].get_attribute('value')    
        
        #si el valor almacenado en el indice es mayor a la ultima, significa que ya recorrio todos los elementos
        if valor > int(ultima_pagina):
            print(f"El ultimo indice guardado para la seccion {seccion} es {valor} y supera al ultimo de la pagina que es {ultima_pagina}\n")
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            return False
        #return false para cortar la ejecucion
        
        encontrado = False
        #recopilar la lista de paginas
        for opcion in lista:
            #verifica que encontremos el ultimo numero de pagina que recorrio
            if valor_string == opcion.get_attribute("value"):
                
                driver.execute_script("arguments[0].scrollIntoView(true);", opcion)
                time.sleep(0.5)
                #clickea la pagina que le indico el indice
                opcion.click()
                encontrado = True
                print(f"Iniciando descarga desde la pagina {valor_string}")
                
                time.sleep(4)
                return True
                
        if not encontrado:
            print(f"No se pudo encontrar la pagina {valor} en el menu...\n")
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    except Exception as e:
        print(f"Error al intentar cargar la pagina {valor}\n Error {e}")
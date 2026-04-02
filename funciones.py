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
                time.sleep(5)
                break
            except Exception:
                print("No se pudo acceder al boton para acceder a todas las secciones...\n")

    
    
    try:
        seccion_por_recorrer = f"//*[contains(@class, 'pointer') and contains(., '{seccion}')]"
        
        carpeta_buscada = driver.find_element(By.XPATH, seccion_por_recorrer)
        carpeta_buscada.click()
        time.sleep(randint(1,5))
        
        #iniciamos la descarga de documentos
        descargar_Documento(driver, seccion)
        
    except NoSuchElementException:
        print(f"Error: no se pudo encontrar ninguna carpeta que coincida con: '{seccion}'...\n")
    
    except Exception:
        print(f"OcurriO un error inesperado al intentar acceder a la carpeta: '{seccion}'...\n")
        
        
    """
     lista = driver.find_elements(By.CSS_SELECTOR, ".pointer.ng-scope.ng-isolate-scope") #busca todas las carpetas de documentos

    #por cada carpeta, buscamos cual coincide con la seccion que queres acceder

    for elemento in lista:

        if seccion in elemento.text:

            try:

                elemento.click()    #accedemos a la carpeta de documentos buscada

                time.sleep(randint(1,5))

                descargar_Documento(driver, seccion)    #iniciamos la descarga de documentos

                break

            except Exception:

                print("No se pudo encontrar la seccion...\n")
    """            
    
    
def descargar_Documento(driver, seccion):
    print(f"Iniciando la descarga de los documentos de {seccion}\n")
    time.sleep(5)
    #calcular pagina
    if ultima_pagina_recorrida(seccion, driver):
    
        while(True):
            #recopilar elementos
            lista = driver.find_elements(By.CSS_SELECTOR, "tr.md-row.ng-scope")
            #accedemos a cada uno
            for elemento in lista:
            
                id_pdf = str(uuid.uuid4())[:8]  #genera un id en hexa de 8 caracteres para que no se repitan los nombres de los pdf
                time.sleep(randint(2,5))
                #descargamos pdf
                try:
                    #accedemos donde se encuentra el documento
                    click_PDF = elemento.find_element(By.CSS_SELECTOR, "i.fas.fa-arrow-circle-down.icon-button")
                    #iniciamos la descarga
                    click_PDF.click()
                except NoSuchElementException:
                    print("No se pudo iniciar la descarga del PDF...\n")
                    id_pdf = "No se encontro archivo"

                #le cambiamos el nombre al archivo descargado
            
                ruta_descarga = os.path.join(conf.DIRECTORIO_DESCARGAS, seccion)
                op.renombrarArchivo(ruta_descarga, id_pdf)
            
                #recopilamos metadatos en un pandas
            
                recopilar_metadatos(elemento, id_pdf)
            
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
                
                time.sleep(2)
                return True
                
        if not encontrado:
            print(f"No se pudo encontrar la pagina {valor} en el menu...\n")
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    except:
        print(f"Error al intentar cargar la pagina {valor}")
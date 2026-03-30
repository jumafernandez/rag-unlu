import os
from selenium import webdriver  
import time
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from random import randint
import opciones as op
import conf
import uuid

def encontrar_Carpeta(opciones, url, seccion):
    
    driver = webdriver.Chrome(options=opciones)
    driver.implicitly_wait(8)
    driver.get(url)
    time.sleep(randint(1,3))
    
    botones = driver.find_elements(By.CSS_SELECTOR, ".md-button.ng-scope.md-ink-ripple")    #buscamos todos los botones
    #por cada boton, buscamos el que sirve para ver todas las secciones
    for boton in botones:
        if "VER TODOS" in boton.text :
            try:
                boton.click()
                break
            except Exception:
                print("No se pudo acceder al boton para acceder a todas las secciones...\n")

    
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
    
                
    
    
def descargar_Documento(driver, seccion):
    print(f"Iniciando la descarga de los documentos de {seccion}\n")
    
    #calcular pagina
    ultima_pagina_recorrida(seccion, driver)
    
    while(True):
        #recopilar elementos
        lista = driver.find_elements(By.CSS_SELECTOR, "tr.md-row.ng-scope")
        #accedemos a cada uno
        for elemento in lista:
        
            id_pdf = str(uuid.uuid4())[:8]  #genera un id en hexa de 8 caracteres para que no se repitan los nombres de los pdf
            time.sleep(randint(1,5))
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
                print(f"No hay mas paginas disponibles para recorrer dentro de la seccion {seccion}...\n")
                break 
    
            click_avanzar.click()
            print("Siguiente pagina...\n")
            
            #actualizar archivo de pagina
            
            time.sleep(5)

        except NoSuchElementException:  #no encontro el boton para seguir
            print(f"No hay mas paginas en la seccion {seccion}...\n")
            break
        
        
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
    
    #agregamos una nueva fila en nuestro .csv
    dataframe = op.pandasDataframe(conf.RUTA_METADATOS)
    dataframe.loc[len(dataframe)] = fila_completa
    dataframe.to_csv(conf.RUTA_METADATOS, index=False, encoding="utf-8-sig")    #guarda las modificaciones 
    
def ultima_pagina_recorrida(seccion, driver):

    #####################################
    
    #recuperar la ultima pagina que recorrio (archivo)
    
    #abrir todas las opciones de paginas
    
    #escrolear hacia abajo
    
    #obtener los elementos con la clase md-option[ng-repeat='page in $pageSelect.pages']
    
    #if valor > lista[len(lista)].value
        #break
        #print
    
    #recopilar la lista de paginas
    
        #if valor != 0
        
            
            #comparar con el numero de pagina que recuperamos
            
                #if valor == elemento.value

                    #click
                    #break
            
            #si llega aca es porq no lo encontro, tira error
        
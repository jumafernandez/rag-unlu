import os
from selenium import webdriver  
import time
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from random import randint
import opciones as op
import conf

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
                #time.sleep(1,5)
                descargar_Documento(driver, seccion)    #iniciamos la descarga de documentos
                break
            except Exception:
                print("No se pudo encontrar la seccion...\n")
    
                
    
    
def descargar_Documento(driver, seccion):
    print(f"Iniciando la descarga de los documentos de {seccion}\n")
    
    #recopilar elementos
    lista = driver.find_elements(By.CSS_SELECTOR, "tr.md-row.ng-scope")
    #accedemos a cada uno
    for elemento in lista:
        time.sleep(randint(1,3))
        #descargamos pdf
        try:
            #accedemos donde se encuentra el documento
            click_PDF = elemento.find_element(By.CSS_SELECTOR, "i.fas.fa-arrow-circle-down.icon-button")
            #iniciamos la descarga
            click_PDF.click()
        except Exception:
            print("No se pudo iniciar la descarga del PDF...\n")

        #le cambiamos el nombre
        
        #recopilamos metadatos en un pandas
        recopilar_metadatos(elemento, seccion)
        
def recopilar_metadatos(elemento, seccion):
    metadatos = elemento.find_elements(By.TAG_NAME, "td")
    
    tipo_documento = metadatos[0].text
    num_disposicion = metadatos[1].text
    estado = metadatos[2].text
    fecha = metadatos[3].text
    titulo = metadatos[5].text
    
    print(f"tipo de documento: {tipo_documento} / numero: {num_disposicion} / estado: {estado} / fecha: {fecha} / titulo: {titulo}")
    
    fila_completa = {
        "Tipo de documento": tipo_documento, 
        "Numero": num_disposicion,
        "Estado" : estado,
        "Fecha" : fecha,
        "Titulo" : titulo
        }
    
    dataframe = op.pandasDataframe(conf.RUTA_METADATOS)
    dataframe.loc[len(dataframe)] = fila_completa
    dataframe.to_csv(conf.RUTA_METADATOS, index=False, encoding="utf-8-sig")    #guarda las modificaciones 
    
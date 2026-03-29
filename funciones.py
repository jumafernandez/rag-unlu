import os
from selenium import webdriver  
import time
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from random import randint


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
                time.sleep(1,5)
                descargar_Documento(driver, seccion)    #iniciamos la descarga de documentos
                break
            except Exception:
                print("No se pudo encontrar la seccion...\n")
    
                
    
    
def descargar_Documento(driver, seccion):
    print("Iniciando la descarga de los documentos de {seccion}\n")
    
    #recopilar elementos
    
    #accedemos a cada uno
        #recopilamos metadatos en un pandas
        #descargamos pdf
        #le cambiamos el nombre
        
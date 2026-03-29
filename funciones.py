import os
from selenium import webdriver  
import time
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from random import randint


def descargar_Documento(opciones, url, seccion):
    
    driver = webdriver.Chrome(options=opciones)
    driver.implicitly_wait(4)
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
                elemento.click()
                break
            except Exception:
                print("No se pudo encontrar la seccion...\n")
                
    time.sleep(4)
    
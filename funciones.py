import os
from selenium import webdriver  
import time
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from random import randint


def descargar_Documento(opciones, url):
    
    driver = webdriver.Chrome(options=opciones)
    driver.implicitly_wait(8)
    driver.get(url)
    time.sleep(randint(1,5))
    
    boton_ver_todos = driver.find_element(By.PARTIAL_LINK_TEXT, "Ocultar")
    boton_ver_todos.click()
    
    
    lista = driver.find_elements(By.CSS_SELECTOR, "carpeta ng-isolate-scope _md")
    #for seccion in lista:
    
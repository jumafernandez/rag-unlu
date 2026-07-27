import collections
import os
import re
def analizar_errores_documentos(ruta_archivo):
    contador_tipos_error = collections.Counter()
    conteo_por_pdf = {}
    
    # Nuevo contador para los errores que aparecen solos en un documento
    contador_errores_solitarios = collections.Counter()

    try:
        with open(ruta_archivo, 'r', encoding='utf-8') as archivo:
            contenido = archivo.read()
    except FileNotFoundError:
        print(f"No se encontró el archivo. Ruta buscada: {os.path.abspath(ruta_archivo)}")
        return

    # Separamos el texto en bloques por cada documento
    fragmentos = contenido.split("Documento: ")[1:]

    for fragmento in fragmentos:
        fragmento_limpio = fragmento.replace('\n', ' ').replace('\r', ' ')
        
        if "|" in fragmento_limpio:
            partes = fragmento_limpio.split("|")
            nombre_pdf = partes[0].strip()
            
            if "Campos faltantes/erróneos:" in partes[1]:
                errores_str = partes[1].split("Campos faltantes/erróneos:")[1]
                lista_errores = [e.strip() for e in errores_str.split(",") if e.strip()]
                
                # Sumamos al conteo total
                contador_tipos_error.update(lista_errores)
                
                # Guardamos cuántos errores tuvo el PDF
                conteo_por_pdf[nombre_pdf] = len(lista_errores)
                
                # NUEVO ANÁLISIS: Si el documento tiene exactamente 1 error
                if len(lista_errores) == 1:
                    contador_errores_solitarios.update(lista_errores)

    # Ordenamos los PDFs de mayor a menor cantidad de errores
    top_pdfs = sorted(conteo_por_pdf.items(), key=lambda x: x[1], reverse=True)

    print("=== TIPOS DE ERROR MÁS FRECUENTES (EN TOTAL) ===")
    for error, cantidad in contador_tipos_error.most_common():
        print(f"- {error}: {cantidad} repeticiones")

    print("\n=== ERRORES ÚNICOS (APARECEN SOLOS EN EL PDF) ===")
    total_documentos_un_error = sum(contador_errores_solitarios.values())
    print(f"Total de documentos que tienen un único error: {total_documentos_un_error}")
    for error, cantidad in contador_errores_solitarios.most_common():
        print(f"- {error}: {cantidad} veces apareció como único fallo")

    print("\n=== TOP 10 PDFs CON MÁS ERRORES ===")
    for pdf, cantidad in top_pdfs[:10]:
        print(f"- {pdf}: {cantidad} errores")

# Construye la ruta combinando la carpeta interna y el archivo
ruta_final = os.path.join('resultados_extractor', 'docus_error.txt')


def contar_documentos_agrupados(ruta_carpeta):
    extensiones_validas = {'.yaml', '.json', '.md'}
    documentos_unicos = set()
    
    total_archivos_validos = 0

    # Recorremos la carpeta principal y todas sus subcarpetas
    for directorio_raiz, carpetas, archivos in os.walk(ruta_carpeta):
        for archivo in archivos:
            nombre_base, extension = os.path.splitext(archivo)
            
            if extension.lower() in extensiones_validas:
                total_archivos_validos += 1
                
                # Buscamos la primera secuencia de números en el nombre
                coincidencia = re.search(r'\d+', nombre_base)
                
                if coincidencia:
                    identificador = coincidencia.group()
                    documentos_unicos.add(identificador)
                else:
                    # Si por algún motivo el archivo no tiene números, guarda el nombre base
                    documentos_unicos.add(nombre_base)

    print(f"Total de archivos sueltos procesados (yaml, json, md): {total_archivos_validos}")
    print("-" * 50)
    print(f"Total de documentos ÚNICOS (agrupados por su ID numérico): {len(documentos_unicos)}")

# Ejecución
ruta_de_mi_carpeta = 'resultados_extractor' 
contar_documentos_agrupados(ruta_de_mi_carpeta)
# Ejecución del script
analizar_errores_documentos(ruta_final)


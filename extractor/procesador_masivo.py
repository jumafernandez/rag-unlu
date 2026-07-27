# Script para procesar todos los PDFs de una carpeta en lote con manejo de errores y logs.

import os
import signal
import subprocess
import logging
import sys
import random
import multiprocessing
import concurrent.futures
import time

# Configuración del log de errores
logging.basicConfig(
    filename='errores_extraccion.log',
    level=logging.ERROR,
    format='%(asctime)s - Archivo: %(message)s'
)

STOP_EVENT = None
MAIN_STOP_EVENT = None # Evento global controlado por el proceso principal para el manejo de señales


def init_worker(stop_event):
    global STOP_EVENT
    STOP_EVENT = stop_event


def registrar_escaneo(carpeta_resultados, archivo, detalle):
    """Guarda el PDF filtrado en docus_escaneos.txt dentro de la carpeta de resultados."""
    ruta_escaneos = os.path.join(carpeta_resultados, "docus_escaneos.txt")
    if os.path.exists(ruta_escaneos):
        with open(ruta_escaneos, "r", encoding="utf-8") as f:
            if any(line.startswith(f"{archivo} |") for line in f):
                return

    with open(ruta_escaneos, "a", encoding="utf-8") as f:
        f.write(f"{archivo} | {detalle}\n")


def _terminar_proceso_hijo(proceso):
    if proceso is None or proceso.poll() is not None:
        return

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proceso.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(proceso.pid, signal.SIGTERM)
    except Exception:
        try:
            proceso.terminate()
        except Exception:
            pass

    try:
        proceso.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proceso.kill()
        except Exception:
            pass


def _ejecutar_comando(comando, cwd, entorno=None):
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    proceso = subprocess.Popen(
        comando,
        cwd=cwd,
        env=entorno,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )

    try:
        while True:
            if STOP_EVENT is not None and STOP_EVENT.is_set():
                _terminar_proceso_hijo(proceso)
                raise KeyboardInterrupt("Interrupción solicitada")

            if proceso.poll() is not None:
                stdout, stderr = proceso.communicate()
                return stdout, stderr, proceso.returncode

            time.sleep(0.2)
    except KeyboardInterrupt:
        _terminar_proceso_hijo(proceso)
        raise
    except Exception:
        _terminar_proceso_hijo(proceso)
        raise


def procesar_un_archivo(archivo, ruta_origen_abs, carpeta_resultados, script_extractor, script_post_procesador):
    """Procesa un único PDF con la misma lógica del flujo actual."""
    ruta_completa_pdf = os.path.join(ruta_origen_abs, archivo)

    try:
        stdout, stderr, returncode = _ejecutar_comando(
            ["python", script_extractor, ruta_completa_pdf],
            cwd=carpeta_resultados,
        )

        if returncode != 0:
            stderr_text = (stderr or "").strip()
            if returncode == 2 or "FILTRADO" in stderr_text.upper() or "ESCANEO" in stderr_text.upper():
                registrar_escaneo(carpeta_resultados, archivo, stderr_text or "Escaneo puro")
                return "FILTRADO", archivo, stderr_text or "Escaneo puro"

            error_msg = f"{archivo} | Error del script: {stderr_text}"
            return "ERROR", archivo, error_msg

        nombre_base = os.path.splitext(archivo)[0]
        nombre_md = f"{nombre_base}.md"
        nombre_json = f"{nombre_base}.json"

        if os.path.exists(os.path.join(carpeta_resultados, name_md := nombre_md)) and os.path.exists(os.path.join(carpeta_resultados, nombre_json)):
            stdout, stderr, returncode = _ejecutar_comando(
                ["python", script_post_procesador, nombre_json, nombre_md],
                cwd=carpeta_resultados,
            )

            if returncode != 0:
                error_msg = f"{archivo} | Error del post-procesador: {(stderr or '').strip()}"
                return "ERROR", archivo, error_msg

            return "EXITO", archivo, None
        else:
            return "ERROR", archivo, "El extractor no generó los archivos MD o JSON esperados."

    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        if e.returncode == 2 or "FILTRADO" in stderr.upper() or "ESCANEO" in stderr.upper():
            registrar_escaneo(carpeta_resultados, archivo, stderr or "Escaneo puro")
            return "FILTRADO", archivo, stderr or "Escaneo puro"

        error_msg = f"{archivo} | Error del script: {stderr}"
        return "ERROR", archivo, error_msg
    except KeyboardInterrupt:
        raise
    except Exception as e:
        error_msg = f"{archivo} | Error inesperado: {str(e)}"
        return "ERROR", archivo, error_msg


def procesar_carpeta(ruta_destino, cantidad_aleatoria=None):
    global MAIN_STOP_EVENT
    # Definir las rutas ABSOLUTAS de los scripts para no perderlos de vista
    directorio_actual = os.getcwd()
    script_extractor = os.path.abspath("script_extractor.py")
    script_post_procesador = os.path.abspath("post_procesador.py")
    
    # Validar que la carpeta con PDFs existe
    if not os.path.exists(ruta_destino):
        print(f"Error: La ruta '{ruta_destino}' no existe.")
        return

    # Crear carpeta de resultados y obtener su ruta absoluta
    carpeta_resultados = os.path.abspath(os.path.join(directorio_actual, "resultados_extractor"))
    if not os.path.exists(carpeta_resultados):
        os.makedirs(carpeta_resultados)
    
    # Obtener lista de archivos PDF
    ruta_origen_abs = os.path.abspath(ruta_destino)
    archivos = [f for f in os.listdir(ruta_origen_abs) if f.lower().endswith('.pdf')]
    
    if not archivos:
        print(f"No se encontraron archivos PDF en '{ruta_destino}'.")
        return

    # Lógica para la selección aleatoria de X archivos
    if cantidad_aleatoria is not None:
        if cantidad_aleatoria >= len(archivos):
            print(f"Aviso: Solicitaste {cantidad_aleatoria} archivos, pero la carpeta solo contiene {len(archivos)}. Se procesarán todos.")
        else:
            archivos = random.sample(archivos, cantidad_aleatoria)
            print(f"🎲 Selección aleatoria activa: Se eligieron {cantidad_aleatoria} PDFs al azar.")

    print(f"--- Se encontraron {len(archivos)} archivos. Iniciando proceso masivo ---")
    
    exitos = 0
    escaneos = 0
    errores = 0

    MAIN_STOP_EVENT = multiprocessing.Event()

    if len(archivos) > 1:
        max_workers = max(1, min(len(archivos), (os.cpu_count() or 1) - 3))
        print(f"--- Iniciando procesamiento multinúcleo con {max_workers} workers ---")

        # El Executor se abre normalmente
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=init_worker,
            initargs=(MAIN_STOP_EVENT,),
        ) as executor:
            try:
                tareas = {
                    executor.submit(
                        procesar_un_archivo,
                        archivo,
                        ruta_origen_abs,
                        carpeta_resultados,
                        script_extractor,
                        script_post_procesador
                    ): archivo for archivo in archivos
                }

                for indice, futuro in enumerate(concurrent.futures.as_completed(tareas), start=1):
                    tipo_res, archivo_procesado, detalle = futuro.result()
                    porcentaje = (indice / len(archivos)) * 100

                    if tipo_res == "EXITO":
                        exitos += 1
                        estado = f"OK: {archivo_procesado}"
                    elif tipo_res == "FILTRADO":
                        escaneos += 1
                        estado = f"FILTRADO: {archivo_procesado}"
                    else:
                        errores += 1
                        logging.error(detalle)
                        estado = f"ERROR: {archivo_procesado}"

                    print(
                        f"\r[{indice}/{len(archivos)} - {porcentaje:.1f}%] {estado}",
                        end="",
                        flush=True
                    )
            # El bloque try/except ahora está ADENTRO del context manager del executor
            except KeyboardInterrupt:
                MAIN_STOP_EVENT.set() # Despierta instantáneamente el bucle de los subprocesos _ejecutar_comando
                print("\n[!] Interrupción recibida. Cancelando tareas pendientes y deteniendo procesos...")
                executor.shutdown(wait=False, cancel_futures=True) # Evita el bloqueo del bloque 'with'
                raise
    else:
        try:
            for indice, archivo in enumerate(archivos, start=1):
                tipo_res, archivo_procesado, detalle = procesar_un_archivo(
                    archivo,
                    ruta_origen_abs,
                    carpeta_resultados,
                    script_extractor,
                    script_post_procesador
                )

                porcentaje = (indice / len(archivos)) * 100
                if tipo_res == "EXITO":
                    exitos += 1
                    estado = f"OK: {archivo_procesado}"
                elif tipo_res == "FILTRADO":
                    escaneos += 1
                    estado = f"FILTRADO: {archivo_procesado}"
                else:
                    errores += 1
                    logging.error(detalle)
                    estado = f"ERROR: {archivo_procesado}"

                print(
                    f"\r[{indice}/{len(archivos)} - {porcentaje:.1f}%] {estado}",
                    end="",
                    flush=True
                )
        except KeyboardInterrupt:
            MAIN_STOP_EVENT.set()
            print("\n[!] Interrupción recibida. Deteniendo procesos hijos...")
            raise

    print(f"\n\n--- Proceso finalizado ---".ljust(60))
    print(f"Exitosos: {exitos}")
    print(f"Filtrados (escaneos): {escaneos}")
    print(f"Fallidos: {errores}")
    print(f"Resultados guardados en: {carpeta_resultados}")
    if errores > 0:
        print("Revisa el archivo 'errores_extraccion.log' para más detalles.")

def formatear_tiempo(segundos):
    """Convierte una cantidad de segundos al formato HH:MM:SS"""
    horas, rem = divmod(segundos, 3600)
    minutos, segundos = divmod(rem, 60)
    return f"{int(horas):02d}:{int(minutos):02d}:{int(segundos):02d}"


if __name__ == "__main__":
    # Configurar manejadores de señales para asegurar una captura limpia e inmediata
    def despachador_limpieza_senal(signum, frame):
        global MAIN_STOP_EVENT
        if MAIN_STOP_EVENT is not None:
            MAIN_STOP_EVENT.set()
        raise KeyboardInterrupt()

    # Escuchamos interrupciones de teclado y llamadas de terminación del sistema operativo
    signal.signal(signal.SIGINT, despachador_limpieza_senal)
    signal.signal(signal.SIGTERM, despachador_limpieza_senal)
    if os.name != "nt":
        signal.signal(signal.SIGHUP, despachador_limpieza_senal)

    try:
        if len(sys.argv) < 2:
            print("Uso: python procesador_masivo.py <ruta_de_la_carpeta_con_pdfs> [cantidad_aleatoria_X]")
        else:
            ruta_carpeta = sys.argv[1]
            x_cantidad = None
            
            if len(sys.argv) > 2:
                try:
                    x_cantidad = int(sys.argv[2])
                    if x_cantidad <= 0:
                        print("Error: El segundo parámetro (X) debe ser un número entero mayor a 0.")
                        sys.exit(1)
                except ValueError:
                    print("Error: El segundo parámetro (X) debe ser un número entero válido.")
                    sys.exit(1)
                    
            inicio = time.time()
            procesar_carpeta(ruta_carpeta, x_cantidad)
            tiempo_total = time.time() - inicio
            print(f"Proceso terminado en {formatear_tiempo(tiempo_total)} (HH:MM:SS).")
    except KeyboardInterrupt:
        print("\n[+] Ejecución interrumpida limpiamente. Todos los procesos e hilos asociados fueron destruidos.")
        sys.exit(130)
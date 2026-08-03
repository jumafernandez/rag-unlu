from __future__ import annotations

import argparse
from collections import Counter
import csv
import time
import unicodedata
from datetime import datetime
from pathlib import Path


CARPETA_DESCARGAS = Path(__file__).resolve().parent / "Descargas"
ARCHIVO_MAPEO = Path(__file__).resolve().parent / "mapeo_renombres.csv"
PREFIJO_TEMPORAL = "__renombrando__"
REINTENTOS_RENAME = 8
ESPERA_REINTENTO_SEGUNDOS = 2


def normalizar_prefijo(nombre: str) -> str:
    """Convierte el nombre de la carpeta en un prefijo seguro y en mayusculas."""
    sin_tildes = unicodedata.normalize("NFKD", nombre)
    sin_tildes = "".join(letra for letra in sin_tildes if not unicodedata.combining(letra))

    caracteres = []
    for letra in sin_tildes.upper():
        if letra.isalnum():
            caracteres.append(letra)
        else:
            caracteres.append("_")

    prefijo = "_".join(parte for parte in "".join(caracteres).split("_") if parte)
    return prefijo


def obtener_fecha_descarga(ruta: Path) -> float:
    # En Windows, st_ctime representa la fecha de creacion del archivo.
    return ruta.stat().st_ctime


def obtener_renombres(carpeta_descargas: Path) -> list[dict[str, str]]:
    renombres = []

    for carpeta in sorted(carpeta_descargas.iterdir(), key=lambda ruta: ruta.name.upper()):
        if not carpeta.is_dir():
            continue

        prefijo = normalizar_prefijo(carpeta.name)
        archivos_pdf = sorted(
            (archivo for archivo in carpeta.iterdir() if archivo.is_file() and archivo.suffix.lower() == ".pdf"),
            key=lambda archivo: (obtener_fecha_descarga(archivo), archivo.name.upper()),
        )

        for indice, archivo in enumerate(archivos_pdf, start=1):
            nuevo_nombre = f"{prefijo}_{indice}.pdf"
            nuevo_path = archivo.with_name(nuevo_nombre)

            renombres.append(
                {
                    "carpeta": carpeta.name,
                    "actual": str(archivo),
                    "nuevo": str(nuevo_path),
                    "fecha_descarga": datetime.fromtimestamp(obtener_fecha_descarga(archivo)).isoformat(sep=" ", timespec="seconds"),
                }
            )

    return renombres


def guardar_mapeo(renombres: list[dict[str, str]], archivo_mapeo: Path) -> None:
    with archivo_mapeo.open("w", newline="", encoding="utf-8") as archivo_csv:
        campos = ["carpeta", "actual", "nuevo", "fecha_descarga"]
        writer = csv.DictWriter(archivo_csv, fieldnames=campos)
        writer.writeheader()
        writer.writerows(renombres)


def cargar_mapeo(archivo_mapeo: Path) -> list[dict[str, str]]:
    with archivo_mapeo.open("r", newline="", encoding="utf-8") as archivo_csv:
        return list(csv.DictReader(archivo_csv))


def mostrar_vista_previa(renombres: list[dict[str, str]], limite: int) -> None:
    print(f"PDF encontrados: {len(renombres)}")
    print("Vista previa de renombres:")

    for renombre in renombres[:limite]:
        actual = Path(renombre["actual"]).name
        nuevo = Path(renombre["nuevo"]).name
        print(f"- [{renombre['carpeta']}] {actual} -> {nuevo}")

    if len(renombres) > limite:
        print(f"... y {len(renombres) - limite} renombres mas.")


def validar_destinos(renombres: list[dict[str, str]]) -> None:
    destinos = [renombre["nuevo"].lower() for renombre in renombres]
    duplicados = {destino for destino, cantidad in Counter(destinos).items() if cantidad > 1}
    if duplicados:
        raise RuntimeError("Hay nombres finales duplicados. No se renombro ningun archivo.")


def ruta_temporal(renombre: dict[str, str], indice: int) -> Path:
    actual = Path(renombre["actual"])
    return actual.with_name(f"{PREFIJO_TEMPORAL}{indice:06d}__{actual.name}")


def renombrar_con_reintentos(origen: Path, destino: Path) -> None:
    ultimo_error = None

    for intento in range(1, REINTENTOS_RENAME + 1):
        try:
            origen.rename(destino)
            return
        except PermissionError as error:
            ultimo_error = error
            print(
                f"Archivo bloqueado, reintento {intento}/{REINTENTOS_RENAME}: "
                f"{origen.name}"
            )
            time.sleep(ESPERA_REINTENTO_SEGUNDOS)

    raise ultimo_error


def hay_temporales(carpeta_descargas: Path) -> bool:
    return any(carpeta_descargas.rglob(f"{PREFIJO_TEMPORAL}*.pdf"))


def completar_desde_temporales(renombres: list[dict[str, str]]) -> None:
    pendientes = 0

    for indice, renombre in enumerate(renombres, start=1):
        temporal = ruta_temporal(renombre, indice)
        destino = Path(renombre["nuevo"])

        if destino.exists() and not temporal.exists():
            continue

        if temporal.exists() and destino.exists():
            raise RuntimeError(
                f"Existen a la vez el temporal y el destino final: {temporal} -> {destino}"
            )

        if temporal.exists():
            renombrar_con_reintentos(temporal, destino)
            pendientes += 1

    print(f"Renombres pendientes completados: {pendientes}")


def aplicar_renombres(renombres: list[dict[str, str]]) -> None:
    validar_destinos(renombres)

    temporales = []
    for indice, renombre in enumerate(renombres, start=1):
        actual = Path(renombre["actual"])
        temporal = ruta_temporal(renombre, indice)
        renombrar_con_reintentos(actual, temporal)
        temporales.append((temporal, Path(renombre["nuevo"])))

    for temporal, destino in temporales:
        renombrar_con_reintentos(temporal, destino)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Renombra los PDF de Descargas por carpeta y fecha de descarga."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica los renombres. Sin esta opcion solo muestra una vista previa.",
    )
    parser.add_argument(
        "--descargas",
        type=Path,
        default=CARPETA_DESCARGAS,
        help="Ruta de la carpeta Descargas.",
    )
    parser.add_argument(
        "--mapeo",
        type=Path,
        default=ARCHIVO_MAPEO,
        help="Archivo CSV donde se guarda el mapeo de renombres.",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=30,
        help="Cantidad de renombres a mostrar en la vista previa.",
    )
    args = parser.parse_args()

    carpeta_descargas = args.descargas.resolve()
    if not carpeta_descargas.exists():
        raise FileNotFoundError(f"No existe la carpeta Descargas: {carpeta_descargas}")

    if args.apply and hay_temporales(carpeta_descargas):
        print("Se detectaron archivos temporales de una ejecucion anterior.")
        if not args.mapeo.exists():
            raise FileNotFoundError(
                f"No se puede retomar sin el archivo de mapeo: {args.mapeo}"
            )

        renombres = cargar_mapeo(args.mapeo)
        completar_desde_temporales(renombres)
        print("Renombres aplicados correctamente.")
        return

    renombres = obtener_renombres(carpeta_descargas)
    mostrar_vista_previa(renombres, args.limite)
    guardar_mapeo(renombres, args.mapeo)
    print(f"Mapeo guardado en: {args.mapeo.resolve()}")

    if not args.apply:
        print("No se aplicaron cambios. Ejecuta con --apply para renombrar los archivos.")
        return

    aplicar_renombres(renombres)
    print("Renombres aplicados correctamente.")


if __name__ == "__main__":
    main()

import json
import os
import sys
import re
import yaml # Requiere instalar la librería: pip install pyyaml
import Levenshtein # <-- NUEVO IMPORT

def _representar_str_yaml(dumper, data):
    """
    Strings con saltos de linea reales (ej. content_markdown) se emiten en
    bloque literal ('|'), preservando cada linea tal cual sin envolver por
    ancho. Strings de una sola linea (ej. issuing_body) usan el estilo por
    defecto, combinado con un width grande para que nunca se corten a la mitad.
    """
    style = '|' if '\n' in data else None
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style=style)

yaml.add_representer(str, _representar_str_yaml)

# <-- NUEVA LISTA DE CANDIDATOS FIJOS
CANDIDATOS_FIJOS = [
  "El Consejo Directivo Departamental",
  "El Centro De Investigación Docencia y Extensión En Producción Agropecuaria De La Universidad Nacional De Luján",
  "El Consejo Directivo Departamental De Ciencias Básicas",
  "La Directora Decana Del Departamento De Ciencias Básicas",
  "La Presidente Del Consejo Directivo Departamental De Ciencias Básicas",
  "El Presidente Del Consejo Directivo Departamental De Ciencias Básicas",
  "El Consejo Directivo Del Departamento De Ciencias Sociales",
  "El Director Decano Del Departamento De Ciencias Sociales",
  "El Presidente Del Consejo Directivo Del Departamento De Ciencias Sociales",
  "El Director Decano Del Departamento Académico De Ciencias Sociales",
  "La Vice Directora Decana Del Departamento De Ciencias Sociales",
  "El Presidente Del Consejo Departamental Del Departamento De Ciencias Sociales",
  "El Consejo Directivo Del Departamento De Educación",
  "La Directora Decana Del Departamento De Educación",
  "La Presidenta Del Consejo Directivo Del Departamento De Educación",
  "El Director Decano Del Departamento De Educación",
  "El Presidente Del Consejo Directivo Del Departamento De Educación",
  "El Director Decano Del Departamento De Educacion",
  "El Consejo Directivo Del Departamento De Tecnología",
  "La Directora Decana Del Departamento De Tecnología",
  "La Vicedirectora Decana Del Departamento De Tecnología",
  "El Consejo Directivo Del Departamento De Tecnologia",
  "La Presidenta Del Consejo Directivo Del Departamento De Tecnología",
  "La Vicerrectora Decana Del Departamento De Tecnología",
  "La Videdirectora Decana Del Departamento De Tecnología",
  "La Vidirectora Decana Del Departamento De Tecnología",
  "La Directora General De Administración Nacional De Luján",
  "El Director General De Administración Nacional De Luján",
  "El Director General De Administración Económico Financiera",
  "El Director General De Extensión",
  "La Directora De La Dirección De Gestión De Rectorado",
  "El Director General De Sistemas",
  "La Dirección De Gestión De Rectorado",
  "La Dirección General De Asuntos Académicos",
  "La Asamblea Universitaria De La Universidad Nacional De Luján",
  "El H. Consejo Superior De La Universidad Nacional De Luján",
  "El Presidente Del H. Consejo Superior De La Universidad Nacional De Luján",
  "El Rector De La Universidad Nacional De Luján",
  "El Secretario Académico De La Universidad Nacional De Luján",
  "El Secretario De Administración De La Universidad Nacional De Luján",
  "El Secretario De Ciencia Y Tecnología De La Universidad Nacional De Luján",
  "El Secretario De Posgrado, De Cooperación Internacional E Internacionalización De La Universidad Nacional De Luján",
  "El Secretario De Bienestar Universitario Y Asuntos Estudiantiles De La Universidad Nacional De Luján"
]

def extraer_codigo_desde_encabezado_md(contenido_md):
    """
    Prioriza encabezados formales del portal, por ejemplo:
    DISPOSICION ... DISPCD-T : 441 / 2024
    """
    lineas = [l.strip() for l in contenido_md.split('\n') if l.strip()]
    patron_encabezado_portal = re.compile(
        r'^\s*(?:#\s*)?(?:DISPOSICI[ÓO]N|RESOLUCI[ÓO]N)\b'
        r'.*?\b([A-Z]{2,}(?:-[A-Z0-9]+)*)\s*:\s*(\d+)\s*(?:[\/\-])\s*(\d{2,4})\b',
        re.IGNORECASE
    )
    patron_encabezado_generado = re.compile(
        r'^\s*(?:#\s*)?(?:DISPOSICI[ÓO]N|RESOLUCI[ÓO]N)\s+'
        r'([A-Z]{1,8}(?:\.[A-Z]{1,8})*|[A-Z]{2,}(?:-[A-Z0-9]+)*)'
        r'\s*[-:]\s*0*(\d+)\s*(?:[\/\-])\s*(\d{2,4})\b',
        re.IGNORECASE
    )

    for linea in lineas[:12]:
        if re.search(r'\b(VISTO|CONSIDERANDO|ART[IÍ]CULO)\b', linea, re.IGNORECASE):
            break

        match = patron_encabezado_portal.search(linea)
        if not match:
            match = patron_encabezado_generado.search(linea)
        if match:
            codigo = match.group(1).strip().rstrip(".")
            numero = match.group(2).strip()
            anio = match.group(3).strip()
            return codigo, f"{numero}/{anio}"

    return None, None

def normalizar_espacios(valor):
    return re.sub(r'\s+', ' ', str(valor)).strip()

def es_linea_contexto_emisor(linea):
    linea = normalizar_espacios(linea).lstrip("#").strip()
    if not linea:
        return False
    if re.match(r'^(por\s+ello|universidad\s+nacional|rep[uú]blica|luj[aá]n|buenos\s+aires)', linea, re.IGNORECASE):
        return False
    if re.search(r'^(?:["“]|19\d{2}|20\d{2})', linea):
        return False
    if re.search(r'(reapertura\s+de\s+la\s+universidad|reconocimiento\s+constitucional|autonom[ií]a\s+universitaria)', linea, re.IGNORECASE):
        return False
    return True

def limpiar_emisor_desde_bloque(candidato, document_type):
    emisor = normalizar_espacios(candidato)
    emisor = re.sub(r'\bD\s*I\s*S\s*P\s*O\s*N\s*E\b.*$', '', emisor, flags=re.IGNORECASE)
    emisor = re.sub(r'\bR\s*E\s*S\s*U\s*E\s*L\s*V\s*E\b.*$', '', emisor, flags=re.IGNORECASE)
    emisor = re.sub(r'^(?:por\s+ello[,;:]?\s*)+', '', emisor, flags=re.IGNORECASE)
    emisor = re.sub(r'^(?:el|la)\s+', '', emisor, flags=re.IGNORECASE)
    emisor = re.sub(
        r'\s+DE\s+LA\s+UNIVERSIDAD\s+NACIONAL\s+DE\s+LUJ[ÁA]N\s*$',
        '',
        emisor,
        flags=re.IGNORECASE
    )
    emisor = re.sub(r'\s+UNIVERSIDAD\s+NACIONAL\s+DE\s+LUJ[ÁA]N.*$', '', emisor, flags=re.IGNORECASE)
    emisor = re.sub(r'\s+DE\s+LA\s*$', '', emisor, flags=re.IGNORECASE)
    emisor = limpiar_emisor_detectado(emisor)

    if re.match(r'^RECTOR(?:A)?\s*(?:DE\s+LA\s+UNIVERSIDAD)?', emisor, re.IGNORECASE):
        tipo = "RESOLUCION" if document_type == "resolucion" else "DISPOSICION"
        return f"{tipo} RECTOR"

    emisor = re.sub(r'^DIRECTOR(?:A)?\s+GENERAL\b', 'DIRECCION GENERAL', emisor, flags=re.IGNORECASE)
    emisor = re.sub(r'^SECRETARIO\b', 'SECRETARIA', emisor, flags=re.IGNORECASE)
    emisor = re.sub(r'^SUBSECRETARIO\b', 'SUBSECRETARIA', emisor, flags=re.IGNORECASE)
    emisor = re.sub(r'^(SECRETARIA\s+)ACAD[ÉE]MICO\b', r'\1ACADÉMICA', emisor, flags=re.IGNORECASE)

    return normalizar_espacios(emisor).upper()

def extraer_emisor_generico_desde_preambulo(preambulo, document_type):
    """
    Extrae la frase de autoridad ubicada despues del ultimo "Por ello".
    Evita depender de una lista cerrada de organismos.
    """
    matches = list(re.finditer(r'\bpor\s+ello\b[,;:]?', preambulo, re.IGNORECASE))
    if not matches:
        return None

    bloque = preambulo[matches[-1].end():]
    lineas = []
    for linea in bloque.splitlines():
        limpia = normalizar_espacios(linea).lstrip("#").strip()
        if re.match(r'^(?:Art[íi]culo|Parte\s+(?:resolutiva|dispositiva)|Firmas|Hoja\s+de\s+firmas)\b', limpia, re.IGNORECASE):
            break
        if lineas and re.search(r'\bUniversidad\s+Nacional\s+de\s+Luj[áa]n\b', limpia, re.IGNORECASE):
            limpia = re.split(r'\bUniversidad\s+Nacional\s+de\s+Luj[áa]n\b', limpia, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if limpia and es_linea_contexto_emisor(limpia):
                lineas.append(limpia)
            break
        if (
            lineas
            and re.match(r'^Departamento\s+de\b', limpia, re.IGNORECASE)
            and not re.search(r'\b(de|del)$', lineas[-1], re.IGNORECASE)
        ):
            break
        if es_linea_contexto_emisor(limpia):
            lineas.append(limpia)

    if not lineas:
        return None

    inicio_autoridad = re.compile(
        r'^(?:EL|LA)\s+'
        r'(?:RECTOR(?:A)?|VICERRECTOR(?:A)?|ASAMBLEA|CONSEJO|'
        r'DIRECTOR(?:A)?|VICEDIRECTOR(?:A)?|DECANO|DECANA|'
        r'SECRETAR\S*|SUBSECRETAR\S*|DIRECCI[OÓ]N|CENTRO|DEPARTAMENTO)\b',
        re.IGNORECASE
    )
    for idx in range(len(lineas) - 1, -1, -1):
        if inicio_autoridad.search(lineas[idx]):
            candidato = normalizar_espacios(" ".join(lineas[idx:idx + 4]))
            return limpiar_emisor_desde_bloque(candidato, document_type)

    candidato = normalizar_espacios(" ".join(lineas[-6:]))
    if not re.search(r'\bsubsecretari[oa]\b', candidato, re.IGNORECASE) and not re.search(
        r'\b(rector|vicerrector|asamblea|consejo|director|directora|vicedirector|vicedirectora|decano|decana|secretari[oa]|direcci[oó]n|centro|departamento)\b',
        candidato,
        re.IGNORECASE
    ):
        return None

    return limpiar_emisor_desde_bloque(candidato, document_type)

def extraer_emisor_desde_encabezado_md(contenido_md):
    """
    Busca el organo emisor en las primeras lineas, antes del cuerpo del acto.
    """
    lineas = [normalizar_espacios(l) for l in contenido_md.split('\n') if l.strip()]
    encabezado = " ".join(lineas[:20])
    encabezado = re.split(r'\b(VISTO|CONSIDERANDO|ART[IÃ]CULO)\b', encabezado, maxsplit=1, flags=re.IGNORECASE)[0]

    patrones = [
        r'\bCONSEJO\s+DIRECTIVO\s+DEL\s+DEPARTAMENTO\s+DE\s+[A-ZÃÃ‰ÃÃ“ÃšÃ‘a-zÃ¡Ã©Ã­Ã³ÃºÃ¼Ã±\s]+?(?=\s+(?:DISP|RES|LUJ[AÃ]N|N[Â°Âº]|\d|$))',
        r'\bH\.\s*CONSEJO\s+SUPERIOR\b',
        r'\bCONSEJO\s+DIRECTIVO\b',
        r'\bRECTORADO\b',
    ]

    for patron in patrones:
        match = re.search(patron, encabezado, re.IGNORECASE)
        if match:
            emisor = normalizar_espacios(match.group(0))
            return emisor.upper()

    return None

def extraer_emisor_antes_parte_dispositiva(contenido_md, document_type):
    """
    Prioriza la frase institucional inmediatamente anterior a RESUELVE/DISPONE.
    Ejemplos:
    EL RECTOR DE LA UNIVERSIDAD NACIONAL DE LUJAN
    EL CONSEJO DIRECTIVO DEL DEPARTAMENTO DE CIENCIAS SOCIALES
    """
    partes = re.split(r'##\s+Parte\s+(?:resolutiva|dispositiva)\b', contenido_md, maxsplit=1, flags=re.IGNORECASE)
    if len(partes) >= 2:
        preambulo = partes[0]
    else:
        partes = re.split(r'(?:###\s*)?Art[íi]culo\s+1\b', contenido_md, maxsplit=1, flags=re.IGNORECASE)
        if len(partes) < 2:
            preambulo = contenido_md
        else:
            preambulo = partes[0]

    if not preambulo.strip():
        return None

    emisor_generico = extraer_emisor_generico_desde_preambulo(preambulo, document_type)
    if emisor_generico:
        return emisor_generico

    contexto = normalizar_espacios(preambulo[-2500:])
    match_director_general = re.search(
        r'\b(?:EL|LA)\s+DIRECTOR(?:A)?\s+GENERAL\s+DE\s+'
        r'([A-ZÁÉÍÓÚÑa-záéíóúüñ\s]+?)(?=\s+DE\s+LA\s+UNIVERSIDAD)',
        contexto,
        re.IGNORECASE
    )
    if match_director_general:
        return f"DIRECCION GENERAL DE {normalizar_espacios(match_director_general.group(1))}".upper()

    match_centro_universidad = re.search(
        r'\b(?:EL\s+)?(CENTRO\s+DE\s+[A-ZÁÉÍÓÚÑa-záéíóúüñ\s]+?)(?=\s+DE\s+LA\s+UNIVERSIDAD)',
        contexto,
        re.IGNORECASE
    )
    if match_centro_universidad:
        return normalizar_espacios(match_centro_universidad.group(1)).upper()

    lineas_previas = [
        normalizar_espacios(l).lstrip("#").strip()
        for l in preambulo.splitlines()
        if normalizar_espacios(l).lstrip("#").strip()
    ]
    for linea in reversed(lineas_previas[-25:]):
        if re.match(r'^(?:DE\s+LA|UNIVERSIDAD\s+NACIONAL|REP[UÚ]BLICA|POR\s+ELLO)', linea, re.IGNORECASE):
            continue

        match = re.match(r'^LA\s+DIRECCI[OÓ]N\s+GENERAL\s+DE\s+(.+)$', linea, re.IGNORECASE)
        if match:
            return limpiar_emisor_detectado(re.sub(r'^LA\s+', '', linea, flags=re.IGNORECASE)).upper()

        match = re.match(r'^(?:EL|LA)\s+DIRECTOR(?:A)?\s+GENERAL\s+DE\s+(.+)$', linea, re.IGNORECASE)
        if match:
            return f"DIRECCION GENERAL DE {normalizar_espacios(match.group(1))}".upper()

        match = re.match(r'^EL\s+CONSEJO\s+DIRECTIVO\s+DEL\s+DEPARTAMENTO\s+DE\s+(.+)$', linea, re.IGNORECASE)
        if match:
            return limpiar_emisor_detectado(re.sub(r'^EL\s+', '', linea, flags=re.IGNORECASE)).upper()

        match = re.match(r'^(?:EL\s+)?CENTRO\s+DE\s+(.+)$', linea, re.IGNORECASE)
        if match:
            return limpiar_emisor_detectado(re.sub(r'^EL\s+', '', linea, flags=re.IGNORECASE)).upper()

        match = re.match(r'^(?:EL|LA)\s+(?:SUBSECRETAR\S*|SECRETAR\S*)\s+(.+)$', linea, re.IGNORECASE)
        if match:
            return limpiar_emisor_desde_bloque(linea, document_type)

        if re.match(r'^(?:EL|LA)\s+RECTOR(?:A)?\s+DE\s+LA\s+UNIVERSIDAD\s+NACIONAL\s+DE\s+LUJ', linea, re.IGNORECASE):
            tipo = "RESOLUCION" if document_type == "resolucion" else "DISPOSICION"
            return f"{tipo} RECTOR"

    patrones = [
        (
            r'\bLA\s+DIRECCI[OÓ]N\s+GENERAL\s+DE\s+'
            r'[A-ZÁÉÍÓÚÑa-záéíóúüñ\s]+?(?=\s+(?:D\s*I\s*S\s*P\s*O\s*N\s*E|DISPONE)|$)'
        ),
        (
            r'\b(?:EL|LA)\s+DIRECTOR(?:A)?\s+GENERAL\s+DE\s+'
            r'[A-ZÁÉÍÓÚÑa-záéíóúüñ\s]+?(?=\s+DE\s+LA\s+UNIVERSIDAD|\s+(?:D\s*I\s*S\s*P\s*O\s*N\s*E|DISPONE)|$)'
        ),
        (
            r'\bEL\s+CONSEJO\s+DIRECTIVO\s+DEL\s+DEPARTAMENTO\s+DE\s+'
            r'[A-ZÃÃ‰ÃÃ“ÃšÃ‘a-zÃ¡Ã©Ã­Ã³ÃºÃ¼Ã±\s]+?(?=\s+(?:D\s*I\s*S\s*P\s*O\s*N\s*E|R\s*E\s*S\s*U\s*E\s*L\s*V\s*E)|$)'
        ),
        r'\b(?:EL|LA)\s+RECTOR(?:A)?\s+DE\s+LA\s+UNIVERSIDAD\s+NACIONAL\s+DE\s+LUJ[AÃ]N\b',
        r'\bEL\s+H\.\s*CONSEJO\s+SUPERIOR\b',
        r'\bEL\s+CONSEJO\s+DIRECTIVO\b',
    ]

    for patron in patrones:
        matches = list(re.finditer(patron, contexto, re.IGNORECASE))
        if not matches:
            continue

        emisor = limpiar_emisor_detectado(matches[-1].group(0))
        emisor = re.sub(r'^(?:EL|LA)\s+', '', emisor, flags=re.IGNORECASE)
        emisor = re.sub(r'^DIRECTOR(?:A)?\s+GENERAL\b', 'DIRECCION GENERAL', emisor, flags=re.IGNORECASE)

        if re.match(r'^RECTOR(?:A)?\s+DE\s+LA\s+UNIVERSIDAD\s+NACIONAL\s+DE\s+LUJ', emisor, re.IGNORECASE):
            tipo = "RESOLUCION" if document_type == "resolucion" else "DISPOSICION"
            return f"{tipo} RECTOR"

        return emisor.upper()

    return None

def inferir_emisor_desde_codigo(document_code, contenido_md):
    codigo = normalizar_espacios(document_code).upper()

    if codigo in {"R", "RR"} or codigo.startswith("RESREC"):
        return "RESOLUCION RECTOR"

    if codigo in {"A.U", "AU"}:
        return "ASAMBLEA UNIVERSITARIA"

    if codigo.startswith("DISPCD"):
        emisor_encabezado = extraer_emisor_desde_encabezado_md(contenido_md)
        if emisor_encabezado and "CONSEJO DIRECTIVO" in emisor_encabezado:
            return emisor_encabezado
        return "CONSEJO DIRECTIVO"

    if codigo.startswith("RESPHCS") or codigo.startswith("RESHCS"):
        return "H. CONSEJO SUPERIOR"

    emisores_por_codigo = {
        "DGAA": "DIRECCION GENERAL DE ASUNTOS ACADEMICOS",
        "DGAEF": "DIRECCION GENERAL DE ADMINISTRACION ECONOMICO FINANCIERA",
        "DGP": "DIRECCION GENERAL DE PERSONAL",
    }
    if codigo in emisores_por_codigo:
        return emisores_por_codigo[codigo]

    return None

def limpiar_emisor_detectado(candidato):
    emisor = normalizar_espacios(candidato)
    emisor = re.sub(
        r'\s+(?:DISPCD|RESPCD|RESHCS|RR|DISP|RES|RESOLUCI[Ã“O]N|DISPOSICI[Ã“O]N)\b.*$',
        '',
        emisor,
        flags=re.IGNORECASE
    )
    return normalizar_espacios(emisor)

def es_candidato_emisor_ruidoso(candidato):
    texto = limpiar_emisor_detectado(candidato).lower()
    if not texto:
        return True
    if texto.startswith("departamento de ") and "consejo directivo" not in texto:
        return True
    return False

def resolver_issuing_body(entidades_brutas, document_code, contenido_md, document_type):
    codigo = normalizar_espacios(document_code).upper()
    issuing_cands = entidades_brutas.get("issuing_body_candidates", [])

    emisor_antes_parte = extraer_emisor_antes_parte_dispositiva(contenido_md, document_type)
    if emisor_antes_parte:
        return emisor_antes_parte

    if codigo.startswith("DISPCD"):
        for candidato in issuing_cands:
            emisor = limpiar_emisor_detectado(candidato)
            if re.search(r'\bCONSEJO\s+DIRECTIVO\s+DEL\s+DEPARTAMENTO\s+DE\b', emisor, re.IGNORECASE):
                return emisor.upper()

    emisor_por_codigo = inferir_emisor_desde_codigo(document_code, contenido_md)
    if emisor_por_codigo:
        return emisor_por_codigo

    emisor_encabezado = extraer_emisor_desde_encabezado_md(contenido_md)
    if emisor_encabezado:
        return emisor_encabezado

    for candidato in issuing_cands:
        if not es_candidato_emisor_ruidoso(candidato):
            return limpiar_emisor_detectado(candidato)

    return "unknown"

def normalizar_ciudad(valor):
    valor = re.sub(r'\s+', ' ', str(valor)).strip(" ,")
    if not valor:
        return "unknown"

    partes = [p.strip().upper() for p in valor.split(",") if p.strip()]
    partes_normalizadas = ["LUJÁN" if p == "LUJAN" else p for p in partes]
    return ", ".join(partes_normalizadas) if partes_normalizadas else "unknown"

def extraer_ciudad_desde_md(contenido_md):
    patron_ciudad = re.compile(
        r'^\s*(?:#\s*)?'
        #verificar que esto tome nombres completamente en mayusculas
        r'(Luj[aá]n|Campana|Chivilcoy|San\s+Miguel|CABA|Buenos\s+Aires|Capital\s+Federal)' 
        r'(?:\s*,\s*(Buenos\s+Aires))?'
        r'\s*,?\s*'
        r'(?:\d{1,2}\s*(?:de\s*)?(?:[a-zA-ZáéíóúÁÉÍÓÚ]{3,10})\s*(?:de\s*)?\d{2,4}'
        r'|[a-zA-ZáéíóúÁÉÍÓÚ]{3,10}\s*(?:de\s*)?\d{2,4}'
        r'|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})?'
        r'\s*(?:\.-)?\s*$',
        re.IGNORECASE
    )

    for linea in contenido_md.splitlines()[:12]:
        match = patron_ciudad.search(linea.strip())
        if match:
            ciudad = normalizar_ciudad(match.group(1))
            provincia = normalizar_ciudad(match.group(2)) if match.group(2) else ""
            if provincia == "BUENOS AIRES" and ciudad != "BUENOS AIRES":
                return f"{ciudad}, BUENOS AIRES"
            return ciudad

    return "unknown"

def procesar_metadatos(json_data, contenido_md):
    """
    Toma los 'hints' del JSON y el texto del Markdown para tomar
    las decisiones definitivas y normalizar la metadata.
    """
    # 1. Definir el tipo de documento (Resolución, Disposición o Unknown)
    doc_id_hint = json_data.get("document_id_hint", "unknown").lower()
    
    # NUEVA LÓGICA: Extraemos la primera línea relevante del MD para inspeccionarla
    lineas_md = [l.strip() for l in contenido_md.split('\n') if l.strip()]
    primera_linea = lineas_md[0].lower() if lineas_md else ""

    # Prioridad 1: Hint del ID de documento
    if doc_id_hint.startswith("res_"):
        document_type = "resolucion"
    elif doc_id_hint.startswith("disp_"):
        document_type = "disposicion"
    elif doc_id_hint.startswith("oc_"):
        document_type = "orden_compra"

    # Prioridad 2: Inspección de la primera línea del MD (títulos #)
    elif "resolución" in primera_linea or "resolucion" in primera_linea:
        document_type = "resolucion"
    elif "disposición" in primera_linea or "disposicion" in primera_linea:
        document_type = "disposicion"
    elif "orden de compra" in primera_linea:
        document_type = "orden_compra"

    else:
        # Prioridad 3: Lógica de respaldo en candidatos detectados
        candidates = json_data.get("detected_entities", {}).get("document_code_candidates", [])
        if candidates:
            primer_cand = candidates[0].lower()
            if "resolución" in primer_cand or "resolucion" in primer_cand:
                document_type = "resolucion"
            elif "disposición" in primer_cand or "disposicion" in primer_cand:
                document_type = "disposicion"
            else:
                document_type = "unknown"
        else:
            document_type = "unknown"

    # 2. Extraer Código y Número del documento principal
    candidates = json_data.get("detected_entities", {}).get("document_code_candidates", [])
    document_code, document_number = extraer_codigo_desde_encabezado_md(contenido_md)

    if (not document_code or not document_number) and candidates:
        partes = candidates[0].split(":")
        if len(partes) >= 2:
            document_code = partes[0].split(" ")[-1].strip()
            document_number = partes[1].strip()

    document_code = document_code or "unknown"
    document_number = document_number or "unknown"

    # 3. Normalizar Fecha y Año
    date_cands = json_data.get("detected_entities", {}).get("date_candidates", [])
    date_issued = date_cands[0] if date_cands else "unknown"
    try:
        year = int(date_issued.split("-")[0]) if date_issued != "unknown" else "unknown"
    except ValueError:
        year = "unknown"

    # 4. Normalizar Ciudad
    city_cands = json_data.get("detected_entities", {}).get("city_candidates", [])
    city = normalizar_ciudad(city_cands[0]) if city_cands else extraer_ciudad_desde_md(contenido_md)

    # 5. Resolver Anexos
    has_annexes = json_data.get("global_hints", {}).get("has_annexes", False)
    annex_count = len(re.findall(r'^#\s*(ANEXO|Anexo)', contenido_md, re.MULTILINE))
    if has_annexes and annex_count == 0:
        annex_count = 1

    # 6. Códigos Auxiliares
    auxiliary_codes = json_data.get("global_hints", {}).get("auxiliary_codes", [])

    # 7. Notas de publicación
    publication_notes = []
    for page in json_data.get("pages", []):
        if page.get("has_web_disclaimer"):
            nota = "El texto publicado en el sitio web no tiene validez para su presentación en terceras instituciones y/o entidades, salvo que contaren con autenticación expedida por la Dir. de Gestión de Doc. y Actos Adm."
            if nota not in publication_notes:
                publication_notes.append(nota)

    # 8. Modalidad de firma
    source_system = json_data.get("source_system_hint", "unknown")
    has_sig_page = json_data.get("global_hints", {}).get("has_signature_page", False)
    
    if source_system == "electronic":
        signature_mode = "digital"
    elif has_sig_page:
        signature_mode = "separate_page"
    else:
        signature_mode = "embedded"

    # 9. Recuperar entidades faltantes del JSON
    # 9. Recuperar entidades faltantes del JSON
    entidades_brutas = json_data.get("detected_entities", {})

    # Capturamos el origen (ya sean candidatos o el fallback)
    issuing_body_candidates = entidades_brutas.get("issuing_body_candidates", [])
    if isinstance(issuing_body_candidates, list) and issuing_body_candidates:
        candidatos_brutos = issuing_body_candidates
    else:
        issuing_body_fallback = resolver_issuing_body(entidades_brutas, document_code, contenido_md, document_type)
        candidatos_brutos = [issuing_body_fallback] if issuing_body_fallback and issuing_body_fallback != "unknown" else []
    normative_references = entidades_brutas.get("normative_candidates", [])

    # PROCESAMIENTO CON LEVENSHTEIN DISTANCE
    issuing_body = "unknown"
    for cand in candidatos_brutos:
        cand_clean = normalizar_espacios(cand).lower()
        if not cand_clean or cand_clean == "unknown":
            continue
            
        mejor_match = None
        distancia_minima = float('inf')
        
        # Comparamos contra cada opción fija para hallar el de menor distancia de edición
        for fijo in CANDIDATOS_FIJOS:
            fijo_clean = normalizar_espacios(fijo).lower()
            dist = Levenshtein.distance(cand_clean, fijo_clean)
            
            if dist < distancia_minima:
                distancia_minima = dist
                mejor_match = fijo
        
        if mejor_match and mejor_match not in issuing_body:
            issuing_body = mejor_match

    # Si por algún motivo quedó vacío, preservamos la consistencia
    if not issuing_body:
        issuing_body = "unknown"
    referenced_entities = {
        "persons": entidades_brutas.get("person_candidates", []),
        "academic_units": entidades_brutas.get("academic_unit_candidates", []),
        "careers": entidades_brutas.get("career_candidates", []),
        "courses": entidades_brutas.get("course_candidates", [])
    }

    signers = entidades_brutas.get("signers_candidates", [])
    
    # CONSTRUCCIÓN DEL DICCIONARIO CANÓNICO
    yaml_dict = {
        "document_id": json_data.get("document_id_hint", "unknown"),
        "source_pdf": json_data.get("source_pdf", "unknown"),
        "source_system": source_system,
        "document_type": document_type,
        "issuing_body": issuing_body,  # <-- CAMBIO
        "institution": "Universidad Nacional de Luján",
        "document_code": document_code,
        "document_number": document_number,
        "date_issued": date_issued,
        "year": year,
        "city": city,
        "has_annexes": has_annexes,
        "annex_count": annex_count,
        "has_signature_page": has_sig_page,
        "signature_mode": signature_mode,
        "signers": signers,  # <-- CAMBIO
        "referenced_entities": referenced_entities,  # <-- CAMBIO
        "normative_references": normative_references,  # <-- CAMBIO
        "auxiliary_codes": auxiliary_codes,
        "publication_notes": publication_notes,
        "extraction_version": "v1.4",
        "content_markdown": contenido_md 
    }

    return yaml_dict

def generar_documento_yaml_final(ruta_json, ruta_md):
    if not os.path.exists(ruta_json) or not os.path.exists(ruta_md):
        print(f"Error: No se encontraron los archivos base para {ruta_md}")
        return

    with open(ruta_json, 'r', encoding='utf-8') as f:
        json_data = json.load(f)

    with open(ruta_md, 'r', encoding='utf-8') as f:
        contenido_md = f.read()

    datos_completos = procesar_metadatos(json_data, contenido_md)

    # =========================================================================
    # NUEVA LÓGICA DE VALIDACIÓN Y DETECCIÓN DE ERRORES
    # =========================================================================
    campos_erroneos = []

    # 1. Campos simples que no deben ser vacíos ni "unknown"
    campos_basicos = [
        "document_id", "source_pdf", "source_system", "document_type", 
        "issuing_body", "document_code", "document_number", 
        "date_issued", "year", "city", "signature_mode"
    ]
    
    for campo in campos_basicos:
        valor = datos_completos.get(campo)
        if valor is None or str(valor).strip() == "" or str(valor).lower() == "unknown":
            campos_erroneos.append(campo)

    # 2. Validación específica de 'signers'
    # Debe haber mínimo 1, y ningún name o role debe ser "unknown" o vacío
    signers = datos_completos.get("signers", [])
    if not signers or not isinstance(signers, list):
        campos_erroneos.append("signers")
    else:
        for s in signers:
            if isinstance(s, dict):
                name = str(s.get("name", "")).lower()
                role = str(s.get("role", "")).lower()
                if name in ["unknown", ""]:
                    campos_erroneos.append("signers")
                    break
                if role in ["unknown", ""]:
                    campos_erroneos.append("role")
                    break
    # 3. Validación específica de 'auxiliary_codes'
    # Debe tener algo (no estar vacío ni poseer elementos "unknown")
    aux_codes = datos_completos.get("auxiliary_codes", [])
    if not aux_codes or not isinstance(aux_codes, list) or len(aux_codes) == 0:
        campos_erroneos.append("auxiliary_codes")
    else:
        if any(str(c).lower() in ["unknown", ""] for c in aux_codes):
            campos_erroneos.append("auxiliary_codes")

    # Si se detectaron fallos, se registran en docus_error.txt
    if campos_erroneos:
        nombre_documento = datos_completos.get("source_pdf", os.path.basename(ruta_md))
        # Modo 'a' abre el archivo para añadir líneas al final sin pisar lo anterior
        with open("docus_error.txt", "a", encoding="utf-8") as f_err:
            f_err.write(f"Documento: {nombre_documento} | Campos faltantes/erróneos: {', '.join(campos_erroneos)}\n")
        print(f"Aviso: El documento se marcó con errores en 'docus_error.txt' debido a: {', '.join(campos_erroneos)}")
    # =========================================================================

    nombre_base = os.path.splitext(os.path.basename(ruta_md))[0]
    ruta_salida = os.path.join(os.path.dirname(ruta_md), f"{nombre_base}_canonico.yaml")

    with open(ruta_salida, "w", encoding="utf-8") as f:
        yaml.dump(datos_completos, f, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000000)
    
    print(f"Éxito: Documento YAML consolidado generado en '{ruta_salida}'")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python post_procesador.py <archivo.json> <archivo.md>")
    else:
        ruta_json = sys.argv[1]
        ruta_md = sys.argv[2]
        generar_documento_yaml_final(ruta_json, ruta_md)

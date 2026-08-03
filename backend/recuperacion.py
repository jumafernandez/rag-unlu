"""
Recuperación híbrida sobre el índice del digesto.

Combina dos señales que se complementan y fallan en casos distintos:

  densa (BGE-m3)  similitud semántica: encuentra "¿puedo pedir licencia para rendir?"
                  aunque el acto diga "licencia con goce de haberes para rendir examen".
                  Es mala con identificadores: para el modelo, "893/2025" y "894/2025"
                  son casi el mismo vector.

  léxica (BM25)   coincidencia de términos exactos. Es la que encuentra "RESHCS 893/2025"
                  o "expediente 175/2008". En un digesto, consultar por número de acto es
                  de los usos más frecuentes, así que esta señal no es opcional.

Se eligió BM25 antes que los pesos esparsos aprendidos del propio modelo de embeddings
porque es recuperación clásica: cada puntaje se puede explicar término por término, sin
depender de que un modelo haya aprendido a pesar bien los identificadores. Para un sistema
que va a ser auditado, esa transparencia importa.

La fusión es RRF (Reciprocal Rank Fusion): combina por POSICIÓN en cada ranking, no por
puntaje. Evita normalizar escalas incomparables y es robusto cuando una de las dos señales
no encuentra nada.
"""

import json
import math
import os
import re
import unicodedata
from collections import defaultdict

import numpy as np

# Tokenizador pensado para normativa: conserva los identificadores enteros. Un tokenizador
# común parte "893/2025" en "893" y "2025", y ahí se pierde justo lo que hace única a la
# consulta. Estos patrones se prueban en orden y el primero que matchea gana.
PATRONES = [
    # El código pegado a su número solo se toma junto cuando hay dos puntos de por medio,
    # que es como los escribe el sistema. Sin esa exigencia, "expediente 175/2008" se
    # pegaba en un token único y se perdía el "175/2008" suelto, que es justo por donde
    # la gente busca.
    r'[A-ZÑ]{2,}[A-ZÑ0-9-]*\s*:\s*\d+\s*[-/]\s*\d{2,4}',   # RESHCS-LUJ: 0000042-24
    r'\d+\s*/\s*\d{2,4}',                                   # 528/2025, 175/2008
    r'[A-ZÑ]{2,}[A-ZÑ0-9]*(?:-[A-ZÑ0-9]+)+',                # DISPCD-CB, RESHCS-LUJ
    r'[a-zñáéíóú0-9]+(?:-[a-zñáéíóú0-9]+)*',                # palabras y siglas
]
RE_TOKENS = re.compile('|'.join(f'({p})' for p in PATRONES), re.IGNORECASE)

# Un identificador de acto: '893/2025'. Sirve para detectar cuándo la consulta nombra
# una norma concreta en vez de describir un tema.
RE_IDENTIFICADOR = re.compile(r'\d{1,6}/\d{4}')

VACIAS = {
    'de', 'la', 'el', 'los', 'las', 'del', 'y', 'o', 'a', 'en', 'que', 'por', 'para',
    'con', 'un', 'una', 'se', 'su', 'sus', 'al', 'lo', 'es', 'como', 'mas', 'pero',
    'sobre', 'entre', 'este', 'esta', 'estos', 'estas', 'ha', 'han', 'ser', 'son',
}


def _fecha_iso(fecha):
    """'23/02/2026' -> '2026-02-23'. Vacío si no tiene esa forma."""
    if fecha and len(fecha) == 10 and fecha[2] == fecha[5] == '/':
        return f'{fecha[6:]}-{fecha[3:5]}-{fecha[:2]}'
    return ''


def normalizar(texto):
    t = unicodedata.normalize('NFKD', texto.lower())
    return ''.join(c for c in t if not unicodedata.combining(c))


def tokenizar(texto):
    """Tokens comparables, con los identificadores en una sola pieza."""
    salida = []
    for m in RE_TOKENS.finditer(texto):
        tok = normalizar(m.group(0))
        tok = re.sub(r'\s+', '', tok)      # 'dispcd-cb : 528 / 2025' -> 'dispcd-cb:528/2025'
        if len(tok) < 2 or tok in VACIAS:
            continue
        salida.append(tok)
        # Un identificador también se indexa por sus partes, para que la consulta
        # encuentre el acto tanto por "528/2025" como por "DISPCD-CB 528".
        if '/' in tok or ':' in tok:
            for parte in re.split(r'[:/]', tok):
                if len(parte) >= 2 and parte not in VACIAS:
                    salida.append(parte)
    return salida


class BM25:
    """BM25 Okapi. Implementado acá para no sumar dependencias y poder auditarlo."""

    def __init__(self, documentos_tokenizados, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.N = len(documentos_tokenizados)
        self.longitudes = np.array([len(d) for d in documentos_tokenizados], dtype=np.float32)
        self.long_media = float(self.longitudes.mean()) if self.N else 0.0

        self.postings = defaultdict(list)   # token -> [(doc, frecuencia)]
        for i, toks in enumerate(documentos_tokenizados):
            frec = defaultdict(int)
            for t in toks:
                frec[t] += 1
            for t, f in frec.items():
                self.postings[t].append((i, f))

        self.idf = {}
        for t, lista in self.postings.items():
            n = len(lista)
            # IDF de Robertson, con el +1 exterior para que nunca quede negativo
            self.idf[t] = math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def puntuar(self, tokens_consulta, permitidos=None):
        puntajes = defaultdict(float)
        for t in tokens_consulta:
            lista = self.postings.get(t)
            if not lista:
                continue
            idf = self.idf[t]
            for i, f in lista:
                if permitidos is not None and i not in permitidos:
                    continue
                norm = 1 - self.b + self.b * (self.longitudes[i] / max(1e-9, self.long_media))
                puntajes[i] += idf * (f * (self.k1 + 1)) / (f + self.k1 * norm)
        return puntajes


class Busqueda:
    """Lógica de búsqueda, independiente de dónde estén guardados los fragmentos.

    Se apoya en unas pocas operaciones que cada almacén resuelve a su manera: `chunk(i)`
    para leer un fragmento, `vecinos()` para la señal densa, y las consultas por acto o por
    entidad. Todo lo demás ---el filtrado por identificador, la fusión RRF, las
    bonificaciones por estado de diálogo, el reparto del contexto--- vive una sola vez acá.

    Antes esta lógica estaba pegada a la carga en memoria. Separarla permite cambiar el
    almacenamiento sin tocar el comportamiento, que es justamente lo que hay que poder
    comparar cuando se cambia.
    """


class Indice(Busqueda):
    def __init__(self, ruta):
        self.ruta = ruta
        self.densos = np.load(os.path.join(ruta, 'densos.npy'))
        normas = np.linalg.norm(self.densos, axis=1, keepdims=True)
        self.densos = self.densos / np.clip(normas, 1e-9, None)

        self.chunks = []
        with open(os.path.join(ruta, 'chunks.jsonl'), encoding='utf-8') as fh:
            for linea in fh:
                if linea.strip():
                    self.chunks.append(json.loads(linea))

        # El BM25 se arma al cargar: sobre ~136k chunks toma unos segundos y evita
        # versionar un artefacto más que podría quedar desincronizado del texto.
        cuerpos = []
        for c in self.chunks:
            partes = [c.get('titulo') or '', c.get('cita') or '', c.get('texto') or '']
            cuerpos.append(tokenizar(' '.join(partes)))
        self.bm25 = BM25(cuerpos)

        # Identidad del acto al que pertenece cada chunk: número y código. Sale de la
        # metadata, no del texto, así que es el identificador del documento y no un
        # número citado de pasada dentro del cuerpo.
        self._ids_chunk, self._codigos_chunk = [], []
        for c in self.chunks:
            num = str(c.get('document_number') or '')
            self._ids_chunk.append({t for t in tokenizar(num) if RE_IDENTIFICADOR.fullmatch(t)})
            self._codigos_chunk.append(normalizar(str(c.get('document_code') or '')).strip())

        with open(os.path.join(ruta, 'indice.json'), encoding='utf-8') as fh:
            self.info = json.load(fh)

    def __len__(self):
        return len(self.chunks)

    def chunk(self, i):
        return self.chunks[i]

    def puntuar_lexico(self, texto, permitidos=None):
        return self.bm25.puntuar(tokenizar(texto), permitidos)

    def fragmentos_de_documento(self, documento):
        return [c for c in self.chunks if c['documento'] == documento]

    def url_de_archivo(self, id_archivo):
        for c in self.chunks:
            if c.get('id_archivo') == id_archivo:
                return c.get('url_documento')
        return None

    def documentos_y_fechas(self):
        """(documentos distintos, fecha de cobertura). Ver la nota en _alcance()."""
        docs, fechas = set(), []
        for c in self.chunks:
            docs.add(c.get('documento'))
            f = c.get('date_issued')
            if isinstance(f, str) and len(f) == 10:
                fechas.append(f)
        fechas.sort()
        return len(docs), (fechas[int(len(fechas) * 0.99)] if fechas else None)

    def vecinos(self, consulta_densa, k, permitidos=None):
        """[(indice, similitud)] por producto interno sobre la matriz completa."""
        q = np.asarray(consulta_densa, dtype=np.float32)
        q = q / max(1e-9, float(np.linalg.norm(q)))
        sims = self.densos @ q
        if permitidos is not None:
            mascara = np.full(len(sims), -np.inf, dtype=np.float32)
            if permitidos:
                idx = np.fromiter(permitidos, dtype=np.int64, count=len(permitidos))
                mascara[idx] = sims[idx]
            sims = mascara
        tope = min(k, len(sims))
        if not tope:
            return []
        sel = np.argpartition(-sims, tope - 1)[:tope]
        sel = sel[np.argsort(-sims[sel])]
        return [(int(i), float(sims[i])) for i in sel if np.isfinite(sims[i])]

    def _filtrar(self, filtros, solo_articulos):
        permitidos = None
        if filtros:
            filtros = dict(filtros)
            # 'desde' no es igualdad: fecha del acto >= la fecha dada (ISO, compara bien
            # como texto). Es lo que usa /novedades.
            desde = filtros.pop('desde', None)
            permitidos = {i for i, c in enumerate(self.chunks)
                          if all(str(c.get(k, '')) == str(v)
                                 for k, v in filtros.items() if v not in (None, ''))
                          and (not desde or _fecha_iso(c.get('fecha_acto')) >= desde)}
        if solo_articulos:
            arts = {i for i, c in enumerate(self.chunks) if c.get('tipo_seccion') == 'articulo'}
            permitidos = arts if permitidos is None else (permitidos & arts)
        return permitidos

    def _ids_de_chunk(self, i):
        return self._ids_chunk[i]

    def _identificadores(self, texto):
        """Números de acto mencionados en la consulta ('893/2025')."""
        return {t for t in tokenizar(texto) if RE_IDENTIFICADOR.fullmatch(t)}

    def _codigos(self, texto):
        """Códigos de acto mencionados en la consulta ('DSECEXT', 'RESHCS', 'DISPCD-CB').

        Se compara contra los códigos que existen en el índice en vez de adivinar por
        forma: así 'disposicion' o 'resolucion' no se confunden con un código, y no hace
        falta mantener una lista de siglas a mano.
        """
        presentes = set(self._codigos_chunk)
        return {t for t in tokenizar(texto) if t in presentes and t}

    def chunks_de_actos(self, actos, tope_por_acto=3):
        """Índices de los chunks que pertenecen a los actos indicados.

        `actos` es un conjunto de (código, "número/año"). Se usa para mantener en el
        contexto los documentos de los que ya se venía hablando.
        """
        if not actos:
            return []
        buscados = {(c.upper(), n) for c, n in actos}
        por_acto, salida = {}, []
        for i, c in enumerate(self.chunks):
            clave = ((c.get('document_code') or '').upper(),
                     re.sub(r'\s+', '', str(c.get('document_number') or '')))
            if clave in buscados and por_acto.get(clave, 0) < tope_por_acto:
                por_acto[clave] = por_acto.get(clave, 0) + 1
                salida.append(i)
        return salida

    def chunks_de_entidad(self, entidad, consulta_densa, tope=3):
        """Fragmentos que mencionan a la entidad, ordenados por similitud con la consulta.

        Es la segunda red: aunque la reescritura falle, si se sabe de quién o de qué se
        está hablando se garantiza que en el contexto haya fragmentos que efectivamente
        lo mencionen. Sin esto, una pregunta sobre una persona puede traer actos del tema
        preguntado donde esa persona no figura, y el modelo atribuirle lo que dicen.
        """
        if not entidad:
            return []
        # Se buscan los tokens distintivos del nombre: así "Carina Natalia Duna" matchea
        # con "Duna Carina" y con "DUNA, Carina".
        piezas = [t for t in tokenizar(entidad) if len(t) >= 4]
        if not piezas:
            return []
        candidatos = []
        for i, c in enumerate(self.chunks):
            texto = normalizar(f"{c.get('titulo') or ''} {c.get('texto') or ''}")
            if sum(1 for p in piezas if p in texto) >= min(2, len(piezas)):
                candidatos.append(i)
        if not candidatos:
            return []
        q = np.asarray(consulta_densa, dtype=np.float32)
        q = q / max(1e-9, float(np.linalg.norm(q)))
        sims = self.densos[candidatos] @ q
        orden = np.argsort(-sims)[:tope]
        return [candidatos[j] for j in orden]

    def menciona_entidad(self, i, piezas):
        """¿El fragmento i nombra a la entidad? Se pide más de una coincidencia para que
        un apellido común no arrastre documentos ajenos."""
        if not piezas:
            return False
        texto = normalizar(f"{self.chunk(i).get('titulo') or ''} {self.chunk(i).get('texto') or ''}")
        return sum(1 for p in piezas if p in texto) >= min(2, len(piezas))

    def buscar(self, consulta_densa, texto_consulta='', k=8, filtros=None,
               solo_articulos=False, candidatos=60, entidad=None,
               peso_entidad=1.0, pesos_actos=None):
        """[(indice, puntaje_rrf, detalle)] ordenado por relevancia.

        El estado de la conversación —la entidad de la que se habla y los actos que se
        vienen mencionando— entra como BONIFICACIÓN sobre el puntaje fusionado, con un
        peso por slot. Antes se agregaban los fragmentos al final de la lista con puntaje
        cero, y eso tenía dos problemas: no competían por posición, y al armar el contexto
        el corte por longitud se los comía justamente a ellos.

        La bonificación está expresada en unidades de RRF: un slot con peso 1 suma lo
        mismo que salir primero en una de las dos señales. Así empuja de verdad pero no
        puede reemplazar a la relevancia semántica, que suma por las dos.
        """
        permitidos = self._filtrar(filtros, solo_articulos)

        # Si la consulta nombra un acto concreto ("RESHCS 893/2025"), ese acto tiene que
        # venir primero. La fusión RRF por sí sola no lo garantiza: un documento con dos
        # señales tibias le gana a uno con una señal decisiva, y termina devolviendo la
        # resolución 444 cuando se pidió la 893. Acá se restringe la búsqueda a los
        # documentos que efectivamente llevan ese número.
        ids = self._identificadores(texto_consulta) if texto_consulta else set()
        codigos = self._codigos(texto_consulta) if texto_consulta else set()
        if ids:
            exactos = {i for i, s in enumerate(self._ids_chunk) if ids & s}
            # El número solo no alcanza: cada tipo de acto lleva su propia numeración, así
            # que "3/2025" existe en decenas de códigos distintos a la vez. Si la consulta
            # también nombra el código, se exige que coincida; si no, el documento pedido
            # queda enterrado entre los homónimos de otros organismos.
            if codigos:
                con_codigo = {i for i in exactos if self._codigos_chunk[i] in codigos}
                if con_codigo:
                    exactos = con_codigo
            if permitidos is not None:
                exactos &= permitidos
            if exactos:
                permitidos = exactos
        elif codigos:
            # Consulta que nombra el tipo de acto sin número ("disposiciones DSECEXT"):
            # acota a ese código y deja que las señales ordenen adentro.
            del_codigo = {i for i, c in enumerate(self._codigos_chunk) if c in codigos}
            if del_codigo:
                permitidos = del_codigo if permitidos is None else (permitidos & del_codigo) or permitidos

        # --- densa ---
        cercanos = (self.vecinos(consulta_densa, candidatos, permitidos)
                    if consulta_densa is not None else [])
        orden_denso = [i for i, _ in cercanos]
        sims = {i: s for i, s in cercanos}

        # --- léxica ---
        puntajes_bm = self.puntuar_lexico(texto_consulta, permitidos) if texto_consulta else {}
        orden_lexico = [i for i, _ in sorted(puntajes_bm.items(), key=lambda x: -x[1])[:candidatos]]

        # --- fusión RRF ---
        K = 60
        total, detalle = defaultdict(float), defaultdict(dict)
        for r, i in enumerate(orden_denso):
            total[i] += 1.0 / (K + r + 1)
            detalle[i]['denso'] = r + 1
        for r, i in enumerate(orden_lexico):
            total[i] += 1.0 / (K + r + 1)
            detalle[i]['lexico'] = r + 1
            detalle[i]['bm25'] = round(float(puntajes_bm[i]), 3)

        # --- estado de la conversación, como bonificación ---
        # Unidad de bonificación: lo que vale salir primero en una de las dos señales.
        # Expresarlo así evita una constante suelta y hace que el efecto sea comparable
        # con el de la recuperación en vez de arbitrario.
        UNIDAD = 1.0 / (K + 1)
        pesos_actos = pesos_actos or {}
        piezas_entidad = [t for t in tokenizar(entidad) if len(t) >= 4] if entidad else []

        # Los actos del estado bonifican a sus propios fragmentos. Se recorre una vez el
        # índice de identidad ya calculado, sin volver a mirar el texto.
        if pesos_actos:
            claves = {(c.upper(), re.sub(r'\s+', '', str(n))): w
                      for (c, n), w in pesos_actos.items() if w > 0}
            if claves:
                for i in list(total):
                    clave = ((self.chunk(i).get('document_code') or '').upper(),
                             re.sub(r'\s+', '', str(self.chunk(i).get('document_number') or '')))
                    w = claves.get(clave)
                    if w:
                        total[i] += w * UNIDAD
                        detalle[i]['continuidad'] = round(w, 2)

                # Los actos que la búsqueda no trajo se incorporan con el puntaje que les
                # da su peso, así compiten por posición en lugar de quedar al final.
                presentes = {((self.chunk(i).get('document_code') or '').upper(),
                              re.sub(r'\s+', '', str(self.chunk(i).get('document_number') or '')))
                             for i in total}
                faltantes = {a for a in claves if a not in presentes}
                if faltantes:
                    for i in self.chunks_de_actos(faltantes, tope_por_acto=2):
                        clave = ((self.chunk(i).get('document_code') or '').upper(),
                                 re.sub(r'\s+', '', str(self.chunk(i).get('document_number') or '')))
                        w = claves.get(clave, 0)
                        total[i] = max(total[i], w * UNIDAD)
                        detalle[i]['continuidad'] = round(w, 2)

        if piezas_entidad and peso_entidad > 0:
            for i in list(total):
                if self.menciona_entidad(i, piezas_entidad):
                    total[i] += peso_entidad * UNIDAD
                    detalle[i]['foco'] = round(peso_entidad, 2)

        mejores = sorted(total.items(), key=lambda x: -x[1])[:k]

        # Garantía de entidad. La bonificación ordena, pero no asegura presencia: si la
        # consulta se fue de tema, puede que ningún fragmento del top-k mencione al sujeto
        # y el modelo termine atribuyéndole lo que dicen documentos donde no figura —que
        # es exactamente la alucinación que este mecanismo existe para evitar. Cuando pasa,
        # se cede la última posición al mejor fragmento que sí lo nombra.
        # No es un filtro duro a propósito: una pregunta como "¿y qué dice el reglamento
        # general?" tiene que poder salirse del foco.
        if piezas_entidad and peso_entidad > 0:
            ya = {i for i, _ in mejores}
            if not any(self.menciona_entidad(i, piezas_entidad) for i in ya):
                for i in self.chunks_de_entidad(entidad, consulta_densa, tope=1):
                    if i not in ya:
                        if len(mejores) >= k:
                            mejores.pop()
                        mejores.append((i, peso_entidad * UNIDAD))
                        detalle[i]['foco'] = round(peso_entidad, 2)
                        detalle[i]['garantia'] = True

        return [(i, s, dict(detalle[i], similitud=sims.get(i))) for i, s in mejores]

    def contexto(self, resultados, max_caracteres=9000):
        """Bloque de contexto para el modelo, con las citas visibles.

        Incluye el TÍTULO del acto además del fragmento. En muchos documentos el título
        es lo único que dice de qué trata: el programa de una asignatura tiene por título
        "PROGRAMA (10821) ÁLGEBRA — INGENIERÍA INDUSTRIAL" y en el cuerpo solo el nombre
        del docente. Sin el título, el modelo ve un nombre y un cargo, y responde con
        razón que no sabe de qué asignatura se trata.

        El presupuesto se REPARTE, no se consume por orden de llegada. Antes se cortaba al
        primer fragmento que no entraba, y eso tenía dos consecuencias malas: un anexo largo
        en las primeras posiciones dejaba afuera a todos los que seguían aunque fueran
        cortos, y los 26 fragmentos del corpus que solos superan el presupuesto entero
        devolvían contexto VACÍO —el modelo respondía que no sabía nada mientras la interfaz
        mostraba ocho fuentes—.

        Ahora cada resultado tiene su parte y lo que sobra se reparte entre los que quedaron
        cortados. Un fragmento largo se trunca; ninguno desaparece.
        """
        if not resultados:
            return ''

        encabezados, cuerpos = [], []
        for i, _, _ in resultados:
            c = self.chunk(i)
            enc = f"[{c['cita']}]"
            if c.get('titulo'):
                enc += f"\n{c['titulo']}"
            if c.get('date_issued'):
                enc += f"\n(fecha: {c['date_issued']})"
            encabezados.append(enc)
            cuerpos.append(c['texto'] or '')

        # El encabezado va siempre: es la cita, el título y la fecha, o sea lo que permite
        # reconocer el documento. Lo que se recorta es el cuerpo.
        disponible = max(0, max_caracteres - sum(len(e) + 1 for e in encabezados))
        parte = disponible // len(cuerpos)

        asignado = [min(len(t), parte) for t in cuerpos]
        sobrante = disponible - sum(asignado)
        # Lo que no usaron los fragmentos cortos se reparte entre los truncados, por orden
        # de relevancia: el presupuesto se aprovecha entero sin que ninguno monopolice.
        for j, t in enumerate(cuerpos):
            if sobrante <= 0:
                break
            falta = len(t) - asignado[j]
            if falta > 0:
                extra = min(falta, sobrante)
                asignado[j] += extra
                sobrante -= extra

        partes = []
        for enc, texto, n in zip(encabezados, cuerpos, asignado):
            if len(texto) > n:
                # Se corta en el último espacio para no partir una palabra al medio, y se
                # marca: el modelo tiene que saber que lo que ve está incompleto.
                recorte = texto[:n]
                corte = recorte.rfind(' ')
                texto = (recorte[:corte] if corte > n // 2 else recorte) + ' […]'
            partes.append(f"{enc}\n{texto}")
        return '\n\n---\n\n'.join(partes)

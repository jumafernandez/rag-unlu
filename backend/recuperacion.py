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


class Indice:
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

    def _filtrar(self, filtros, solo_articulos):
        permitidos = None
        if filtros:
            permitidos = {i for i, c in enumerate(self.chunks)
                          if all(str(c.get(k, '')) == str(v)
                                 for k, v in filtros.items() if v not in (None, ''))}
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

    def buscar(self, consulta_densa, texto_consulta='', k=8, filtros=None,
               solo_articulos=False, candidatos=60, anclas=None):
        """[(indice, puntaje_rrf, detalle)] ordenado por relevancia."""
        permitidos = self._filtrar(filtros, solo_articulos)

        # Si la consulta nombra un acto concreto ("RESHCS 893/2025"), ese acto tiene que
        # venir primero. La fusión RRF por sí sola no lo garantiza: un documento con dos
        # señales tibias le gana a uno con una señal decisiva, y termina devolviendo la
        # resolución 444 cuando se pidió la 893. Acá se restringe la búsqueda a los
        # documentos que efectivamente llevan ese número.
        ids = self._identificadores(texto_consulta) if texto_consulta else set()
        codigos = self._codigos(texto_consulta) if texto_consulta else set()
        if ids:
            exactos = {i for i in range(len(self.chunks)) if ids & self._ids_chunk[i]}
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
            del_codigo = {i for i in range(len(self.chunks)) if self._codigos_chunk[i] in codigos}
            if del_codigo:
                permitidos = del_codigo if permitidos is None else (permitidos & del_codigo) or permitidos

        # --- densa ---
        orden_denso, sims = [], None
        if consulta_densa is not None:
            q = np.asarray(consulta_densa, dtype=np.float32)
            q = q / max(1e-9, float(np.linalg.norm(q)))
            sims = self.densos @ q
            if permitidos is not None:
                mascara = np.full(len(sims), -np.inf, dtype=np.float32)
                if permitidos:
                    idx = np.fromiter(permitidos, dtype=np.int64, count=len(permitidos))
                    mascara[idx] = sims[idx]
                sims = mascara
            tope = min(candidatos, len(sims))
            if tope:
                sel = np.argpartition(-sims, tope - 1)[:tope]
                sel = sel[np.argsort(-sims[sel])]
                orden_denso = [int(i) for i in sel if np.isfinite(sims[i])]

        # --- léxica ---
        puntajes_bm = self.bm25.puntuar(tokenizar(texto_consulta), permitidos) if texto_consulta else {}
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

        mejores = sorted(total.items(), key=lambda x: -x[1])[:k]

        # Continuidad de la conversación: los actos que ya se citaron se agregan al final
        # si la búsqueda no los trajo. Cuestan pocas posiciones y evitan que una
        # repregunta sobre "el artículo 2" se quede sin el documento del que se hablaba.
        if anclas:
            # Solo se agrega el acto que la búsqueda NO trajo. Si ya está representado,
            # sumar más fragmentos suyos desplaza a otros documentos y la respuesta
            # termina girando alrededor de un único acto.
            docs_presentes = {
                ((self.chunks[i].get('document_code') or '').upper(),
                 re.sub(r'\s+', '', str(self.chunks[i].get('document_number') or '')))
                for i, _ in mejores
            }
            faltantes = {a for a in {(c.upper(), n) for c, n in anclas} if a not in docs_presentes}
            if faltantes:
                for i in self.chunks_de_actos(faltantes, tope_por_acto=2):
                    mejores.append((i, 0.0))
                    detalle[i]['continuidad'] = True

        return [(i, s, dict(detalle[i],
                            similitud=(float(sims[i]) if sims is not None and np.isfinite(sims[i]) else None)))
                for i, s in mejores]

    def contexto(self, resultados, max_caracteres=9000):
        """Bloque de contexto para el modelo, con las citas visibles."""
        partes, usados = [], 0
        for i, _, _ in resultados:
            c = self.chunks[i]
            bloque = f"[{c['cita']}]\n{c['texto']}"
            if usados + len(bloque) > max_caracteres:
                break
            partes.append(bloque)
            usados += len(bloque)
        return '\n\n---\n\n'.join(partes)

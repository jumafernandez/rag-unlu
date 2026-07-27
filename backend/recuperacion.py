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
    r'[A-ZÑ]{2,}[A-ZÑ0-9-]*\s*:\s*\d+[-/]\d+',   # RESHCS-LUJ: 0000042-24
    r'[A-ZÑ]{2,}[A-ZÑ0-9-]*\s*:?\s*\d+\s*/\s*\d{2,4}',  # DISPCD-CB : 528 / 2025
    r'\d+\s*/\s*\d{2,4}',                         # 893/2025
    r'[a-zñáéíóú0-9]+(?:-[a-zñáéíóú0-9]+)*',      # palabras y siglas
]
RE_TOKENS = re.compile('|'.join(f'({p})' for p in PATRONES), re.IGNORECASE)

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

    def buscar(self, consulta_densa, texto_consulta='', k=8, filtros=None,
               solo_articulos=False, candidatos=60):
        """[(indice, puntaje_rrf, detalle)] ordenado por relevancia."""
        permitidos = self._filtrar(filtros, solo_articulos)

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

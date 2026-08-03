"""Almacén de fragmentos sobre SQLite y FAISS.

Reemplaza a la carga completa en memoria de `recuperacion.Indice`, conservando su interfaz.
La diferencia es dónde vive cada cosa:

    antes                        ahora
    -----------------------      ------------------------------------------
    140.902 dicts de Python      tabla `chunk` en SQLite, se leen los que se usan
    matriz numpy de 577 MB       índice FAISS, mapeado por la biblioteca
    2,1 GB de proceso            los postings de BM25 y poco más

El texto de un fragmento se consulta para los ocho que se devuelven, no para los 140.902
que se recorren. Mantenerlo todo en RAM era pagar por algo que casi nunca se mira, y con el
digesto histórico ---seis veces el corpus--- dejaba de entrar.

Lo que NO cambia y es deliberado: el BM25 propio, con el tokenizador que preserva los
identificadores normativos. SQLite trae su propio buscador de texto con BM25 incorporado,
pero su tokenizador parte `RESHCS-LUJ: 0000042-24` en pedazos, que es justamente lo que este
sistema no puede permitirse.

Requiere los artefactos que genera `pipeline/construir_indice.py`. Si no están, la API sigue
usando `recuperacion.Indice`.
"""
import json
import os
import re
import sqlite3
import threading

import numpy as np

from .recuperacion import BM25, RE_IDENTIFICADOR, Indice, normalizar, tokenizar

# Columnas que se devuelven al pedir un fragmento. Se nombran explícitamente en vez de
# usar SELECT *: si mañana la tabla suma una columna, la respuesta de la API no cambia sola.
CAMPOS = ['chunk_id', 'documento', 'seccion', 'tipo_seccion', 'cita', 'titulo', 'texto',
          'document_code', 'document_number', 'date_issued', 'fecha_acto', 'estado',
          'metadata_confianza', 'source_pdf', 'url_documento', 'id_archivo', 'id_documento',
          'seccion_portal']


class AlmacenSQL(Indice):
    """Hereda de Indice para reutilizar la búsqueda tal cual está ---filtrado por
    identificador, fusión RRF, bonificaciones por estado, reparto del contexto--- y
    reemplaza únicamente de dónde salen los datos. No se llama a su __init__: este almacén
    no carga nada en memoria."""

    def __init__(self, ruta, lexico=None):
        import faiss

        self.lexico = lexico or os.environ.get('RAG_LEXICO', 'bm25')
        self.ruta = ruta
        self.ruta_bd = os.path.join(ruta, 'chunks.sqlite')
        self.indice_vectorial = faiss.read_index(os.path.join(ruta, 'vectores.faiss'))

        # Una conexión por hilo: SQLite no permite compartir una entre hilos, y uvicorn
        # atiende cada petición en el suyo.
        self._local = threading.local()

        with open(os.path.join(ruta, 'indice.json'), encoding='utf-8') as fh:
            self.info = json.load(fh)

        cur = self._bd().execute('SELECT COUNT(*) FROM chunk')
        self.n = cur.fetchone()[0]
        if self.n != self.indice_vectorial.ntotal:
            raise RuntimeError(f'desalineados: sqlite={self.n} faiss={self.indice_vectorial.ntotal}')

        self._preparar()

    def _bd(self):
        if not hasattr(self._local, 'c'):
            c = sqlite3.connect(self.ruta_bd, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA query_only = ON')
            self._local.c = c
        return self._local.c

    def puntuar_lexico(self, texto, permitidos=None):
        """Señal léxica. Por defecto, el BM25 propio.

        FTS5 está implementado y disponible con RAG_LEXICO=fts5: baja la carga de 38 s a
        menos de uno y la memoria a un tercio. No es el valor por omisión porque cambia
        los documentos devueltos ---en una comparación de quince consultas, once---, y sin
        una evaluación no se puede afirmar que ese cambio sea una mejora. Cuando la haya,
        el cambio es una variable de entorno.
        """
        if self.lexico != 'fts5':
            return self.bm25.puntuar(tokenizar(texto), permitidos)
        return self._puntuar_fts5(texto, permitidos)

    def _puntuar_fts5(self, texto, permitidos=None):
        """BM25 delegado a SQLite.

        El índice invertido vive en disco, así que deja de haber un gigabyte de listas de
        postings en memoria ---que era lo que de verdad no escalaba---. El tokenizador de
        FTS5 se configuró para no partir los identificadores normativos, que es la única
        razón por la que este sistema tenía BM25 propio.

        El valor del puntaje no importa: en la fusión el aporte léxico entra por POSICIÓN
        en el ranking. FTS5 devuelve valores negativos con otra escala y el resultado es
        el mismo, siempre que el orden se conserve.
        """
        tokens = [t for t in tokenizar(texto) if t]
        if not tokens:
            return {}
        # Cada token entre comillas: así FTS5 no interpreta como operadores los guiones y
        # las barras que justamente queremos conservar.
        consulta = ' OR '.join('"' + t.replace('"', '') + '"' for t in tokens)
        sql = ('SELECT rowid, bm25(chunk_fts) AS p FROM chunk_fts '
               'WHERE chunk_fts MATCH ? ORDER BY p LIMIT 400')
        try:
            filas = self._bd().execute(sql, (consulta,)).fetchall()
        except Exception:
            return {}
        # bm25() de SQLite es más negativo cuanto mejor: se invierte para que, como en el
        # resto del sistema, un número mayor sea un resultado mejor.
        return {f['rowid']: -float(f['p']) for f in filas
                if permitidos is None or f['rowid'] in permitidos}

    def _preparar(self):
        """Estructuras que sí conviene tener en memoria: son chicas y se usan en cada consulta."""
        bd = self._bd()

        if self.lexico != 'fts5':
            # BM25 propio: se construye recorriendo la tabla, sin materializar los 140.902
            # textos tokenizados a la vez.
            cuerpos = []
            for fila in bd.execute('SELECT titulo, cita, texto FROM chunk ORDER BY i'):
                cuerpos.append(tokenizar(' '.join(x or '' for x in fila)))
            self.bm25 = BM25(cuerpos)
            del cuerpos

        # Identidad del acto por posición: son cadenas cortas y se consultan al filtrar por
        # número o código, antes de tocar la base.
        self._ids_chunk, self._codigos_chunk = [], []
        for fila in bd.execute('SELECT document_code, document_number FROM chunk ORDER BY i'):
            num = str(fila['document_number'] or '')
            self._ids_chunk.append({t for t in tokenizar(num) if RE_IDENTIFICADOR.fullmatch(t)})
            self._codigos_chunk.append(normalizar(str(fila['document_code'] or '')).strip())
        self._codigos_presentes = set(self._codigos_chunk)

    # ---------------------------------------------------------------- lectura
    def __len__(self):
        return self.n

    def chunk(self, i):
        fila = self._bd().execute(
            f"SELECT {', '.join(CAMPOS)} FROM chunk WHERE i=?", (i,)).fetchone()
        return dict(fila) if fila else {}

    def chunks_de(self, indices):
        """Varios fragmentos de una consulta, devueltos en el orden pedido."""
        indices = list(indices)
        if not indices:
            return []
        marcas = ','.join('?' * len(indices))
        filas = self._bd().execute(
            f"SELECT i, {', '.join(CAMPOS)} FROM chunk WHERE i IN ({marcas})", indices).fetchall()
        por_i = {f['i']: dict(f) for f in filas}
        return [por_i.get(i, {}) for i in indices]

    def documentos_y_fechas(self):
        bd = self._bd()
        docs = bd.execute('SELECT COUNT(DISTINCT documento) FROM chunk').fetchone()[0]
        n = bd.execute("SELECT COUNT(*) FROM chunk WHERE LENGTH(date_issued)=10").fetchone()[0]
        if not n:
            return docs, None
        fila = bd.execute('SELECT date_issued FROM chunk WHERE LENGTH(date_issued)=10 '
                          'ORDER BY date_issued LIMIT 1 OFFSET ?', (int(n * 0.99),)).fetchone()
        return docs, (fila[0] if fila else None)

    def url_de_archivo(self, id_archivo):
        fila = self._bd().execute(
            'SELECT url_documento FROM chunk WHERE id_archivo=? LIMIT 1', (id_archivo,)).fetchone()
        return fila['url_documento'] if fila else None

    def fragmentos_de_documento(self, documento):
        return [dict(f) for f in self._bd().execute(
            f"SELECT {', '.join(CAMPOS)} FROM chunk WHERE documento=? ORDER BY i", (documento,))]

    # ---------------------------------------------------------------- filtros
    def _filtrar(self, filtros, solo_articulos):
        condiciones, valores = [], []
        for k, v in (filtros or {}).items():
            if v in (None, ''):
                continue
            if k == 'desde':
                # La fecha del acto está guardada dd/mm/aaaa: se reordena a ISO en la
                # consulta para que la comparación textual tenga sentido. Para /novedades.
                condiciones.append(
                    "LENGTH(fecha_acto)=10 AND "
                    "substr(fecha_acto,7,4)||'-'||substr(fecha_acto,4,2)||'-'||"
                    "substr(fecha_acto,1,2) >= ?")
            else:
                condiciones.append(f'{k}=?')
            valores.append(str(v))
        if solo_articulos:
            condiciones.append("tipo_seccion='articulo'")
        if not condiciones:
            return None
        sql = f"SELECT i FROM chunk WHERE {' AND '.join(condiciones)}"
        return {f['i'] for f in self._bd().execute(sql, valores)}

    def _identificadores(self, texto):
        return {t for t in tokenizar(texto) if RE_IDENTIFICADOR.fullmatch(t)}

    def _codigos(self, texto):
        return {t for t in tokenizar(texto) if t in self._codigos_presentes and t}

    def chunks_de_actos(self, actos, tope_por_acto=3):
        if not actos:
            return []
        salida, por_acto = [], {}
        for codigo, numero in actos:
            filas = self._bd().execute(
                'SELECT i FROM chunk WHERE UPPER(document_code)=? '
                'AND REPLACE(document_number, " ", "")=? ORDER BY i LIMIT ?',
                (str(codigo).upper(), re.sub(r'\s+', '', str(numero)), tope_por_acto))
            for f in filas:
                salida.append(f['i'])
        return salida

    def menciona_entidad(self, i, piezas):
        if not piezas:
            return False
        fila = self._bd().execute('SELECT titulo, texto FROM chunk WHERE i=?', (i,)).fetchone()
        if not fila:
            return False
        texto = normalizar(f"{fila['titulo'] or ''} {fila['texto'] or ''}")
        return sum(1 for p in piezas if p in texto) >= min(2, len(piezas))

    def chunks_de_entidad(self, entidad, consulta_densa, tope=3):
        """Fragmentos que mencionan a la entidad, ordenados por similitud con la consulta.

        El filtrado ocurre en SQLite en lugar de en un bucle de Python sobre todos los
        fragmentos: es el mismo recorrido, pero sin construir un objeto por fila.
        """
        if not entidad:
            return []
        piezas = [t for t in tokenizar(entidad) if len(t) >= 4]
        if not piezas:
            return []

        # Se acota con la pieza más larga ---la más selectiva--- y después se verifica el
        # resto, que es lo que evita recorrer la tabla entera comparando todas.
        ancla = max(piezas, key=len)
        candidatos = []
        for fila in self._bd().execute(
                'SELECT i, titulo, texto FROM chunk '
                'WHERE titulo LIKE ? OR texto LIKE ?', (f'%{ancla}%', f'%{ancla}%')):
            texto = normalizar(f"{fila['titulo'] or ''} {fila['texto'] or ''}")
            if sum(1 for p in piezas if p in texto) >= min(2, len(piezas)):
                candidatos.append(fila['i'])
        if not candidatos:
            return []

        q = np.asarray(consulta_densa, dtype=np.float32)
        q = q / max(1e-9, float(np.linalg.norm(q)))
        vectores = np.vstack([self.indice_vectorial.reconstruct(int(i)) for i in candidatos])
        sims = vectores @ q
        orden = np.argsort(-sims)[:tope]
        return [candidatos[j] for j in orden]

    # ---------------------------------------------------------------- búsqueda
    def vecinos(self, consulta_densa, k, permitidos=None):
        """[(indice, similitud)] por similitud coseno.

        FAISS no filtra por metadata, así que cuando hay filtro se pide de más y se descarta
        después. Se pide en tandas crecientes en vez de un número fijo: con un filtro muy
        restrictivo un tope fijo se quedaría corto, y sin filtro no tiene sentido pagar por
        buscar de más.
        """
        import faiss
        q = np.asarray(consulta_densa, dtype=np.float32).reshape(1, -1).copy()
        faiss.normalize_L2(q)

        if permitidos is None:
            sims, idx = self.indice_vectorial.search(q, min(k, self.n))
            return [(int(i), float(s)) for i, s in zip(idx[0], sims[0]) if i >= 0]

        if not permitidos:
            return []

        # Con filtro se busca DENTRO del conjunto permitido, no se busca de más y se
        # descarta. La diferencia no es de eficiencia: pidiendo de a tandas y filtrando
        # después se pierden candidatos permitidos que quedaron por debajo del corte, y eso
        # se notaba justo en las consultas por número de acto ---donde el conjunto
        # permitido es chico y disperso--- que son las que tienen que ser exactas.
        ids = np.fromiter(sorted(permitidos), dtype=np.int64, count=len(permitidos))
        selector = faiss.IDSelectorArray(ids)
        parametros = faiss.SearchParameters()
        parametros.sel = selector
        sims, idx = self.indice_vectorial.search(q, min(k, len(permitidos)), params=parametros)
        return [(int(i), float(s)) for i, s in zip(idx[0], sims[0]) if i >= 0]

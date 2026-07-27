"""
API del asistente de consulta del digesto UNLu.

Principio de diseño: TRAZABILIDAD. Toda respuesta devuelve las fuentes que la sustentan,
y cada fuente permite reconstruir el camino completo hasta el documento original:

    respuesta -> cita -> chunk_id -> documento -> source_pdf -> PDF publicado

El modelo recibe la instrucción de responder únicamente con el contexto recuperado y de
citar cada afirmación. Si el contexto no alcanza, debe decirlo en vez de completar con
conocimiento propio: en un digesto normativo una respuesta inventada es peor que "no sé".

La generación está detrás de una interfaz (`generar`) para poder cambiar de proveedor sin
tocar el resto: hoy OpenAI, mañana un modelo propio en infraestructura de la Universidad.

Levantar:
    uvicorn backend.api:app --reload --port 8000
Variables:
    RAG_INDICE      ruta al índice (default: indice/)
    RAG_MODELO_EMB  modelo de embeddings (default: BAAI/bge-m3)
    OPENAI_API_KEY  para la generación
    RAG_MODELO_GEN  modelo de generación (default: gpt-4o-mini)
"""

import os
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .recuperacion import Indice

RUTA_INDICE = os.environ.get('RAG_INDICE', 'indice')
MODELO_EMB = os.environ.get('RAG_MODELO_EMB', 'BAAI/bge-m3')
MODELO_GEN = os.environ.get('RAG_MODELO_GEN', 'gpt-4o-mini')

app = FastAPI(title='ChatDigesto UNLu', version='1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get('RAG_CORS', 'http://localhost:5174,http://localhost:5173').split(','),
    allow_methods=['*'], allow_headers=['*'],
)

_indice: Optional[Indice] = None
_codificador = None


def indice() -> Indice:
    global _indice
    if _indice is None:
        if not os.path.isdir(RUTA_INDICE):
            raise HTTPException(503, f'no está el índice en {RUTA_INDICE}')
        _indice = Indice(RUTA_INDICE)
    return _indice


def codificador():
    global _codificador
    if _codificador is None:
        from FlagEmbedding import BGEM3FlagModel
        _codificador = BGEM3FlagModel(MODELO_EMB, use_fp16=False)
    return _codificador


class Consulta(BaseModel):
    pregunta: str
    k: int = Field(8, ge=1, le=30)
    anio: Optional[int] = None
    tipo: Optional[str] = None
    solo_articulos: bool = False
    generar: bool = True


class Fuente(BaseModel):
    cita: str
    texto: str
    documento: str
    source_pdf: Optional[str] = None
    seccion: Optional[str] = None
    date_issued: Optional[str] = None
    estado: Optional[str] = None
    metadata_confianza: Optional[str] = None
    puntaje: float
    ranking: dict


class Respuesta(BaseModel):
    pregunta: str
    respuesta: Optional[str]
    fuentes: List[Fuente]
    modelo_generacion: Optional[str]
    modelo_embeddings: str
    segundos: float
    advertencia: Optional[str] = None


INSTRUCCION = """Sos un asistente de consulta de la normativa de la Universidad Nacional de Luján.

Reglas, sin excepción:
1. Respondé ÚNICAMENTE con lo que dice el CONTEXTO. No uses conocimiento propio sobre la UNLu
   ni sobre normativa universitaria en general.
2. Citá la fuente de cada afirmación con el identificador que aparece entre corchetes, tal cual
   está escrito. Ejemplo: (Disposición DISPCD-CB 528/2025 — Artículo 2).
3. Si el contexto no alcanza para responder, decilo explícitamente y señalá qué falta. No
   completes ni supongas: en normativa una respuesta inventada causa más daño que una negativa.
4. Si hay normas que se contradicen o se modifican entre sí, mostralo en vez de elegir una.
5. Respondé en español rioplatense, claro y breve. Sin preámbulos."""


def generar(pregunta: str, contexto: str) -> str:
    """Única puerta a la generación: cambiar de proveedor se hace acá."""
    from openai import OpenAI
    cliente = OpenAI()
    r = cliente.chat.completions.create(
        model=MODELO_GEN,
        temperature=0,          # normativa: se busca reproducibilidad, no creatividad
        messages=[
            {'role': 'system', 'content': INSTRUCCION},
            {'role': 'user', 'content': f'CONTEXTO:\n\n{contexto}\n\n---\n\nPREGUNTA: {pregunta}'},
        ],
    )
    return r.choices[0].message.content


@app.get('/salud')
def salud():
    try:
        ix = indice()
        return {'estado': 'ok', 'chunks': len(ix), **ix.info}
    except HTTPException as e:
        return {'estado': 'sin_indice', 'detalle': e.detail}


@app.post('/consultar', response_model=Respuesta)
def consultar(c: Consulta):
    t0 = time.time()
    ix = indice()

    salida = codificador().encode([c.pregunta], return_dense=True, return_sparse=True,
                                  return_colbert_vecs=False)
    denso = salida['dense_vecs'][0]
    esparso = {k: float(v) for k, v in salida['lexical_weights'][0].items()}

    filtros = {}
    if c.anio:
        filtros['year'] = c.anio
    if c.tipo:
        filtros['document_type'] = c.tipo

    resultados = ix.buscar(denso, esparso, k=c.k, filtros=filtros or None,
                           solo_articulos=c.solo_articulos)

    fuentes = [
        Fuente(
            cita=ix.chunks[i]['cita'], texto=ix.chunks[i]['texto'],
            documento=ix.chunks[i]['documento'], source_pdf=ix.chunks[i].get('source_pdf'),
            seccion=ix.chunks[i].get('seccion'), date_issued=ix.chunks[i].get('date_issued'),
            estado=ix.chunks[i].get('estado'),
            metadata_confianza=ix.chunks[i].get('metadata_confianza'),
            puntaje=round(s, 5), ranking=d,
        )
        for i, s, d in resultados
    ]

    respuesta = advertencia = None
    if not fuentes:
        advertencia = 'No se encontró normativa relacionada con la consulta.'
    elif c.generar:
        if not os.environ.get('OPENAI_API_KEY'):
            advertencia = 'Sin OPENAI_API_KEY: se devuelven las fuentes sin respuesta generada.'
        else:
            try:
                respuesta = generar(c.pregunta, ix.contexto(resultados))
            except Exception as e:
                advertencia = f'Falló la generación ({type(e).__name__}). Se devuelven las fuentes.'

    # Si alguna fuente tiene metadata de baja confianza, se avisa: el usuario tiene que
    # saber cuándo el dato de fecha o número no está verificado contra el sistema origen.
    dudosas = [f.cita for f in fuentes if f.metadata_confianza not in ('alta', 'media')]
    if dudosas:
        aviso = f'{len(dudosas)} fuente(s) con metadata sin verificar contra el sistema origen.'
        advertencia = f'{advertencia} {aviso}'.strip() if advertencia else aviso

    return Respuesta(
        pregunta=c.pregunta, respuesta=respuesta, fuentes=fuentes,
        modelo_generacion=MODELO_GEN if respuesta else None,
        modelo_embeddings=MODELO_EMB,
        segundos=round(time.time() - t0, 3), advertencia=advertencia,
    )


@app.get('/documento/{documento}')
def documento(documento: str):
    """Todos los fragmentos de un documento, para poder auditar una cita."""
    ix = indice()
    partes = [c for c in ix.chunks if c['documento'] == documento]
    if not partes:
        raise HTTPException(404, 'documento no encontrado en el índice')
    cab = partes[0]
    return {
        'documento': documento,
        'source_pdf': cab.get('source_pdf'),
        'document_code': cab.get('document_code'),
        'document_number': cab.get('document_number'),
        'date_issued': cab.get('date_issued'),
        'titulo': cab.get('titulo'),
        'estado': cab.get('estado'),
        'metadata_confianza': cab.get('metadata_confianza'),
        'secciones': [{'seccion': p['seccion'], 'tipo': p['tipo_seccion'],
                       'cita': p['cita'], 'texto': p['texto']} for p in partes],
    }

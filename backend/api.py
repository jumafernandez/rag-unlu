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

import contextlib
import json
import os
import re
import threading
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from fastapi import Header

from . import historial, sesion
from .recuperacion import Indice


def _cargar_env():
    """Lee un .env en la raíz del repo, si existe.

    Evita tener que exportar la clave en cada sesión y, sobre todo, evita que quede
    en el historial del shell. El archivo está en .gitignore: nunca se versiona.
    Las variables ya presentes en el entorno tienen prioridad.
    """
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding='utf-8') as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea or linea.startswith('#') or '=' not in linea:
                continue
            clave, valor = linea.split('=', 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env()

RUTA_INDICE = os.environ.get('RAG_INDICE', 'indice')
MODELO_EMB = os.environ.get('RAG_MODELO_EMB', 'BAAI/bge-m3')
MODELO_GEN = os.environ.get('RAG_MODELO_GEN', 'gpt-4o-mini')

@contextlib.asynccontextmanager
async def ciclo_de_vida(_app):
    """Carga el índice y el modelo ANTES de aceptar tráfico.

    Con carga perezosa, varias consultas simultáneas al arranque disparaban cada una su
    propia construcción del índice (~2,5 GB y 37 s), y el servidor se ahogaba solo. Acá
    se carga una vez; uvicorn no atiende hasta que termina.
    """
    t0 = time.time()
    try:
        ix = indice()
        print(f'índice cargado: {len(ix)} chunks en {time.time() - t0:.0f}s', flush=True)
    except Exception as e:
        print(f'AVISO: no se pudo cargar el índice ({e}). /salud lo va a reportar.', flush=True)
    try:
        t1 = time.time()
        codificador()
        print(f'modelo de embeddings cargado en {time.time() - t1:.0f}s', flush=True)
    except Exception as e:
        print(f'AVISO: no se pudo cargar el modelo de embeddings ({e})', flush=True)
    yield


app = FastAPI(title='ChatDigesto UNLu', version='1.0', lifespan=ciclo_de_vida)


@app.middleware('http')
async def limitar_tamano(request, call_next):
    """Rechaza cuerpos desmedidos antes de leerlos.

    La validación del modelo actúa DESPUÉS de parsear el JSON: con un cuerpo de varios MB
    el servidor ya gastó tiempo y memoria antes de rechazarlo. Este corte es previo.
    """
    largo = request.headers.get('content-length')
    if largo and largo.isdigit() and int(largo) > 64 * 1024:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'La consulta es demasiado extensa.'}, status_code=413)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get('RAG_CORS', 'http://localhost:5174,http://localhost:5173').split(','),
    allow_methods=['*'], allow_headers=['*'],
)

_indice: Optional[Indice] = None
_codificador = None
# Candado para que dos peticiones simultáneas no construyan el índice dos veces.
_candado = threading.Lock()


def indice() -> Indice:
    global _indice
    if _indice is None:
        with _candado:
            if _indice is None:          # revisar de nuevo: otra petición pudo cargarlo
                if not os.path.isdir(RUTA_INDICE):
                    raise HTTPException(503, f'no está el índice en {RUTA_INDICE}')
                _indice = Indice(RUTA_INDICE)
    return _indice


def codificador():
    """Modelo de embeddings, cargado una sola vez (la primera consulta tarda ~20 s).

    Por defecto en CPU: una consulta es un texto corto y se codifica en milisegundos,
    así que la GPU no aporta acá (sí en la generación masiva del índice, que corre en
    Clementina). Además mantiene la API desplegable en cualquier servidor sin GPU.
    Se puede forzar otro dispositivo con RAG_DISPOSITIVO.
    """
    global _codificador
    if _codificador is None:
        with _candado:
            if _codificador is None:
                from sentence_transformers import SentenceTransformer
                _codificador = SentenceTransformer(
                    MODELO_EMB, device=os.environ.get('RAG_DISPOSITIVO', 'cpu'))
    return _codificador


class Turno(BaseModel):
    rol: str
    texto: str = Field(..., max_length=4000)


class Consulta(BaseModel):
    # Últimos intercambios de la conversación. Los manda el front en cada consulta, así
    # el contexto funciona también sin sesión iniciada.
    historial: List[Turno] = Field(default_factory=list, max_length=8)
    # Foco vigente que devolvió la consulta anterior. Lo mantiene el cliente, así el
    # sujeto de la conversación persiste sin depender de que haya sesión iniciada.
    foco: Optional[dict] = None
    # Si viene, la respuesta se guarda en esa conversación; si no, se crea una nueva.
    # Sin sesión iniciada este campo se ignora y no se guarda nada.
    conversacion_id: Optional[int] = None
    # El techo de longitud NO es cosmético: sin él, una sola petición con un texto muy
    # largo hace que el modelo de embeddings quede minutos codificando y deja al servidor
    # sin responder a nadie más, ni siquiera a /salud. Verificado: 1 MB lo tumba por
    # completo. 2000 caracteres son de sobra para una consulta sobre normativa.
    pregunta: str = Field(..., min_length=1, max_length=2000)
    k: int = Field(8, ge=1, le=30)
    anio: Optional[int] = None
    tipo: Optional[str] = None
    solo_articulos: bool = False
    generar: bool = True

    # --- Mecanismos activables, para poder medir la contribución de cada uno ---
    # Todos vienen encendidos: apagarlos es lo excepcional, y sirve para la ablación.
    # Cada uno es independiente, así se puede aislar el aporte en vez de medir el
    # conjunto y no saber qué parte hizo la diferencia.
    usar_lexico: bool = True        # BM25 además de la señal densa
    usar_reescritura: bool = True   # reescribir la repregunta con el historial
    usar_foco: bool = True          # seguir el sujeto de la conversación y reforzarlo
    usar_anclaje: bool = True       # mantener disponibles los actos ya citados
    usar_historial_generacion: bool = True   # pasarle los turnos previos al modelo


class Fuente(BaseModel):
    cita: str
    texto: str
    documento: str
    # Título del acto según el sistema fuente. Es lo que permite reconocer de qué trata
    # un documento sin leer el fragmento entero.
    titulo: Optional[str] = None
    source_pdf: Optional[str] = None
    seccion: Optional[str] = None
    date_issued: Optional[str] = None
    estado: Optional[str] = None
    metadata_confianza: Optional[str] = None
    puntaje: float
    ranking: dict


class Respuesta(BaseModel):
    # Se devuelve lo que efectivamente se usó para buscar y cuál es el foco vigente:
    # sin esto una evaluación no puede explicar por qué una consulta salió como salió.
    consulta_efectiva: Optional[str] = None
    foco: Optional[dict] = None
    conversacion_id: Optional[int] = None
    mensaje_id: Optional[int] = None
    pregunta: str
    respuesta: Optional[str]
    fuentes: List[Fuente]
    modelo_generacion: Optional[str]
    modelo_embeddings: str
    segundos: float
    advertencia: Optional[str] = None


# La instrucción distingue TRES situaciones. Antes trataba todo como consulta normativa, y
# entonces a un "hola" respondía "el contexto no proporciona información", que suena a error
# del sistema. La regla que no se afloja en ningún caso es la última: no inventar normativa.
INSTRUCCION = """Sos el asistente de consulta del Digesto de la Universidad Nacional de Luján.
Ayudás a encontrar y entender disposiciones, resoluciones y demás actos administrativos de
la Universidad.

Según lo que te escriban, actuás distinto:

**Si es un saludo, una cortesía o una pregunta sobre vos** ("hola", "¿qué podés hacer?",
"gracias"): respondé con naturalidad y brevedad, y ofrecé ayuda contando qué tipo de
consultas podés resolver: buscar por tema, por número de acto (por ejemplo "RESHCS 893/2025"),
por órgano emisor o por año. No cites normativa en estos casos ni menciones el contexto.

**Si es una consulta sobre normativa y el CONTEXTO tiene la respuesta**: respondé apoyándote
solo en el contexto, y citá la fuente de cada afirmación con el identificador que aparece
entre corchetes, tal cual está escrito. Ejemplo: (Disposición DISPCD-CB 528/2025 — Artículo 2).
Si hay normas que se modifican o contradicen entre sí, mostralo en vez de elegir una.

**Si es una consulta sobre normativa y el CONTEXTO no alcanza**: decilo con naturalidad y
ayudá a seguir. Sugerí reformular, precisar el tema, o buscar por número de acto si lo tienen.
No lo plantees como una falla técnica ni hables de "el contexto": la persona no sabe ni tiene
por qué saber cómo funciona el sistema por dentro.

Y esto vale siempre, sin excepción: **nunca inventes contenido normativo, números de acto ni
citas**. Si no lo tenés en el contexto, no existe para vos. En normativa una respuesta
inventada hace más daño que una negativa.

Atención especial cuando la pregunta es sobre UNA PERSONA: solo podés afirmar que participa
de algo si su nombre aparece en el fragmento que estás citando. Que el contexto traiga un
acto sobre el tema preguntado NO significa que esa persona esté mencionada ahí. Si el nombre
no figura, decí que no encontraste normativa que la vincule, aunque hayas recibido documentos
sobre el tema. Lo mismo vale para cualquier entidad concreta: carrera, departamento, cargo.

**Tiempo verbal.** Cada acto describe algo que pasó en una fecha, no un estado actual.
Respetá lo que dice el acto: si alguien renunció, decí que renunció; si fue designado, que
fue designado. Nunca conviertas un cese o una designación pasada en una afirmación en
presente: escribir "tiene el cargo de X" cuando el acto dice que renunció es afirmar lo
contrario de la fuente.

Y no afirmes vigencia. Este sistema no tiene información sobre qué normas siguen en vigor
ni sobre si un acto posterior modificó o dejó sin efecto a otro: la Universidad no lleva ese
registro. Podés decir qué dispuso un acto y en qué fecha; no puedes decir que algo "está
vigente", "sigue en vigor" ni "es la normativa actual". Si te preguntan por la situación
actual de algo, respondé con lo que dicen los actos que tenés y su fecha, y aclarás que
puede haber normativa posterior que no estés viendo.

Escribí en español rioplatense, claro y directo. Sin preámbulos ni fórmulas de relleno."""


# Señales de que una pregunta se apoya en lo dicho antes y no se sostiene sola.
RE_DEPENDE_CONTEXTO = re.compile(
    r'\b(ella|el mismo|la misma|eso|esa|ese|esto|esta|este|esos|esas|'
    r'ahi|ah[ií]|dicha|dicho|mencionad[oa]|anterior|lo que dijiste|resum[ií]|'
    r'ampli[aá]|detall[aá]|explic[aá]melo|y qu[eé] m[aá]s|contame m[aá]s)\b',
    re.IGNORECASE)


def necesita_contexto(pregunta: str, historial) -> bool:
    """¿La pregunta depende de los turnos anteriores?

    Con conversación en curso, se asume que SÍ salvo que la pregunta traiga su propia
    ancla (un número de acto). El criterio anterior buscaba pronombres explícitos y se
    perdía las repreguntas más naturales del castellano, donde el sujeto se omite:
    "¿Está en alguna comisión?" es "¿[Ella] está...?" y no contiene ningún pronombre.
    Esa falla hizo que una consulta se buscara sin el nombre de la persona y el modelo
    terminara atribuyéndole una comisión de la que no formaba parte.

    Reescribir de más cuesta una llamada corta al modelo; reescribir de menos produce
    respuestas equivocadas.
    """
    if not historial:
        return False
    # Si la pregunta nombra un acto concreto, se sostiene sola.
    if re.search(r'\d+\s*/\s*\d{2,4}', pregunta):
        return False
    return True


def reescribir_y_enfocar(pregunta: str, historial, foco_previo=None):
    """Reescribe la consulta y actualiza el foco de la conversación, en una sola llamada.

    El foco es el sujeto del que se viene hablando: una persona, una carrera, un
    departamento, un acto. Se mantiene entre turnos porque rara vez cambia, y sirve para
    dos cosas distintas: resolver referencias al reescribir, y reforzar la recuperación.

    Ese segundo uso es el que importa. Sin él, una repregunta como "¿está en alguna
    comisión?" recupera actos sobre comisiones en general y el modelo puede atribuirle a
    la persona algo que el documento no dice. Sabiendo de quién se habla, se garantiza que
    haya fragmentos que la mencionen.

    A diferencia de un sistema orientado a tareas, acá el esquema es abierto: el foco no
    sale de una lista de campos conocidos de antemano.

    Devuelve (consulta_para_buscar, foco).
    """
    from openai import OpenAI

    conversacion = '\n'.join(
        f"{'Usuario' if t.rol == 'user' else 'Asistente'}: {t.texto[:600]}"
        for t in (historial or [])[-6:]
    )
    previo = ''
    if foco_previo and foco_previo.get('entidad'):
        previo = (f"\nFOCO ACTUAL: {foco_previo['entidad']} "
                  f"({foco_previo.get('tipo') or 'sin tipo'})")

    try:
        r = OpenAI().chat.completions.create(
            model=MODELO_GEN,
            temperature=0,
            response_format={'type': 'json_object'},
            messages=[
                {'role': 'system', 'content':
                    'Analizás una conversación sobre normativa universitaria. Devolvé un JSON con:\n'
                    '- "consulta": la última pregunta reescrita para que se entienda sin leer la '
                    'conversación, resolviendo pronombres y sujetos omitidos. No agregues temas '
                    'que nadie mencionó.\n'
                    '- "entidad": el sujeto concreto del que se está hablando (nombre de persona, '
                    'carrera, departamento, órgano o acto), tal como se lo nombra. null si la '
                    'conversación no gira alrededor de ninguno.\n'
                    '- "tipo": "persona" | "carrera" | "departamento" | "organo" | "acto" | null.\n'
                    'Si el foco actual sigue vigente, repetilo; si la conversación cambió de '
                    'sujeto, devolvé el nuevo.'},
                {'role': 'user', 'content':
                    f'CONVERSACIÓN:\n{conversacion}{previo}\n\nÚLTIMA PREGUNTA: {pregunta}'},
            ],
        )
        datos = json.loads(r.choices[0].message.content or '{}')
        foco = {'entidad': (datos.get('entidad') or None), 'tipo': (datos.get('tipo') or None)}
        return (datos.get('consulta') or pregunta), foco
    except Exception:
        # Ante cualquier falla se busca con la pregunta original y se conserva el foco.
        return pregunta, (foco_previo or {'entidad': None, 'tipo': None})


# Identificadores de acto tal como aparecen # Identificadores de acto tal como aparecen en las citas: "DISPCD-CB 528/2025".
RE_ACTO_CITADO = re.compile(r'\b([A-ZÑ][A-ZÑ0-9-]{2,})\s+(\d{1,6}\s*/\s*\d{2,4})\b')


def actos_en_juego(historial) -> set:
    """Actos que ya se citaron en la conversación.

    Se los mantiene disponibles en la recuperación aunque la reescritura se desvíe: si
    se estuvo hablando de una resolución y la repregunta es "¿y qué dice el artículo 2?",
    ese acto tiene que seguir al alcance. Sin esto la continuidad depende de que la
    reescritura acierte, que es una apuesta.
    """
    encontrados = set()
    for t in (historial or [])[-6:]:
        for m in RE_ACTO_CITADO.finditer(t.texto or ''):
            encontrados.add((m.group(1).upper(), re.sub(r'\s+', '', m.group(2))))
    return encontrados


def _mensajes(pregunta: str, contexto: str, historial=None):
    mensajes = [{'role': 'system', 'content': INSTRUCCION}]
    for t in (historial or [])[-6:]:
        mensajes.append({'role': 'assistant' if t.rol != 'user' else 'user',
                         'content': t.texto[:2000]})
    mensajes.append({'role': 'user',
                     'content': f'CONTEXTO:\n\n{contexto}\n\n---\n\nPREGUNTA: {pregunta}'})
    return mensajes


def generar(pregunta: str, contexto: str, historial=None) -> str:
    """Única puerta a la generación: cambiar de proveedor se hace acá."""
    from openai import OpenAI
    cliente = OpenAI()
    r = cliente.chat.completions.create(
        model=MODELO_GEN,
        temperature=0,          # normativa: se busca reproducibilidad, no creatividad
        messages=_mensajes(pregunta, contexto, historial),
    )
    return r.choices[0].message.content


def generar_en_partes(pregunta: str, contexto: str, historial=None):
    """Igual que `generar`, pero devolviendo el texto a medida que llega.

    La respuesta tarda lo mismo; lo que cambia es que se empieza a leer enseguida en vez
    de mirar un cartel de espera. Es la diferencia entre sentir que el sistema piensa y
    sentir que se colgó.
    """
    from openai import OpenAI
    cliente = OpenAI()
    flujo = cliente.chat.completions.create(
        model=MODELO_GEN,
        temperature=0,
        messages=_mensajes(pregunta, contexto, historial),
        stream=True,
    )
    for parte in flujo:
        if parte.choices and parte.choices[0].delta.content:
            yield parte.choices[0].delta.content


@app.get('/salud')
def salud():
    """Estado del índice y alcance del corpus.

    Además del tamaño, informa hasta qué fecha llega la normativa indexada. Para quien
    consulta un digesto esa es la pregunta importante: no cuántos documentos hay, sino si
    lo que busca puede llegar a estar. Un sistema que no dice hasta cuándo cubre obliga a
    desconfiar de cada respuesta vacía.
    """
    try:
        ix = indice()
        return {'estado': 'ok', 'chunks': len(ix), **ix.info, **_alcance(ix)}
    except HTTPException as e:
        return {'estado': 'sin_indice', 'detalle': e.detail}


_ALCANCE = {}


def _alcance(ix):
    """Documentos distintos y fecha del acto más reciente. Se calcula una sola vez."""
    if not _ALCANCE:
        docs, fechas = set(), []
        for c in ix.chunks:
            docs.add(c.get('documento'))
            f = c.get('date_issued')
            if isinstance(f, str) and len(f) == 10:
                fechas.append(f)
        _ALCANCE['documentos'] = len(docs)
        _ALCANCE['normativa_hasta'] = max(fechas) if fechas else None
        try:
            import datetime
            marca = os.path.getmtime(os.path.join(RUTA_INDICE, 'densos.npy'))
            _ALCANCE['indice_generado'] = datetime.date.fromtimestamp(marca).isoformat()
        except OSError:
            _ALCANCE['indice_generado'] = None
    return _ALCANCE


@app.post('/consultar', response_model=Respuesta)
def consultar(c: Consulta, authorization: Optional[str] = Header(None)):
    t0 = time.time()
    usuario = usuario_de(authorization)
    ix = indice()

    consulta_busqueda, foco = _preparar(c)
    denso = codificador().encode([consulta_busqueda], normalize_embeddings=True)[0]

    filtros = {}
    if c.anio:
        filtros['year'] = c.anio
    if c.tipo:
        filtros['document_type'] = c.tipo

    # El texto crudo va también a la señal léxica: BM25 lo tokeniza conservando los
    # identificadores, que es lo que el vector denso no distingue.
    resultados = ix.buscar(denso,
                           texto_consulta=consulta_busqueda if c.usar_lexico else '',
                           k=c.k, filtros=filtros or None, solo_articulos=c.solo_articulos,
                           anclas=actos_en_juego(c.historial) if c.usar_anclaje else None,
                           entidad=(foco or {}).get('entidad') if c.usar_foco else None)

    fuentes = [
        Fuente(
            cita=ix.chunks[i]['cita'], texto=ix.chunks[i]['texto'],
            documento=ix.chunks[i]['documento'], titulo=ix.chunks[i].get('titulo'),
            source_pdf=ix.chunks[i].get('source_pdf'),
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
                respuesta = generar(c.pregunta, ix.contexto(resultados),
                                    c.historial if c.usar_historial_generacion else None)
            except Exception as e:
                advertencia = f'Falló la generación ({type(e).__name__}). Se devuelven las fuentes.'

    # Si alguna fuente tiene metadata de baja confianza, se avisa: el usuario tiene que
    # saber cuándo el dato de fecha o número no está verificado contra el sistema origen.
    dudosas = [f.cita for f in fuentes if f.metadata_confianza not in ('alta', 'media')]
    if dudosas:
        aviso = f'{len(dudosas)} fuente(s) con metadata sin verificar contra el sistema origen.'
        advertencia = f'{advertencia} {aviso}'.strip() if advertencia else aviso

    # Guardado: solo si hay sesión. Es accesorio, así que un fallo acá no debe romper
    # la respuesta que el usuario ya tiene.
    conversacion_id = mensaje_id = None
    if usuario:
        try:
            conversacion_id = c.conversacion_id
            if not conversacion_id:
                conversacion_id = historial.crear_conversacion(usuario, c.pregunta)
            historial.agregar_mensaje(conversacion_id, usuario, 'user', c.pregunta)
            mensaje_id = historial.agregar_mensaje(
                conversacion_id, usuario, 'assistant',
                respuesta or (fuentes and 'Se recuperó normativa relacionada.') or
                'No se encontró normativa relacionada.',
                [f.model_dump() for f in fuentes])
        except Exception:
            conversacion_id = mensaje_id = None

    return Respuesta(
        consulta_efectiva=consulta_busqueda, foco=foco,
        conversacion_id=conversacion_id, mensaje_id=mensaje_id,
        pregunta=c.pregunta, respuesta=respuesta, fuentes=fuentes,
        modelo_generacion=MODELO_GEN if respuesta else None,
        modelo_embeddings=MODELO_EMB,
        segundos=round(time.time() - t0, 3), advertencia=advertencia,
    )


# ---------------------------------------------------------------------------
# Sesión e historial
#
# El inicio de sesión es OPCIONAL: sin cuenta el asistente funciona igual, solo que no
# guarda nada. Quien entra, recupera sus consultas anteriores.
# ---------------------------------------------------------------------------

class Credencial(BaseModel):
    credencial: str = Field(..., max_length=4096)


class Titulo(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=120)


class Valoracion(BaseModel):
    util: Optional[bool] = None


def _preparar(c: 'Consulta'):
    """Consulta con la que se va a buscar y foco vigente, según qué mecanismos estén activos."""
    foco = dict(c.foco or {'entidad': None, 'tipo': None})
    if not c.historial or not os.environ.get('OPENAI_API_KEY'):
        return c.pregunta, foco
    if not (c.usar_reescritura or c.usar_foco):
        return c.pregunta, foco
    if not necesita_contexto(c.pregunta, c.historial):
        return c.pregunta, foco

    consulta, foco_nuevo = reescribir_y_enfocar(c.pregunta, c.historial, foco)
    return (consulta if c.usar_reescritura else c.pregunta), (foco_nuevo if c.usar_foco else foco)


def _sse(evento: str, datos: dict) -> str:
    return f'event: {evento}\ndata: {json.dumps(datos, ensure_ascii=False)}\n\n'


def _fuente_de(ix, i, puntaje, detalle) -> 'Fuente':
    c = ix.chunks[i]
    return Fuente(
        cita=c['cita'], texto=c['texto'], documento=c['documento'],
        titulo=c.get('titulo'),
        source_pdf=c.get('source_pdf'), seccion=c.get('seccion'),
        date_issued=c.get('date_issued'), estado=c.get('estado'),
        metadata_confianza=c.get('metadata_confianza'),
        puntaje=round(puntaje, 5), ranking=detalle,
    )


def usuario_de(autorizacion: Optional[str]) -> Optional[str]:
    """Usuario de la petición, o None si no hay sesión. No falla si no la hay."""
    if not autorizacion or not autorizacion.lower().startswith('bearer '):
        return None
    return sesion.leer_sesion(autorizacion[7:])


def exigir_usuario(autorizacion: Optional[str]) -> str:
    uid = usuario_de(autorizacion)
    if not uid:
        raise HTTPException(401, 'sesión no válida o vencida')
    return uid


@app.post('/sesion')
def iniciar_sesion(c: Credencial):
    """Recibe el token de Google, lo valida y devuelve una sesión propia."""
    try:
        datos = sesion.verificar_credencial_google(c.credencial)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception:
        # Sin detalles: un mensaje preciso acá solo le sirve a quien esté probando tokens.
        raise HTTPException(401, 'no se pudo validar la credencial')

    if not sesion.dominio_autorizado(datos['email']):
        raise HTTPException(403, 'esa cuenta no está autorizada')

    historial.registrar_usuario(datos['sub'], datos['email'], datos['nombre'])
    return {'token': sesion.emitir_sesion(datos['sub']),
            'nombre': datos['nombre'], 'correo': datos['email']}


@app.get('/conversaciones')
def conversaciones(authorization: Optional[str] = Header(None)):
    return {'conversaciones': historial.listar_conversaciones(exigir_usuario(authorization))}


@app.get('/conversaciones/{cid}')
def conversacion(cid: int, authorization: Optional[str] = Header(None)):
    d = historial.leer_conversacion(cid, exigir_usuario(authorization))
    if d is None:
        raise HTTPException(404, 'conversación no encontrada')
    return d


@app.patch('/conversaciones/{cid}')
def renombrar(cid: int, t: Titulo, authorization: Optional[str] = Header(None)):
    if not historial.renombrar_conversacion(cid, exigir_usuario(authorization), t.titulo):
        raise HTTPException(404, 'conversación no encontrada')
    return {'ok': True}


@app.delete('/conversaciones/{cid}')
def borrar(cid: int, authorization: Optional[str] = Header(None)):
    if not historial.borrar_conversacion(cid, exigir_usuario(authorization)):
        raise HTTPException(404, 'conversación no encontrada')
    return {'ok': True}


@app.post('/mensajes/{mid}/valoracion')
def valorar(mid: int, v: Valoracion, authorization: Optional[str] = Header(None)):
    if not historial.valorar_mensaje(mid, exigir_usuario(authorization), v.util):
        raise HTTPException(404, 'mensaje no encontrado')
    return {'ok': True}


@app.post('/consultar/flujo')
def consultar_en_flujo(c: Consulta, authorization: Optional[str] = Header(None)):
    """Igual que /consultar pero devolviendo la respuesta a medida que se genera.

    Formato: server-sent events. Primero viaja un evento `fuentes` con lo recuperado (así
    la interfaz ya puede mostrarlas), después los fragmentos de texto, y al final un
    evento `fin` con los identificadores para guardar y valorar.
    """
    from fastapi.responses import StreamingResponse

    t0 = time.time()
    usuario = usuario_de(authorization)
    ix = indice()

    # Una repregunta ("¿me resumís qué sabés de ella?") no se sostiene sola: se reescribe
    # con lo conversado antes de buscar. Si no hace falta, se busca tal cual y no se paga
    # una llamada extra al modelo.
    consulta_busqueda, foco = _preparar(c)
    denso = codificador().encode([consulta_busqueda], normalize_embeddings=True)[0]
    filtros = {}
    if c.anio:
        filtros['year'] = c.anio
    if c.tipo:
        filtros['document_type'] = c.tipo
    resultados = ix.buscar(denso,
                           texto_consulta=consulta_busqueda if c.usar_lexico else '',
                           k=c.k, filtros=filtros or None, solo_articulos=c.solo_articulos,
                           anclas=actos_en_juego(c.historial) if c.usar_anclaje else None,
                           entidad=(foco or {}).get('entidad') if c.usar_foco else None)
    fuentes = [_fuente_de(ix, i, s, d) for i, s, d in resultados]

    def eventos():
        yield _sse('fuentes', {'fuentes': [f.model_dump() for f in fuentes],
                               'consulta_efectiva': consulta_busqueda, 'foco': foco})

        partes = []
        if not fuentes:
            texto = 'No encontré normativa relacionada con esa consulta.'
            partes.append(texto)
            yield _sse('texto', {'t': texto})
        elif not os.environ.get('OPENAI_API_KEY'):
            yield _sse('aviso', {'mensaje': 'Sin clave de generación: se muestran las fuentes.'})
        else:
            try:
                for parte in generar_en_partes(
                        c.pregunta, ix.contexto(resultados),
                        c.historial if c.usar_historial_generacion else None):
                    partes.append(parte)
                    yield _sse('texto', {'t': parte})
            except Exception as e:
                yield _sse('aviso', {'mensaje': f'Falló la generación ({type(e).__name__}).'})

        respuesta = ''.join(partes)
        conversacion_id = mensaje_id = None
        if usuario:
            try:
                conversacion_id = c.conversacion_id or historial.crear_conversacion(usuario, c.pregunta)
                historial.agregar_mensaje(conversacion_id, usuario, 'user', c.pregunta)
                mensaje_id = historial.agregar_mensaje(
                    conversacion_id, usuario, 'assistant',
                    respuesta or 'Se recuperó normativa relacionada.',
                    [f.model_dump() for f in fuentes])
            except Exception:
                conversacion_id = mensaje_id = None

        yield _sse('fin', {'conversacion_id': conversacion_id, 'mensaje_id': mensaje_id,
                           'segundos': round(time.time() - t0, 3)})

    return StreamingResponse(eventos(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Accel-Buffering': 'no'})


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

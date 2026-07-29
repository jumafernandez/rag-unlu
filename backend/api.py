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
import pathlib
import re
import threading
import unicodedata
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fastapi import Header

from . import admin, historial, sesion
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
    """El almacén de fragmentos.

    Si están los artefactos de `pipeline/construir_indice.py` ---la tabla SQLite y el
    índice FAISS--- se usa ese camino: devuelve los mismos documentos que la carga en
    memoria y ocupa bastante menos. Si no están, se cae al comportamiento anterior, que
    solo necesita chunks.jsonl y densos.npy.
    """
    global _indice
    if _indice is None:
        with _candado:
            if _indice is None:          # revisar de nuevo: otra petición pudo cargarlo
                if not os.path.isdir(RUTA_INDICE):
                    raise HTTPException(503, f'no está el índice en {RUTA_INDICE}')
                artefactos = all(os.path.exists(os.path.join(RUTA_INDICE, x))
                                 for x in ('chunks.sqlite', 'vectores.faiss'))
                if artefactos and os.environ.get('RAG_ALMACEN', 'sql') != 'memoria':
                    from .almacen import AlmacenSQL
                    _indice = AlmacenSQL(RUTA_INDICE)
                else:
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
    # Estado de la conversación que devolvió la consulta anterior. Lo mantiene el cliente,
    # así persiste sin depender de que haya sesión iniciada. Ver EstadoDialogo.
    estado: Optional[dict] = None
    # Nombre anterior del mismo campo, cuando el estado era un único foco. Se acepta para
    # que un cliente con el bundle viejo en caché no pierda la continuidad.
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
    # Enlace permanente al PDF publicado en el portal. Con esto la cadena de trazabilidad
    # llega hasta el documento oficial y no se corta en nuestro índice: cualquiera puede
    # abrir el acto y comprobar que dice lo que la respuesta afirma.
    url_documento: Optional[str] = None
    # Mismo PDF, servido por nosotros para que el navegador lo MUESTRE en vez de
    # descargarlo. Ver el endpoint /pdf.
    url_ver: Optional[str] = None
    # Fecha del acto según el propio documento. La tabla del portal muestra la de
    # autorización, que es posterior y a veces de otro día: tenerlas separadas evita que el
    # asistente informe una fecha que no coincide con la que la persona lee en el PDF.
    fecha_acto: Optional[str] = None
    puntaje: float
    ranking: dict


class Respuesta(BaseModel):
    # Se devuelve lo que efectivamente se usó para buscar y el estado vigente: sin esto
    # una evaluación no puede explicar por qué una consulta salió como salió.
    consulta_efectiva: Optional[str] = None
    estado: Optional[dict] = None
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

**Si te piden más** ("¿no hay más?", "¿qué otra cosa hay?", "ampliá"): esa rama de arriba NO
es la que corresponde salvo que el contexto venga realmente vacío. Fijate qué hay en el
contexto que todavía no contaste y contalo, con su cita. Aunque sea un solo acto más, aunque
sea un detalle del mismo acto que ya mencionaste. Recién si de verdad no queda nada sin
mencionar, decílo, y ahí sí ofrecé cómo seguir.

Nunca contestes "encontré varios actos, decime cuál te interesa" teniendo esos actos delante:
la persona te está pidiendo justamente que se los cuentes. Preguntar en vez de responder,
cuando tenés con qué responder, es hacerle perder el tiempo.

**Sé generoso con lo que encontraste.** El riesgo de este sistema no es extenderse de más:
es quedarse corto y que alguien crea que no existe normativa que sí existe. Si el contexto
trae cinco actos pertinentes, mencioná los cinco. Sin relleno ni preámbulos, pero completo:
breve no es lo mismo que escueto.

Y esto vale siempre, sin excepción: **nunca inventes contenido normativo, números de acto ni
citas**. Si no lo tenés en el contexto, no existe para vos. En normativa una respuesta
inventada hace más daño que una negativa.

Atención especial cuando la pregunta es sobre UNA PERSONA: solo podés afirmar que participa
de algo si su nombre aparece en el fragmento que estás citando. Que el contexto traiga un
acto sobre el tema preguntado NO significa que esa persona esté mencionada ahí. Si el nombre
no figura, decí que no encontraste normativa que la vincule, aunque hayas recibido documentos
sobre el tema. Lo mismo vale para cualquier entidad concreta: carrera, departamento, cargo.

Ojo con el reverso de esa regla. "¿Conocés a X?", "¿sabés algo de X?" o "¿quién es X?" no son
preguntas de sí o no sobre trato personal: te están pidiendo qué dice la normativa sobre X.
Si el contexto tiene actos donde X figura, contá lo que dicen. **Nunca abras con una negativa
cuando después vas a informar algo**: empezar con "no encontré normativa que vincule a X" y
seguir con lo que sí encontraste se lee como que el sistema falló, y además se contradice.
Si tenés información, empezá por la información.

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


def nombra_entidad(texto: str, entidad: str) -> bool:
    """¿El texto menciona a la entidad?

    Se compara por piezas y sin tildes para que "Carina Duna" reconozca a "Carina Natalia
    Duna" y a "DUNA, Carina": la reescritura suele completar o reordenar el nombre.
    """
    if not entidad or not texto:
        return False
    quitar = lambda t: re.sub(r'[̀-ͯ]', '', unicodedata.normalize('NFD', t)).lower()
    objetivo, base = quitar(texto), quitar(entidad)
    piezas = [p for p in re.split(r'\W+', base) if len(p) >= 4]
    if not piezas:
        return base in objetivo
    return sum(1 for p in piezas if p in objetivo) >= min(2, len(piezas))


def reescribir_y_enfocar(pregunta: str, historial, estado_previo=None):
    """Reescribe la consulta y actualiza el estado de la conversación, en una sola llamada.

    El estado resume de qué se viene hablando: la entidad —una persona, una carrera, un
    departamento— y los actos mencionados. Sirve para dos cosas distintas: resolver
    referencias al reescribir, y pesar la recuperación.

    Ese segundo uso es el que importa. Sin él, una repregunta como "¿está en alguna
    comisión?" recupera actos sobre comisiones en general y el modelo puede atribuirle a
    la persona algo que el documento no dice. Sabiendo de quién se habla, se garantiza que
    haya fragmentos que la mencionen.

    A diferencia de un sistema orientado a tareas, acá el esquema es abierto: la entidad
    no sale de una lista de campos conocidos de antemano.

    Si el usuario fijó la entidad a mano, el modelo NO la puede cambiar: su decisión vale
    más que la inferencia, y si no fuera así editarla no serviría de nada.

    Devuelve (consulta_para_buscar, estado).
    """
    from openai import OpenAI

    estado = normalizar_estado(estado_previo)
    fijada = estado['entidad_origen'] == 'usuario' and estado['entidad']

    conversacion = '\n'.join(
        f"{'Usuario' if t.rol == 'user' else 'Asistente'}: {t.texto[:600]}"
        for t in (historial or [])[-6:]
    )
    previo = ''
    if estado.get('entidad'):
        previo = (f"\nENTIDAD ACTUAL: {estado['entidad']} "
                  f"({estado.get('tipo') or 'sin tipo'})")
        if fijada:
            previo += ' [FIJADA POR EL USUARIO: es una corrección, no la cambies]'

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
                    'Si la entidad actual sigue vigente, repetila; si la conversación cambió '
                    'de sujeto, devolvé la nueva.\n' + CRITERIO_ENTIDAD_UNICA + '\n'
                    'Si la entidad viene FIJADA POR EL USUARIO, es una corrección suya: te está '
                    'diciendo que interpretaste mal de qué se habla. Reescribí la pregunta sobre '
                    'esa entidad. La única excepción es que la última pregunta sea claramente '
                    'sobre otra cosa; ahí reescribila según la pregunta.'},
                {'role': 'user', 'content':
                    f'CONVERSACIÓN:\n{conversacion}{previo}\n\nÚLTIMA PREGUNTA: {pregunta}'},
            ],
        )
        datos = json.loads(r.choices[0].message.content or '{}')

        # Aviso de discrepancia: el usuario fijó una entidad y la consulta con la que se
        # va a buscar no la nombra. Se decide acá y no se le pregunta al modelo: cuando se
        # le pedía que juzgara si su desvío "correspondía", contestaba que la pregunta era
        # de otro tema y el aviso no aparecía nunca. Un aviso que a veces sale y a veces no,
        # sin que se note cuál de las dos, es peor que no tenerlo.
        #
        # Es transitorio a propósito: describe UNA decisión de ESTE turno, y
        # normalizar_estado() lo descarta cuando el cliente devuelve el estado.
        consulta_nueva = datos.get('consulta') or pregunta
        if fijada and not nombra_entidad(consulta_nueva, estado['entidad']):
            estado['discrepancia'] = estado['entidad']

        if not fijada:
            nueva = (datos.get('entidad') or None)
            if nueva != estado['entidad']:
                # Cambió el sujeto: vuelve a ser una inferencia del sistema.
                estado['entidad_origen'] = 'sistema'
            estado.update(entidad_valida(nueva, datos.get('tipo') or None))
        return (datos.get('consulta') or pregunta), estado
    except Exception:
        # Ante cualquier falla se busca con la pregunta original y se conserva el estado.
        return pregunta, estado


# Identificadores de acto tal como aparecen # Identificadores de acto tal como aparecen en las citas: "DISPCD-CB 528/2025".
RE_ACTO_CITADO = re.compile(r'\b([A-ZÑ][A-ZÑ0-9-]{2,})\s+(\d{1,6}\s*/\s*\d{2,4})\b')


# El estado sigue UNA entidad. Ante una consulta sobre varias cosas conviene no seguir
# ninguna: un foco inventado es peor que un foco vacío, porque pesa en los turnos siguientes
# sin que nadie lo haya pedido.
CRITERIO_ENTIDAD_UNICA = (
    'Devolvé null en "entidad" si la consulta es sobre un CONJUNTO y no sobre uno solo: "¿qué diplomaturas se crearon?", "¿qué resoluciones hay sobre licencias?", "actos del Departamento en 2025". Aunque la respuesta mencione varios, el sujeto de la consulta no es ninguno de ellos en particular. Elegir el primero que apareció sería inventar un foco que la persona no pidió, y ese foco después pesa en las búsquedas siguientes.\\nDevolvé una entidad solo cuando la consulta gira alrededor de UNA sola, nombrada o inequívoca por el contexto.'
)


TIPOS_ENTIDAD = ('persona', 'carrera', 'departamento', 'organo', 'acto')


def entidad_valida(entidad, tipo):
    """La entidad vale solo si su tipo es uno de los que el estado sabe seguir.

    Regla en el código y no en la instrucción: pidiéndolo por prompt, ante "¿qué
    resoluciones hay sobre licencias del personal docente?" devolvía "licencias del personal
    docente" con tipo nulo. Eso es un TEMA, y un tema no es un sujeto que convenga seguir
    entre turnos: el tema ya viaja en la consulta, mientras que el sujeto es lo que la
    repregunta omite. Sin tipo válido, no hay entidad.
    """
    if entidad and tipo in TIPOS_ENTIDAD:
        return {'entidad': entidad, 'tipo': tipo}
    return {'entidad': None, 'tipo': None}


def detectar_entidad(pregunta: str, respuesta: str) -> dict:
    """Sujeto de la conversación a partir del primer intercambio.

    Existe porque la entidad salía de `reescribir_y_enfocar`, que solo corre cuando hay
    historial ---su trabajo es resolver referencias---. Con lo cual en el primer turno la
    barra de contexto mostraba los actos detectados pero el sujeto en "sin definir", aunque
    la pregunta lo nombrara con toda claridad. Detectarlo no necesita historial.

    Se llama DESPUÉS de generar, en el mismo lugar donde se suman los actos citados: para
    ese momento la respuesta ya está en pantalla, así que el usuario no espera por esto.
    """
    from openai import OpenAI
    try:
        r = OpenAI().chat.completions.create(
            model=MODELO_GEN, temperature=0, response_format={'type': 'json_object'},
            messages=[
                {'role': 'system', 'content':
                    'Devolvé un JSON con el sujeto concreto sobre el que gira este intercambio '
                    'de una consulta de normativa universitaria:\n'
                    '- "entidad": nombre de persona, carrera, departamento, órgano o acto, tal '
                    'como se lo nombra. null si la consulta es general y no gira alrededor de '
                    'ninguno.\n'
                    '- "tipo": "persona" | "carrera" | "departamento" | "organo" | "acto" | null.\n'
                    + CRITERIO_ENTIDAD_UNICA},
                {'role': 'user', 'content': f'PREGUNTA: {pregunta}\n\nRESPUESTA: {respuesta[:1500]}'},
            ])
        d = json.loads(r.choices[0].message.content or '{}')
        return entidad_valida(d.get('entidad') or None, d.get('tipo') or None)
    except Exception:
        return {'entidad': None, 'tipo': None}


def actos_en_juego(historial, pregunta='') -> set:
    """Actos citados en los turnos recientes y en la pregunta actual.

    Se los mantiene disponibles en la recuperación aunque la reescritura se desvíe: si
    se estuvo hablando de una resolución y la repregunta es "¿y qué dice el artículo 2?",
    ese acto tiene que seguir al alcance. Sin esto la continuidad depende de que la
    reescritura acierte, que es una apuesta.

    La ventana acota lo que se DESCUBRE, no lo que se recuerda: una vez que un acto entra
    al estado se queda ahí. Antes no había estado y la ventana era el único registro, así
    que un acto de siete turnos atrás desaparecía de golpe en medio de la conversación.
    """
    encontrados = set()
    textos = [t.texto or '' for t in (historial or [])[-6:]]
    if pregunta:
        textos.append(pregunta)
    for texto in textos:
        for m in RE_ACTO_CITADO.finditer(texto):
            encontrados.add((m.group(1).upper(), re.sub(r'\s+', '', m.group(2))))
    return encontrados


# --- Estado de la conversación -----------------------------------------------------
# Dos slots: la ENTIDAD de la que se habla y los ACTOS que se vienen mencionando. Cada
# valor lleva su ORIGEN, y del origen sale el peso con el que entra en la recuperación.
#
# Que el usuario fije un valor no es solo corregir un error: es afirmarlo con más
# confianza que la que puede tener el sistema, y por eso pesa más. Y lo que descarta no
# se borra, queda con peso cero y a la vista, porque saber qué infirió el sistema es
# parte de poder controlarlo.
PESOS_POR_ORIGEN = {'sistema': 0.5, 'usuario': 1.0, 'descartado': 0.0}


def peso_de(origen) -> float:
    return PESOS_POR_ORIGEN.get(origen or 'sistema', PESOS_POR_ORIGEN['sistema'])


def estado_vacio() -> dict:
    return {'entidad': None, 'tipo': None, 'entidad_origen': 'sistema', 'actos': []}


def normalizar_estado(bruto) -> dict:
    """Sanea el estado que llega del cliente.

    Es entrada de usuario —viaja por la red y puede venir editada a mano, incompleta o
    de una versión anterior—, así que no se confía en su forma. También acepta el formato
    viejo de foco simple, que traía solo entidad y tipo.
    """
    e = estado_vacio()
    if not isinstance(bruto, dict):
        return e
    ent = bruto.get('entidad')
    e['entidad'] = ent.strip()[:200] if isinstance(ent, str) and ent.strip() else None
    tipo = bruto.get('tipo')
    e['tipo'] = tipo.strip()[:40] if isinstance(tipo, str) and tipo.strip() else None
    origen = bruto.get('entidad_origen')
    e['entidad_origen'] = origen if origen in PESOS_POR_ORIGEN else 'sistema'
    for a in (bruto.get('actos') or [])[:60]:
        if not isinstance(a, dict):
            continue
        codigo, numero = a.get('codigo'), a.get('numero')
        if not (isinstance(codigo, str) and isinstance(numero, str) and codigo and numero):
            continue
        og = a.get('origen')
        e['actos'].append({
            'codigo': codigo.strip().upper()[:40],
            'numero': re.sub(r'\s+', '', numero)[:20],
            'origen': og if og in PESOS_POR_ORIGEN else 'sistema',
        })
    return e


def fusionar_actos(estado: dict, mencionados: set) -> dict:
    """Suma al estado los actos que aparecieron recién, sin pisar lo que decidió el usuario.

    Los actos NO se acumulan sin control: lo que los acota no es un tope arbitrario sino
    el propio puntaje. Un acto que el usuario no fijó entra con peso medio, que lo deja
    por debajo de casi cualquier resultado que la búsqueda haya traído por relevancia, y
    el corte en k se encarga del resto. Un acto que el usuario fijó pesa el doble y sí
    compite. La lista puede crecer; el ranking no se inunda.
    """
    ya = {(a['codigo'], a['numero']) for a in estado['actos']}
    for codigo, numero in sorted(mencionados):
        if (codigo, numero) not in ya:
            estado['actos'].append({'codigo': codigo, 'numero': numero, 'origen': 'sistema'})
    return estado


def pesos_de_actos(estado: dict) -> dict:
    """{(codigo, numero): peso} para los actos que todavía pesan."""
    salida = {}
    for a in estado.get('actos') or []:
        w = peso_de(a.get('origen'))
        if w > 0:
            salida[(a['codigo'], a['numero'])] = w
    return salida


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
        docs, fechas = ix.documentos_y_fechas()
        _ALCANCE['documentos'] = docs
        # Percentil 99, no el máximo. El máximo lo fija un puñado de actos con fecha
        # atípica: con el corpus recolectado hasta el 10 de abril, el máximo decía 6 de
        # julio, y la interfaz anunciaba una actualización que no existía. Informar de
        # menos es preferible a informar de más sobre la propia cobertura.
        _ALCANCE['normativa_hasta'] = fechas
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

    consulta_busqueda, estado = _preparar(c)
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
                           pesos_actos=pesos_de_actos(estado) if c.usar_anclaje else None,
                           peso_entidad=peso_de(estado.get('entidad_origen')),
                           entidad=estado.get('entidad') if c.usar_foco else None)

    # Se arma con la misma función que el camino en flujo. Antes estaba duplicado acá, y
    # agregar un campo a la fuente lo dejaba funcionando en un endpoint y no en el otro.
    fuentes = [_fuente_de(ix, i, s, d) for i, s, d in resultados]

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

    # Los actos que la respuesta acaba de citar entran al estado ahora, no en el turno
    # siguiente. Sin esto el estado va siempre un turno atrasado: se arma con el historial,
    # y la respuesta que el usuario está leyendo todavía no es historial.
    if respuesta:
        estado = fusionar_actos(estado, actos_en_juego(None, respuesta))
        # Primer turno: la entidad todavía no está porque la reescritura no corrió.
        if not estado.get('entidad') and c.usar_foco and os.environ.get('OPENAI_API_KEY'):
            estado.update(detectar_entidad(c.pregunta, respuesta))

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
        consulta_efectiva=consulta_busqueda, estado=estado,
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
    """Consulta con la que se va a buscar y estado vigente, según qué mecanismos estén activos."""
    estado = normalizar_estado(c.estado if c.estado is not None else c.foco)

    # Los actos nombrados se incorporan al estado aunque no haya reescritura ni modelo:
    # el estado refleja la conversación, no depende de que alguien la interprete.
    estado = fusionar_actos(estado, actos_en_juego(c.historial, c.pregunta))

    if not c.historial or not os.environ.get('OPENAI_API_KEY'):
        return c.pregunta, estado
    if not (c.usar_reescritura or c.usar_foco):
        return c.pregunta, estado
    if not necesita_contexto(c.pregunta, c.historial):
        return c.pregunta, estado

    previo = dict(estado)
    consulta, nuevo = reescribir_y_enfocar(c.pregunta, c.historial, estado)
    return (consulta if c.usar_reescritura else c.pregunta), (nuevo if c.usar_foco else previo)


def _sse(evento: str, datos: dict) -> str:
    return f'event: {evento}\ndata: {json.dumps(datos, ensure_ascii=False)}\n\n'


def _fuente_de(ix, i, puntaje, detalle) -> 'Fuente':
    c = ix.chunk(i)
    return Fuente(
        cita=c['cita'], texto=c['texto'], documento=c['documento'],
        titulo=c.get('titulo'),
        source_pdf=c.get('source_pdf'), seccion=c.get('seccion'),
        date_issued=c.get('date_issued'), estado=c.get('estado'),
        metadata_confianza=c.get('metadata_confianza'),
        url_documento=c.get('url_documento') or None,
        url_ver=(f"/pdf/{c['id_archivo']}" if c.get('id_archivo') else None),
        fecha_acto=c.get('fecha_acto') or None,
        puntaje=round(puntaje, 5), ranking=detalle,
    )


def usuario_de(autorizacion: Optional[str]) -> Optional[str]:
    """Usuario de la petición, o None si no hay sesión. No falla si no la hay."""
    if not autorizacion or not autorizacion.lower().startswith('bearer '):
        return None
    return sesion.leer_sesion(autorizacion[7:])


def exigir_admin(autorizacion: Optional[str]) -> str:
    """Sub del administrador, o 403. Toda ruta de /admin pasa por acá.

    Se comprueba en el servidor y no en la interfaz: esconder el botón no es una medida de
    seguridad. Un panel que puede cambiar el modelo de generación o lanzar el pipeline tiene
    que verificar quién pide, no confiar en quién dice ser.
    """
    uid = exigir_usuario(autorizacion)
    if not admin.es_admin_por_sub(uid):
        raise HTTPException(403, 'requiere permisos de administración')
    return uid


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


class MensajeAdoptado(BaseModel):
    rol: str
    texto: str = Field(..., max_length=8000)
    # Las fuentes viajan con el mensaje. Sin ellas la conversación adoptada quedaría con
    # respuestas sin cita, que es justo lo que este sistema no puede permitirse.
    fuentes: Optional[list] = None


class Adopcion(BaseModel):
    mensajes: List[MensajeAdoptado] = Field(..., max_length=40)


@app.post('/conversaciones')
def adoptar(a: Adopcion, authorization: Optional[str] = Header(None)):
    """Guarda en el historial una conversación que venía sin sesión iniciada.

    Quien consulta sin cuenta y después entra lo hace, casi siempre, porque quiere
    conservar lo que está viendo. Perdérselo en ese momento es el peor momento posible.

    Sin esto, además, quedaba algo peor que molesto: la conversación seguía en pantalla
    pero no en la base, y la siguiente consulta guardaba una respuesta apoyada en turnos
    que no quedaban registrados en ninguna parte. Para un sistema que promete trazabilidad,
    una respuesta guardada sin el intercambio que la explica es un registro roto.
    """
    usuario = exigir_usuario(authorization)
    if not a.mensajes:
        raise HTTPException(400, 'no hay mensajes para guardar')

    primera = next((m.texto for m in a.mensajes if m.rol == 'user'), None)
    cid = historial.crear_conversacion(usuario, primera or 'Consulta')
    for m in a.mensajes:
        historial.agregar_mensaje(cid, usuario,
                                  'user' if m.rol == 'user' else 'assistant',
                                  m.texto, m.fuentes)
    return {'conversacion_id': cid}


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
    consulta_busqueda, estado = _preparar(c)
    denso = codificador().encode([consulta_busqueda], normalize_embeddings=True)[0]
    filtros = {}
    if c.anio:
        filtros['year'] = c.anio
    if c.tipo:
        filtros['document_type'] = c.tipo
    resultados = ix.buscar(denso,
                           texto_consulta=consulta_busqueda if c.usar_lexico else '',
                           k=c.k, filtros=filtros or None, solo_articulos=c.solo_articulos,
                           pesos_actos=pesos_de_actos(estado) if c.usar_anclaje else None,
                           peso_entidad=peso_de(estado.get('entidad_origen')),
                           entidad=estado.get('entidad') if c.usar_foco else None)
    fuentes = [_fuente_de(ix, i, s, d) for i, s, d in resultados]

    def eventos():
        # Se reasigna más abajo, al sumarle los actos que la respuesta acabe de citar.
        # Sin declararlo, esa asignación convertiría a `estado` en local de esta función
        # y este primer yield fallaría por referenciarla antes de asignarla.
        nonlocal estado

        yield _sse('fuentes', {'fuentes': [f.model_dump() for f in fuentes],
                               'consulta_efectiva': consulta_busqueda, 'estado': estado})

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

        # Igual que en /consultar: los actos recién citados entran al estado en este turno.
        # Se emite actualizado para que la barra de contexto muestre lo que el usuario
        # acaba de leer y no lo del turno anterior.
        if respuesta:
            nuevo = fusionar_actos(dict(estado, actos=list(estado.get('actos') or [])),
                                   actos_en_juego(None, respuesta))
            if not nuevo.get('entidad') and c.usar_foco and os.environ.get('OPENAI_API_KEY'):
                nuevo.update(detectar_entidad(c.pregunta, respuesta))
            if nuevo != estado:
                estado = nuevo
                yield _sse('estado', {'estado': estado})

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
    partes = ix.fragmentos_de_documento(documento)
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


@app.get('/pdf/{id_archivo}')
def pdf_en_linea(id_archivo: str):
    """Sirve el PDF del acto para que se VEA en el navegador, no que se descargue.

    El portal lo entrega con `Content-Disposition: attachment`, y ante esa cabecera el
    navegador guarda el archivo en lugar de mostrarlo. No se puede cambiar del lado del
    portal, así que se reenvía acá con la cabecera correcta.

    Solo acepta identificadores que existan en el índice, y la URL de origen sale de lo que
    tenemos guardado: si aceptara una URL como parámetro sería un proxy abierto, y cualquiera
    podría usar este servidor para pedir lo que quisiera a donde quisiera.
    """
    import urllib.request
    from fastapi.responses import Response

    if not re.fullmatch(r'[0-9a-fA-F-]{36}', id_archivo or ''):
        raise HTTPException(400, 'identificador inválido')

    url = indice().url_de_archivo(id_archivo)
    if not url:
        raise HTTPException(404, 'el documento no está en el índice')

    try:
        pedido = urllib.request.Request(url, headers={'User-Agent': 'rag-unlu/1.0'})
        with urllib.request.urlopen(pedido, timeout=60) as r:
            datos = r.read()
    except Exception:
        raise HTTPException(502, 'el portal no entregó el documento')
    if not datos.startswith(b'%PDF'):
        raise HTTPException(502, 'el portal no devolvió un PDF')

    return Response(
        content=datos, media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{id_archivo}.pdf"',
                 # Es normativa publicada y no cambia: se puede cachear con tranquilidad.
                 'Cache-Control': 'public, max-age=86400'})


# ---------------------------------------------------------------------------
# Panel de administración
#
# Solo lectura y ajustes. Las operaciones del pipeline no se ejecutan desde acá: viven en
# scripts y el panel las lanzará a través del registro de corridas, para que todo lo que se
# puede hacer por la interfaz se pueda hacer y explicar desde una terminal.
# ---------------------------------------------------------------------------


class Tema(BaseModel):
    colores: dict


class CorreoAdmin(BaseModel):
    correo: str = Field(..., max_length=200)


@app.get('/admin/soy')
def admin_soy(authorization: Optional[str] = Header(None)):
    """Si el usuario actual es administrador. La interfaz usa esto para mostrar la entrada."""
    uid = usuario_de(authorization)
    return {'admin': bool(uid) and admin.es_admin_por_sub(uid)}


@app.get('/admin/estado')
def admin_estado(authorization: Optional[str] = Header(None)):
    exigir_admin(authorization)
    try:
        ix = indice()
    except Exception:
        ix = None
    return admin.estado(ix, RUTA_INDICE)


@app.get('/admin/documentos')
def admin_documentos(authorization: Optional[str] = Header(None)):
    exigir_admin(authorization)
    return {'secciones': admin.documentos_por_seccion(indice())}


@app.get('/admin/tema')
def admin_tema_leer():
    """Sin autenticación a propósito: la interfaz necesita los colores para pintarse antes
    de que nadie inicie sesión, y son públicos por naturaleza."""
    return {'tema': admin.leer_tema(), 'por_omision': admin.TEMA_POR_OMISION}


@app.put('/admin/tema')
def admin_tema_guardar(t: Tema, authorization: Optional[str] = Header(None)):
    uid = exigir_admin(authorization)
    try:
        return {'tema': admin.guardar_tema(t.colores, admin.correo_de(uid) or uid)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get('/admin/admins')
def admin_lista(authorization: Optional[str] = Header(None)):
    exigir_admin(authorization)
    return {'admins': admin.listar_admins()}


@app.post('/admin/admins')
def admin_agregar(c: CorreoAdmin, authorization: Optional[str] = Header(None)):
    uid = exigir_admin(authorization)
    try:
        admin.agregar_admin(c.correo, admin.correo_de(uid) or uid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {'admins': admin.listar_admins()}


@app.delete('/admin/admins/{correo}')
def admin_quitar(correo: str, authorization: Optional[str] = Header(None)):
    exigir_admin(authorization)
    if not admin.quitar_admin(correo):
        raise HTTPException(400, 'no se puede quitar: no existe o viene del entorno')
    return {'admins': admin.listar_admins()}


# ---------------------------------------------------------------------------
# Interfaz web
#
# Si existe una compilación del front (frontend/dist), se sirve desde acá. Con eso la
# aplicación entera queda en UN solo origen: no hace falta un segundo servidor, ni
# configurar CORS, ni saber de antemano con qué dominio se va a publicar. En desarrollo
# esta carpeta no existe y el front se sirve con Vite como siempre.
#
# El montaje va al FINAL a propósito: monta la raíz, así que cualquier ruta de la API
# declarada después quedaría tapada por él.
# ---------------------------------------------------------------------------

_DIST = pathlib.Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
if _DIST.is_dir():
    app.mount('/', StaticFiles(directory=str(_DIST), html=True), name='web')

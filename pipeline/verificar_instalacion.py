"""¿Puede ESTA instalación correr cada paso del pipeline? Un informe, no un bug por vez.

Por qué existe. El sistema se construyó en una máquina y se desplegó en otra, y las dos
diferían en cosas que ningún paso declara: dependencias que solo usa el extractor, rutas
que el sandbox del servicio no deja escribir, la salida a internet por un proxy que
únicamente heredan los procesos hijos del servicio. Cada una de esas diferencias apareció
sola, en producción, disfrazada de otra cosa: el extractor fallando en los 96 documentos
sin más explicación que ERROR_EXTRACTOR, el catálogo muriendo a los 75 segundos, una
consulta al portal agotando un timeout de nueve minutos.

Ninguna era difícil de arreglar. Lo caro fue encontrarlas de a una, usando el sistema.

Esto las busca todas juntas y de una vez, sin escribir nada que importe ni gastar plata.
Está pensado para correrse recién instalado, antes de confiar en el panel, y cada vez que
se cambia algo del entorno.

    python -m pipeline.verificar_instalacion

Devuelve 0 si todo lo indispensable está, y 1 si falta algo que va a impedir que un paso
funcione. Lo que es opcional se informa como aviso y no cambia el código de salida.
"""
import argparse
import ast
import importlib.util
import os
import shutil
import sys
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cada paso del panel, con el archivo que lo ejecuta. Las dependencias NO se listan a
# mano: se leen de los propios archivos. Una lista escrita aparte envejece en silencio, y
# el silencio es justamente lo que estamos tratando de evitar.
PASOS = [
    ('1 · Catálogo', ['scrapers/recolectar_api.py', 'scrapers/catalogo_comun.py']),
    ('2 · Descarga', ['scrapers/bajar_pdfs.py']),
    # `extractor/` va como directorio y no archivo por archivo: el extractor se apoya en
    # módulos hermanos, y listarlos a mano ya falló una vez ---Levenshtein lo importa
    # post_procesador.py, no el script principal--- que es exactamente el tipo de omisión
    # silenciosa que este chequeo viene a evitar.
    ('3 · Vectorización', ['pipeline/procesar_corpus.py', 'extractor/',
                           'pipeline/chunkear.py', 'pipeline/embeddings.py',
                           'pipeline/metadata_desde_catalogo.py']),
    ('4 · Indexación', ['pipeline/construir_indice.py', 'pipeline/catalogo.py']),
]

# Dónde escribe el pipeline. En la VM el servicio corre con el sistema de archivos en
# solo lectura salvo estas rutas, así que una que falte no da un error de permisos claro:
# da un paso que "falla" sin decir por qué.
ESCRITURA = ['data', 'datos', 'indice', 'scrapers', 'data/tandas']


def modulos_de(ruta):
    """Los módulos que importa un archivo, leídos del archivo.

    Si `ruta` termina en `/` se toma el directorio entero: un paso puede apoyarse en
    varios archivos y enumerarlos a mano es volver a la lista que envejece.
    """
    completa = os.path.join(RAIZ, ruta)
    if ruta.endswith('/'):
        if not os.path.isdir(completa):
            return None
        juntos = set()
        for f in sorted(os.listdir(completa)):
            if f.endswith('.py'):
                m = modulos_de(os.path.join(ruta, f))
                if m:
                    juntos |= m
        return juntos
    if not os.path.exists(completa):
        return None
    try:
        arbol = ast.parse(open(completa, encoding='utf-8').read())
    except SyntaxError:
        return None
    mods = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            mods |= {a.name.split('.')[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            mods.add(n.module.split('.')[0])
    # Los módulos del propio repositorio no son dependencias a instalar.
    propios = {'backend', 'pipeline', 'scrapers', 'extractor', 'conf', 'catalogo_comun'}
    return {m for m in mods if m not in propios}


def falta(modulo):
    try:
        return importlib.util.find_spec(modulo) is None
    except (ImportError, ValueError):
        return True


class Informe:
    def __init__(self):
        self.problemas, self.avisos = [], []

    def ok(self, que, detalle=''):
        print(f'  ok      {que}{"  ·  " + detalle if detalle else ""}')

    def error(self, que, detalle):
        print(f'  FALTA   {que}  ·  {detalle}')
        self.problemas.append(f'{que}: {detalle}')

    def aviso(self, que, detalle):
        print(f'  aviso   {que}  ·  {detalle}')
        self.avisos.append(f'{que}: {detalle}')


def revisar_pasos(inf):
    print('\n=== dependencias de cada paso ===')
    for nombre, archivos in PASOS:
        faltantes, ausentes = set(), []
        for ruta in archivos:
            mods = modulos_de(ruta)
            if mods is None:
                ausentes.append(ruta)
                continue
            faltantes |= {m for m in mods if falta(m)}
        if ausentes:
            inf.error(nombre, f'no está el archivo {", ".join(ausentes)}')
        elif faltantes:
            inf.error(nombre, f'faltan módulos: {", ".join(sorted(faltantes))}')
        else:
            inf.ok(nombre, 'todas las dependencias presentes')


def revisar_escritura(inf):
    print('\n=== permisos de escritura ===')
    for rel in ESCRITURA:
        ruta = os.path.join(RAIZ, rel)
        try:
            os.makedirs(ruta, exist_ok=True)
            prueba = os.path.join(ruta, '.prueba-de-escritura')
            with open(prueba, 'w') as f:
                f.write('x')
            os.unlink(prueba)
            inf.ok(rel + '/')
        except OSError as e:
            inf.error(rel + '/', f'no se puede escribir ({e.strerror})')


def revisar_portal(inf):
    print('\n=== salida a internet ===')
    proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    print(f'  {"proxy: " + proxy if proxy else "sin proxy configurado (salida directa)"}')

    portal = os.environ.get('SUDOCU_PORTAL_URL', '')
    if not portal:
        inf.error('portal SUDOCU', 'falta SUDOCU_PORTAL_URL en el entorno')
        return
    api = portal.split('/sudocu/')[0] + '/sudocu/api/mpd/contenedores/?id_area=0'
    try:
        pedido = urllib.request.Request(api, headers={'User-Agent': 'rag-unlu/1.0'})
        with urllib.request.urlopen(pedido, timeout=60) as r:
            cuerpo = r.read()
        if not cuerpo:
            inf.aviso('portal SUDOCU', 'contestó vacío: puede ser intermitencia del portal')
        else:
            inf.ok('portal SUDOCU', f'{len(cuerpo)} bytes')
    except Exception as e:
        inf.error('portal SUDOCU', f'{type(e).__name__}: {e}')


def revisar_llm(inf):
    print('\n=== modelo de lenguaje ===')
    if falta('openai'):
        inf.error('cliente OpenAI', 'falta el módulo openai')
        return
    clave = os.environ.get('OPENAI_API_KEY')
    if not clave:
        inf.aviso('clave del LLM', 'no está en el entorno; puede estar cargada en el panel')
        return
    try:
        import openai
        # Listar modelos no consume tokens: verifica la clave y la salida sin gastar.
        modelos = openai.OpenAI(api_key=clave).models.list()
        inf.ok('clave del LLM', f'válida, {len(modelos.data)} modelos disponibles')
    except Exception as e:
        inf.error('clave del LLM', f'{type(e).__name__}: {str(e)[:120]}')


def revisar_embeddings(inf):
    print('\n=== modelo de embeddings ===')
    if falta('sentence_transformers'):
        inf.error('sentence-transformers', 'falta el módulo')
        return
    hf = os.environ.get('HF_HOME') or os.path.expanduser('~/.cache/huggingface')
    offline = os.environ.get('HF_HUB_OFFLINE') == '1'
    hay_cache = os.path.isdir(hf) and any(os.scandir(hf))
    if hay_cache:
        inf.ok('caché de modelos', hf)
    elif offline:
        inf.error('caché de modelos', f'{hf} vacío y HF_HUB_OFFLINE=1: no va a poder bajarlo')
    else:
        inf.aviso('caché de modelos', f'{hf} vacío: el primer uso baja ~2,3 GB')


def revisar_espacio(inf):
    print('\n=== espacio en disco ===')
    libre = shutil.disk_usage(RAIZ).free / 1e9
    # Una reconstrucción total escribe el índice nuevo al lado del viejo antes de
    # reemplazarlo, así que el pico es alto y el momento de descubrirlo no es ese.
    if libre < 5:
        inf.error('espacio libre', f'{libre:.1f} GB: una reconstrucción no entra')
    elif libre < 15:
        inf.aviso('espacio libre', f'{libre:.1f} GB: alcanza para actualizar, justo para reconstruir')
    else:
        inf.ok('espacio libre', f'{libre:.1f} GB')


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--sin-red', action='store_true',
                   help='saltear las comprobaciones que salen a internet')
    a = p.parse_args()

    print(f'instalación en {RAIZ}')
    print(f'python {sys.version.split()[0]} · {sys.executable}')

    inf = Informe()
    revisar_pasos(inf)
    revisar_escritura(inf)
    revisar_embeddings(inf)
    revisar_espacio(inf)
    if not a.sin_red:
        revisar_portal(inf)
        revisar_llm(inf)

    print()
    if inf.problemas:
        print(f'=== {len(inf.problemas)} problema(s) que van a impedir que algo funcione ===')
        for x in inf.problemas:
            print(f'  · {x}')
    if inf.avisos:
        print(f'=== {len(inf.avisos)} aviso(s), no impiden funcionar ===')
        for x in inf.avisos:
            print(f'  · {x}')
    if not inf.problemas and not inf.avisos:
        print('=== la instalación está completa: todos los pasos pueden correr ===')
    elif not inf.problemas:
        print('=== la instalación puede correr todos los pasos ===')
    return 1 if inf.problemas else 0


if __name__ == '__main__':
    sys.exit(main())

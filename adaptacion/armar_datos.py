#!/usr/bin/env python3
"""Arma los datos para adaptar con LoRA un modelo chico a la generación de ChatDigesto.

Cada ejemplo reproduce el primer turno de una consulta real del sistema: la pregunta, la
recuperación de producción (glosario, BM25 y vector denso fusionados con RRF, anclaje por
identificador) y el contexto que recibe el modelo de generación, con el mismo prompt del
sistema. Todo sale del backend, no de una copia: si cambia el prompt o la recuperación,
los datos se rearman con el cambio. La respuesta la escribe un modelo maestro local
(Qwen 3.5 27B) siguiendo ese prompt, sin salir de Clementina.

Tres tipos de ejemplo:

    tematica       pregunta en lenguaje corriente que el maestro escribe a partir de un
                   fragmento dispositivo (artículo, anexo o parte resolutiva), sin nombrar
                   el acto. Es la consulta más frecuente y la más difícil de recuperar.
    identificador  pregunta por el número del acto, armada con plantillas.
    sin_respuesta  una pregunta temática recuperada SIN los fragmentos del acto del que
                   salió. Enseña a decir que no se encontró en vez de completar con lo que
                   haya: es la falla que marcaron los jueces humanos ("inventa sobre la
                   marcha"). Si otro acto del contexto sí responde, la respuesta del maestro
                   lo usa, que también es lo correcto.

Partición por acto: un acto de prueba no aporta ningún ejemplo al entrenamiento, y los
actos de las consultas de la evaluación del paper (evaluacion_automatica/consultas.jsonl)
quedan fuera de las dos particiones.

Un proceso por par de tiles: el maestro de 27B se reparte entre los dos tiles visibles.
Cada proceso toma una parte de los actos y escribe su propio archivo; si se corta, al
volver a correr retoma donde quedó.

Uso:
    ZE_AFFINITY_MASK=0,1 python -m adaptacion.armar_datos --parte 0 --partes 4 \
        --salida DIR [--n-actos 3200] [--limite 6]
"""
import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# El mismo patrón con el que verificar_citas.py extrae los actos citados.
from evaluacion_automatica.verificar_citas import RE_ACTO  # noqa: E402

TIPOS_DISPOSITIVOS = ('articulo', 'anexo', 'parte_resolutiva')
PROPORCION_IDENTIFICADOR = 0.15
PROPORCION_SIN_RESPUESTA = 0.15

PLANTILLAS_IDENTIFICADOR = [
    '¿Qué establece {art} {acto}?',
    '¿De qué trata {art} {acto}?',
    '¿Qué dispuso {art} {acto}?',
    'Resumime {art} {acto}',
    '{acto}',
]

PEDIDO_PREGUNTA = """Este es un fragmento de un acto administrativo de la Universidad Nacional de Luján.

TÍTULO DEL ACTO: {titulo}
FRAGMENTO ({seccion}):
{texto}

Escribí UNA pregunta que podría hacerle a un buscador de normativa un estudiante, un docente o alguien del personal de la universidad, y que este fragmento responda.
Reglas:
- No menciones el número ni el código del acto.
- Usá palabras de todos los días, no copies frases del fragmento.
- Si el fragmento nombra a una persona, una carrera o una dependencia, la pregunta puede nombrarla.
- Una sola oración.
Respondé solo con la pregunta, sin comillas ni explicaciones."""


def clave_acto(codigo, numero, anio):
    anio = anio if len(anio) == 4 else '20' + anio
    return f'{codigo.upper()}|{int(numero)}|{anio}'


def actos_citados(texto):
    return {clave_acto(*m.groups()) for m in RE_ACTO.finditer(texto or '')}


def acto_de_cita(cita):
    """'Disposición DISSECPCII 64/2026 — Considerando' -> 'Disposición DISSECPCII 64/2026'."""
    return (cita or '').split(' — ')[0].strip()


def articulo_para(acto):
    tipo = acto.split(' ')[0].lower()
    return 'la' if tipo.endswith(('ción', 'cion', 'a')) else 'el'


def elegir_actos(ruta_bd, excluidos, n_actos, semilla):
    """Un fragmento dispositivo por acto, con texto suficiente, en orden aleatorio fijo."""
    bd = sqlite3.connect(ruta_bd)
    marcas = ','.join('?' * len(TIPOS_DISPOSITIVOS))
    filas = bd.execute(
        f'SELECT i, documento FROM chunk WHERE tipo_seccion IN ({marcas}) '
        'AND length(texto) >= 250 ORDER BY i', TIPOS_DISPOSITIVOS).fetchall()
    por_acto = {}
    for i, doc in filas:
        if doc not in excluidos:
            por_acto.setdefault(doc, []).append(i)
    rng = random.Random(semilla)
    docs = sorted(por_acto)
    rng.shuffle(docs)
    return [(doc, rng.choice(por_acto[doc])) for doc in docs[:n_actos]]


def planificar(elegidos, n_prueba, semilla):
    """Tipo y partición de cada acto. Lo decide la semilla, no el proceso que lo atiende."""
    rng = random.Random(semilla + 1)
    plan = []
    for k, (doc, i) in enumerate(elegidos):
        particion = 'prueba' if k < n_prueba else 'entrenamiento'
        if rng.random() < PROPORCION_IDENTIFICADOR:
            plan.append({'documento': doc, 'chunk': i, 'particion': particion,
                         'tipos': ['identificador'], 'plantilla': rng.randrange(len(PLANTILLAS_IDENTIFICADOR))})
        else:
            tipos = ['tematica'] + (['sin_respuesta'] if rng.random() < PROPORCION_SIN_RESPUESTA else [])
            plan.append({'documento': doc, 'chunk': i, 'particion': particion, 'tipos': tipos})
    return plan


class Maestro:
    def __init__(self, ruta, max_nuevos):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(ruta)
        self.tok.padding_side = 'left'
        self.modelo = AutoModelForCausalLM.from_pretrained(
            ruta, dtype=torch.bfloat16, device_map='balanced').eval()
        # Qwen 3.5 trae <|endoftext|> como eos en config.json: sin <|im_end|> en la lista la
        # generación no corta al terminar el turno.
        fin = {self.tok.eos_token_id, self.tok.convert_tokens_to_ids('<|im_end|>'),
               self.tok.convert_tokens_to_ids('<|endoftext|>')}
        self.fin = sorted(t for t in fin if isinstance(t, int) and t >= 0)
        self.max_nuevos = max_nuevos

    def texto(self, mensajes):
        return self.tok.apply_chat_template(mensajes, tokenize=False, add_generation_prompt=True,
                                            enable_thinking=False)

    def generar(self, lista_mensajes, max_nuevos=None, presupuesto=20000):
        """Greedy, como la temperatura 0 de producción. Devuelve (texto, terminó, tokens_prompt)
        en el orden pedido.

        Los prompts se agrupan por largo y cada grupo se limita a `presupuesto` tokens con
        relleno (largo máximo por cantidad). Lo que ocupa memoria es el prefill: sin los
        núcleos de fla, la atención lineal de Qwen 3.5 pasa la secuencia entera a float32, y
        un lote de 8 prompts de ~6.000 tokens agotó el tile. Ese agotamiento no siempre llega
        como OutOfMemoryError: a veces el runtime de Intel aborta con "Segmentation fault
        from GPU" y el proceso muere sin poder atraparlo. Por eso el límite va antes. Con
        28.000 todavía hubo agotamientos tras horas de corrida (fragmentación): va 20.000.
        """
        largos = [len(self.tok(self.texto(m), add_special_tokens=False)['input_ids'])
                  for m in lista_mensajes]
        orden = sorted(range(len(lista_mensajes)), key=lambda j: largos[j])
        grupos, actual = [], []
        for j in orden:
            if actual and largos[j] * (len(actual) + 1) > presupuesto:
                grupos.append(actual)
                actual = []
            actual.append(j)
        if actual:
            grupos.append(actual)
        res = [None] * len(lista_mensajes)
        for g in grupos:
            for j, r in zip(g, self._generar_lote([lista_mensajes[j] for j in g], max_nuevos)):
                res[j] = r
        return res

    def _generar_lote(self, lista_mensajes, max_nuevos):
        textos = [self.texto(m) for m in lista_mensajes]
        enc = self.tok(textos, return_tensors='pt', padding=True, add_special_tokens=False)
        disp = self.modelo.get_input_embeddings().weight.device
        enc = {k: v.to(disp) for k, v in enc.items()}
        with self.torch.no_grad():
            out = self.modelo.generate(**enc, max_new_tokens=max_nuevos or self.max_nuevos, do_sample=False,
                                       pad_token_id=self.tok.pad_token_id or self.fin[0],
                                       eos_token_id=self.fin)
        nuevos = out[:, enc['input_ids'].shape[1]:]
        res = []
        for fila, att in zip(nuevos, enc['attention_mask']):
            ids = fila.tolist()
            termino = any(t in self.fin for t in ids)
            res.append((self.tok.decode(ids, skip_special_tokens=True).strip(), termino, int(att.sum())))
        del out, enc
        self.torch.xpu.empty_cache()
        return res


def limpiar_pregunta(texto):
    linea = next((l for l in texto.strip().splitlines() if l.strip()), '')
    linea = linea.strip().strip('"«»“”\'').strip()
    linea = re.sub(r'^(pregunta|consulta)\s*:\s*', '', linea, flags=re.IGNORECASE)
    return linea if 12 <= len(linea) <= 300 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--salida', required=True)
    ap.add_argument('--parte', type=int, default=0)
    ap.add_argument('--partes', type=int, default=1)
    ap.add_argument('--n-actos', type=int, default=3200)
    ap.add_argument('--n-prueba', type=int, default=200)
    ap.add_argument('--semilla', type=int, default=0)
    ap.add_argument('--maestro', default=os.path.expanduser('~/hf-models/Qwen3.5-27B'))
    ap.add_argument('--lote', type=int, default=8)
    ap.add_argument('--max-nuevos', type=int, default=900)
    ap.add_argument('--limite', type=int, default=0, help='actos por proceso (canario)')
    a = ap.parse_args()
    os.makedirs(a.salida, exist_ok=True)

    import torch
    if not torch.xpu.is_available():   # sin XPU el 27B correría en CPU sin avisar
        raise SystemExit(f'sin XPU visible en {os.uname().nodename}')
    from backend import api

    ix = api.indice()
    excluidos = {json.loads(l)['documento'] for l in open(
        os.path.join(RAIZ, 'evaluacion_automatica', 'consultas.jsonl'), encoding='utf-8')}
    elegidos = elegir_actos(ix.ruta_bd, excluidos, a.n_actos, a.semilla)
    plan = planificar(elegidos, a.n_prueba, a.semilla)
    if a.parte == 0:
        with open(os.path.join(a.salida, 'plan.json'), 'w', encoding='utf-8') as fh:
            json.dump({'n_actos': len(plan), 'n_prueba': a.n_prueba, 'semilla': a.semilla,
                       'excluidos_por_evaluacion': len(excluidos), 'maestro': a.maestro,
                       'sistema': api.instruccion(), 'plan': plan}, fh, ensure_ascii=False)
    mio = plan[a.parte::a.partes]
    if a.limite:
        mio = mio[:a.limite]

    ruta = os.path.join(a.salida, f'ejemplos_{a.parte}.jsonl')
    hechos = set()
    if os.path.exists(ruta):
        hechos = {json.loads(l)['id'] for l in open(ruta, encoding='utf-8')}
    pendientes = [p for p in mio if any(f"{p['documento']}::{t}" not in hechos for t in p['tipos'])]
    print(f'parte {a.parte}/{a.partes}: {len(mio)} actos, {len(pendientes)} pendientes; '
          f'tiles visibles {torch.xpu.device_count()}', flush=True)
    if not pendientes:
        return

    t0 = time.time()
    maestro = Maestro(a.maestro, a.max_nuevos)
    cod = api.codificador()
    print(f'modelos cargados en {time.time() - t0:.0f} s', flush=True)

    def recuperar(pregunta, sin_doc=None):
        """El primer turno de producción: _preparar, el mismo buscar y el mismo contexto."""
        c = api.Consulta(pregunta=pregunta)
        consulta, estado = api._preparar(c)
        denso = cod.encode([consulta], normalize_embeddings=True)[0]
        res = ix.buscar(denso, texto_consulta=consulta if c.usar_lexico else '',
                        k=c.k + (20 if sin_doc else 0), filtros=None, solo_articulos=c.solo_articulos,
                        pesos_actos=api.pesos_de_actos(estado) if c.usar_anclaje else None,
                        peso_entidad=api.peso_de(estado.get('entidad_origen')),
                        entidad=estado.get('entidad') if c.usar_foco else None)
        chunks = ix.chunks_de([i for i, _, _ in res])
        if sin_doc:
            pares = [(r, ch) for r, ch in zip(res, chunks) if ch.get('documento') != sin_doc][:c.k]
            res, chunks = [r for r, _ in pares], [ch for _, ch in pares]
        return consulta, res, chunks, ix.contexto(res)

    salida = open(ruta, 'a', encoding='utf-8')
    for s in range(0, len(pendientes), a.lote):
        lote = pendientes[s:s + a.lote]
        t1 = time.time()
        fuentes = {p['documento']: ix.chunk(p['chunk']) for p in lote}

        # 1. Preguntas: las temáticas las escribe el maestro, las de identificador salen de
        # una plantilla.
        preguntas = {}
        tematicas = [p for p in lote if 'tematica' in p['tipos']]
        if tematicas:
            pedidos = [[{'role': 'user', 'content': PEDIDO_PREGUNTA.format(
                titulo=fuentes[p['documento']].get('titulo') or '(sin título)',
                seccion=fuentes[p['documento']].get('seccion') or '',
                texto=(fuentes[p['documento']].get('texto') or '')[:2500])}] for p in tematicas]
            for p, (txt, _, _) in zip(tematicas, maestro.generar(pedidos, max_nuevos=80)):
                preguntas[p['documento']] = limpiar_pregunta(txt)
        for p in lote:
            if 'identificador' in p['tipos']:
                acto = acto_de_cita(fuentes[p['documento']].get('cita'))
                preguntas[p['documento']] = PLANTILLAS_IDENTIFICADOR[p['plantilla']].format(
                    art=articulo_para(acto), acto=acto)

        # 2. Recuperación de producción y 3. respuesta del maestro con el prompt del sistema.
        casos = []
        for p in lote:
            pregunta = preguntas.get(p['documento'])
            if not pregunta:
                continue
            fuente = fuentes[p['documento']]
            acto_fuente = actos_citados(acto_de_cita(fuente.get('cita')))
            for tipo in p['tipos']:
                if f"{p['documento']}::{tipo}" in hechos:
                    continue
                consulta, res, chunks, contexto = recuperar(
                    pregunta, sin_doc=p['documento'] if tipo == 'sin_respuesta' else None)
                docs = [ch.get('documento') for ch in chunks]
                casos.append({
                    'id': f"{p['documento']}::{tipo}", 'documento': p['documento'],
                    'particion': p['particion'], 'tipo': tipo, 'pregunta': pregunta,
                    'consulta_busqueda': consulta, 'fragmento_fuente': fuente.get('chunk_id'),
                    'recuperados': [ch.get('chunk_id') for ch in chunks],
                    'posicion_fuente': docs.index(p['documento']) + 1 if p['documento'] in docs else None,
                    'actos_en_contexto': sorted(set().union(*[actos_citados(acto_de_cita(ch.get('cita')))
                                                             for ch in chunks])) if chunks else [],
                    'acto_fuente': sorted(acto_fuente), 'contexto': contexto,
                })
        con_contexto = [c for c in casos if c['contexto']]
        respuestas = maestro.generar([api._mensajes(c['pregunta'], c['contexto']) for c in con_contexto])
        for c, (txt, termino, n_prompt) in zip(con_contexto, respuestas):
            citas = actos_citados(txt)
            c.update({'respuesta': txt, 'termino': termino, 'tokens_prompt': n_prompt,
                      'citas': sorted(citas),
                      'anclaje_ok': citas <= set(c['actos_en_contexto']),
                      'cita_fuente': bool(citas & set(c['acto_fuente']))})
        for c in casos:
            if not c['contexto']:
                c.update({'respuesta': None, 'termino': False, 'citas': [], 'anclaje_ok': False,
                          'cita_fuente': False})
            salida.write(json.dumps(c, ensure_ascii=False) + '\n')
        salida.flush()
        print(f'  {s + len(lote)}/{len(pendientes)} actos · {len(casos)} ejemplos · '
              f'{time.time() - t1:.0f} s el lote', flush=True)
    print(f'LISTO parte {a.parte} en {time.time() - t0:.0f} s', flush=True)


if __name__ == '__main__':
    # Después de un error del runtime de Intel ("Enqueue process failed", UR_RESULT_ERROR_
    # OUT_OF_DEVICE_MEMORY) el intérprete se colgaba al cerrar, con el error ya impreso, y el
    # reintento del sbatch no llegaba nunca. os._exit sale sin pasar por esa limpieza.
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(1)

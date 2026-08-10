"""Las respuestas que alguien marcó como que no sirvieron, con su pregunta y quién fue.

Esto no está en el panel a propósito: el panel muestra métricas agregadas, y el contenido
de las conversaciones se mira con una consulta deliberada sobre la base, que deja fuera a
cualquier administrador que solo esté chusmeando.

    sudo python3 valoraciones.py            # las que no sirvieron
    sudo python3 valoraciones.py --utiles   # las que sí
"""
import argparse
import sqlite3
import textwrap
import time

BASE = '/opt/rag-unlu/datos/chatdigesto.sqlite'

p = argparse.ArgumentParser()
p.add_argument('--utiles', action='store_true', help='mostrar las valoradas como útiles')
p.add_argument('--base', default=BASE)
a = p.parse_args()

valor = 1 if a.utiles else 0
bd = sqlite3.connect(f'file:{a.base}?mode=ro', uri=True)
bd.row_factory = sqlite3.Row

filas = bd.execute("""
    SELECT m.id, m.momento, m.texto AS respuesta, m.fuentes,
           c.id AS conv, c.titulo,
           u.correo, u.nombre,
           (SELECT p.texto FROM mensaje p
             WHERE p.conversacion_id = m.conversacion_id
               AND p.rol = 'user' AND p.id < m.id
             ORDER BY p.id DESC LIMIT 1) AS pregunta
      FROM mensaje m
      JOIN conversacion c ON c.id = m.conversacion_id
      LEFT JOIN usuario u ON u.id = c.usuario_id
     WHERE m.valoracion = ?
     ORDER BY m.momento DESC
""", (valor,)).fetchall()

etiqueta = 'ÚTILES' if a.utiles else 'QUE NO SIRVIERON'
print(f'{len(filas)} respuesta(s) valoradas como {etiqueta}\n')

for f in filas:
    cuando = time.strftime('%d/%m/%Y %H:%M', time.localtime(f['momento']))
    quien = f['correo'] or '(sin cuenta)'
    if f['nombre']:
        quien += f' · {f["nombre"]}'
    print('=' * 78)
    print(f'{cuando}   {quien}   conversación #{f["conv"]}: {f["titulo"] or ""}')
    print('\nPREGUNTA')
    print(textwrap.indent(textwrap.fill(f['pregunta'] or '(no se encontró)', 74), '  '))
    print('\nRESPUESTA')
    print(textwrap.indent(textwrap.fill(f['respuesta'] or '', 74), '  '))
    if f['fuentes']:
        import json
        try:
            fu = json.loads(f['fuentes'])
            print(f'\nFUENTES CITADAS ({len(fu)})')
            for x in fu[:8]:
                print(f'  · {x.get("cita") or x}')
        except Exception:
            pass
    else:
        print('\nFUENTES CITADAS: ninguna')
    print()

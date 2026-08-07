"""Actualización programada: cuándo le toca correr al pipeline, y quién lo dispara.

Hasta ahora la rutina periódica se documentaba como una línea de cron. Eso tiene dos
problemas. El primero es que pide acceso de root a la máquina, que en otra universidad
puede no estar disponible para quien administra el sistema. El segundo es peor: una
corrida lanzada por cron NO pasa por el registro de corridas, y la exclusión mutua que
impide dos operaciones simultáneas vive justamente ahí. Un cron puede arrancar en el
mismo momento en que alguien apretó Ejecutar, y quedan dos procesos escribiendo el mismo
catálogo.

Programarlo desde adentro resuelve las dos cosas: se configura desde el panel y se lanza
por la misma puerta que el botón, con su registro, su log y su exclusión.

Este módulo tiene la aritmética de fechas separada del reloj a propósito: `proxima` y
`anterior` son funciones puras y se prueban solas, que es lo que hace falta cuando el
problema son los meses sin día 31 y los reinicios a destiempo.
"""
import calendar
import datetime as dt
import hashlib

CADENCIAS = ('diaria', 'semanal', 'mensual')

POR_OMISION = {
    'activa': False,          # apagada: una instalación nueva no hace nada sin que se lo pidan
    'cadencia': 'diaria',
    'dia_semana': 6,          # domingo, para el caso semanal (lunes = 0)
    'dia_mes': 1,             # para el caso mensual
    'hora': 3,                # de madrugada, cuando nadie consulta
}

# Ventana para recuperar una ejecución perdida. Si el servicio estuvo caído justo a la
# hora programada, al volver corre igual, siempre que no haya pasado demasiado: recuperar
# la actualización de anoche tiene sentido, la de la semana pasada no ---ya la tapó la
# siguiente--- y arrancar un pipeline largo apenas arranca el servicio, por algo que pasó
# hace días, es más molesto que útil.
HORAS_DE_GRACIA = 12


def normalizar(cfg):
    """Completa y acota lo que venga del panel. Nunca levanta: un ajuste corrupto no
    puede impedir que el sistema arranque, solo apagar la programación."""
    c = {**POR_OMISION, **(cfg or {})}
    if c.get('cadencia') not in CADENCIAS:
        c['cadencia'] = POR_OMISION['cadencia']
    for clave, tope in (('dia_semana', 6), ('dia_mes', 31), ('hora', 23)):
        try:
            c[clave] = max(1 if clave == 'dia_mes' else 0, min(tope, int(c[clave])))
        except (TypeError, ValueError):
            c[clave] = POR_OMISION[clave]
    c['activa'] = bool(c.get('activa'))
    return c


def desfase(semilla):
    """Minutos de corrimiento dentro de la hora elegida, propios de esta instalación.

    Si el sistema se instala en varias universidades, todas van a elegir la madrugada y
    todas van a salir a las 3 en punto contra el mismo SUDOCU. El corrimiento las separa
    sin que nadie tenga que pensarlo.

    Se deriva del portal configurado, así que es estable ---la misma instalación cae
    siempre en el mismo minuto, y se puede anunciar en el panel--- y distinto entre
    universidades. No se usa azar: un minuto que cambia en cada arranque haría imposible
    explicar por qué corrió cuando corrió.
    """
    d = hashlib.sha256((semilla or '').encode('utf-8')).digest()
    return d[0] % 60


def _con_hora(dia, hora, minuto):
    return dt.datetime(dia.year, dia.month, dia.day, hora, minuto)


def _dia_del_mes(anio, mes, dia_pedido):
    """El día pedido, o el último del mes si ese mes no llega.

    Elegir 31 no puede significar "en febrero no se actualiza". La regla es explícita
    porque la alternativa ---saltear el mes--- es un silencio que nadie va a notar hasta
    que la normativa esté vieja.
    """
    return min(dia_pedido, calendar.monthrange(anio, mes)[1])


def _candidatas(cfg, ahora, minuto):
    """Momentos programados cerca de `ahora`, de más viejo a más nuevo."""
    hora = cfg['hora']
    hoy = ahora.date()

    if cfg['cadencia'] == 'diaria':
        return [_con_hora(hoy + dt.timedelta(days=n), hora, minuto) for n in (-1, 0, 1)]

    if cfg['cadencia'] == 'semanal':
        # weekday(): lunes = 0. Se retrocede al día elegido de esta semana y se miran la
        # anterior y la siguiente.
        base = hoy - dt.timedelta(days=(hoy.weekday() - cfg['dia_semana']) % 7)
        return [_con_hora(base + dt.timedelta(days=7 * n), hora, minuto) for n in (-1, 0, 1)]

    momentos = []
    for salto in (-1, 0, 1):
        mes = ahora.month + salto
        anio = ahora.year + (mes - 1) // 12
        mes = (mes - 1) % 12 + 1
        dia = _dia_del_mes(anio, mes, cfg['dia_mes'])
        momentos.append(dt.datetime(anio, mes, dia, hora, minuto))
    return momentos


def proxima(cfg, ahora, semilla=''):
    """El primer momento programado posterior a `ahora`. None si está apagada."""
    cfg = normalizar(cfg)
    if not cfg['activa']:
        return None
    minuto = desfase(semilla)
    return min(m for m in _candidatas(cfg, ahora, minuto) if m > ahora)


def anterior(cfg, ahora, semilla=''):
    """El último momento programado que ya pasó. None si está apagada."""
    cfg = normalizar(cfg)
    if not cfg['activa']:
        return None
    minuto = desfase(semilla)
    pasadas = [m for m in _candidatas(cfg, ahora, minuto) if m <= ahora]
    return max(pasadas) if pasadas else None


def toca_ahora(cfg, ahora, ultima, semilla=''):
    """Si corresponde lanzar en este momento.

    Corresponde cuando el último momento programado ya pasó, todavía no se ejecutó, y no
    pasó tanto como para que recuperarlo deje de tener sentido.

    `ultima` es el momento PROGRAMADO que se ejecutó por última vez, no cuándo terminó:
    comparar contra el momento programado es lo que hace que un reinicio a las 3:14 no
    saltee la corrida de las 3:15 ni la repita dos veces.
    """
    ult = anterior(cfg, ahora, semilla)
    if ult is None:
        return False
    if ultima is not None and ultima >= ult:
        return False
    return (ahora - ult) <= dt.timedelta(hours=HORAS_DE_GRACIA)


def describir(cfg, ahora, semilla=''):
    """Cómo contarlo en el panel, con el minuto real y no el que se eligió."""
    cfg = normalizar(cfg)
    if not cfg['activa']:
        return 'La actualización automática está desactivada.'
    p = proxima(cfg, ahora, semilla)
    dias = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')
    if cfg['cadencia'] == 'diaria':
        cada = 'Todos los días'
    elif cfg['cadencia'] == 'semanal':
        cada = f'Cada {dias[cfg["dia_semana"]]}'
    else:
        cada = f'El día {cfg["dia_mes"]} de cada mes'
    return (f'{cada} a las {p.hour:02d}:{p.minute:02d}. '
            f'Próxima: {dias[p.weekday()]} {p.day}/{p.month} a las '
            f'{p.hour:02d}:{p.minute:02d}.')

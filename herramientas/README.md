# Herramientas de administración

Consultas que se hacen a mano, deliberadamente fuera del panel.

El panel muestra métricas agregadas: cuántas consultas, cuántos usuarios, cuántas
quedaron sin fuentes. El contenido de las conversaciones no está ahí a propósito, porque
cualquiera con el rol de administrador podría leerlo sin dejar rastro ni intención. Mirar
lo que preguntó una persona tiene que costar un paso explícito, con acceso a la máquina y
a la base.

Estos scripts son ese paso. Abren la base en modo de solo lectura.

## valoraciones.py

Las respuestas que alguien marcó con el pulgar, con la pregunta que las originó, quién la
hizo y qué fuentes se citaron.

    sudo python3 /opt/rag-unlu/herramientas/valoraciones.py            # las que no sirvieron
    sudo python3 /opt/rag-unlu/herramientas/valoraciones.py --utiles   # las que sí

Sirve para lo que el número agregado no puede: entender POR QUÉ una respuesta no sirvió.
Si no citó fuentes, es un problema de recuperación; si citó las correctas y aun así no
sirvió, es de generación o de encuadre.

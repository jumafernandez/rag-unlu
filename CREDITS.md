# Créditos

Este repositorio integra trabajo de varias personas. La idea es que quede explícito quién
hizo qué, y que el historial de git lo refleje.

## Extractor de PDFs — `extractor/`

Autoría original: **Sebastián** y el equipo de becarios de la UNLu.
Repositorio original: https://github.com/SebasUNLu/Extractor_pdf

El código se incorporó con `git subtree` **conservando su historial de commits**, así que la
autoría de cada línea sigue registrada en `git log` y `git blame`. Nuestras modificaciones
posteriores son commits nuevos encima, no reescrituras de los suyos.

Las mejoras que hicimos están además aisladas como parches en `patches/`, para poder
proponerlas al repositorio original vía pull request.

## Scrapers — `scrapers/`

Autoría: **Fran** (pendiente de incorporar).

## Ingesta y pipeline — `ingesta/`, `pipeline/`

Juan Manuel Fernández, con asistencia de Claude Code.

## Documentos fuente

El digesto de la Universidad Nacional de Luján es documentación pública de la institución.
Los PDFs no se versionan en este repositorio (ver `.gitignore`).

# Offsets por versión de Rekordbox

Un archivo JSON por versión exacta: `<versión>.json` (p. ej. `7.2.20.json`).
La app lee la versión del ejecutable al conectar y solo usa el archivo de esa
versión; si no existe, funciona en **modo limitado** (biblioteca y sesiones,
sin detección en vivo). Nunca se usan offsets de otra versión.

- Formato: ver la documentación de `src/rkbx_practice/memory/offsets.py` y la
  plantilla `_example.json` (los archivos que empiezan por `_` se ignoran).
- Generación: `python -m tools.offset_detect` con Rekordbox abierto (guía paso a paso).
- Campos por deck: `track_path` (o `track_id`), `bpm`, `position`, `is_master`,
  `fader`, `eq_low`. Globales: `crossfader`, `master_deck` (alternativa a `is_master`).

# Asistente de práctica para Rekordbox

Aplicación personal para Windows que funciona junto a Rekordbox 7: genera
sesiones de práctica (BPM cercanos y tonalidades compatibles Camelot), tacha
en vivo los temas que cargas en cada deck, sugiere el siguiente y graba la
sesión (temas, transiciones, fader cuts y bass swaps).

Diseño y decisiones: ver [CLAUDE.md](CLAUDE.md).

## Instalación (Windows 10/11, Python 3.11+)

```powershell
cd rkbx-practise-tool
python -m venv .venv
.venv\Scripts\activate
pip install -e .[tools,dev]
```

Dependencias: `pyrekordbox` (master.db y ANLZ), `pymem` (lectura de memoria,
solo Windows), `PySide6` (ventana). `numpy` solo para la herramienta de
offsets (ya lo instala pyrekordbox). `pytest` para los tests.

## Uso

```powershell
# Biblioteca (master.db en solo lectura; se abre una copia temporal)
python -m rkbx_practice library list
python -m rkbx_practice library show "Techno/Warmup"
python -m rkbx_practice library export biblioteca.json

# Sesión de práctica
python -m rkbx_practice session "Techno/Warmup"
python -m rkbx_practice session "Techno" --tolerance 6 --strictness relaxed --seed 1

# Estado en vivo de los decks (requiere offsets para tu versión)
python -m rkbx_practice offsets
python -m rkbx_practice live --session "Techno/Warmup" --record

# Ventana de práctica
python -m rkbx_practice ui

# Histórico
python -m rkbx_practice history list
python -m rkbx_practice history show session-20261125-220000.json
```

Sin Rekordbox:

```powershell
python -m rkbx_practice --library-json data/demo_library.json session Techno
python -m rkbx_practice --library-json data/demo_library.json --demo ui
python -m rkbx_practice --library-json data/demo_library.json --demo live --session Favoritos
```

`--demo` simula dos decks que pinchan las sugerencias (cargas, transiciones de
8 s con bass swap) para ver la ventana y las detecciones funcionando.

## Offsets (lectura en vivo)

Las direcciones de memoria cambian con cada versión de Rekordbox y van en
`data/offsets/<versión>.json` (nunca en el código). Sin archivo para tu versión
la app funciona en **modo limitado** (biblioteca y sesiones, sin detección en
vivo).

Para generarlos, con Rekordbox abierto:

```powershell
python -m tools.offset_detect                    # guía paso a paso (una acción cada vez)
python -m tools.offset_detect --fields fader eq_low --decks 1 2
python -m tools.offset_detect --recheck      # tras reiniciar Rekordbox: deja solo cadenas estables
python -m tools.offset_detect --validate data/offsets/7.2.20.json
```

La herramienta solo lee memoria. Ver `data/offsets/README.md`.

## Tests

```powershell
pytest
```

Todos los tests funcionan sin Rekordbox, sin Qt y en cualquier sistema.

## Garantías de seguridad

- Nunca se escribe en `master.db` ni en ningún archivo de Rekordbox: se lee una
  copia temporal y nunca se hace commit.
- Nunca se escribe en la memoria de Rekordbox: la capa de memoria solo tiene
  métodos de lectura.
- No se usan offsets de otra versión.

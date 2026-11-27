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
pip install -e .[tools,dev,audio]
```

Dependencias: `pyrekordbox` (master.db y ANLZ), `pymem` (lectura de memoria,
solo Windows), `PySide6` (ventana). `numpy` solo para la herramienta de
offsets (ya lo instala pyrekordbox). `pytest` para los tests. Extra `audio`:
`soundcard` (captura del sonido de Windows, WASAPI) y `soundfile` (guarda FLAC) para
grabar el audio de las sesiones; sin ellas las sesiones se guardan sin audio.

## Uso diario (Rekordbox 7.2.3 con controladora)

```powershell
cd C:\Users\Casa\Desktop\rkbx-practise-tool
.venv\Scripts\activate
git pull
python -m rkbx_practice ui
```

1. Abre Rekordbox y conecta la controladora (los faders se leen de la controladora).
2. En la ventana elige la playlist y pulsa **Generar sesión**. Si ya hay un tema
   sonando, la sesión empieza desde él.
3. Carga los temas en los decks: se ponen en **verde** (tachados), la siguiente
   sugerencia en **amarillo** (naranja si es comodín) y las sugerencias que te
   saltas en **rojo**. Clic en un tema verde para destacharlo.
4. **● Iniciar grabación** / **■ Parar grabación** para guardar la sesión **y su audio**,
   y **Histórico** para revisarla (transiciones, fader cuts a tiempo, bass swaps).
   **🎧 Escuchar en el navegador** abre la página de revisión.

## Comandos

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
python -m rkbx_practice history review            # escuchar en el navegador

# Audio de la sesión
python -m rkbx_practice audio devices             # salidas y entradas de audio
python -m rkbx_practice audio test                # graba 5 s y dice si llega sonido
```

Sin Rekordbox:

```powershell
python -m rkbx_practice --library-json data/demo_library.json session Techno
python -m rkbx_practice --library-json data/demo_library.json --demo ui
python -m rkbx_practice --library-json data/demo_library.json --demo live --session Favoritos
```

`--demo` simula dos decks que pinchan las sugerencias (cargas, transiciones de
8 s con bass swap) para ver la ventana y las detecciones funcionando.

## Audio de las sesiones

Al grabar una sesión, la app graba también **lo que suena por la controladora** (loopback
WASAPI: lo que Windows envía a esa salida) en `history/<sesión>.flac`, con la hora de inicio para
alinearlo con las transiciones, fader cuts y bass swaps. Configuración en `audio` de
`config/config.json`:

- `device`: parte del nombre del dispositivo (p. ej. `"DDJ-FLX4"`); `null` = salida
  predeterminada de Windows. Lista con `audio devices`.
- `loopback`: `true` graba una salida (lo que suena); `false` graba una entrada (línea/micro).
- `format`: `flac` (por defecto, ~300 MB por hora), `wav` u `ogg` (mucho más pequeño, con pérdida).
- `enabled: false` desactiva la grabación de audio.
- El audio empieza cuando suena algo: el silencio antes de dar al play no se graba.
- `auto_stop_silence_s` (300 = 5 min): si deja de sonar ese tiempo seguido, la grabación se para
  sola y se corta donde dejó de sonar (0 = nunca). `silence_threshold_db`: nivel por debajo del
  cual se considera silencio (−50 dBFS).

**Comprueba antes de la primera sesión** con `audio test` mientras suena algo. Si sale
«Silencio», Rekordbox está usando el driver **ASIO** de la controladora, que Windows no ve.
Opciones: en Rekordbox → Preferencias → Audio, elegir el driver WASAPI/Windows de la
controladora si lo ofrece; o un cable de la salida de la controladora a la entrada de línea del
PC (`loopback: false`, `device: "Línea"`).

**Revisión** (`history review` o **Histórico → 🎧 Escuchar en el navegador**,
`http://127.0.0.1:8766/`): forma de onda de toda la sesión y un zoom con las marcas (temas
cargados, transiciones en azul, fader cuts ✓/✗, bass swaps); clic en cualquier punto o en la lista
para escuchar desde ahí; **Mezcla ⏭ / ⏮** salta a la siguiente/anterior transición (empieza 6 s
antes). Teclas: espacio, ← →, N / P, + / − (zoom; también con la rueda).

Bajo el zoom, los **faders y EQ (HI/MID/LOW) de cada deck** en el tiempo, y un panel con el % de
cada uno en el punto que suena (0 % = cortado, 50 % = centro, 100 % = máximo). Cada transición
indica si hubo **choque de graves** (los graves de los dos decks sonando a la vez) y con qué EQ
entró el tema nuevo.

## Luces

**Montaje…** (en la ventana): cuántas tiras (hasta 4), cuántos píxeles y cuántos segmentos (zonas)
tiene cada una. Se guarda en `config/config.json` y se envía al ESP32, que lo recuerda; no hace
falta reprogramarlo. **Identificar zonas** enciende cada zona de un color para ver su número.

- **Efectos…** en la ventana: análisis de cada tema (frases y cambios de energía) en una línea de
  tiempo y asignación de efectos; se guardan en `data/lights/<id>.json`.
- **Luces**: activa el envío en directo al ESP32 (`lights.esp32_ip:esp32_port`).
- **Simulador**: abre en el navegador una tira de LEDs que hace de ESP32.

```powershell
python -m rkbx_practice lights show "Welcome to Hell"
python -m rkbx_practice lights simulate
python -m rkbx_practice lights send flash color=rojo dur_ms=200 --simulator
```

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

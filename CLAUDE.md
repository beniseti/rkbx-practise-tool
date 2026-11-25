# CLAUDE.md — Asistente de práctica para Rekordbox

Este archivo describe el proyecto completo, las decisiones ya tomadas y cómo trabajar en él. Léelo entero antes de escribir código.

## 1. Qué es el proyecto

Aplicación externa para **Windows**, de **uso personal**, que funciona junto a **Rekordbox 7** (DJ con controladora):

1. El usuario elige una carpeta o playlist de Rekordbox y la app genera una **sesión de práctica**: una lista ordenada de temas con BPM cercanos y, opcionalmente, tonalidades compatibles según la rueda Camelot.
2. Mientras pincha, la app detecta **en tiempo real** qué tema se carga en cada deck y lo **tacha automáticamente**, y sugiere el siguiente.
3. La app puede **grabar la sesión**: registra los temas cargados, las **transiciones** (como intervalos de tiempo), los **fader cuts** y los **bass swaps**, para revisarlos después.
4. Fase futura: **control de luces LED** con un ESP32 por UDP, a partir del análisis de Rekordbox.

Rekordbox no tiene API de plugins. La app es un programa independiente con una ventana flotante (siempre visible) encima de Rekordbox.

## 2. Decisiones cerradas

| Tema | Decisión |
|---|---|
| Sistema | Solo Windows 10/11 |
| Lenguaje | Python 3.11+ |
| Biblioteca de Rekordbox | `master.db` con `pyrekordbox`, **solo lectura** |
| Datos en tiempo real | Lectura de memoria del proceso `rekordbox.exe` con `pymem` |
| Plan B de detección | UI Automation (leer el texto de los decks en pantalla) si la memoria falla tras una actualización |
| Interfaz | PySide6 (Qt), ventana flotante siempre visible |
| Decks | Mínimo 2, máximo 4, configurable en JSON |
| Tachado | Al **cargar** el tema en un deck |
| Carga por error | El usuario puede destachar un tema desde la app; vuelve a estar disponible |
| Persistencia del tachado | No se mantiene entre sesiones; cada práctica empieza de cero |
| Histórico | Sí: se guarda cada sesión grabada |
| Inicio/fin de grabación | Botón en nuestra app (no el REC de Rekordbox) |
| Tonalidad | La detectada por Rekordbox |
| Análisis para luces | El de Rekordbox (rejilla de beats, frases, forma de onda por bandas en ANLZ). Sin análisis espectral propio |
| Crossfader | Desactivado por defecto; activable en JSON |
| Quién manda en las luces | El deck con el fader más alto; si hay empate, el último que entró |
| Luces | UDP al ESP32, que crea su propia Wi-Fi y tiene los efectos preprogramados |

Identificadores de código en inglés; textos de la interfaz en español.

## 3. Restricciones (obligatorias)

- **Nunca escribir en `master.db`** ni en ningún archivo de Rekordbox. Escribir mientras Rekordbox está abierto puede corromper la biblioteca. Solo lectura.
- **Nunca escribir en la memoria de Rekordbox.** Solo `ReadProcessMemory` (vía pymem).
- Las **direcciones de memoria** (cadenas de punteros) van en archivos de datos por versión (`data/offsets/`), **nunca** en el código.
- Al arrancar, la app lee la **versión del ejecutable** de Rekordbox. Si no hay offsets para esa versión, funciona en **modo limitado** (biblioteca y sesiones sin detección en vivo) y lo indica claramente. No leer memoria con offsets de otra versión.
- Todo lo ajustable (umbrales, decks, puertos, rutas) va en `config/config.json`.
- Cada error de lectura de memoria o de BD se gestiona sin cerrar la app; se reintenta la conexión a Rekordbox automáticamente.

## 4. Estructura

```
rkbx-practise-tool/
├─ CLAUDE.md
├─ README.md
├─ pyproject.toml
├─ config/
│  └─ config.json
├─ data/
│  ├─ demo_library.json   # biblioteca de ejemplo para probar sin Rekordbox
│  └─ offsets/            # un archivo por versión de Rekordbox (<versión>.json); _example.json = plantilla
├─ history/               # sesiones grabadas (JSON, una por archivo)
├─ src/rkbx_practice/
│  ├─ config.py           # carga de config.json (con valores por defecto)
│  ├─ state.py            # DeckState, MixerState, LiveSnapshot, ConnectionStatus
│  ├─ events.py           # TrackLoaded, TransitionEvent, CutBurst, BassSwap
│  ├─ app.py              # PracticeController: une biblioteca, sesión, bucle en vivo e histórico
│  ├─ cli.py              # línea de comandos (python -m rkbx_practice …)
│  ├─ library/            # master.db (rekordbox_db.py), ANLZ (anlz.py), Camelot, modelos, snapshot en memoria
│  ├─ session/            # compatibilidad, orden (engine.py), sesión en curso (practice.py)
│  ├─ memory/             # proceso y versión, tablas de offsets, cadenas de punteros, lectura de decks
│  ├─ live/               # fuentes (memoria, demo, guion), enriquecido, canalización, bucle de sondeo
│  ├─ detection/          # volumen efectivo, transiciones, fader cuts, bass swaps
│  ├─ history/            # grabación y guardado de sesiones
│  ├─ ui/                 # PySide6
│  └─ lights/             # FASE FUTURA: solo puntos de enganche y formato de comando propuesto
├─ tools/
│  └─ offset_detect/      # detector de offsets propio (enfoque de rb_offset_detect, rkbx_wave)
└─ tests/
```

## 5. Configuración (`config/config.json`)

```json
{
  "rekordbox": {
    "process_name": "rekordbox.exe",
    "decks": 2,
    "offsets_dir": "data/offsets"
  },
  "library": {
    "master_db_path": null,
    "db_key": null,
    "copy_before_open": true
  },
  "session": {
    "bpm_tolerance_pct": 4.0,
    "allow_half_double_tempo": true,
    "camelot_enabled": true,
    "camelot_strictness": "moderate",
    "wildcard_probability": 0.15,
    "optimize_time_budget_s": 1.0
  },
  "live": {
    "poll_interval_ms": 20,
    "reconnect_interval_s": 3,
    "position_jump_tolerance_s": 0.25,
    "max_consecutive_read_errors": 50
  },
  "mixer": {
    "crossfader_enabled": false,
    "crossfader_assign": {"1": "A", "2": "B", "3": "A", "4": "B"}
  },
  "detection": {
    "fader_zero_threshold": 0.05,
    "preview_touch_max_ms": 1500,
    "transition_stable_s": 4,
    "cut_max_beats": 1,
    "cut_group_gap_beats": 2,
    "bass_swap_window_beats": 4,
    "eq_low_threshold": 0.3,
    "on_beat_tolerance_ms": 60,
    "bass_swap_bar_tolerance_beats": 0.5,
    "default_bpm": 120.0
  },
  "history": {
    "dir": "history"
  },
  "ui": {
    "always_on_top": true,
    "start_compact": false
  },
  "lights": {
    "enabled": false,
    "esp32_ip": "192.168.4.1",
    "esp32_port": 4210,
    "latency_compensation_ms": 0
  }
}
```

Claves añadidas durante la implementación:

- `library.master_db_path`: ruta a master.db (`null` = autodetección: configuración de pyrekordbox o `%APPDATA%\Pioneer\rekordbox`).
- `library.db_key`: clave de descifrado si pyrekordbox no la encuentra sola.
- `library.copy_before_open`: se abre una **copia temporal** de master.db (y `-wal`/`-shm`). Así ni un checkpoint de SQLite puede tocar el original.
- `session.optimize_time_budget_s`: tiempo máximo de optimización del orden (el recálculo en directo usa como máximo 0,25 s).
- `live.position_jump_tolerance_s`: margen para detectar saltos de posición (loops, hot cues, arrastre).
- `live.max_consecutive_read_errors`: lecturas fallidas seguidas antes de reconectar.
- `mixer.crossfader_assign`: lado del crossfader de cada deck (`A`, `B` o `THRU`).
- `detection.on_beat_tolerance_ms`: tolerancia para considerar un fader cut "a tiempo".
- `detection.bass_swap_bar_tolerance_beats`: tolerancia (en beats) para considerar que un bass swap cae en inicio de compás/frase.
- `detection.default_bpm`: BPM supuesto si un deck no informa BPM (para convertir beats a segundos).
- `ui.always_on_top`, `ui.start_compact`.

Los valores de umbral son puntos de partida; se ajustarán probando con el usuario.

## 6. Biblioteca (master.db + ANLZ)

- Usar `pyrekordbox` para abrir `master.db` en solo lectura (descifrado incluido).
- Extraer por tema: ID, título, artista, ruta del archivo, BPM, tonalidad, duración, ruta de análisis (`AnalysisDataPath` o equivalente; verificar el nombre del campo en la versión instalada).
- Listar carpetas y playlists (incluido su contenido recursivo para carpetas).
- **Verificar escalas**: en master.db el BPM puede estar guardado multiplicado por 100. Comprobar y normalizar.
- La tonalidad puede venir en notación clásica (`Am`) o alfanumérica (`8A`) según la configuración de Rekordbox. Normalizar siempre a Camelot. Tener en cuenta enarmónicos (`G#m` = `Abm`, `C#` = `Db`, etc.).
- Temas sin BPM o sin tonalidad (no analizados): excluir de la sesión y avisar.

Implementación:

- La biblioteca se lee una vez y se guarda en memoria (`InMemoryLibrary`); se puede exportar/importar a JSON (`library export`, `--library-json`).
- Campos usados de `DjmdContent`: `ID`, `Title`, `ArtistName`, `FolderPath`, `BPM`, `KeyName`, `Length`, `AnalysisDataPath`. Playlists: `DjmdPlaylist.Attribute` (0 playlist, 1 carpeta, 4 inteligente), `ParentID`, `Seq`; contenido con `get_playlist_contents` (si existe) o `Songs` ordenado por `TrackNo`.
- BPM: un valor ≥ 1000 se interpreta como ×100.
- Cruce memoria ↔ master.db por ruta ANLZ: clave normalizada desde `pioneer/usbanlz/` hasta la carpeta del tema, en minúsculas y sin extensión (`.DAT`/`.EXT`/`.2EX`), así coinciden la ruta absoluta de memoria y la relativa de master.db.
- Rejilla (`PQTZ`) y frases (`PSSI`) con `pyrekordbox.anlz`; modelo `BeatGrid` con caché por ruta.

### Tabla Camelot

| Camelot | Menor (A) | Camelot | Mayor (B) |
|---|---|---|---|
| 1A | Abm | 1B | B |
| 2A | Ebm | 2B | F# |
| 3A | Bbm | 3B | Db |
| 4A | Fm | 4B | Ab |
| 5A | Cm | 5B | Eb |
| 6A | Gm | 6B | Bb |
| 7A | Dm | 7B | F |
| 8A | Am | 8B | C |
| 9A | Em | 9B | G |
| 10A | Bm | 10B | D |
| 11A | F#m | 11B | A |
| 12A | Dbm | 12B | E |

## 7. Motor de sesiones

Entrada: carpeta o playlist. Salida: lista ordenada de temas.

- **BPM**: dos temas son compatibles si su diferencia está dentro de `bpm_tolerance_pct`. Si `allow_half_double_tempo`, comparar también con el doble y la mitad.
- **Camelot** (si `camelot_enabled`), compatibilidad de A hacia B:
  - Misma clave.
  - ±1 en el número, misma letra (12 y 1 son vecinos).
  - Mismo número, cambio de letra (relativa mayor/menor).
  - Energy boost: +2 misma letra.
  - +7 misma letra (subida de un semitono).
  - `camelot_strictness`: `strict` (solo las tres primeras), `moderate` (todas menos +7), `relaxed` (todas).
- **Orden**: una cadena en la que cada tema enlaza con el siguiente con saltos de BPM pequeños, priorizando las relaciones Camelot más suaves. Tomar ideas de `keyid` (generación de cadena) y `djkr8` (optimización y puntuación de transiciones).
- **Comodín**: con probabilidad `wildcard_probability`, la sugerencia siguiente es un tema fuera del orden previsto pero compatible en tono y BPM.
- **Recálculo**: si el usuario carga un tema distinto al sugerido, se tacha el que realmente cargó y la siguiente sugerencia se calcula a partir de él.
- Debe poder ejecutarse y probarse **sin Rekordbox abierto** (CLI o tests).

Implementación:

- Coste de A→B = diferencia de BPM / tolerancia + coste de la relación Camelot (misma 0 · vecina 0,5 · relativa 0,8 · energy boost 1,5 · +7 2,5) + 0,75 si es a mitad/doble tempo + 20 si es incompatible. Las incompatibles se penalizan pero no se prohíben: la sesión incluye todos los temas y marca los saltos incompatibles.
- Orden: cadena voraz desde varios inicios + mejora local 2-opt (coste direccional) y Or-opt, con límite de tiempo.
- Recálculo: el plan pasa a ser "temas cargados en orden + resto encadenado desde el último cargado". Si se carga el tema sugerido (no comodín), el plan no cambia. Un tema que no es de la sesión pero sí de la biblioteca sirve de ancla para la siguiente sugerencia (no se tacha nada).
- Destachar: vuelve a estar disponible y, si era el último cargado, el ancla pasa al anterior.
- Lo que ya está cargado en los decks al conectar no se tacha; solo las cargas posteriores.

## 8. Lectura de memoria (tiempo real)

Datos necesarios **por deck** (de 1 a `decks`):

- Tema cargado (identificador).
- BPM actual y posición de reproducción.
- Deck master.
- Posición del fader de canal.
- Posición del EQ de graves.

Datos globales:

- Posición del crossfader (solo se usa si `crossfader_enabled`).

Notas:

- Base de partida: **rkbx_wave** (`mrmilbe/rkbx_wave`, MIT). Su herramienta `rb_offset_detect` guía al usuario (cargar un tema, play/stop, mover el pitch), escanea la memoria y genera las cadenas de punteros. Detecta BPM, posición y ruta ANLZ. **Hay que ampliarla** para faders, EQ de graves, crossfader y deck master.
- **Identificación del tema cargado**: leer la ruta ANLZ del deck y cruzarla con la ruta de análisis guardada en `master.db` para obtener el ID del tema. Verificarlo; si no funciona, buscar el ID del tema directamente en memoria.
- Formato de offsets: inspirado en rkbx_link (dirección base relativa al módulo + desplazamientos). Las direcciones cambian con cada versión de Rekordbox.
- Referencia para faders: `0-ft/rbxjacker` (Rust) lee faders y crossfader.
- `grufkork/rkbx_link` es GPL-3: usarlo solo como **referencia** de enfoque; no copiar código salvo que el usuario acepte la GPL.
- Frecuencia de sondeo: `poll_interval_ms` (≈20 ms).
- Detectar saltos de posición (loops, hot cues, arrastre) para no disparar eventos dos veces o a destiempo.

Implementación:

- Formato de `data/offsets/<versión>.json`: documentado en `memory/offsets.py` y en la plantilla `data/offsets/_example.json`. Campos por deck: `track_path` (o `track_id`), `bpm`, `position`, `is_master`, `fader`, `eq_low`; globales: `crossfader`, `master_deck`. Tipos `f32`, `f64`, `i32`, `u32`, `u8`, `bool`, `utf8`, `utf16`, `ptr_utf8`, `ptr_utf16`; normalización opcional `map` (→ 0..1), `invert`, `scale`.
- La versión se lee del recurso de versión del ejecutable (`7.2.20.0` → `7.2.20`) y solo se usa el archivo con esa versión exacta.
- `tools/offset_detect` es una implementación propia (no se ha copiado código de rkbx_wave ni de rkbx_link): escaneo de valores con numpy, filtrado guiado una acción cada vez, búsqueda hacia atrás de cadenas de punteros estáticas y validación (`--validate`). Guarda todas las cadenas candidatas en `data/offsets/<versión>.candidates.json`; tras reiniciar Rekordbox, `--recheck` repite unas acciones y se queda solo con las que siguen funcionando (comprobado en 7.2.3: la primera cadena del fader no sobrevivió a un reinicio y la del BPM sí; ninguna de las 135 cadenas de hasta 4 niveles del fader sobrevivió). Por eso la búsqueda llega ahora a 6 niveles y desplazamientos de 0x1000 (`--depth`, `--max-offset`), y si se repite el asistente para un campo tras reiniciar Rekordbox se guardan solo las cadenas que aparecen en ambas sesiones. Tampoco funcionó para el fader (búsquedas truncadas a 5000 cadenas, ninguna común). Siguiente enfoque: `--anchor bpm` busca solo caminos que salen de los objetos intermedios de una cadena ya comprobada como estable (la del BPM del mismo deck). Con el ancla salieron 142 cadenas, pero ninguna sobrevivió al reinicio. El usuario indica que la curva del fader es logarítmica: el filtro de "fader a la mitad" (15–85 % del rango) descartaba la ganancia real del motor; ahora basta con que quede estrictamente entre mínimo y máximo, se guardan las cadenas de hasta 10 direcciones candidatas (cada una con su `map`) y se muestra un diagnóstico de posibles vtables del objeto. Resultado: el fader del deck 1 es una variable global del módulo (f64, posición 0..1); el asistente ahora pone primero los candidatos dentro del módulo (marcados `[GLOBAL, estable]`) y omite el f32 que es la mitad alta de un f64. Cubre ruta ANLZ, BPM, posición (detecta unidades: s, ms o muestras), fader, EQ de graves, deck master (flag por deck e índice global) y crossfader.
- Un deck se considera parado si la posición no avanza en 0,3 s (la memoria puede no refrescarse en cada sondeo). Un deck parado cuenta como volumen 0 para las transiciones.
- Saltos: la posición cambia de forma distinta a la esperada por el tempo (`bpm actual / bpm del tema`) o retrocede → `DeckState.jumped`.

## 9. Ventana de práctica (UI)

- Selector de carpeta/playlist y botón para generar la sesión.
- Lista de la sesión: tema, BPM, Camelot; los temas cargados aparecen tachados.
- Clic sobre un tema tachado para destacharlo.
- Sugerencia siguiente destacada (marcar si es un comodín).
- Estado por deck: tema cargado, BPM, fader.
- Indicador de conexión con Rekordbox, versión detectada y modo (completo / limitado).
- Botón **Iniciar / Parar grabación** de sesión.
- Ventana siempre visible encima de Rekordbox; que se pueda redimensionar y compactar.

Implementación: `ui/main_window.py`. El bucle de sondeo corre en un hilo y pasa los datos a la UI con señales Qt; la sesión solo se modifica en el hilo de la UI. Ajustes rápidos en la ventana: tolerancia de BPM, Camelot sí/no y exigencia. `--demo` simula los decks para probar la ventana sin Rekordbox.

## 10. Histórico y detecciones

La grabación empieza y termina con el botón de la app. Cada sesión se guarda en `history/` como JSON.

### Contenido de una sesión

- Fecha y hora de inicio y fin.
- Carpeta/playlist de origen y ajustes usados (tolerancia BPM, Camelot, etc.).
- Temas en orden de carga, con hora y deck.
- Lista de transiciones, fader cuts y bass swaps (abajo).

### Volumen efectivo de cada deck

`volumen = fader_canal` (si el crossfader está activado: `fader_canal × factor_crossfader` según el lado del deck).

### Transiciones (intervalos de tiempo)

- **Inicio**: el fader del deck entrante sale de cero (supera `fader_zero_threshold`).
- **Fin**: el fader del deck saliente llega a cero. Si nunca baja del todo, la transición termina cuando el entrante supera al saliente y se mantiene así `transition_stable_s` segundos.
- **Toques de pre-escucha**: si un deck que no estaba sonando sube y vuelve a cero en menos de `preview_touch_max_ms`, se descarta.
- Los cortes secos (cambio en un compás) también son transiciones, de duración muy corta.
- Datos guardados: hora de inicio y fin, duración, deck y tema saliente y entrante, BPM, Camelot de ambos y si eran compatibles.

Implementación (`detection/transitions.py`):

- Una bajada a cero del saliente se confirma si dura más de `cut_max_beats`; si vuelve antes es un fader cut y la transición sigue.
- "Nunca baja del todo" = el entrante lleva `transition_stable_s` por encima del saliente **y** el saliente lleva `transition_stable_s` sin bajar; el fin se fecha en el cruce.
- Corte seco: el saliente se va y el entrante aparece con menos de un compás (o de `preview_touch_max_ms`) de diferencia.

### Fader cuts

- El fader de un deck que **ya estaba sonando** baja por debajo de `fader_zero_threshold` y vuelve a subir en menos de `cut_max_beats`.
- Cortes separados por menos de `cut_group_gap_beats` se agrupan en una ráfaga.
- Guardar: hora, deck, número de cortes y si cada uno cayó a tiempo según la rejilla de beats.

Implementación: "ya estaba sonando" = al menos un beat por encima del umbral. "A tiempo" se mide en el instante de la bajada, con `on_beat_tolerance_ms`; se guarda también el desfase en ms. Se usa el volumen efectivo, así también cuentan los cortes con crossfader (si está activado).

### Bass swaps

- El EQ de graves de un deck baja mientras el de otro sube, y se cruzan dentro de `bass_swap_window_beats`.
- Guardar: hora, decks implicados y si coincidió con inicio de compás o de frase (usando el análisis de frases de Rekordbox).

Implementación: "baja"/"sube" = cruza `eq_low_threshold` (con 0,02 de histéresis); ambos decks deben sonar. Compás/frase se evalúan en la posición del deck entrante cuando suben sus graves.

## 11. FASE FUTURA: luces con ESP32 (no implementar todavía)

Diseñar el resto del código para que añadir esto después sea sencillo.

- **Archivo de eventos por tema**, generado a partir del análisis de Rekordbox (rejilla, frases, forma de onda por bandas en los archivos ANLZ). Eventos en **beats**, no en segundos, para que sigan cuadrando con cambios de pitch. Editable a mano.
- **En directo**: el deck que manda es el de mayor volumen efectivo; en empate, el último que entró. Al llegar a cada evento se envía el comando, adelantado según `latency_compensation_ms`.
- **Transporte**: UDP. El ESP32 crea su propia Wi-Fi (el PC se conecta a ella). Los efectos están preprogramados en el ESP32.
- **Comandos** (propuesta de formato, a confirmar):
  - `{"fx": "flash", "color": "rojo", "dur_ms": 200}` — color y duración.
  - `{"fx": "strobe", "dur_ms": 1000}` — solo duración.
  - `{"fx": "rainbow", ...}` — parámetros por definir.
- Cada efecto tiene su propio conjunto de parámetros; el formato debe admitir parámetros distintos por efecto.

Preparado: `LeadDeckTracker` (deck que manda), `BeatGrid`, `DeckState.jumped`, oyentes del bucle en vivo y `lights/commands.py` (formato propuesto). Ver `lights/__init__.py`.

## 12. Fases de desarrollo

Trabajar en este orden. Las fases 1 y 2 no necesitan Rekordbox abierto.

1. **Biblioteca**: leer master.db, listar carpetas/playlists, normalizar BPM y Camelot. *Hecho cuando*: un comando lista una playlist con BPM y Camelot correctos.
2. **Motor de sesiones**: orden, filtro Camelot, comodines, recálculo. *Hecho cuando*: hay tests y un comando genera una sesión a partir de una playlist.
3. **Memoria**: detección de versión, carga de offsets, lectura de tema cargado, BPM, posición, master, faders, EQ de graves y crossfader. Adaptar `rb_offset_detect`. *Hecho cuando*: un comando muestra en vivo el estado de cada deck.
4. **Ventana de práctica**: UI con tachado automático, destachado manual y sugerencias.
5. **Histórico y detecciones**: grabación de sesiones, transiciones, fader cuts y bass swaps.
6. **Luces** (futura).

## 13. Pendiente de confirmar

- **Versión exacta de Rekordbox** instalada: **7.2.3** (`C:\Program Files\rekordbox\rekordbox 7.2.3\rekordbox.exe`). Los offsets irán en `data/offsets/7.2.3.json`.
- Que la ruta ANLZ en memoria permite identificar el tema en master.db.
- Direcciones de faders, EQ de graves, crossfader y deck master para esa versión. Fader del deck 1 hallado como **variable global** dentro de `rekordbox.exe` (`módulo+0x5a876c8`, f64, posición lineal 0..1): no necesita cadena de punteros; **confirmado estable tras reiniciar**. Fader del deck 2 en `módulo+0x5a876d0` (+8 bytes): los faders forman una tabla de f64 contiguos (decks 3 y 4 probablemente en `+0x5a876d8` y `+0x5a876e0`, sin confirmar). Para buscar controles vecinos (crossfader, EQ) se usa `python -m tools.offset_detect --watch DESDE HASTA`. BPM del deck 1 (cadena `0x58d38c8,0x0,0x2b0,0x1a0`) también confirmado tras reiniciar. EQ: tabla global de f64 (0..1, centro 0.5), 3 por deck en orden HI/MID/LOW, 0x18 bytes por deck: deck 1 HI `+0x58b3398`, MID `+0x58b33a0`, LOW `+0x58b33a8`; deck 2 HI `+0x58b33b0`, MID `+0x58b33b8`, LOW `+0x58b33c0` (confirmado con --watch; decks 3-4 previsiblemente LOW `+0x58b33d8` y `+0x58b33f0`, sin comprobar). BPM del deck 2: `0x58d38c8,0x8,0x2b0,0x1a0` (confirmado con `live`). Ruta ANLZ: deck 1 `0x59ff0a8,0x38,0x3f0`, deck 2 `0x59ff0a8,0x10,0x3f0` (halladas con el asistente; pendiente de confirmar tras reinicio). En la zona de los faders solo hay faders y gain. La curva del fader es logarítmica: decidir si la app debe usar la posición del fader o la ganancia (afecta a `fader_zero_threshold` y a qué deck suena más).
- Formato final de los comandos UDP y lista completa de efectos.
- Probar en Windows real: lectura de master.db con la versión de pyrekordbox instalada, ventana PySide6 y `tools/offset_detect` con Rekordbox abierto (el código se ha escrito y probado en Linux con dobles de prueba).
- Plan B (UI Automation): solo está prevista la interfaz (`live/sources.py`, `DeckSource`); falta decidir la dependencia (`uiautomation` o `pywinauto`) cuando haga falta.

## 14. Repositorios de referencia

| Repositorio | Licencia | Uso |
|---|---|---|
| `dylanljones/pyrekordbox` | MIT | Lectura de master.db y ANLZ |
| `mrmilbe/rkbx_wave` | MIT | Lectura de memoria en Python y detector de offsets (base de la fase 3) |
| `grufkork/rkbx_link` | GPL-3 | Referencia de lectura de memoria, formato de offsets y salidas OSC/sACN. No copiar código |
| `0-ft/rbxjacker` | Revisar | Referencia para faders y crossfader |
| `xdave/keyid` | Revisar | Referencia de sugerencias Camelot y generación de cadena |
| `schoi80/djkr8` | Revisar | Referencia de optimización de orden, mitad/doble tempo |
| `Jessepeene/rekordbox-now-playing` | Revisar | Plan B: lectura de decks con UI Automation |

Comprobar la licencia de cada repositorio antes de reutilizar código. (Hasta ahora no se ha copiado código de ninguno.)

## 15. Cómo trabajar con el usuario

- Claude Code se ejecuta en el mismo PC con Windows donde está Rekordbox.
- Para la fase 3, el usuario tiene que tener Rekordbox abierto y hacer las acciones que se le pidan (cargar un tema, dar al play, mover un fader o un EQ). Pide cada acción de forma clara, una cada vez, y espera confirmación.
- Antes de añadir una dependencia nueva, indicar cuál y por qué.
- Ante cualquier duda de diseño no resuelta aquí, preguntar antes de decidir.
- Mantener este archivo actualizado cuando se tomen decisiones nuevas.

## 16. Estado de la implementación

- Fases 1, 2, 3, 4 y 5 implementadas; fase 6 solo con puntos de enganche.
- Tests: `pytest` (funcionan sin Rekordbox, sin Qt y en cualquier sistema). Incluyen una prueba de punta a punta con la fuente `--demo` y otra del asistente de offsets contra un "Rekordbox" simulado en memoria.
- Comandos: ver `README.md` (`library`, `session`, `live`, `ui`, `history`, `offsets`, y `python -m tools.offset_detect`).
- Falta la validación en Windows real con Rekordbox (§13): generar `data/offsets/<versión>.json` con el asistente y ajustar umbrales.

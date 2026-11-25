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
    "optimize_time_budget_s": 1.0,
    "variety": 0.5,
    "follow_list": true
  },
  "live": {
    "poll_interval_ms": 20,
    "reconnect_interval_s": 3,
    "position_jump_tolerance_s": 0.25,
    "max_consecutive_read_errors": 50,
    "load_debounce_ms": 300,
    "empty_deck_without_bpm": true
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
    "start_compact": false,
    "theme": "rekordbox"
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
- `live.empty_deck_without_bpm`: un deck sin BPM se considera vacío (en 7.2.3 la ruta ANLZ conserva un valor antiguo con el deck vacío).
- `live.load_debounce_ms`: una carga de tema solo cuenta si la ruta lleva ese tiempo estable (evita cargas fantasma mientras Rekordbox carga).
- `mixer.crossfader_assign`: lado del crossfader de cada deck (`A`, `B` o `THRU`).
- `detection.on_beat_tolerance_ms`: tolerancia para considerar un fader cut "a tiempo".
- `detection.bass_swap_bar_tolerance_beats`: tolerancia (en beats) para considerar que un bass swap cae en inicio de compás/frase.
- `detection.default_bpm`: BPM supuesto si un deck no informa BPM (para convertir beats a segundos).
- `ui.always_on_top`, `ui.start_compact`, `ui.theme` (`rekordbox` o `system`).

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
- Rejilla (`PQTZ`) y frases (`PSSI`) con `pyrekordbox.anlz`; modelo `BeatGrid` con caché por ruta; se lee en segundo plano al cargar cada tema (el bucle en vivo usa `get_nowait` y nunca espera al disco).

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
- Variedad (`session.variety`, por defecto 0,5; `--variety` en la CLI): el usuario pidió que cada sesión generada sea distinta. Se suma ruido aleatorio solo a las transiciones compatibles y el inicio se elige al azar entre las mejores cadenas. Con `--seed` se puede repetir una sesión; con `variety: 0` siempre sale la misma.
- **Decisión del usuario tras probar en real (sustituye al recálculo):** la lista se genera **una sola vez** y no cambia; la sugerencia es siempre el **siguiente tema de la lista** tras el último cargado que esté en ella (`session.follow_list: true`, por defecto). Si se carga un tema fuera de orden, se tacha y se sigue desde su posición; un tema que no está en la lista no mueve la posición; destachar devuelve la posición al cargado anterior. Los comodines siguen existiendo (fila naranja) y se comparan con el último tema realmente cargado.
- Al generar la sesión con un tema ya cargado (deck master; si no, el de fader más alto), la sesión empieza desde él, se tacha si está en la lista y la primera sugerencia es compatible con él.
- Un tema cargado se reconoce en la sesión por ID, o si no por ruta del archivo, o por título + artista (en real hay temas importados dos veces con IDs distintos y no se tachaban).
- Modo anterior, solo con `follow_list: false` — recálculo: el plan pasa a ser "temas cargados en orden + resto encadenado desde el último cargado". Si se carga el tema sugerido (no comodín), el plan no cambia. Un tema que no es de la sesión pero sí de la biblioteca sirve de ancla para la siguiente sugerencia (no se tacha nada).
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

Implementación: `ui/main_window.py`. Estilo oscuro inspirado en Rekordbox (`ui/theme.py`, `ui.theme: "rekordbox"`; `"system"` para el estilo normal de Windows): número de deck en azul, MASTER en naranja, BPM grande, claves coloreadas según la rueda Camelot; sugerencia y decks con campos Tema / BPM / Clave. Colores de la lista (pedidos por el usuario, en tonos oscuros): **verde** = cargado (y tachado), **amarillo** = siguiente sugerencia (naranja si es comodín), **rojo** = sugerencia que se saltó (cargó otro en su lugar; sigue disponible y pasa a verde si luego se carga). El bucle de sondeo corre en un hilo y pasa los datos a la UI con señales Qt; la sesión solo se modifica en el hilo de la UI. Ajustes rápidos en la ventana: tolerancia de BPM, Camelot sí/no y exigencia. `--demo` simula los decks para probar la ventana sin Rekordbox. Botón **Histórico**: lista las sesiones grabadas y muestra el resumen (transiciones, cada ráfaga de cortes con ✓/✗ por corte y desfase medio, cada bass swap con compás/frase).

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

## 11. Luces con ESP32 (implementado, pendiente de probar con el ESP32)

Diseñar el resto del código para que añadir esto después sea sencillo.

- **Archivo de eventos por tema**, generado a partir del análisis de Rekordbox (rejilla, frases, forma de onda por bandas en los archivos ANLZ). Eventos en **beats**, no en segundos, para que sigan cuadrando con cambios de pitch. Editable a mano.
- **En directo**: el deck que manda es el de mayor volumen efectivo; en empate, el último que entró. Al llegar a cada evento se envía el comando, adelantado según `latency_compensation_ms`.
- **Transporte**: UDP. El ESP32 crea su propia Wi-Fi (el PC se conecta a ella). Los efectos están preprogramados en el ESP32.
- **Comandos** (propuesta de formato, a confirmar):
  - `{"fx": "flash", "color": "rojo", "dur_ms": 200}` — color y duración.
  - `{"fx": "strobe", "dur_ms": 1000}` — solo duración.
  - `{"fx": "rainbow", ...}` — parámetros por definir.
- Cada efecto tiene su propio conjunto de parámetros; el formato debe admitir parámetros distintos por efecto.

Implementación (`src/rkbx_practice/lights/`):

- `structure.py` (pedido por el usuario: «análisis profundo» y efectos que cuadren con lo que pasa; sin análisis espectral propio, con la forma de onda de **3 bandas** de Rekordbox): energía de graves/medios/agudos por beat (normalizada al percentil 95) y **papel** de cada sección (frase de Rekordbox, partida si hay un cambio fuerte de energía dentro): `intro`, `groove`, `peak`, `breakdown` (sin graves), `buildup` (sin graves y medios/agudos subiendo, o frase Up), `drop` (vuelven los graves tras breakdown/buildup; **regla del usuario: Up → Chorus = drop**) y `outro`. Umbrales relativos al nivel normal de graves del tema (percentil 80).
- Generación por papel (`show.generate_show`, formato de archivo v2): efecto del papel al inicio de cada sección; en un drop, `drop_hit` (golpe) en el primer beat y el efecto de `drop` en el segundo; en una subida que acaba en drop, `pre_drop` en el último compás. Valores especiales en parámetros: `until_next` (ms hasta la siguiente sección, calculado al generar) y `live` (BPM real del deck al enviar). Reglas por papel en `lights.rules` (por defecto en `show.DEFAULT_RULES`). Los archivos de la versión 1 sin eventos manuales se regeneran solos.
- **Montaje (decisión del usuario): 3 tiras con 3 secciones cada una = 9 zonas** (`lights.layout`; tira 1 → zonas 1-3, tira 2 → 4-6, tira 3 → 7-9). Parámetro `zones` en cualquier comando: `all`, `odd`/`impares` (1,3,5,7,9 = tablero de ajedrez), `even`/`pares`, `strip:N`, `section:N` o lista `1,5,9`; cada zona guarda su estado. Protocolo completo para el ESP32: `docs/protocolo_luces.md`.
- Efectos del catálogo: de fondo `color`, `off`, `pulse` (latido al BPM), `breathe`, `alternate` (pares/impares se turnan en cada beat), `chase` (la luz recorre las zonas); temporales `flash`, `strobe` (`hz`), `rainbow`, `build` (tensión creciente hasta el final). **Nunca estático (pedido por el usuario):** todos los fondos por defecto se mueven; solo el apagón antes del drop es quieto. Efectos de movimiento añadidos: `wave` (ola que recorre las tiras al BPM) y `twinkle` (destellos aleatorios). Reglas por defecto: intro ola lenta (4 beats), groove alterna pares/impares, pico persecución, breakdown destellos en todas las zonas, subida build + strobe en el último compás, outro ola muy lenta (8 beats). Formato de archivo v4. **Subida → drop (Up → Chorus), pedido por el usuario:** apagón (`blackout`: `off`, `beats` beats antes del drop, por defecto 1) y en el drop se encienden con un efecto **en movimiento** (`drop`: persecución roja rápida, `beats: 0.5`). El golpe blanco (`drop_hit`) queda desactivado (`fx: null`); si se activa, va en el primer beat del drop y el efecto de drop en el segundo. Formato de archivo v3 (los v1/v2 sin eventos manuales se regeneran solos).
- `analysis.py`: rejilla (`PQTZ`), frases con tipo (`PSSI`: Intro/Up/Down/Chorus/Outro o Verse/Bridge según el mood) y energía de graves por beat (`PWV7` de 3 bandas en .2EX; si no, altura de `PWV5`). Cambios de energía: en inicio de compás, media de 16 beats antes/después; `drop` si sube ×1,8, `break` si baja a ×0,55; separados ≥ 32 beats.
- `show.py`: archivo `data/lights/<track_id>.json` (`lights.events_dir`), eventos en beats con `label`, `source` (`phrase`/`energy`/`manual`), `fx` (o `null`) y `params`. Se genera con `lights.rules` (etiqueta → efecto; «Verse 2» usa la regla «Verse»); si frase y cambio de energía coinciden, queda uno («Chorus + drop») con la regla del cambio.
- `engine.py`: manda el deck de más volumen efectivo (empate: el último que entró). **Un deck parado cuenta como volumen 0** (visto en real: el fader 2 arriba sin sonar se tomaba como principal); lo mismo al elegir el tema desde el que empieza la sesión. La posición se adelanta `latency_compensation_ms` × (bpm actual / bpm del tema) y se disparan los eventos cruzados desde el sondeo anterior. Nunca dispara eventos atrasados: tras un salto, cambio de deck o tema, o con el deck parado, solo resincroniza.
- **Efectos básicos (pedido por el usuario):** `lights.fallback` (por defecto ola azul/morada al BPM) mientras suena un tema **hasta que se cargan sus eventos** (preparando archivo, leyendo rejilla) o si no tiene; `lights.idle` (destellos suaves) cuando no suena nada o el deck que manda está vacío. Cada uno se envía una sola vez por situación. Al engancharse a un tema ya empezado (tema nuevo, cambio de deck, salto/hot cue) se envía el último efecto **de fondo** de la sección actual (`BACKGROUND_FX`; los temporales como flash/strobe/build no se repiten a destiempo) o, si aún no hay ninguno, el básico.
- **Latencia (pregunta del usuario):** (1) los eventos que caen entre dos sondeos se programan con `threading.Timer` para salir en su milisegundo exacto (antes podían salir hasta `poll_interval_ms` tarde); (2) los efectos rítmicos (`BEAT_FX`) llevan `phase` y cada compás se envía `sync` (BPM y fase reales) para que el ESP32 no se desfase aunque cambie el pitch; (3) botón **Latencia…**: metrónomo (flash de 80 ms en cada beat, silencia los efectos del tema) y ajuste en vivo de `latency_compensation_ms`, que se guarda en config.json (`config.save_value`).
- **Paleta por tema y drops por intensidad (pedido del usuario: mismos tonos en una canción, pero el Chorus no siempre igual):** colores `$primary`/`$secondary`/`$accent` sacados de la tonalidad Camelot (`palette.py`; sin tonalidad, hash del ID). Drops y picos tienen `intensity` (graves, agudos y contraste con la sección anterior) → `tier` suave/medio/fuerte, con reglas `drop_soft|medium|hard` y `peak_soft|medium|hard`; cada una puede ser una lista de variantes que se van alternando en los drops sucesivos del tema (`occurrence`). `drop`/`peak` sueltos en config.json valen para todas las intensidades. Formato de archivo v5.
- `udp.py`: JSON por UDP no bloqueante a `esp32_ip:esp32_port`; los errores de red se registran y no paran nada.
- `manager.py`: caché de archivos para el bucle en vivo (nunca lee el disco en el bucle); si un tema no tiene archivo, se genera en segundo plano la primera vez que suena.
- Catálogo de efectos y parámetros por defecto en `lights.effects`; reglas en `lights.rules` (ambos en config.json).
- Ventana: casilla **Luces** (activar en directo) y botón **Efectos…** (editor por tema: compás, tiempo, sección, efecto, parámetros, Probar, Añadir/Quitar, Regenerar, Guardar). Encima de la tabla, **línea de tiempo** (pedida por el usuario): frases en bandas de colores, curva de energía de graves, drops/breaks en línea discontinua, eventos como triángulos y cursor de reproducción naranja si el tema está sonando; clic = seleccionar evento (o preparar el compás para añadir), tooltip con compás/tiempo/sección.
- CLI: `lights show <tema> [--regenerate]`, `lights send <fx> color=rojo dur_ms=200 [--simulator]`, `lights simulate`, `lights debug [--simulator]` (en vivo: imprime por qué se envía o no cada efecto). `lights regenerate [--playlist X] [--only-existing] [--force]`: rehace los archivos de efectos con el análisis y las reglas actuales (conserva los que tienen eventos a mano salvo `--force`).
- Diagnóstico: `LightsEngine.status` explica en todo momento qué pasa (luces desactivadas, faders abajo, deck sin tema o sin posición, preparando archivo, leyendo rejilla, sin eventos con efecto, parado, próximo evento y en cuántos segundos); `LightsManager.problems` guarda por qué un tema no tiene archivo (sin ruta de análisis o ANLZ ilegible). La ventana lo muestra bajo la lista y en el tooltip de «Luces».
- **Simulador en el navegador** (pedido por el usuario antes de pasar al ESP32; `lights/simulator.py` + `lights/simulator.html`, solo biblioteca estándar): hace de ESP32. Escucha UDP en `127.0.0.1:lights.simulator_port` (4211) y pasa los comandos a la página `http://127.0.0.1:lights.simulator_http_port/` (8765) por Server-Sent Events. La página dibuja la tira (en línea o en anillo, `simulator_leds` LEDs) y ejecuta `color`, `off` (color base, con fundido), `flash` (decae en `dur_ms`), `strobe` (`hz`, por defecto 12), `rainbow` (`speed`) y cualquier efecto desconocido como un pulso; colores por nombre en español/inglés o `#rrggbb`. Registro de comandos y botones de prueba (`POST /send`). Se abre con el botón **Simulador** de la ventana (arranca dentro de la app y le envía también los efectos) o con `lights simulate`; `lights.send_to_simulator: true` hace que la app envíe siempre también al simulador. El emisor UDP admite varios destinos (ESP32 + simulador). Si al pulsar **Simulador** el puerto ya está ocupado (simulador abierto aparte con `lights simulate`), la app usa ese en vez de fallar (antes no le enviaba nada); pulsar Simulador también activa «Luces». La línea de estado de luces muestra cuántos mensajes se han enviado y a qué destinos, y los errores del motor de luces (antes solo en consola).

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
- Direcciones de faders, EQ de graves, crossfader y deck master para esa versión. Fader del deck 1 hallado como **variable global** dentro de `rekordbox.exe` (`módulo+0x5a876c8`, f64, posición lineal 0..1): no necesita cadena de punteros; **confirmado estable tras reiniciar**. Fader del deck 2 en `módulo+0x5a876d0` (+8 bytes): los faders forman una tabla de f64 contiguos (decks 3 y 4 probablemente en `+0x5a876d8` y `+0x5a876e0`, sin confirmar). Para buscar controles vecinos (crossfader, EQ) se usa `python -m tools.offset_detect --watch DESDE HASTA`. BPM del deck 1 (cadena `0x58d38c8,0x0,0x2b0,0x1a0`) también confirmado tras reiniciar. EQ: tabla global de f64 (0..1, centro 0.5), 3 por deck en orden HI/MID/LOW, 0x18 bytes por deck: deck 1 HI `+0x58b3398`, MID `+0x58b33a0`, LOW `+0x58b33a8`; deck 2 HI `+0x58b33b0`, MID `+0x58b33b8`, LOW `+0x58b33c0` (confirmado con --watch; decks 3-4 previsiblemente LOW `+0x58b33d8` y `+0x58b33f0`, sin comprobar). BPM del deck 2: `0x58d38c8,0x8,0x2b0,0x1a0` (confirmado con `live`). Ruta ANLZ: deck 1 `0x59ff0a8,0x8,0x3f0`, deck 2 `0x59ff0a8,0x10,0x3f0` (lista de decks, 0x8 por deck; decks 3-4 previsiblemente 0x18 y 0x20). La del deck 1 (`0x59ff0a8,0x38,0x3f0`) apuntaba al tema ANTERIOR del deck (Rekordbox guarda ambos); se quitó y el asistente ahora pide volver a cargar el primer tema para distinguir la posición del tema actual. Posición: deck 1 `0x58d38c8,0x0,0x2b0,0x138`, deck 2 `0x58d38c8,0x8,0x2b0,0x138` (mismo objeto que el BPM; hallada con `--anchor bpm` + `--compare-decks`; f64 en segundos, confirmado con `--validate`). En la zona de los faders solo hay faders y gain. **Los faders globales son la posición del fader físico de la controladora**: si se mueven con el ratón o la controladora no está conectada, valen 0. El usuario mezcla siempre con la controladora, así que se da por bueno (no se busca la dirección del fader de pantalla); **confirmado funcionando en la ventana con la controladora**. La app usa la **posición** lineal del fader (no la ganancia logarítmica): `fader_zero_threshold` = 5 % del recorrido.
- **Deck master: descartado.** El usuario no usa SYNC, así que el master no aporta; para saber qué tema suena se usa el deck con el fader más alto. `is_master` queda como campo opcional (no se avisa si falta).
- Formato final de los comandos UDP y lista completa de efectos (implementado con la propuesta `{"fx": …, parámetros}` y el catálogo de `lights.effects`; falta confirmarlo con el firmware del ESP32). Falta comprobar en real los tipos de frase y la lectura de `PWV7`/`PWV5` con pyrekordbox.
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
- Validado en Windows real con Rekordbox 7.2.3: biblioteca, ventana PySide6, tachado y sugerencias en vivo; offsets de BPM, posición, ruta ANLZ, fader (controladora) y EQ de graves para los decks 1 y 2.
- **Siguiente paso:** grabar una sesión real (transición normal, corte seco, fader cuts y bass swap), comparar el resumen de `history show` con lo que hizo el usuario y ajustar umbrales de `detection`.

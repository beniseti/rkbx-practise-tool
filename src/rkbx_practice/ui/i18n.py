"""Idiomas de la ventana (pedido del usuario: multidioma; español e inglés).

Los textos se escriben en español en el código y se pasan por `tr()`; aquí está su traducción.
Si falta una traducción se muestra el texto en español. Con parámetros:
`tr("Deck {deck}: tachado «{title}»", deck=1, title="…")`.

El idioma se elige en el menú Idioma (`ui.language` en config.json) y se aplica al reiniciar la
ventana. Los mensajes de diagnóstico que vienen del motor (p. ej. por qué no salen luces) siguen
en español.
"""

from __future__ import annotations

LANGUAGES = {"es": "Español", "en": "English"}

_lang = "es"

EN: dict[str, str] = {
    # --- ventana principal y menús ---
    "Práctica Rekordbox": "Rekordbox Practice",
    "&Archivo": "&File",
    "&Salir": "E&xit",
    "&Práctica": "&Practice",
    "&Generar sesión": "&Generate session",
    "Iniciar / parar &grabación": "Start / stop &recording",
    "&Histórico…": "&History…",
    "&Escuchar grabaciones (navegador)": "&Listen to recordings (browser)",
    "&Luces": "&Lights",
    "&Activar luces": "&Enable lights",
    "&Efectos por tema…": "Per-track &effects…",
    "&Mis tiras…": "&My strips…",
    "&Simulador (navegador)": "&Simulator (browser)",
    "&Latencia…": "&Latency…",
    "&Ver": "&View",
    "Módulo &Práctica": "&Practice module",
    "Módulo &Luces": "&Lights module",
    "&Compactar": "&Compact",
    "Siempre &visible": "Always on &top",
    "&Idioma": "&Language",
    "Ay&uda": "&Help",
    "&Acerca de…": "&About…",
    "Práctica": "Practice",
    "Luces": "Lights",
    "Listo": "Ready",
    "Idioma": "Language",
    "El idioma se aplicará al reiniciar la ventana. ¿Reiniciar ahora?":
        "The language will be applied when the window restarts. Restart now?",
    "Acerca de": "About",
    "Asistente de práctica para Rekordbox: sesiones, grabación de mezclas y luces.":
        "Practice assistant for Rekordbox: sessions, mix recording and lights.",
    "● Buscando Rekordbox…": "● Looking for Rekordbox…",
    "Rekordbox{version} — {mode}": "Rekordbox{version} — {mode}",
    "desconectado": "disconnected",
    "modo limitado": "limited mode",
    "modo completo": "full mode",
    # --- módulo práctica ---
    "Generar sesión": "Generate session",
    " % BPM": " % BPM",
    "Estricto": "Strict",
    "Moderado": "Moderate",
    "Relajado": "Relaxed",
    "Genera una sesión para empezar": "Generate a session to start",
    "¡Sesión completada!": "Session completed!",
    "🃏 COMODÍN (fuera de orden, compatible)": "🃏 WILDCARD (out of order, compatible)",
    "Siguiente": "Next",
    "Tema": "Track",
    "BPM": "BPM",
    "Clave": "Key",
    "Camelot": "Camelot",
    "Verde: cargado (clic para destachar) · Amarillo: siguiente · Naranja: comodín · Rojo: te lo saltaste":
        "Green: loaded (click to un-strike) · Yellow: next · Orange: wildcard · Red: you skipped it",
    "Fader de canal": "Channel fader",
    "Nueva sesión": "New session",
    "Se perderá el tachado de la sesión actual. ¿Continuar?":
        "The struck tracks of the current session will be lost. Continue?",
    "{n} tema(s) sin analizar (sin BPM o tonalidad) excluidos: {names}":
        "{n} unanalysed track(s) (no BPM or key) excluded: {names}",
    "Destachado: {title}": "Un-struck: {title}",
    "Deck {deck}: tachado «{title}»": "Deck {deck}: struck «{title}»",
    "Deck {deck}: «{title}» no está en la sesión (no se tacha)":
        "Deck {deck}: «{title}» is not in the session (not struck)",
    "Deck {deck}: tema no encontrado en master.db ({path})": "Deck {deck}: track not found in master.db ({path})",
    "Transición {a}→{b} ({s:.1f} s)": "Transition {a}→{b} ({s:.1f} s)",
    "Fader cuts en deck {deck}: {n}": "Fader cuts on deck {deck}: {n}",
    "Bass swap {a}→{b}": "Bass swap {a}→{b}",
    "(no está en la biblioteca)": "(not in the library)",
    "● Iniciar grabación": "● Start recording",
    "■ Parar grabación {t}": "■ Stop recording {t}",
    "■ Parar grabación {t} (sin audio)": "■ Stop recording {t} (no audio)",
    "Grabando sesión…": "Recording session…",
    "Grabando sesión y audio ({device})…": "Recording session and audio ({device})…",
    "Grabando sesión SIN AUDIO: {error}": "Recording session WITHOUT AUDIO: {error}",
    "Sesión guardada en {path}": "Session saved to {path}",
    "Grabación parada sola: {mins:g} min sin sonido (cortada donde dejó de sonar)":
        "Recording stopped by itself: {mins:g} min without sound (cut where the sound stopped)",
    "audio desactivado (audio.enabled)": "audio disabled (audio.enabled)",
    "SIN AUDIO: {error}": "NO AUDIO: {error}",
    "audio: esperando a que suene algo (aún no se graba)": "audio: waiting for sound (not recording yet)",
    "audio: {s:.0f} s sin sonido (se para sola en {left:.0f} s)":
        "audio: {s:.0f} s without sound (stops by itself in {left:.0f} s)",
    "audio {db:.0f} dB": "audio {db:.0f} dB",
    " (silencio)": " (silence)",
    "Temas {tracks} · transiciones {tr} · cuts {cuts} · bass swaps {swaps} · {audio}":
        "Tracks {tracks} · transitions {tr} · cuts {cuts} · bass swaps {swaps} · {audio}",
    "Compactar": "Compact",
    "Ampliar": "Expand",
    # --- histórico ---
    "Histórico de sesiones": "Session history",
    "🎧 Escuchar en el navegador": "🎧 Listen in the browser",
    "Abre la página de revisión: forma de onda de la sesión con transiciones, fader cuts y bass swaps; clic para escuchar desde ese punto":
        "Opens the review page: session waveform with transitions, fader cuts and bass swaps; click to listen from that point",
    "{path} (ilegible)": "{path} (unreadable)",
    "Todavía no hay sesiones grabadas.\n\nPulsa «Iniciar grabación» para grabar una.":
        "No recorded sessions yet.\n\nPress «Start recording» to record one.",
    "Revisión": "Review",
    "No se pudo abrir la página de revisión: {error}": "Could not open the review page: {error}",
    "No se pudo leer la sesión: {error}": "Could not read the session: {error}",
    # --- módulo luces ---
    "Luces apagadas": "Lights off",
    "Luces encendidas": "Lights on",
    "Encender luces": "Turn lights on",
    "Apagar luces": "Turn lights off",
    "Las luces siguen la música que suena en Rekordbox.": "The lights follow the music playing in Rekordbox.",
    "Ahora": "Now",
    "Último efecto": "Last effect",
    "Destino": "Destination",
    "Efectos por tema": "Per-track effects",
    "Mira y cambia qué efecto sale en cada parte de una canción.":
        "See and change which effect plays in each part of a song.",
    "Mis tiras": "My strips",
    "Cuántas tiras tienes, sus luces y en cuántas partes se dividen.":
        "How many strips you have, their lights and how many parts they are split into.",
    "Simulador": "Simulator",
    "Ver las luces en el navegador, sin el ESP32.": "See the lights in the browser, without the ESP32.",
    "Latencia": "Latency",
    "Ajustar el retraso para que las luces vayan con el bombo.":
        "Adjust the delay so the lights hit with the kick.",
    "Deck {deck}": "Deck {deck}",
    "Ver luces aquí": "Show lights here",
    "Las luces en un lateral de esta ventana, sin abrir el navegador.":
        "The lights in a side panel of this window, without opening the browser.",
    "&Ver luces en la ventana (lateral)": "&Show lights in the window (side panel)",
    "Luces en directo": "Live lights",
    "Esta instalación de PySide6 no incluye el navegador integrado (QtWebEngine). Se abre el simulador en el navegador.":
        "This PySide6 installation does not include the built-in browser (QtWebEngine). The simulator opens in the browser.",
    "Simulador en {url}{extra} · luces activadas": "Simulator at {url}{extra} · lights enabled",
    " (ya estaba abierto: se usa ese)": " (already open: using it)",
    "No se pudo abrir el simulador: {error}": "Could not open the simulator: {error}",
    "Luces: {label} → {fx}": "Lights: {label} → {fx}",
    "Carga un tema en un deck o genera una sesión primero.": "Load a track on a deck or generate a session first.",
    "enviados {n} a {dest}": "{n} sent to {dest}",
    "{n} errores de envío": "{n} send errors",
    "Deck {deck}: sin luces: {why}": "Deck {deck}: no lights: {why}",
    # --- editor de efectos ---
    "(ninguno)": "(none)",
    "Sin análisis": "No analysis",
    "Compás": "Bar",
    "Tiempo": "Time",
    "Sección": "Section",
    "Efecto": "Effect",
    "Parámetros": "Parameters",
    "Efectos de luces": "Light effects",
    "Tema:": "Track:",
    "compás ": "bar ",
    "Añadir": "Add",
    "Quitar": "Remove",
    "Regenerar desde el análisis": "Regenerate from analysis",
    "Guardar": "Save",
    "Probar": "Test",
    "Sin análisis de Rekordbox para este tema (¿está analizado?).":
        "No Rekordbox analysis for this track (is it analysed?).",
    "Ese compás no existe en el tema.": "That bar does not exist in the track.",
    "Se perderán los cambios hechos a mano en este tema. ¿Regenerar desde el análisis?":
        "Manual changes to this track will be lost. Regenerate from the analysis?",
    "Enviado: ": "Sent: ",
    "NO enviado (¿conectado a la Wi-Fi del ESP32?): ": "NOT sent (connected to the ESP32 Wi-Fi?): ",
    "Guardado en {path}": "Saved to {path}",
    "archivo: {path}": "file: {path}",
    "Calibrar latencia de las luces": "Calibrate light latency",
    "1. Pon un tema con bombo claro a sonar (fader arriba) y marca «Metrónomo».\n"
    "2. Las luces darán un destello en cada beat. Mira las luces reales (ESP32) mientras escuchas.\n"
    "3. Si el destello llega DESPUÉS del bombo, sube la latencia; si llega ANTES, bájala.\n"
    "4. Guarda cuando coincidan.":
        "1. Play a track with a clear kick (fader up) and tick «Metronome».\n"
        "2. The lights will flash on every beat. Watch the real lights (ESP32) while you listen.\n"
        "3. If the flash comes AFTER the kick, raise the latency; if it comes BEFORE, lower it.\n"
        "4. Save when they match.",
    "Metrónomo (destello en cada beat; silencia los efectos del tema)":
        "Metronome (flash on every beat; mutes the track effects)",
    "Adelanto:": "Advance:",
    "Guardar en config.json": "Save to config.json",
    "Cerrar": "Close",
    "Guardado: {ms} ms en {path}": "Saved: {ms} ms to {path}",
    # --- mis tiras ---
    "Mis luces": "My lights",
    "Tus tiras de luz": "Your light strips",
    "Añade tus tiras (hasta 8) y dile cuántas luces tiene cada una y en cuántas partes la quieres dividir. Cada parte de color puede tener su propio efecto. Toca una parte para verla encenderse.":
        "Add your strips (up to 8) and set how many lights each one has and how many parts you want to split it into. Each coloured part can have its own effect. Tap a part to see it light up.",
    "Puntos de luz": "Light points",
    "Partes": "Parts",
    "Cuántas luces se pueden encender por separado.\nEn tiras de 12 V (WS2811), cuenta cada grupo de 3 LEDs como 1.":
        "How many lights can be switched separately.\nOn 12 V strips (WS2811), count each group of 3 LEDs as 1.",
    "En cuántos trozos iguales se divide la tira.\nCada trozo puede tener su propio efecto.":
        "How many equal pieces the strip is split into.\nEach piece can have its own effect.",
    "Quitar esta tira": "Remove this strip",
    "Cable de datos en el pin": "Data wire on pin",
    "Pin del ESP32 al que va el cable de datos de esta tira.\nSe elige una vez al montarla; si lo cambias, las luces se reinician un momento.":
        "ESP32 pin where this strip's data wire goes.\nChosen once when you install it; if you change it, the lights restart for a moment.",
    "  (otra tira)": "  (another strip)",
    "Clic en una parte para encenderla en las luces": "Click a part to light it up",
    "＋  Añadir tira": "＋  Add strip",
    "Máximo {n} tiras": "Maximum {n} strips",
    "Tira {n}": "Strip {n}",
    "{n} tira · {parts} partes con efecto propio": "{n} strip · {parts} parts with their own effect",
    "{n} tiras · {parts} partes con efecto propio": "{n} strips · {parts} parts with their own effect",
    "✨  Probar en las luces": "✨  Test on the lights",
    "Cada parte se enciende de su color en las luces de verdad (y en el simulador)":
        "Each part lights up in its colour on the real lights (and in the simulator)",
    "Cancelar": "Cancel",
    "Guardado. Las luces no han respondido (¿están encendidas y en la misma Wi-Fi?); se les enviará al activar «Luces».":
        "Saved. The lights did not respond (are they on and on the same Wi-Fi?); it will be sent when «Lights» is enabled.",
    "Guardado. Como has cambiado pines o el número de tiras, las luces se reinician un momento para usarlos.":
        "Saved. Since you changed pins or the number of strips, the lights restart for a moment to use them.",
}


def set_language(lang: str | None) -> None:
    global _lang
    _lang = lang if lang in LANGUAGES else "es"


def language() -> str:
    return _lang


def tr(text: str, **kwargs) -> str:
    out = EN.get(text, text) if _lang == "en" else text
    return out.format(**kwargs) if kwargs else out

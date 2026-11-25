"""Luces con ESP32 por UDP (CLAUDE.md §11).

- `analysis`: rejilla, frases y cambios de energía desde el análisis de Rekordbox (ANLZ).
- `show`: archivo de eventos por tema (en beats, editable a mano) y generación con reglas.
- `engine`: en directo, el deck que manda dispara los eventos (con compensación de latencia).
- `udp`: envío de comandos JSON al ESP32.
- `manager`: une todo y mantiene una caché para que el bucle en vivo no toque el disco.
"""

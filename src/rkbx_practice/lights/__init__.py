"""FASE FUTURA: luces con ESP32 por UDP. NO implementado todavía (CLAUDE.md §11).

Puntos de enganche ya preparados en el resto del código:
- Rejilla de beats y frases de Rekordbox: `library.anlz.BeatGrid` / `BeatGridCache`.
- Deck que manda (más volumen efectivo; empate → el último que entró):
  `detection.volume.LeadDeckTracker`.
- Volumen efectivo con crossfader: `detection.volume.effective_volume`.
- Posición, BPM actual y saltos (loops/hot cues) por deck: `state.DeckState`
  (`position_s`, `bpm`, `jumped`), que llega en cada sondeo a los oyentes de
  `live.LivePoller` (añadir un oyente más para las luces).
- Configuración: sección `lights` de config.json.
- Formato de comando propuesto: `commands.LightCommand` (a confirmar).
"""

"""Normalización de rutas de análisis (ANLZ).

En `master.db` la ruta de análisis es relativa (p. ej.
`/PIONEER/USBANLZ/1ab/2c3d-.../ANLZ0000.DAT`), mientras que en memoria
Rekordbox puede tener la ruta absoluta y con otra extensión (`.EXT`, `.2EX`).
Para cruzarlas usamos como clave la parte desde `pioneer/usbanlz/` hasta la
carpeta del tema, en minúsculas y sin extensión.
"""

from __future__ import annotations

import re

_EXT_RE = re.compile(r"\.(dat|ext|2ex)$", re.IGNORECASE)


def normalize_anlz_key(path: str | None) -> str | None:
    if not path:
        return None
    p = str(path).strip().strip("\x00").replace("\\", "/").lower()
    if not p:
        return None
    idx = p.find("pioneer/usbanlz/")
    if idx >= 0:
        p = p[idx:]
    else:
        # Sin el prefijo estándar: nos quedamos con las dos últimas carpetas + archivo.
        parts = [x for x in p.split("/") if x]
        p = "/".join(parts[-3:])
    p = _EXT_RE.sub("", p)
    # Quitar el nombre del archivo (ANLZ0000) deja la carpeta única del tema.
    if p.rsplit("/", 1)[-1].startswith("anlz"):
        p = p.rsplit("/", 1)[0]
    return p or None

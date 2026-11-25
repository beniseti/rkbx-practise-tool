# Protocolo de luces (PC → ESP32)

Un datagrama **UDP** por comando, con un objeto **JSON** en UTF-8. Sin respuesta.
Por defecto al `192.168.4.1:4210` (el ESP32 crea su propia Wi-Fi). El simulador
(`python -m rkbx_practice lights simulate`) entiende exactamente lo mismo.

```json
{"fx": "flash", "color": "blanco", "dur_ms": 400, "zones": "odd"}
```

## Montaje y zonas

3 tiras × 3 secciones = **9 zonas** (`lights.layout` en `config/config.json`):

| | Sección 1 | Sección 2 | Sección 3 |
|---|---|---|---|
| Tira 1 | 1 | 2 | 3 |
| Tira 2 | 4 | 5 | 6 |
| Tira 3 | 7 | 8 | 9 |

`zones` (opcional, por defecto todas):

| Valor | Zonas |
|---|---|
| `all` / `todas` | 1-9 |
| `odd` / `impares` | 1, 3, 5, 7, 9 (tablero de ajedrez) |
| `even` / `pares` | 2, 4, 6, 8 |
| `strip:N` / `tira:N` | las 3 secciones de la tira N |
| `section:N` / `seccion:N` | la sección N de las 3 tiras |
| `1,5,9` o `[1,5,9]` | lista |

Cada zona tiene su propio estado: un comando solo cambia las zonas a las que va.

## Efectos

**De fondo** (se quedan hasta el siguiente efecto de fondo en esa zona). Regla del usuario: **las
luces nunca se quedan estáticas**; los fondos por defecto son siempre efectos en movimiento
(`wave`, `twinkle`, `alternate`, `chase`, `pulse`). `color`/`breathe` existen pero no se usan por defecto.

| fx | Parámetros | Qué hace |
|---|---|---|
| `color` | `color` | Color fijo |
| `off` | — | Apagado |
| `pulse` | `color`, `bpm` | Golpe en cada beat que decae |
| `breathe` | `color`, `period_ms` | Sube y baja suavemente |
| `alternate` | `color`, `color2`, `bpm` | Zonas pares e impares se turnan en cada beat |
| `chase` | `color`, `bpm`, `beats` | La luz recorre las zonas indicadas, un paso cada `beats` beats |
| `wave` | `color`, `color2`, `bpm`, `beats` | Ola de `color` sobre `color2` que recorre cada tira (una vuelta cada `beats` beats; cada tira algo desfasada) |
| `twinkle` | `color`, `color2`, `density`, `bpm` | Destellos aleatorios de `color` (fracción `density` de LEDs, nuevo reparto cada medio beat) sobre `color2` tenue |

**Temporales** (encima del fondo, durante `dur_ms`; luego vuelve el fondo):

| fx | Parámetros | Qué hace |
|---|---|---|
| `flash` | `color`, `dur_ms` | Destello que se apaga poco a poco |
| `strobe` | `color`, `dur_ms`, `hz` | Estroboscopio |
| `rainbow` | `dur_ms`, `speed` | Arcoíris que se mueve |
| `build` | `color`, `dur_ms` | Tensión: se encienden LEDs desde el centro de cada zona y el parpadeo se acelera hasta el final |

Colores: nombre en español o inglés (`rojo`, `verde`, `azul`, `blanco`, `amarillo`, `naranja`,
`morado`, `rosa`, `cian`, `magenta`, `negro`…), `#rrggbb` o `[r, g, b]`.
Un `fx` desconocido no debe romper nada (el simulador hace un pulso breve).

`bpm` llega ya resuelto con el BPM real del deck (en los archivos de efectos aparece como `"live"`).

## Sincronización con el beat (latencia)

- Los efectos que siguen el beat (`pulse`, `alternate`, `chase`, `wave`, `twinkle`) llevan `phase`
  (0..1): en qué punto del beat está la música en el momento en que el comando debe aplicarse. El
  ESP32 debe arrancar su reloj de beats en `ahora − phase × (60000 / bpm)` ms.
- Cada compás llega `{"fx": "sync", "bpm": 131.5, "phase": 0.0}` en el instante del primer beat del
  compás. No cambia el efecto ni el color: solo re-alinea el reloj de beats y el BPM (si se mueve el
  pitch, las luces no se desfasan). Conserva el n.º de beat para alternancias y persecuciones.
- La app ya adelanta todos los envíos `lights.latency_compensation_ms` (calibrable con el botón
  **Latencia…**: metrónomo con un `flash` de 80 ms en cada beat) y los programa al milisegundo,
  así que el ESP32 debe aplicar cada comando en cuanto lo recibe.

## Colores por tema

Los archivos de efectos usan la paleta del tema (`$primary`, `$secondary`, `$accent`, sacados de su
tonalidad Camelot) y llegan al ESP32 ya convertidos a `#rrggbb`.

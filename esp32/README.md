# Firmware de luces (ESP32)

Programa para un **ESP32 (ESP32-WROOM-32D, «ESP32D»)** que recibe los comandos de la app por
**UDP** (protocolo en [`docs/protocolo_luces.md`](../docs/protocolo_luces.md)) y los ejecuta en
**3 tiras × 3 secciones = 9 zonas**, con los mismos efectos que el simulador del navegador.

| Pieza | Elección | Por qué |
|---|---|---|
| LEDs | **FastLED** 3.7 | En el ESP32 usa el periférico RMT: las 3 tiras se envían por hardware, sin bloquear la CPU |
| Recepción | **AsyncUDP** (incluida en el núcleo Arduino del ESP32) | Por eventos en la tarea de red, sin sondear |
| JSON | **ArduinoJson** 7 | El analizador más rápido y ligero para microcontroladores |
| Latencia | Wi-Fi sin ahorro de energía, cola entre recepción y dibujo, 200 fps | El ahorro de energía del Wi-Fi añade decenas de ms |

La lógica de los efectos (`src/lights_core.cpp`) no depende de Arduino y se prueba en el PC:
`tests/test_esp32_core.py` la compila con `g++` y ejecuta `test_host/test_core.cpp`.

## Montaje

- Tira 1 → GPIO **25**, tira 2 → GPIO **26**, tira 3 → GPIO **27** (cámbialo en `include/config.h`).
- **Masa común** entre la fuente de los LEDs y el ESP32. Alimenta las tiras desde la fuente, no
  desde el USB del ESP32.
- Recomendado: resistencia de 330 Ω en serie en cada línea de datos y un condensador de
  1000 µF entre +5 V y masa en la entrada de las tiras. Con tiras de 5 V, un conversor de nivel
  (p. ej. 74AHCT125) para los datos si parpadean (el ESP32 da 3,3 V).
- `LEDS_PER_SECTION` (por defecto 20), tipo de LED (`WS2812B`, orden `GRB`), brillo y límite de
  consumo (`POWER_MAX_MILLIAMPS`) en `include/config.h`. Debe coincidir con `lights.layout` de
  `config/config.json` de la app.

## Compilar y subir (PlatformIO)

PlatformIO descarga solo la plataforma ESP32 y las librerías con las versiones fijadas en
`platformio.ini`.

```powershell
pip install platformio          # una vez (o la extensión «PlatformIO IDE» de VS Code)
cd esp32\rkbx_lights
pio run -t upload               # compila y sube (con la placa conectada por USB)
pio device monitor              # monitor serie a 115200
```

Si no detecta el puerto, instala el driver USB de la placa (CP210x o CH340, según el chip que
lleve) y, si hace falta, mantén pulsado **BOOT** al empezar la subida.

Con **Arduino IDE**: placa «ESP32 Dev Module», instala *FastLED* y *ArduinoJson* desde el
gestor de librerías, crea un sketch `rkbx_lights` y copia dentro `src/main.cpp` (renombrado a
`rkbx_lights.ino`), `src/lights_core.cpp`, `include/lights_core.h` e `include/config.h`.

## Uso

1. Al arrancar hace un barrido (tira 1 roja, 2 verde, 3 azul) para comprobar el cableado y queda
   en reposo con destellos suaves.
2. Conecta el PC a la Wi-Fi **RKBX-LUCES** (clave `rekordbox`). El ESP32 queda en `192.168.4.1`,
   puerto `4210`: lo que ya espera la app (`lights.esp32_ip` / `lights.esp32_port`).
   - Si el PC necesita su Wi-Fi para internet, usa cable para internet o pon `USE_STATION 1` en
     `config.h` para que el ESP32 se una a tu red (y copia la IP que muestra el monitor serie en
     `lights.esp32_ip`).
3. Prueba desde el PC:
   ```powershell
   python -m rkbx_practice lights send flash color=rojo dur_ms=500
   python -m rkbx_practice lights send chase color=naranja bpm=128 zones=odd
   ```
4. En la ventana de la app marca **Luces** y calibra con **Latencia…**.

Sin Wi-Fi también se puede probar por USB: en `pio device monitor` escribe un JSON por línea, por
ejemplo `{"fx":"wave","color":"azul","color2":"morado","bpm":128}`.

El monitor serie muestra cada 10 s: paquetes recibidos, inválidos, perdidos, fps y el último BPM
de `sync`. El LED de la placa parpadea con cada comando.

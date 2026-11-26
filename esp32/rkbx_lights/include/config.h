// Configuración del montaje y de la red. Cambia aquí pines, número de LEDs, potencia y Wi-Fi.
#pragma once

// ---- Tiras: 3 tiras × 3 secciones = 9 zonas (igual que lights.layout en config/config.json) ----
#define STRIPS 3
#define SECTIONS_PER_STRIP 3
#define LEDS_PER_SECTION 20                // LEDs de cada sección (cada tira = 3 × este valor)

// Pines de datos (GPIO). En un ESP32 (ESP32-WROOM-32D) estos son seguros para salida:
// evita 0, 2, 12 y 15 (arranque) y 6-11 (flash interna).
#define PIN_STRIP_1 25
#define PIN_STRIP_2 26
#define PIN_STRIP_3 27
#define PIN_STRIP_4 14                     // solo si STRIPS = 4

// Tipo de LED (FastLED): WS2812B / SK6812 → WS2812B con orden GRB.
#define LED_TYPE WS2812B
#define COLOR_ORDER GRB

// Brillo global (0-255) y límite de consumo: FastLED baja el brillo si se pasaría.
// ¡Cada LED WS2812B a blanco total gasta ~60 mA! 180 LEDs ≈ 10,8 A. Ajusta a tu fuente.
#define BRIGHTNESS 200
#define POWER_VOLTS 5
#define POWER_MAX_MILLIAMPS 4000

// Fotogramas por segundo del dibujo (los LEDs WS2812B admiten de sobra 200 fps con 60 LEDs/tira).
#define FRAMES_PER_SECOND 200

// ---- Red: el ESP32 crea su propia Wi-Fi (el PC se conecta a ella) ----
#define WIFI_AP_SSID "RKBX-LUCES"
#define WIFI_AP_PASSWORD "rekordbox"       // mínimo 8 caracteres
#define WIFI_AP_CHANNEL 6
#define WIFI_AP_IP 192, 168, 4, 1          // la app envía a lights.esp32_ip = 192.168.4.1
#define UDP_PORT 4210                      // lights.esp32_port

// Alternativa: unirse a una Wi-Fi existente (pon USE_STATION a 1 y la IP que mostrará el monitor serie
// en lights.esp32_ip de config/config.json).
#define USE_STATION 0
#define WIFI_STA_SSID "mi-wifi"
#define WIFI_STA_PASSWORD "mi-clave"

// LED de la placa (parpadea al recibir comandos).
#define STATUS_LED_PIN 2

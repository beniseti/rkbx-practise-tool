// Configuración del montaje y de la red. Cambia aquí pines, número de LEDs, potencia y Wi-Fi.
#pragma once

// ---- Tiras: 3 tiras × 3 secciones = 9 zonas (igual que lights.layout en config/config.json) ----
// Montaje del usuario: WS2811 a 12 V, 70 cm por tira, fuente de 12 V / 150 W.
// En WS2811 de 12 V cada chip controla un grupo de 3 LEDs = 1 PÍXEL (se corta cada 3 LEDs).
// PIXELS_PER_STRIP = número de chips (o de grupos de 3 LEDs) de cada tira:
//   60 LEDs/m → 20 píxeles/m → 70 cm ≈ 14 · 30 LEDs/m → 10 píxeles/m → 70 cm = 7.
// Las 3 secciones se reparten los píxeles lo más igual posible (14 → 4 + 5 + 5).
#define STRIPS 3
#define SECTIONS_PER_STRIP 3
#define PIXELS_PER_STRIP 14

// Pines de datos (GPIO). En un ESP32 (ESP32-WROOM-32D) estos son seguros para salida:
// evita 0, 2, 12 y 15 (arranque), 6-11 (flash interna) y 34-39 (solo entrada).
#define PIN_STRIP_1 25
#define PIN_STRIP_2 26
#define PIN_STRIP_3 27
#define PIN_STRIP_4 14                     // solo si STRIPS = 4

// Tipo de LED (FastLED). WS2811 a 800 kHz (si parpadea o no responde, prueba WS2811_400).
// El orden de color de las WS2811 varía según el fabricante: en el barrido de arranque la tira 1
// debe verse ROJA, la 2 VERDE y la 3 AZUL. Si no, cambia COLOR_ORDER (RGB, RBG, GRB, GBR, BRG, BGR).
#define LED_TYPE WS2811
#define COLOR_ORDER RGB

// Brillo global (0-255) y límite de consumo (FastLED baja el brillo si se pasaría).
// Un píxel WS2811 de 12 V a blanco total gasta ~60 mA a 12 V: 3 × 14 píxeles ≈ 2,5 A (30 W),
// muy por debajo de la fuente de 150 W (12,5 A). El límite queda solo como protección.
#define BRIGHTNESS 255
#define POWER_VOLTS 12
#define POWER_MAX_MILLIAMPS 10000

// Fotogramas por segundo del dibujo.
#define FRAMES_PER_SECOND 200

// ---- Red ----
// 1) Al arrancar intenta unirse a la Wi-Fi de casa (WIFI_STA_SSID / WIFI_STA_PASSWORD, en
//    include/secrets.h, que NO se sube a GitHub; copia secrets.example.h como secrets.h).
// 2) Si no hay Wi-Fi configurada o no conecta en WIFI_STA_TIMEOUT_MS, crea su propia Wi-Fi.
// En los dos casos se anuncia por mDNS como «rkbx-luces.local» (la app lo busca solo).
#if __has_include("secrets.h")
#include "secrets.h"
#endif
#ifndef WIFI_STA_SSID
#define WIFI_STA_SSID ""                   // vacío = no intentar Wi-Fi de casa
#define WIFI_STA_PASSWORD ""
#endif
#define WIFI_STA_TIMEOUT_MS 15000
// IP fija en la Wi-Fi de casa (opcional; recomendable para que la app la encuentre siempre).
// Déjalo comentado para usar DHCP. Formato: 192, 168, 1, 200
// #define WIFI_STA_STATIC_IP 192, 168, 1, 200
// #define WIFI_STA_GATEWAY 192, 168, 1, 1
// #define WIFI_STA_SUBNET 255, 255, 255, 0

#define HOSTNAME "rkbx-luces"              // mDNS: rkbx-luces.local

// Wi-Fi propia (si no hay Wi-Fi de casa): el PC se conecta a ella.
#define WIFI_AP_SSID "RKBX-LUCES"
#define WIFI_AP_PASSWORD "rekordbox"       // mínimo 8 caracteres
#define WIFI_AP_CHANNEL 6
#define WIFI_AP_IP 192, 168, 4, 1
#define UDP_PORT 4210                      // lights.esp32_port

// LED de la placa (parpadea al recibir comandos).
#define STATUS_LED_PIN 2

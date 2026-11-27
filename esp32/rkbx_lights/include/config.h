// Configuración del montaje y de la red. Cambia aquí pines, número de LEDs, potencia y Wi-Fi.
#pragma once

// ---- Tiras ----
// Hasta 8 tiras, cada una en su pin. El montaje (cuántas tiras, en qué pin, cuántos píxeles y
// partes tiene cada una) se elige en la app (ventana «Mis tiras…»), sin reprogramar: el ESP32 lo
// guarda en su memoria. Cambiar píxeles o partes se aplica al momento; cambiar pines o número de
// tiras reinicia el ESP32 un segundo. Estos son solo los valores de fábrica.
// En WS2811 de 12 V cada chip controla un grupo de 3 LEDs = 1 PÍXEL (se corta cada 3 LEDs).
//   60 LEDs/m → 20 píxeles/m → 70 cm ≈ 14 · 30 LEDs/m → 10 píxeles/m → 70 cm = 7.
#define DEFAULT_STRIPS 3
#define DEFAULT_SEGMENTS 3
#define DEFAULT_PIXELS 14

// Pin de fábrica de cada tira (1..8) y pines que se pueden elegir en la app. Seguros para salida
// en un ESP32-WROOM-32D: sin 0, 2, 5, 12 y 15 (arranque), 1 y 3 (USB), 6-11 (flash), 34-39 (entrada).
#define DEFAULT_PINS {25, 26, 27, 14, 32, 33, 13, 4}
// (la lista de pines elegibles está en main.cpp, función addStrip: un caso por pin)

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

// Monitor serie: 0 = no mostrar comandos · 1 = mostrar cada comando recibido (sin «sync») · 2 = todos.
// Se puede cambiar en marcha escribiendo «v» + Intro en el monitor.
#define LOG_COMMANDS 1

// LED de la placa (parpadea al recibir comandos).
#define STATUS_LED_PIN 2

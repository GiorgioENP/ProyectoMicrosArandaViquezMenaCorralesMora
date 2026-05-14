/*
 * firmware_esp32.ino — Firmware del ESP32 para el Sistema de Percheros Inteligentes
 *
 * Hardware controlado por este microcontrolador:
 *   1) Motor a pasos (driver tipo A4988 / DRV8825) que rota la base
 *      del set para presentar uno de los 3 percheros al usuario.
 *   2) Tres servomotores (uno por perchero), cada uno con 5 posiciones
 *      angulares para presentar la prenda solicitada.
 *
 * Comunicación:
 *   UART2 (RX2=GPIO16, TX2=GPIO17) a 115200 baud, 8N1.
 *   Recibe comandos en texto plano de la Raspberry Pi y responde con
 *   "OK", "BUSY" o "ERR <razón>".
 *
 * Cumple los requerimientos del PDF:
 *   - Microcontrolador NO Arduino (es ESP32). ✓
 *   - Comunicación con Pi por RS-232/UART. ✓
 *   - USO DE INTERRUPCIONES (obligatorio): se usa una interrupción de
 *     timer hardware (timerAttachInterrupt) para generar los pulsos
 *     STEP del motor a pasos sin bloquear el loop principal. ✓
 *   - El microcontrolador gobierna sensores y actuadores. ✓
 *
 * Librerías necesarias (Arduino IDE):
 *   - ESP32Servo  (Kevin Harrington / John K. Bennett)
 *
 * Conexiones:
 *   STEP   → GPIO 14   (paso del stepper)
 *   DIR    → GPIO 27   (dirección del stepper)
 *   ENABLE → GPIO 26   (active LOW)
 *   SERVO1 → GPIO 18
 *   SERVO2 → GPIO 19
 *   SERVO3 → GPIO 21
 *   UART2 RX → GPIO 16   (al TX del Pi, GPIO14)
 *   UART2 TX → GPIO 17   (al RX del Pi, GPIO15)
 *   GND común con Pi y con la fuente de los motores.
 */

#include <ESP32Servo.h>

// ─────────────────────────────────────────
// Configuración
// ─────────────────────────────────────────

#define STEP_PIN     14
#define DIR_PIN      27
#define ENABLE_PIN   26
#define SERVO1_PIN   18
#define SERVO2_PIN   19
#define SERVO3_PIN   21

#define UART_BAUD    115200
#define UART_RX      16
#define UART_TX      17

// Stepper: 1.8°/paso × microstepping 1/8 = 0.225°/paso
// Una revolución completa = 1600 pasos (microstepped)
// 360° / 3 percheros = 120° por perchero = 533 pasos
const int PASOS_POR_REV       = 1600;
const int PASOS_POR_PERCHERO  = PASOS_POR_REV / 3;

// Servos: 5 posiciones de 18° a 162° en pasos de 36°
const int ANG_BASE = 18;
const int ANG_PASO = 36;

// Frecuencia del timer para los pulsos STEP.
// Velocidad del motor: 800 Hz → 800 pasos/s → 0.5 rev/s
// Período del timer = 1/(2*800) = 625 µs (cambia el pin cada medio período)
const int STEP_HALF_PERIOD_US = 625;

// ─────────────────────────────────────────
// Estado global (compartido con la ISR)
// ─────────────────────────────────────────

Servo servo[3];
volatile int  pasos_pendientes = 0;   // pasos restantes por dar
volatile bool nivel_step       = false;

hw_timer_t* timer_step = nullptr;
portMUX_TYPE timer_mux = portMUX_INITIALIZER_UNLOCKED;

int  perchero_actual = 1;             // 1..3, posición frontal de la base
int  servo_pos[3]    = {0, 0, 0};     // 0..4, posición actual de cada servo

// ─────────────────────────────────────────
// ISR del timer (genera los pulsos STEP)
// ─────────────────────────────────────────
//
// Esta función se ejecuta cada STEP_HALF_PERIOD_US microsegundos.
// Cumple el requerimiento "uso de interrupciones obligatorio" del PDF:
// el motor avanza sin que el loop principal tenga que ocuparse de
// generar los pulsos.

void IRAM_ATTR onStepTimer() {
  portENTER_CRITICAL_ISR(&timer_mux);
  if (pasos_pendientes > 0) {
    nivel_step = !nivel_step;
    digitalWrite(STEP_PIN, nivel_step ? HIGH : LOW);
    // un "paso" completo es flanco de subida + flanco de bajada
    if (!nivel_step) {
      pasos_pendientes--;
    }
  }
  portEXIT_CRITICAL_ISR(&timer_mux);
}

// ─────────────────────────────────────────
// Movimiento del stepper
// ─────────────────────────────────────────

bool stepper_ocupado() {
  bool ocupado;
  portENTER_CRITICAL(&timer_mux);
  ocupado = (pasos_pendientes > 0);
  portEXIT_CRITICAL(&timer_mux);
  return ocupado;
}

void mover_stepper(int delta_pasos) {
  // delta_pasos negativo = sentido contrario
  digitalWrite(DIR_PIN, delta_pasos >= 0 ? HIGH : LOW);
  digitalWrite(ENABLE_PIN, LOW);  // activa el driver
  portENTER_CRITICAL(&timer_mux);
  pasos_pendientes = abs(delta_pasos);
  portEXIT_CRITICAL(&timer_mux);
  while (stepper_ocupado()) { delay(1); }   // bloqueamos hasta terminar
  digitalWrite(ENABLE_PIN, HIGH); // libera (motor sin torque, ahorra energía)
}

// ─────────────────────────────────────────
// Comandos de alto nivel
// ─────────────────────────────────────────

bool ir_a_perchero(int p) {
  if (p < 1 || p > 3) return false;

  // Calcular delta directo en pasos
  int delta = (p - perchero_actual) * PASOS_POR_PERCHERO;

  // Tomar SIEMPRE el camino más corto:
  // Si el arco directo supera la mitad de una revolución, es más rápido
  // ir en el sentido contrario (ahorra hasta PASOS_POR_PERCHERO pasos).
  // Ejemplo: perchero 3 → 1 directo = -1066 pasos, al revés = +533 pasos.
  int mitad_rev = PASOS_POR_REV / 2;
  if (delta >  mitad_rev) delta -= PASOS_POR_REV;
  if (delta < -mitad_rev) delta += PASOS_POR_REV;

  if (delta != 0) {
    mover_stepper(delta);
  }
  perchero_actual = p;
  return true;
}

bool rotar_servo(int p, int slot) {
  if (p < 1 || p > 3 || slot < 0 || slot > 4) return false;
  int angulo = ANG_BASE + slot * ANG_PASO;
  servo[p - 1].write(angulo);
  servo_pos[p - 1] = slot;
  delay(400);  // tiempo aproximado para que el servo llegue
  return true;
}

void home_todos() {
  // Lleva el stepper a perchero 1 y todos los servos al slot 0
  // (usuarios de producción pondrían un sensor de fin de carrera)
  ir_a_perchero(1);
  for (int i = 0; i < 3; i++) {
    rotar_servo(i + 1, 0);
  }
}

// ─────────────────────────────────────────
// Parser de comandos UART
// ─────────────────────────────────────────

void responder(const String& msg) {
  Serial2.print(msg);
  Serial2.print('\n');
  Serial.print("→ ");          // eco por USB serial para debug
  Serial.println(msg);
}

void procesar_comando(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  Serial.print("← ");
  Serial.println(cmd);

  if (cmd == "PING") {
    responder("OK");
    return;
  }

  if (cmd == "HOME") {
    home_todos();
    responder("OK");
    return;
  }

  if (cmd == "STATUS") {
    String r = "STATUS " + String(perchero_actual) + " "
             + String(servo_pos[0]) + " "
             + String(servo_pos[1]) + " "
             + String(servo_pos[2]);
    responder(r);
    return;
  }

  if (cmd.startsWith("MOVE_BASE ")) {
    int p = cmd.substring(10).toInt();
    if (ir_a_perchero(p)) responder("OK");
    else                  responder("ERR perchero invalido");
    return;
  }

  if (cmd.startsWith("ROTATE ")) {
    int sp1 = cmd.indexOf(' ', 7);
    if (sp1 < 0) { responder("ERR sintaxis"); return; }
    int p = cmd.substring(7, sp1).toInt();
    int s = cmd.substring(sp1 + 1).toInt();
    if (rotar_servo(p, s)) responder("OK");
    else                   responder("ERR rango");
    return;
  }

  if (cmd.startsWith("PRESENT ")) {
    int sp1 = cmd.indexOf(' ', 8);
    if (sp1 < 0) { responder("ERR sintaxis"); return; }
    int p = cmd.substring(8, sp1).toInt();
    int s = cmd.substring(sp1 + 1).toInt();
    if (!ir_a_perchero(p))  { responder("ERR perchero invalido"); return; }
    if (!rotar_servo(p, s)) { responder("ERR slot invalido");     return; }
    responder("OK");
    return;
  }

  responder("ERR comando desconocido: " + cmd);
}

// ─────────────────────────────────────────
// setup / loop
// ─────────────────────────────────────────

void setup() {
  Serial.begin(115200);                         // USB serial (debug)
  Serial2.begin(UART_BAUD, SERIAL_8N1, UART_RX, UART_TX);  // UART al Pi

  pinMode(STEP_PIN,   OUTPUT);
  pinMode(DIR_PIN,    OUTPUT);
  pinMode(ENABLE_PIN, OUTPUT);
  digitalWrite(ENABLE_PIN, HIGH);  // driver desactivado al boot

  // Servos
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  servo[0].attach(SERVO1_PIN);
  servo[1].attach(SERVO2_PIN);
  servo[2].attach(SERVO3_PIN);

  // Timer hardware para pulsos del stepper (interrupción)
  // ESP32 tiene 4 timers de 64 bits, divider de 80 → 1 µs por tick
  timer_step = timerBegin(0, 80, true);
  timerAttachInterrupt(timer_step, &onStepTimer, true);
  timerAlarmWrite(timer_step, STEP_HALF_PERIOD_US, true);
  timerAlarmEnable(timer_step);

  Serial.println("\n[ESP32] Firmware iniciado. Esperando comandos...");
  // Calibración inicial
  home_todos();
  Serial.println("[ESP32] HOME completo. Listo.");
}

void loop() {
  // Lee líneas completas del UART2
  static String buffer;
  while (Serial2.available()) {
    char c = (char)Serial2.read();
    if (c == '\n' || c == '\r') {
      if (buffer.length() > 0) {
        procesar_comando(buffer);
        buffer = "";
      }
    } else {
      buffer += c;
      if (buffer.length() > 64) buffer = "";  // protección
    }
  }
}

/*
 * firmware_esp32.ino — Firmware del ESP32 para el Sistema de Percheros Inteligentes
 *
 * ── Arquitectura de comunicación I2C ────────────────────────────────────────
 *
 * El driver I2C del ESP-IDF (modo slave) ya registra internamente un ISR de
 * hardware que transfiere cada byte recibido a un ring buffer circular en RAM.
 * Ese ISR no es accesible directamente desde Arduino/IDF sin reescribir el
 * driver completo.
 *
 * Lo que SÍ se puede y tiene sentido hacer es eliminar el polling del loop()
 * principal reemplazándolo por:
 *
 *   1. Un ISR de GPIO sobre la línea SDA (flanco de START de I2C) que notifica
 *      a una tarea FreeRTOS vía una cola (Queue) sin despertar el CPU para nada
 *      hasta que llegue tráfico real.
 *
 *   2. Una tarea FreeRTOS dedicada (tareaI2C) que duerme bloqueada en
 *      xQueueReceive() y solo despierta cuando el ISR deposita una notificación.
 *      Al despertar, drena el ring buffer del driver I2C, ensambla el comando
 *      y lo encola en cmdQueue para que lo ejecute la tarea de motores.
 *
 *   3. Una tarea FreeRTOS de motores (tareaMotores) que duerme bloqueada en
 *      xQueueReceive(cmdQueue) y ejecuta los movimientos solo cuando llega
 *      un comando. Envía la respuesta OK/ERR por I2C al terminar.
 *
 * Con este esquema el CPU queda en idle (WFI) entre comandos; solo lo
 * despiertan eventos reales de hardware (flanco SDA), no un contador de 10 ms.
 *
 * ── Pines ───────────────────────────────────────────────────────────────────
 *   ULN2003  IN1 → GPIO 14
 *            IN2 → GPIO 27
 *            IN3 → GPIO 26
 *            IN4 → GPIO 25
 *   SERVO1   → GPIO 18   (MiniDisco 1)
 *   SERVO2   → GPIO 19   (MiniDisco 2)
 *   SERVO3   → GPIO 23   (MiniDisco 3)
 *   SDA      → GPIO 21
 *   SCL      → GPIO 22
 *
 * ── Protocolo Pi → ESP32 (texto plano, terminado en '\n') ───────────────────
 *   PING          — verificar comunicación
 *   GOTO <n>      — ir al estado n (1–15): mueve stepper + servo
 *   RETIRAR       — confirmación del usuario (sin movimiento de motor)
 *   HOME_STEPPER  — fin de sesión: servos a 0° + stepper al disco 0
 *   STATUS        — responde con estado actual
 *
 * ── Protocolo ESP32 → Pi ────────────────────────────────────────────────────
 *   OK
 *   ERR <razón>
 *   STATUS <estado> <disco+1> <posServo+1>
 */

#include <Arduino.h>
#include <ESP32Servo.h>
#include <Stepper.h>
#include "driver/i2c.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

// ─────────────────────────────────────────
// Pines
// ─────────────────────────────────────────

#define IN1        14
#define IN2        27
#define IN3        26
#define IN4        25

#define SERVO1_PIN 18
#define SERVO2_PIN 19
#define SERVO3_PIN 23

#define I2C_SLAVE_ADDR  0x08
#define SDA_PIN         21
#define SCL_PIN         22
#define I2C_PORT        I2C_NUM_0
#define I2C_RX_BUF_LEN 256   // más grande para absorber ráfagas sin pérdida
#define I2C_TX_BUF_LEN 128

// ─────────────────────────────────────────
// Motores
// ─────────────────────────────────────────

const int stepsPerRevolution = 2048;
const int steps120           = stepsPerRevolution / 3;

Stepper stepperMotor(stepsPerRevolution, IN1, IN3, IN2, IN4);

Servo servo1;
Servo servo2;
Servo servo3;

int servoAngles[5] = {0, 45, 90, 135, 180};

// ─────────────────────────────────────────
// Estado global (solo accedido desde tareaMotores)
// ─────────────────────────────────────────

static int currentDisk     = 0;
static int currentServoPos = 0;
static int currentState    = 1;

// ─────────────────────────────────────────
// Colas FreeRTOS
// ─────────────────────────────────────────

// ISR → tareaI2C: notificación de actividad en bus (valor ignorado)
static QueueHandle_t sdaQueue;

// tareaI2C → tareaMotores: comando completo como string fijo
#define CMD_MAX_LEN 32
typedef struct { char cmd[CMD_MAX_LEN]; } CmdMsg;
static QueueHandle_t cmdQueue;

// ─────────────────────────────────────────
// ISR de GPIO — flanco de START en SDA
// ─────────────────────────────────────────
//
// El master I2C (Raspberry Pi) baja SDA mientras SCL está alto para
// señalizar una condición de START. Ese flanco descendente en SDA es
// la señal más temprana posible de que va a llegar un comando.
//
// El ISR no lee datos; solo deposita un token en sdaQueue para
// despertar la tareaI2C. xQueueSendFromISR no bloquea y es segura
// desde contexto de interrupción.
//
// IRAM_ATTR: el ISR debe residir en RAM interna para ejecutarse
// incluso cuando el caché de flash está ocupado.

static void IRAM_ATTR isrSDA(void* arg) {
  BaseType_t xHigherPriorityTaskWoken = pdFALSE;
  uint8_t token = 1;
  xQueueSendFromISR(sdaQueue, &token, &xHigherPriorityTaskWoken);
  // Si despertar la tarea requiere un cambio de contexto inmediato,
  // portYIELD_FROM_ISR lo solicita al scheduler antes de salir del ISR.
  portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
}

// ─────────────────────────────────────────
// Helpers de respuesta
// ─────────────────────────────────────────

static char respBuf[I2C_TX_BUF_LEN];

static void enviarRespuesta(const char* msg) {
  int len = snprintf(respBuf, sizeof(respBuf), "%s\n", msg);
  i2c_slave_write_buffer(I2C_PORT,
                         (uint8_t*)respBuf,
                         len,
                         pdMS_TO_TICKS(20));
  Serial.print("→ ");
  Serial.println(msg);
}

// ─────────────────────────────────────────
// Lógica de motores
// ─────────────────────────────────────────

static void printPos() {
  Serial.printf("Estado: %d | Disco: %d | ServoPos: %d\n",
                currentState, currentDisk + 1, currentServoPos + 1);
}

static void moveToDisk(int target) {
  while (currentDisk != target) {
    int dir = (target > currentDisk) ? 1 : -1;
    currentDisk += dir;
    stepperMotor.step(dir * steps120);
    delay(400);
  }
}

static void moveServo(int disk, int pos) {
  switch (disk) {
    case 0: servo1.write(servoAngles[pos]); break;
    case 1: servo2.write(servoAngles[pos]); break;
    case 2: servo3.write(servoAngles[pos]); break;
  }
  currentServoPos = pos;
  delay(500);
}

static void goToState(int state) {
  int disk = (state - 1) / 5;
  int pos  = (state - 1) % 5;
  moveToDisk(disk);
  moveServo(disk, pos);
  currentState = state;
  printPos();
}

static void homeStepper() {
  servo1.write(servoAngles[0]);
  servo2.write(servoAngles[0]);
  servo3.write(servoAngles[0]);
  currentServoPos = 0;
  delay(600);
  moveToDisk(0);
  currentState = 1;
  Serial.println("[HOME_STEPPER] Servos a 0° y stepper en disco 0.");
}

// ─────────────────────────────────────────
// Procesador de comandos (llamado desde tareaMotores)
// ─────────────────────────────────────────

static void procesarComando(const char* rawCmd) {
  String cmd = String(rawCmd);
  cmd.trim();
  if (cmd.length() == 0) return;

  Serial.print("← ");
  Serial.println(cmd);

  if (cmd == "PING") {
    enviarRespuesta("OK");
    return;
  }

  if (cmd.startsWith("GOTO ")) {
    int estado = cmd.substring(5).toInt();
    if (estado >= 1 && estado <= 15) {
      goToState(estado);
      enviarRespuesta("OK");
    } else {
      enviarRespuesta("ERR estado invalido (1-15)");
    }
    return;
  }

  if (cmd == "RETIRAR") {
    Serial.println("[RETIRAR] Confirmado. Motores sin cambio.");
    enviarRespuesta("OK");
    return;
  }

  if (cmd == "HOME_STEPPER") {
    homeStepper();
    enviarRespuesta("OK");
    return;
  }

  if (cmd == "STATUS") {
    char buf[40];
    snprintf(buf, sizeof(buf), "STATUS %d %d %d",
             currentState, currentDisk + 1, currentServoPos + 1);
    enviarRespuesta(buf);
    return;
  }

  char err[48];
  snprintf(err, sizeof(err), "ERR cmd desconocido");
  enviarRespuesta(err);
}

// ─────────────────────────────────────────
// Tarea FreeRTOS: recepción I2C
// ─────────────────────────────────────────
//
// Duerme bloqueada en xQueueReceive(sdaQueue) esperando la notificación
// del ISR de SDA. Al despertar, drena el ring buffer del driver I2C
// byte a byte, ensambla el comando terminado en '\n' y lo encola en
// cmdQueue para que tareaMotores lo ejecute.
//
// No ejecuta ningún movimiento de motor; solo transforma bytes en comandos.
// Corre en el Core 0 (mismo que el ISR de GPIO por defecto en ESP32).

static void tareaI2C(void* arg) {
  static char rxBuf[CMD_MAX_LEN];
  static int  rxIdx = 0;

  uint8_t token;
  uint8_t b;

  for (;;) {
    // Dormir hasta que el ISR de SDA notifique actividad
    // Timeout de 500 ms como red de seguridad ante bytes que llegan
    // justo antes de que el ISR se registre (condición de arranque)
    xQueueReceive(sdaQueue, &token, pdMS_TO_TICKS(500));

    // Drenar todo lo que haya en el ring buffer del driver I2C
    while (i2c_slave_read_buffer(I2C_PORT, &b, 1, pdMS_TO_TICKS(2)) == 1) {
      char c = (char)b;

      if (c == '\n' || c == '\r') {
        if (rxIdx > 0) {
          rxBuf[rxIdx] = '\0';
          rxIdx = 0;

          // Encolar el comando para tareaMotores
          CmdMsg msg;
          strncpy(msg.cmd, rxBuf, CMD_MAX_LEN - 1);
          msg.cmd[CMD_MAX_LEN - 1] = '\0';
          // xQueueSend no bloquea si la cola está llena (descarta):
          // en este sistema la Pi espera el OK antes de enviar otro
          // comando, así que la cola nunca debería llenarse.
          xQueueSend(cmdQueue, &msg, 0);
        }
      } else {
        if (rxIdx < CMD_MAX_LEN - 1) {
          rxBuf[rxIdx++] = c;
        } else {
          // Overflow de buffer: descartar y reiniciar
          rxIdx = 0;
          Serial.println("[WARN] Buffer I2C overflow, descartando.");
        }
      }
    }
  }
}

// ─────────────────────────────────────────
// Tarea FreeRTOS: ejecución de motores
// ─────────────────────────────────────────
//
// Duerme bloqueada en xQueueReceive(cmdQueue) hasta que tareaI2C
// deposite un comando ensamblado. Luego lo ejecuta sin límite de tiempo
// (los movimientos de motor pueden tomar varios segundos).
// Corre en el Core 1 para no competir con la recepción I2C en Core 0.

static void tareaMotores(void* arg) {
  CmdMsg msg;
  for (;;) {
    // Bloquear indefinidamente hasta que llegue un comando
    if (xQueueReceive(cmdQueue, &msg, portMAX_DELAY) == pdTRUE) {
      procesarComando(msg.cmd);
    }
  }
}

// ─────────────────────────────────────────
// setup
// ─────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n[ESP32] Iniciando — I2C slave con ISR + FreeRTOS");

  // ── Motores ──────────────────────────────────────────────────────────
  stepperMotor.setSpeed(10);

  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  servo1.attach(SERVO1_PIN);
  servo2.attach(SERVO2_PIN);
  servo3.attach(SERVO3_PIN);

  servo1.write(servoAngles[0]);
  servo2.write(servoAngles[0]);
  servo3.write(servoAngles[0]);
  currentServoPos = 0;
  delay(1000);
  Serial.println("Motores inicializados.");
  printPos();

  // ── Driver I2C slave (ESP-IDF) ───────────────────────────────────────
  i2c_config_t conf = {};
  conf.mode                = I2C_MODE_SLAVE;
  conf.sda_io_num          = SDA_PIN;
  conf.scl_io_num          = SCL_PIN;
  conf.sda_pullup_en       = GPIO_PULLUP_ENABLE;
  conf.scl_pullup_en       = GPIO_PULLUP_ENABLE;
  conf.slave.addr_10bit_en = 0;
  conf.slave.slave_addr    = I2C_SLAVE_ADDR;

  ESP_ERROR_CHECK(i2c_param_config(I2C_PORT, &conf));
  ESP_ERROR_CHECK(i2c_driver_install(I2C_PORT,
                                     I2C_MODE_SLAVE,
                                     I2C_RX_BUF_LEN,
                                     I2C_TX_BUF_LEN,
                                     0));
  Serial.printf("[I2C] Slave  addr=0x%02X  SDA=GPIO%d  SCL=GPIO%d\n",
                I2C_SLAVE_ADDR, SDA_PIN, SCL_PIN);

  // ── Colas FreeRTOS ───────────────────────────────────────────────────
  // sdaQueue: profundidad 4 (absorbe ráfagas de flancos START sin perder)
  sdaQueue = xQueueCreate(4, sizeof(uint8_t));
  // cmdQueue: profundidad 2 (la Pi espera OK antes de enviar otro cmd)
  cmdQueue = xQueueCreate(2, sizeof(CmdMsg));

  // ── ISR de GPIO en SDA ───────────────────────────────────────────────
  // El driver I2C del ESP-IDF reclama el pin SDA internamente, por lo que
  // no se puede usar gpio_isr_handler_add() directamente sobre ese pin
  // sin quitarle el control al driver.
  //
  // Solución: instalar el ISR sobre SCL en lugar de SDA.
  // SCL tiene un flanco descendente al inicio de cada bit transmitido,
  // por lo que la notificación llega igual de temprano.
  // El driver I2C sigue gestionando SDA/SCL para la recepción real;
  // el ISR solo usa SCL como señal de "hay actividad en el bus".
  //
  // Configuración:
  //   - gpio_set_intr_type: NEGEDGE (flanco descendente de SCL = inicio de bit)
  //   - ESP_INTR_FLAG_IRAM: ISR reside en RAM interna (requerido)
  //   - ESP_INTR_FLAG_LEVEL1: prioridad baja para no interferir con el
  //     ISR interno del driver I2C (que corre a prioridad más alta)

  gpio_set_direction((gpio_num_t)SCL_PIN, GPIO_MODE_INPUT);
  gpio_set_intr_type((gpio_num_t)SCL_PIN, GPIO_INTR_NEGEDGE);
  gpio_install_isr_service(ESP_INTR_FLAG_LEVEL1 | ESP_INTR_FLAG_IRAM);
  gpio_isr_handler_add((gpio_num_t)SCL_PIN, isrSDA, nullptr);
  gpio_intr_enable((gpio_num_t)SCL_PIN);

  Serial.println("[ISR] GPIO SCL configurado (NEGEDGE → notifica tareaI2C).");

  // ── Tareas FreeRTOS ───────────────────────────────────────────────────
  // tareaI2C en Core 0 (mismo core que los ISR de GPIO por defecto)
  xTaskCreatePinnedToCore(
    tareaI2C,
    "tareaI2C",
    4096,    // stack: suficiente para buffers de recepción
    nullptr,
    2,       // prioridad 2: por encima de idle, por debajo de tareaMotores
    nullptr,
    0        // Core 0
  );

  // tareaMotores en Core 1 (libre del scheduler de Wi-Fi/BT si los hubiera)
  xTaskCreatePinnedToCore(
    tareaMotores,
    "tareaMotores",
    4096,    // stack: suficiente para String + lógica de motores
    nullptr,
    3,       // prioridad 3: más alta que tareaI2C para ejecutar sin demora
    nullptr,
    1        // Core 1
  );

  Serial.println("[ESP32] Listo. Esperando comandos vía I2C...");
  Serial.println("  PING | GOTO <1-15> | RETIRAR | HOME_STEPPER | STATUS");
}

// ─────────────────────────────────────────
// loop — vacío
// ─────────────────────────────────────────
//
// Todo el trabajo lo hacen las tareas FreeRTOS y el ISR.
// El scheduler de FreeRTOS pone el CPU en idle (WFI) cuando no hay
// ninguna tarea lista para ejecutar, reduciendo el consumo al mínimo.

void loop() {
  vTaskDelay(portMAX_DELAY);  // ceder el procesador indefinidamente
}

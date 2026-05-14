"""
comunicacion.py — Driver UART entre Raspberry Pi y ESP32.

Protocolo: texto plano sobre UART a 115200 baud, 8N1.
Comandos terminados en '\\n'. Respuestas terminadas en '\\n'.

PROTOCOLO Pi → ESP32:
    PING               ¿estás vivo?
    HOME               calibrar; todos los motores a posición de origen
    MOVE_BASE <p>      mueve el motor a pasos para presentar el perchero p (1..3)
    ROTATE <p> <s>     gira el servo del perchero p al slot s (0..4)
    PRESENT <p> <s>    hace MOVE_BASE p y luego ROTATE p s
    STATUS             pide estado actual

PROTOCOLO ESP32 → Pi:
    OK                          comando ejecutado
    BUSY                        en movimiento, espera (se reintenta automáticamente)
    ERR <razón>                 error
    STATUS <p> <s1> <s2> <s3>   respuesta a STATUS

Cumple el requerimiento del PDF: "La comunicación con el microcontrolador
se realizará mediante protocolo RS-232, I2C o SPI". UART es RS-232 lógico.

CORRECCIONES respecto a versión anterior:
  - HOME usa timeout extendido (12 s) para mover stepper + 3 servos (~2.5 s real)
  - PRESENT usa timeout extendido (8 s) para mover base + 1 servo
  - Respuesta BUSY se reintenta automáticamente hasta MAX_REINTENTOS_BUSY veces
  - timeout ajustable por comando sin reconectar el puerto serie
"""

from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional


# ─────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────

BAUDRATE = 115200

# Timeouts diferenciados por tipo de operación.
# HOME:    stepper (≤1.3 s) + 3 servos × 400 ms = ~2.5 s  → margin 9.5 s
# PRESENT: stepper (≤1.3 s) + 1 servo  × 400 ms = ~1.7 s  → margin 6.3 s
# Normal:  solo respuesta de texto                          → margin ~4.5 s
TIMEOUT_NORMAL_S  = 5.0
TIMEOUT_HOME_S    = 12.0
TIMEOUT_PRESENT_S = 8.0

# Reintentos cuando el ESP32 responde BUSY (motor aún en movimiento)
MAX_REINTENTOS_BUSY = 6
PAUSA_BUSY_S        = 0.5   # espera entre reintentos

# Puerto UART por defecto en Raspberry Pi (mini-UART / PL011).
# En algunas Pi puede ser /dev/ttyAMA0. Si no existe,
# el driver pasa a modo mock automáticamente.
PUERTO_DEFAULT = "/dev/serial0"


# ─────────────────────────────────────────
# Resultado uniforme
# ─────────────────────────────────────────

@dataclass
class RespuestaESP:
    """Resultado de un comando enviado al ESP32."""
    ok: bool
    mensaje: str
    crudo: str = ""   # respuesta cruda recibida (útil para debug)

    @classmethod
    def desde_linea(cls, linea: str) -> "RespuestaESP":
        """Parsea una línea de respuesta del ESP32."""
        linea = linea.strip()
        if linea == "OK":
            return cls(True, "OK", linea)
        if linea == "BUSY":
            return cls(False, "ESP32 ocupado", linea)
        if linea.startswith("ERR"):
            return cls(False, f"ESP32 error: {linea[3:].strip()}", linea)
        if linea.startswith("STATUS"):
            return cls(True, linea, linea)
        return cls(False, f"Respuesta desconocida: '{linea}'", linea)


# ─────────────────────────────────────────
# Driver real (UART con pyserial)
# ─────────────────────────────────────────

class _DriverSerial:
    """Implementación con pyserial. Solo se instancia si pyserial existe
    y el puerto se puede abrir."""

    def __init__(self, puerto: str, baudrate: int):
        import serial  # type: ignore  (pyserial)
        self._ser = serial.Serial(
            port=puerto,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=TIMEOUT_NORMAL_S,
        )
        # Espera al boot del ESP32 después de abrir el puerto (suele
        # resetearse cuando llega la señal DTR del host).
        time.sleep(2.0)
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

    def enviar_y_leer(self, comando: str, timeout_s: float = TIMEOUT_NORMAL_S) -> str:
        """Envía comando y espera una línea de respuesta.

        El timeout se ajusta por comando (HOME necesita más tiempo que PING)
        y se restaura al valor normal al finalizar.
        """
        self._ser.timeout = timeout_s
        self._ser.write((comando + "\n").encode("utf-8"))
        self._ser.flush()
        linea = self._ser.readline().decode("utf-8", errors="replace")
        self._ser.timeout = TIMEOUT_NORMAL_S   # restaurar
        return linea

    def cerrar(self):
        try:
            self._ser.close()
        except Exception:
            pass


# ─────────────────────────────────────────
# Driver mock (Windows / sin hardware)
# ─────────────────────────────────────────

class _DriverMock:
    """Simula al ESP32 para desarrollo en Windows o sin hardware conectado.
    Imprime los comandos que 'enviaría' y devuelve respuestas plausibles."""

    def __init__(self):
        self.perchero_actual = 1
        self.servo_pos = [0, 0, 0]
        self.log: list[str] = []

    def enviar_y_leer(self, comando: str, timeout_s: float = TIMEOUT_NORMAL_S) -> str:
        self.log.append(comando)
        print(f"  [MOCK ESP32] ← {comando}")
        partes = comando.strip().split()
        if not partes:
            return "ERR comando vacío\n"
        cmd = partes[0]

        if cmd == "PING":
            return "OK\n"

        if cmd == "HOME":
            self.perchero_actual = 1
            self.servo_pos = [0, 0, 0]
            return "OK\n"

        if cmd == "MOVE_BASE":
            try:
                p = int(partes[1])
                if not 1 <= p <= 3:
                    return "ERR perchero fuera de rango\n"
                self.perchero_actual = p
                return "OK\n"
            except (IndexError, ValueError):
                return "ERR argumento inválido\n"

        if cmd == "ROTATE":
            try:
                p = int(partes[1]); s = int(partes[2])
                if not 1 <= p <= 3 or not 0 <= s <= 4:
                    return "ERR rango\n"
                self.servo_pos[p - 1] = s
                return "OK\n"
            except (IndexError, ValueError):
                return "ERR argumento inválido\n"

        if cmd == "PRESENT":
            try:
                p = int(partes[1]); s = int(partes[2])
                if not 1 <= p <= 3 or not 0 <= s <= 4:
                    return "ERR rango\n"
                self.perchero_actual = p
                self.servo_pos[p - 1] = s
                return "OK\n"
            except (IndexError, ValueError):
                return "ERR argumento inválido\n"

        if cmd == "STATUS":
            return (f"STATUS {self.perchero_actual} "
                    f"{self.servo_pos[0]} {self.servo_pos[1]} {self.servo_pos[2]}\n")

        return f"ERR comando desconocido: {cmd}\n"

    def cerrar(self):
        pass


# ─────────────────────────────────────────
# Driver de alto nivel (lo que usa el resto del sistema)
# ─────────────────────────────────────────

class ESP32Driver:
    """Punto de acceso único al ESP32.

    Si pyserial está instalado y el puerto se abre, usa UART real.
    Si no, cae a un mock que imprime los comandos en consola y
    responde plausiblemente. Esto permite desarrollar la lógica
    completa en Windows sin tener el ESP32 conectado.

    Mejoras sobre la versión anterior:
      - home()     usa timeout de 12 s (stepper + 3 servos)
      - presentar() usa timeout de 8 s  (stepper + 1 servo)
      - _comando() reintenta automáticamente si el ESP32 responde BUSY
    """

    def __init__(self, puerto: Optional[str] = None,
                 baudrate: int = BAUDRATE,
                 forzar_mock: bool = False):
        self._driver = None
        self._es_mock = False

        if forzar_mock:
            self._driver = _DriverMock()
            self._es_mock = True
            print("[ESP32Driver] Usando MOCK (forzado).")
            return

        try:
            import serial  # noqa: F401
        except ImportError:
            self._driver = _DriverMock()
            self._es_mock = True
            print("[ESP32Driver] pyserial no instalado → usando MOCK.")
            return

        try:
            self._driver = _DriverSerial(puerto or PUERTO_DEFAULT, baudrate)
            print(f"[ESP32Driver] UART real abierto en '{puerto or PUERTO_DEFAULT}'.")
        except Exception as e:
            self._driver = _DriverMock()
            self._es_mock = True
            print(f"[ESP32Driver] No se pudo abrir UART ({e}) → usando MOCK.")

    @property
    def es_mock(self) -> bool:
        return self._es_mock

    def cerrar(self):
        if self._driver is not None:
            self._driver.cerrar()

    # ─── primitiva de bajo nivel ──────────

    def _comando(self, texto: str, timeout_s: float = TIMEOUT_NORMAL_S) -> RespuestaESP:
        """Envía un comando UART y gestiona reintentos por BUSY.

        Si el ESP32 responde BUSY (motor en movimiento), espera PAUSA_BUSY_S
        segundos y reintenta, hasta MAX_REINTENTOS_BUSY veces.
        """
        if self._driver is None:
            return RespuestaESP(False, "Driver no inicializado")

        for intento in range(MAX_REINTENTOS_BUSY):
            try:
                crudo = self._driver.enviar_y_leer(texto, timeout_s)
            except Exception as e:
                return RespuestaESP(False, f"Error de comunicación: {e}")

            if not crudo:
                return RespuestaESP(False, "Sin respuesta (timeout)")

            resp = RespuestaESP.desde_linea(crudo)

            if resp.crudo != "BUSY":
                return resp   # OK, ERR o STATUS → devolver de inmediato

            # ESP32 está moviendo motores: esperar y reintentar
            print(f"  [ESP32] BUSY (intento {intento + 1}/{MAX_REINTENTOS_BUSY}), "
                  f"esperando {PAUSA_BUSY_S:.1f} s...")
            time.sleep(PAUSA_BUSY_S)

        return RespuestaESP(False,
                            f"ESP32 siguió BUSY tras {MAX_REINTENTOS_BUSY} reintentos")

    # ─── API pública ──────────────────────

    def ping(self) -> RespuestaESP:
        """Verifica que el ESP32 responde."""
        return self._comando("PING")

    def home(self) -> RespuestaESP:
        """Calibración: lleva el motor de pasos y los 3 servos al origen.

        Usa timeout extendido de 12 s porque el HOME mueve el stepper
        (hasta ~1.3 s) más los 3 servos (400 ms × 3 = 1.2 s) = ~2.5 s totales.
        """
        return self._comando("HOME", timeout_s=TIMEOUT_HOME_S)

    def mover_base(self, perchero_id: int) -> RespuestaESP:
        """Gira la base para presentar el perchero indicado."""
        if not 1 <= perchero_id <= 3:
            return RespuestaESP(False, f"perchero_id {perchero_id} fuera de rango")
        return self._comando(f"MOVE_BASE {perchero_id}")

    def rotar_servo(self, perchero_id: int, slot_idx: int) -> RespuestaESP:
        """Gira el servo del perchero indicado al slot deseado."""
        if not 1 <= perchero_id <= 3:
            return RespuestaESP(False, f"perchero_id {perchero_id} fuera de rango")
        if not 0 <= slot_idx <= 4:
            return RespuestaESP(False, f"slot_idx {slot_idx} fuera de rango")
        return self._comando(f"ROTATE {perchero_id} {slot_idx}")

    def presentar(self, perchero_id: int, slot_idx: int) -> RespuestaESP:
        """Atómico: mueve la base y rota el servo en una sola operación.

        Usa timeout extendido de 8 s porque PRESENT mueve el stepper
        (≤1.3 s) más 1 servo (400 ms) = ~1.7 s totales.
        """
        if not 1 <= perchero_id <= 3:
            return RespuestaESP(False, f"perchero_id {perchero_id} fuera de rango")
        if not 0 <= slot_idx <= 4:
            return RespuestaESP(False, f"slot_idx {slot_idx} fuera de rango")
        return self._comando(f"PRESENT {perchero_id} {slot_idx}",
                             timeout_s=TIMEOUT_PRESENT_S)

    def status(self) -> RespuestaESP:
        """Pide el estado actual al ESP32 (perchero presentado y pos. de servos)."""
        return self._comando("STATUS")

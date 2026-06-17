"""
comunicacion.py — Driver I2C entre Raspberry Pi (master) y ESP32 (slave).

Modelo de hardware:
  - Stepper 28BYJ-48 + ULN2003A: mueve el disco principal entre 3 posiciones
    (MiniDisco 1, 2, 3) de manera SECUENCIAL (nunca vuelta completa).
  - 3x servo SG90: cada servo cubre las 5 posiciones de su MiniDisco.

Modelo de estados (1–15):
  Estado  1– 5 → MiniDisco 1, servo en posición 0–4  (18°,54°,90°,126°,162°)
  Estado  6–10 → MiniDisco 2, servo en posición 0–4
  Estado 11–15 → MiniDisco 3, servo en posición 0–4

  Cada ropero usa solo media circunferencia: arco de -72° a +72°
  centrado en el reposo del servo (90°), repartido en 5 pasos de 36°.

  disco      = (estado - 1) // 5       →  0, 1 o 2
  pos_servo  = (estado - 1) %  5       →  0 … 4

Protocolo Pi → ESP32 (texto plano sobre I2C, terminado en '\\n'):
  PING              — verificar comunicación
  GOTO <n>          — presentar el estado n (1–15): mueve disco + servo
  RETIRAR           — fin de operación: servo activo a pos 0 (0°)
  HOME_STEPPER      — mueve SOLO el stepper al disco 0 (cierre de sesión)
  STATUS            — pedir estado actual

Protocolo ESP32 → Pi:
  OK
  ERR <razón>
  STATUS <estado> <disco+1> <pos_servo+1>

Bus I2C: /dev/i2c-1  (SDA=GPIO2 pin3, SCL=GPIO3 pin5 en la Pi)
Habilitar con: sudo raspi-config → Interface Options → I2C → Yes

Dependencia: pip install smbus2 --break-system-packages
"""

from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional


# ─────────────────────────────────────────
# Configuración I2C
# ─────────────────────────────────────────

ESP32_I2C_ADDR = 0x08   # debe coincidir con I2C_SLAVE_ADDR en el firmware
I2C_BUS        = 1      # /dev/i2c-1 en la Raspberry Pi
MAX_RESP_LEN   = 32     # bytes máximos a leer del slave por respuesta

# ─────────────────────────────────────────
# Pausas de espera tras escribir un comando
# ─────────────────────────────────────────
#
# En I2C la Pi escribe el comando y luego lee la respuesta en
# transacciones separadas. La pausa debe ser mayor que el tiempo
# que tarda el ESP32 en ejecutar la operación y llenar su buffer TX.
#
# Tiempos reales del hardware:
#   28BYJ-48 a 10 RPM → 1 paso ≈ 29 ms → 682 pasos (120°) ≈ 2 s por disco
#   SG90 → 0.1 s / 60° → 180° ≈ 0.3 s + delay(500) del firmware = ~0.8 s
#
# Peor caso GOTO:
#   disco 0 → disco 2 pasa por disco 1: 2 × 2 s (stepper) + 0.8 s (servo) ≈ 4.8 s
#   Con margen: 7 s
#
# Peor caso HOME:
#   Regresa hasta 2 posiciones de disco (4 s) + pone 3 servos a 0° (0.8 s × 3) ≈ 6.4 s
#   Con margen: 10 s

PAUSA_RAPIDA_S = 0.05    # PING, STATUS
PAUSA_GOTO_S   = 7.0     # GOTO: hasta 2 movimientos de disco + 1 servo
PAUSA_HOME_S   = 10.0    # HOME: hasta 2 movimientos de disco + 3 servos
PAUSA_RETIRAR_S = 7.0    # RETIRAR: 1 servo a centro + hasta 2 discos de vuelta

# Reintentos automáticos si el slave no devuelve respuesta útil
MAX_REINTENTOS = 4
PAUSA_REINTENTO_S = 0.3


# ─────────────────────────────────────────
# Tabla de estados (espejo del firmware)
# ─────────────────────────────────────────
#
# Se calcula dinámicamente con la misma fórmula del firmware:
#   disco     = (estado - 1) // 5
#   pos_servo = (estado - 1) %  5
#
# Se expone como función de utilidad para que el resto del sistema
# pueda usarla sin depender del ESP32.

ANGULOS_SERVO = [0, 45, 90, 135, 180]   # grados para pos 0–4 (de pruebamotores.ino)
ANGULO_INICIO = 0                        # posición de reposo del servo

def desglosar_estado(estado: int) -> tuple[int, int]:
    """Devuelve (disco, pos_servo) para un estado 1–15.

    disco:     0, 1 o 2  (MiniDisco 1, 2, 3)
    pos_servo: 0 … 4

    Ejemplo:
        desglosar_estado(7)  →  (1, 1)   # disco 2, servo a 45°
        desglosar_estado(15) →  (2, 4)   # disco 3, servo a 180°
    """
    if not 1 <= estado <= 15:
        raise ValueError(f"estado debe estar entre 1 y 15, se recibió {estado}")
    disco     = (estado - 1) // 5
    pos_servo = (estado - 1) %  5
    return disco, pos_servo


# ─────────────────────────────────────────
# Resultado uniforme
# ─────────────────────────────────────────

@dataclass
class RespuestaESP:
    """Resultado de un comando enviado al ESP32."""
    ok: bool
    mensaje: str
    crudo: str = ""

    @classmethod
    def desde_linea(cls, linea: str) -> "RespuestaESP":
        linea = linea.strip()
        if linea == "OK":
            return cls(True, "OK", linea)
        if linea.startswith("STATUS"):
            return cls(True, linea, linea)
        if linea.startswith("ERR"):
            return cls(False, f"ESP32 error: {linea[3:].strip()}", linea)
        return cls(False, f"Respuesta desconocida: '{linea}'", linea)


# ─────────────────────────────────────────
# Driver real (I2C con smbus2)
# ─────────────────────────────────────────

class _DriverI2C:
    """Comunicación I2C real usando smbus2 con i2c_msg (sin número de registro).

    El ESP32 slave no usa registros; es un dispositivo de stream puro.
    Por eso se usan i2c_msg.write / i2c_msg.read en lugar de
    write_i2c_block_data / read_i2c_block_data, que agregan un byte de
    registro al inicio y romperían el protocolo.
    """

    def __init__(self, bus: int, addr: int):
        from smbus2 import SMBus  # type: ignore
        self._bus  = SMBus(bus)
        self._addr = addr
        time.sleep(0.1)

    def _escribir(self, comando: str):
        from smbus2 import i2c_msg  # type: ignore
        data = (comando + "\n").encode("utf-8")
        msg  = i2c_msg.write(self._addr, list(data))
        self._bus.i2c_rdwr(msg)

    def _leer(self, n: int = MAX_RESP_LEN) -> str:
        from smbus2 import i2c_msg  # type: ignore
        msg = i2c_msg.read(self._addr, n)
        self._bus.i2c_rdwr(msg)
        raw = bytes(list(msg)).rstrip(b'\x00').decode("utf-8", errors="replace")
        return raw

    def enviar_y_leer(self, comando: str, pausa_s: float = PAUSA_RAPIDA_S) -> str:
        """Escribe el comando, espera pausa_s, lee la respuesta."""
        self._escribir(comando)
        time.sleep(pausa_s)
        raw = self._leer()
        # Extraer la primera línea completa
        if '\n' in raw:
            return raw.split('\n')[0] + '\n'
        return raw if raw else ""

    def cerrar(self):
        try:
            self._bus.close()
        except Exception:
            pass


# ─────────────────────────────────────────
# Driver mock (sin hardware)
# ─────────────────────────────────────────

class _DriverMock:
    """Simula el ESP32 para pruebas sin hardware.

    Reproduce exactamente la misma lógica de estados que el firmware:
      disco     = (estado - 1) // 5
      pos_servo = (estado - 1) %  5

    El stepper se mueve de forma secuencial (igual que moveToDisk del firmware),
    lo que hace que GOTO de disco 0 a disco 2 pase por disco 1.
    """

    def __init__(self):
        self.estado_actual   = 1
        self.disco_actual    = 0   # 0, 1, 2
        self.pos_servo_actual = 0  # 0 … 4
        self.log: list[str] = []

    def _mover_a_disco(self, disco_obj: int):
        """Simula el movimiento del stepper por el camino más corto.

        Los 3 discos están a 120° en un círculo, así que siempre se llega
        con un solo giro de 120° (o ninguno). disco 0 → disco 2 gira en
        sentido contrario en vez de pasar por el disco 1.
        """
        self.disco_actual = disco_obj

    def enviar_y_leer(self, comando: str, pausa_s: float = PAUSA_RAPIDA_S) -> str:
        self.log.append(comando)
        print(f"  [MOCK ESP32] ← {comando}")
        partes = comando.strip().split()
        if not partes:
            return "ERR comando vacío\n"
        cmd = partes[0]

        if cmd == "PING":
            return "OK\n"

        if cmd == "RETIRAR":
            # Sin movimiento; solo confirmación
            return "OK\n"

        if cmd == "HOME_STEPPER":
            # Servos a pos 0 y stepper al disco 0
            self.pos_servo_actual = 0
            self._mover_a_disco(0)
            self.estado_actual = 1
            return "OK\n"

        if cmd == "GOTO":
            try:
                estado = int(partes[1])
            except (IndexError, ValueError):
                return "ERR argumento inválido\n"
            if not 1 <= estado <= 15:
                return "ERR estado invalido (1-15)\n"
            disco_obj, pos_obj = desglosar_estado(estado)
            self._mover_a_disco(disco_obj)
            self.pos_servo_actual = pos_obj
            self.estado_actual    = estado
            return "OK\n"

        if cmd == "STATUS":
            return (f"STATUS {self.estado_actual} "
                    f"{self.disco_actual + 1} "
                    f"{self.pos_servo_actual + 1}\n")

        return f"ERR comando desconocido: {cmd}\n"

    def cerrar(self):
        pass


# ─────────────────────────────────────────
# Driver de alto nivel
# ─────────────────────────────────────────

class ESP32Driver:
    """Punto de acceso único al ESP32.

    Usa I2C real (smbus2) si está disponible; cae a mock si no.
    La API principal es ir_a_estado(n), que envía GOTO <n> al ESP32.

    Ejemplo de uso:
        esp = ESP32Driver()
        esp.ping()
        esp.ir_a_estado(7)    # MiniDisco 2, servo a 45°
        esp.status()
        esp.home()
        esp.cerrar()
    """

    def __init__(self,
                 bus: int  = I2C_BUS,
                 addr: int = ESP32_I2C_ADDR,
                 forzar_mock: bool = False):
        self._driver  = None
        self._es_mock = False

        if forzar_mock:
            self._driver  = _DriverMock()
            self._es_mock = True
            print("[ESP32Driver] Usando MOCK (forzado).")
            return

        try:
            import smbus2  # noqa: F401
        except ImportError:
            self._driver  = _DriverMock()
            self._es_mock = True
            print("[ESP32Driver] smbus2 no instalado → usando MOCK.")
            return

        try:
            self._driver = _DriverI2C(bus, addr)
            print(f"[ESP32Driver] I2C real  bus={bus}  addr=0x{addr:02X}.")
        except Exception as e:
            self._driver  = _DriverMock()
            self._es_mock = True
            print(f"[ESP32Driver] No se pudo abrir I2C ({e}) → usando MOCK.")

    @property
    def es_mock(self) -> bool:
        return self._es_mock

    def cerrar(self):
        if self._driver is not None:
            self._driver.cerrar()

    # ─── primitiva interna ────────────────

    def _comando(self, texto: str, pausa_s: float = PAUSA_RAPIDA_S) -> RespuestaESP:
        """Envía un comando y devuelve la respuesta parseada.

        Reintenta si el slave no responde (línea vacía).
        """
        if self._driver is None:
            return RespuestaESP(False, "Driver no inicializado")

        for intento in range(MAX_REINTENTOS):
            try:
                crudo = self._driver.enviar_y_leer(texto, pausa_s)
            except Exception as e:
                return RespuestaESP(False, f"Error I2C: {e}")

            if crudo.strip():
                return RespuestaESP.desde_linea(crudo)

            # Slave no respondió aún — esperar y reintentar
            if intento < MAX_REINTENTOS - 1:
                print(f"  [ESP32] Sin respuesta (intento {intento + 1}/"
                      f"{MAX_REINTENTOS}), reintentando...")
                time.sleep(PAUSA_REINTENTO_S)

        return RespuestaESP(False, "Sin respuesta tras varios intentos")

    # ─── API pública ──────────────────────

    def ping(self) -> RespuestaESP:
        """Verifica que el ESP32 responde."""
        return self._comando("PING", pausa_s=PAUSA_RAPIDA_S)

    def home_stepper(self) -> RespuestaESP:
        """Fin de sesión: todos los servos a 0° y stepper al disco 0.

        Peor caso: 3 servos × 0.6 s + 2 movimientos de disco × 2 s ≈ 5.8 s.
        Con margen: 10 s.
        """
        return self._comando("HOME_STEPPER", pausa_s=PAUSA_HOME_S)

    def retirar(self) -> RespuestaESP:
        """Confirmación de que el usuario retiró/colgó la prenda.

        El ESP32 NO mueve ningún motor; solo acusa recibo.
        Los motores quedan en su posición actual hasta el fin de sesión.
        Pausa mínima (solo round-trip I2C).
        """
        return self._comando("RETIRAR", pausa_s=PAUSA_RAPIDA_S)

    def ir_a_estado(self, estado: int) -> RespuestaESP:
        """Mueve el sistema al estado indicado (1–15).

        El ESP32 calcula internamente el disco y la posición del servo:
            disco     = (estado - 1) // 5
            pos_servo = (estado - 1) %  5

        El stepper se mueve de forma SECUENCIAL (nunca vuelta completa).
        Pausa de 7 s para cubrir el peor caso (2 movimientos de disco).

        Ejemplo:
            ir_a_estado(1)   →  MiniDisco 1, servo 0°
            ir_a_estado(6)   →  MiniDisco 2, servo 0°
            ir_a_estado(11)  →  MiniDisco 3, servo 0°
            ir_a_estado(15)  →  MiniDisco 3, servo 180°
        """
        if not 1 <= estado <= 15:
            return RespuestaESP(False, f"Estado {estado} fuera de rango (1–15)")
        return self._comando(f"GOTO {estado}", pausa_s=PAUSA_GOTO_S)

    def status(self) -> RespuestaESP:
        """Pide el estado actual al ESP32.

        La respuesta tiene el formato:
            STATUS <estado> <disco+1> <pos_servo+1>

        Ejemplo: "STATUS 7 2 2"  →  estado 7, MiniDisco 2, posición 2 (45°)
        """
        resp = self._comando("STATUS", pausa_s=PAUSA_RAPIDA_S)
        if resp.ok and resp.crudo.startswith("STATUS"):
            partes = resp.crudo.split()
            if len(partes) == 4:
                estado    = int(partes[1])
                disco     = int(partes[2])
                pos_servo = int(partes[3])
                angulo    = ANGULOS_SERVO[pos_servo - 1] if 1 <= pos_servo <= 5 else -1
                print(f"  [STATUS] Estado={estado}  "
                      f"MiniDisco={disco}  "
                      f"Servo pos={pos_servo} ({angulo}°)")
        return resp

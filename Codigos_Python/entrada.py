"""
entrada.py — Abstracción de los controles físicos.

Controles físicos en Raspberry Pi 5:
  · Encoder EC11  A=GPIO17, B=GPIO27  → Boton.SIGUIENTE / Boton.ANTERIOR
  · Botón SEL     GPIO22              → Boton.SI   (seleccionar / confirmar)
  · Botón BACK    GPIO4               → Boton.NO   (atrás / cancelar)
  · Botón ABORT   GPIO10              → Boton.ABORT (abortar al menú principal)

En Windows / desarrollo los mismos eventos se disparan con:
  · Flecha-izq  → Boton.ANTERIOR
  · Flecha-der  → Boton.SIGUIENTE
  · Enter       → Boton.SI
  · Delete      → Boton.NO   (atrás)
  · Space       → Boton.ABORT

Uso de lgpio (único backend compatible con el chip GPIO RP1 del Pi 5;
RPi.GPIO no soporta el Pi 5).
"""

from __future__ import annotations
import platform
from enum import Enum
from typing import Callable, Optional


# ─────────────────────────────────────────
# Detección de plataforma
# ─────────────────────────────────────────

def _es_raspberry_pi() -> bool:
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/device-tree/model", "r") as f:
            return "raspberry pi" in f.read().lower()
    except OSError:
        return False


ES_RASPBERRY = _es_raspberry_pi()


# ─────────────────────────────────────────
# Pinout (BCM)
# ─────────────────────────────────────────

PIN_ENC_A = 17   # Encoder EC11 fase A  (pin físico 11)
PIN_ENC_B = 27   # Encoder EC11 fase B  (pin físico 13)
PIN_SEL   = 22   # Botón SELECCIONAR    (pin físico 15)
PIN_BACK  = 4    # Botón ATRÁS          (pin físico 7)
PIN_ABORT = 10   # Botón ABORT          (pin físico 19)

DEBOUNCE_MS = 200


# ─────────────────────────────────────────
# Botones lógicos
# ─────────────────────────────────────────

class Boton(Enum):
    SI       = "si"        # SEL: seleccionar / confirmar
    NO       = "no"        # BACK: un paso atrás
    SIGUIENTE = "siguiente" # Encoder CW
    ANTERIOR  = "anterior"  # Encoder CCW
    ABORT    = "abort"     # ABORT: volver al menú principal desde cualquier lugar


# ─────────────────────────────────────────
# Clase principal
# ─────────────────────────────────────────

class Entrada:
    """Maneja encoder EC11 + 3 botones (físicos en Pi, teclado/click en PC).

    Uso:
        entrada = Entrada()
        entrada.on(Boton.SI,    seleccionar)
        entrada.on(Boton.ABORT, abortar)
        ...
        entrada.cleanup()
    """

    def __init__(self, tk_root=None):
        self._callbacks: dict[Boton, Callable[[], None]] = {}
        self._tk_root   = tk_root
        self._gpio      = None
        self._enc_last  = 0
        self._enc_raw   = 0

        if ES_RASPBERRY:
            self._init_gpio()
        if tk_root is not None:
            self._init_keyboard(tk_root)

    # ─── API pública ─────────────────────

    def on(self, boton: Boton, callback: Callable[[], None]) -> None:
        self._callbacks[boton] = callback

    def disparar(self, boton: Boton) -> None:
        cb = self._callbacks.get(boton)
        if cb is not None:
            if self._tk_root is not None:
                self._tk_root.after(0, cb)
            else:
                cb()

    def cleanup(self) -> None:
        if self._gpio is not None:
            try:
                self._gpio.gpiochip_close(self._chip)
            except Exception:
                pass

    # ─── inicialización GPIO ──────────────

    def _init_gpio(self) -> None:
        try:
            import lgpio  # type: ignore
        except ImportError:
            print("⚠ lgpio no disponible.")
            print("  pip install lgpio --break-system-packages")
            return

        chip = lgpio.gpiochip_open(0)
        self._chip = chip
        self._gpio = lgpio

        # Encoder EC11
        lgpio.gpio_claim_input(chip, PIN_ENC_A, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_input(chip, PIN_ENC_B, lgpio.SET_PULL_UP)

        a = lgpio.gpio_read(chip, PIN_ENC_A)
        b = lgpio.gpio_read(chip, PIN_ENC_B)
        self._enc_last = (a << 1) | b

        lgpio.gpio_claim_alert(chip, PIN_ENC_A, lgpio.BOTH_EDGES)
        lgpio.gpio_claim_alert(chip, PIN_ENC_B, lgpio.BOTH_EDGES)
        self._cb_enc_a = lgpio.callback(chip, PIN_ENC_A, lgpio.BOTH_EDGES,
                                        self._isr_encoder)
        self._cb_enc_b = lgpio.callback(chip, PIN_ENC_B, lgpio.BOTH_EDGES,
                                        self._isr_encoder)

        # Botones (flanco descendente = LOW al presionar)
        for pin, boton in [
            (PIN_SEL,   Boton.SI),
            (PIN_BACK,  Boton.NO),
            (PIN_ABORT, Boton.ABORT),
        ]:
            lgpio.gpio_claim_input(chip, pin, lgpio.SET_PULL_UP)
            lgpio.gpio_claim_alert(chip, pin, lgpio.FALLING_EDGE)
            lgpio.callback(chip, pin, lgpio.FALLING_EDGE,
                           lambda _c, _g, _l, _t, b=boton: self.disparar(b))

    _ENC_CW  = {0b1101, 0b0100, 0b0010, 0b1011}
    _ENC_CCW = {0b1110, 0b0111, 0b0001, 0b1000}

    def _isr_encoder(self, chip, gpio, level, tick):
        lgpio = self._gpio
        a = lgpio.gpio_read(self._chip, PIN_ENC_A)
        b = lgpio.gpio_read(self._chip, PIN_ENC_B)
        encoded = (a << 1) | b
        total   = (self._enc_last << 2) | encoded

        if total in self._ENC_CW:
            self._enc_raw += 1
        elif total in self._ENC_CCW:
            self._enc_raw -= 1

        self._enc_last = encoded

        steps = self._enc_raw // 4
        if steps != 0:
            self._enc_raw -= steps * 4
            if steps > 0:
                self.disparar(Boton.SIGUIENTE)
            else:
                self.disparar(Boton.ANTERIOR)

    def _init_keyboard(self, root) -> None:
        root.bind("<Left>",   lambda _e: self.disparar(Boton.ANTERIOR))
        root.bind("<Right>",  lambda _e: self.disparar(Boton.SIGUIENTE))
        root.bind("<Return>", lambda _e: self.disparar(Boton.SI))
        root.bind("<Delete>", lambda _e: self.disparar(Boton.NO))
        root.bind("<space>",  lambda _e: self.disparar(Boton.ABORT))

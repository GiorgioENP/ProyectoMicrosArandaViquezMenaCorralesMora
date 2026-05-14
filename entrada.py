"""
entrada.py — Abstracción de los 4 botones físicos.

En Raspberry Pi escucha los GPIO con interrupciones (cumple requerimiento
del PDF: 'El uso de interrupciones para el microcontrolador es de carácter
obligatorio'... el mismo principio aplica aquí del lado del microprocesador
para los botones de la interfaz).

En Windows / desarrollo, los mismos eventos se disparan desde:
  · Teclas físicas: Flecha-izq=Anterior, Flecha-der=Siguiente, Enter=Sí, Esc=No.
  · Botones clickeables que el módulo `interfaz` agrega abajo de la GUI.

Sea cual sea la fuente, todo termina llamando al mismo callback registrado
con `Entrada.on(boton, callback)`.
"""

from __future__ import annotations
import platform
from enum import Enum
from typing import Callable, Optional


# ─────────────────────────────────────────
# Detección de plataforma
# ─────────────────────────────────────────

def _es_raspberry_pi() -> bool:
    """Detecta si estamos corriendo en una Raspberry Pi."""
    if platform.system() != "Linux":
        return False
    # /proc/device-tree/model existe en RPi y dice "Raspberry Pi …"
    try:
        with open("/proc/device-tree/model", "r") as f:
            return "raspberry pi" in f.read().lower()
    except OSError:
        return False


ES_RASPBERRY = _es_raspberry_pi()


# ─────────────────────────────────────────
# Pinout (BCM)
# ─────────────────────────────────────────
# Estos números los puedes cambiar según tu cableado físico.
# Los elegí de pines GPIO seguros (ni I2C, ni SPI, ni UART)
# para que el bus de comunicación con el ESP32 quede libre.

PIN_SI         = 17   # Pin físico 11
PIN_NO         = 27   # Pin físico 13
PIN_SIGUIENTE  = 22   # Pin físico 15
PIN_ANTERIOR   = 23   # Pin físico 16

DEBOUNCE_MS = 200     # antirrebote


# ─────────────────────────────────────────
# Botones lógicos
# ─────────────────────────────────────────

class Boton(Enum):
    SI        = "si"
    NO        = "no"
    SIGUIENTE = "siguiente"
    ANTERIOR  = "anterior"


# ─────────────────────────────────────────
# Clase principal
# ─────────────────────────────────────────

class Entrada:
    """Maneja los 4 botones (físicos en Pi, simulados en Windows).

    Uso:
        entrada = Entrada()
        entrada.on(Boton.SI, mi_funcion)
        ...
        entrada.cleanup()  # al cerrar la aplicación
    """

    def __init__(self, tk_root=None):
        """tk_root: la ventana de Tkinter; si se pasa, se enlazan
        las teclas (flechas, Enter, Esc) como atajos."""
        self._callbacks: dict[Boton, Callable[[], None]] = {}
        self._tk_root = tk_root
        self._gpio = None  # módulo RPi.GPIO si está disponible

        if ES_RASPBERRY:
            self._init_gpio()
        if tk_root is not None:
            self._init_keyboard(tk_root)

    # ─── API pública ─────────────────────

    def on(self, boton: Boton, callback: Callable[[], None]) -> None:
        """Registra el callback que se ejecuta al presionar `boton`.

        Sobrescribe el callback anterior si lo había. La interfaz
        usa esto para reconfigurar los botones cuando cambia de pantalla.
        """
        self._callbacks[boton] = callback

    def disparar(self, boton: Boton) -> None:
        """Llama manualmente al callback de un botón. Lo usan los
        botones clickeables de la GUI en Windows."""
        cb = self._callbacks.get(boton)
        if cb is not None:
            # Si estamos dentro de una interrupción GPIO, hay que volver
            # al hilo principal de Tkinter. Si no hay tk_root, llamamos directo.
            if self._tk_root is not None:
                self._tk_root.after(0, cb)
            else:
                cb()

    def cleanup(self) -> None:
        """Libera los GPIO. Llamar al cerrar la aplicación."""
        if self._gpio is not None:
            self._gpio.cleanup()

    # ─── inicialización plataforma-específica ──

    def _init_gpio(self) -> None:
        """Configura los GPIO de la Pi con interrupciones (FALLING edge)."""
        try:
            import RPi.GPIO as GPIO  # type: ignore
        except ImportError:
            print("⚠ RPi.GPIO no disponible, GPIO deshabilitado.")
            return

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        for pin, boton in [
            (PIN_SI,         Boton.SI),
            (PIN_NO,         Boton.NO),
            (PIN_SIGUIENTE,  Boton.SIGUIENTE),
            (PIN_ANTERIOR,   Boton.ANTERIOR),
        ]:
            # Pull-up interno: el botón conecta a GND al presionarse,
            # por eso detectamos flanco descendente (FALLING).
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                pin, GPIO.FALLING,
                callback=lambda _ch, b=boton: self.disparar(b),
                bouncetime=DEBOUNCE_MS,
            )
        self._gpio = GPIO

    def _init_keyboard(self, root) -> None:
        """Mapea teclas a botones para desarrollo en Windows."""
        root.bind("<Left>",   lambda _e: self.disparar(Boton.ANTERIOR))
        root.bind("<Right>",  lambda _e: self.disparar(Boton.SIGUIENTE))
        root.bind("<Return>", lambda _e: self.disparar(Boton.SI))
        root.bind("<Escape>", lambda _e: self.disparar(Boton.NO))

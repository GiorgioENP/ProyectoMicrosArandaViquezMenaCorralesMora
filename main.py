"""
main.py — Punto de entrada de la aplicación de Percheros Inteligentes.

Arranca:
  1. Configura DISPLAY si corre en Raspberry Pi sin variable de entorno.
  2. La base de datos (Sistema) y carga estado previo si existe.
  3. El driver I2C al ESP32 (mock automático si no hay hardware).
  4. La interfaz Tkinter con los 4 botones (GPIO en Pi, teclado/click
     simulado en Windows).
  5. El mainloop hasta que el usuario salga.

Uso:
    python3 main.py        # I2C real si está disponible; cae a mock si no.
    python3 main.py --mock # fuerza el modo mock (no toca el bus I2C).

Raspberry Pi + HDMI:
    La GUI se pone automáticamente en fullscreen en el monitor HDMI conectado.
    Si se ejecuta desde SSH (sin sesión gráfica), se necesita:
        export DISPLAY=:0 && python3 main.py
    Este script intenta setear DISPLAY=:0 automáticamente si no está definido.
"""

import os
import sys

# ── Configurar DISPLAY para Raspberry Pi ──────────────────────────────────────
# Si se lanza desde SSH o desde un servicio sin sesión gráfica activa,
# Tkinter no sabe a qué pantalla conectarse. Seteamos :0 (la pantalla HDMI
# principal del Pi) como valor por defecto si la variable no está definida.
if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":0"
# ──────────────────────────────────────────────────────────────────────────────

from Ropero import Sistema, ARCHIVO_ESTADO
from comunicacion import ESP32Driver
from interfaz import Interfaz


def parsear_args(argv: list[str]):
    forzar_mock = "--mock" in argv
    return forzar_mock


def main():
    forzar_mock = parsear_args(sys.argv[1:])

    print("═" * 55)
    print("  Sistema de Percheros Inteligentes — Arranque")
    print("═" * 55)

    # 1) Base de datos
    sistema = Sistema()
    if os.path.exists(ARCHIVO_ESTADO):
        r = sistema.cargar_estado(ARCHIVO_ESTADO)
        print(("✓ " if r.ok else "✗ ") + r.mensaje)
    else:
        print(f"ℹ  Sin estado previo ('{ARCHIVO_ESTADO}' no existe). Comenzando vacío.")

    # 2) Driver al ESP32 (I2C real si está, mock si no)
    driver = ESP32Driver(forzar_mock=forzar_mock)

    rping = driver.ping()
    print(("✓ " if rping.ok else "✗ ") + f"PING ESP32: {rping.mensaje}")

    print("─" * 55)
    print("  Iniciando interfaz gráfica...")
    if driver.es_mock:
        print("  ⚠  Modo MOCK activo — los motores no se moverán.")
    print("═" * 55)

    # 3) Interfaz gráfica
    app = Interfaz(sistema, driver=driver)
    try:
        app.correr()
    except KeyboardInterrupt:
        pass
    finally:
        # guardar estado; el HOME_STEPPER lo envía interfaz._cerrar()
        r = sistema.guardar_estado(ARCHIVO_ESTADO)
        print(("✓ " if r.ok else "✗ ") + r.mensaje)
        driver.cerrar()
        print("  Sistema cerrado.")


if __name__ == "__main__":
    main()

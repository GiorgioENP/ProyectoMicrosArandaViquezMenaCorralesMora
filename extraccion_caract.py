#!/usr/bin/env python3
"""
extraccion_caract.py — Mecanismo DISTINTO a la interfaz gráfica para
extraer prendas por características (requerimiento PDF §III).

═══════════════════════════════════════════════════════════════════════
  ESTE SCRIPT ES EL "MECANISMO DISTINTO A LA INTERFAZ" QUE EXIGE EL PDF.
  No usa Tkinter. No usa los 4 botones físicos. Corre en una terminal
  independiente o por sesión SSH desde cualquier computadora en red.
═══════════════════════════════════════════════════════════════════════

Uso:
    python3 extraccion_caract.py
    python3 extraccion_caract.py --puerto /dev/serial0
    python3 extraccion_caract.py --mock

Flujo:
    1. Carga el estado actual desde estado.txt.
    2. Presenta un menú numerado por texto para seleccionar características.
    3. Busca la prenda que coincida con los criterios dados.
    4. Conecta al ESP32 por UART y mueve los motores para presentarla.
    5. Actualiza el estado en estado.txt.

Nota de uso simultáneo:
    Dado que ambas interfaces (GUI y CLI) comparten el puerto UART, no deben
    ejecutarse al mismo tiempo enviando comandos. Para la demo, usa una mientras
    la otra está en reposo (p.ej. GUI en el monitor HDMI, CLI por SSH).
"""

import os
import sys

# Asegurar que los módulos del proyecto se encuentren aunque se llame
# desde otro directorio
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from Ropero import (
    Sistema, ARCHIVO_ESTADO,
    TIPOS_PRENDA, COLORES, TIPOS_TELA, TALLAS, FITS,
)
from comunicacion import ESP32Driver


# ─────────────────────────────────────────
# Helpers de consola
# ─────────────────────────────────────────

SEP      = "─" * 52
SEP_ANCHO = "═" * 52


def _titulo(texto: str):
    print(f"\n{SEP_ANCHO}")
    print(f"  {texto}")
    print(SEP_ANCHO)


def _seccion(texto: str):
    print(f"\n  {texto}")
    print(f"  {SEP}")


def seleccionar_opcion(titulo: str, opciones: list,
                       obligatorio: bool = True) -> object:
    """Menú numerado en terminal. Devuelve la opción elegida o None.

    Si obligatorio=False, ofrece la opción 0 = Omitir (None).
    Maneja entrada inválida pidiendo reingreso hasta que sea válida.
    """
    _seccion(titulo)
    if not obligatorio:
        print("    0. (Omitir — acepta cualquier valor)")
    for i, op in enumerate(opciones, start=1):
        print(f"    {i}. {op}")
    print()

    while True:
        try:
            entrada = input("  → Opción: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

        if not entrada:
            continue

        if entrada == "0" and not obligatorio:
            return None

        try:
            n = int(entrada)
            if 1 <= n <= len(opciones):
                return opciones[n - 1]
        except ValueError:
            pass

        rango = f"0-{len(opciones)}" if not obligatorio else f"1-{len(opciones)}"
        print(f"  ✗ Opción inválida. Ingresa un número entre {rango}.")


# ─────────────────────────────────────────
# Lógica principal
# ─────────────────────────────────────────

def parsear_args(argv: list) -> tuple:
    forzar_mock = "--mock" in argv
    puerto = None
    if "--puerto" in argv:
        try:
            puerto = argv[argv.index("--puerto") + 1]
        except IndexError:
            print("ERROR: --puerto requiere un valor.")
            sys.exit(1)
    return puerto, forzar_mock


def main():
    puerto, forzar_mock = parsear_args(sys.argv[1:])

    _titulo("EXTRACCIÓN POR CARACTERÍSTICAS")
    print("  Sistema de Percheros Inteligentes — MT-7003")
    print("  Mecanismo distinto a la interfaz gráfica (PDF §III)")
    print(f"  Terminal: {os.ttyname(sys.stdin.fileno()) if sys.stdin.isatty() else 'no-tty'}")

    # ── 1. Cargar estado ─────────────────────────────────────────────────────
    print()
    sistema = Sistema()

    if not os.path.exists(ARCHIVO_ESTADO):
        print(f"✗ No se encontró el archivo de estado: '{ARCHIVO_ESTADO}'")
        print("  Asegúrate de haber guardado el estado desde la GUI primero.")
        sys.exit(1)

    r = sistema.cargar_estado(ARCHIVO_ESTADO)
    if not r.ok:
        print(f"✗ Error cargando estado: {r.mensaje}")
        sys.exit(1)

    total = len(sistema.prendas())
    en_perchero = sum(
        1 for p in sistema.prendas()
        if sistema._perchero_que_contiene(p.id) is not None
    )
    print(f"✓ Estado cargado — {total} prendas conocidas, {en_perchero} en percheros.")

    # ── 2. Conectar ESP32 ────────────────────────────────────────────────────
    driver = ESP32Driver(puerto=puerto, forzar_mock=forzar_mock)
    if driver.es_mock:
        print("⚠  Usando modo MOCK — los motores no se moverán físicamente.")
    else:
        rp = driver.ping()
        print(("✓ " if rp.ok else "✗ ") + f"ESP32 PING: {rp.mensaje}")

    # ── 3. Bucle de extracción ────────────────────────────────────────────────
    try:
        while True:
            _titulo("NUEVA SOLICITUD DE EXTRACCIÓN")
            print("  Ingresa las características de la prenda que deseas retirar.")
            print("  El TIPO es obligatorio. Los demás atributos son opcionales (0 = omitir).")

            # ── Tipo (obligatorio) ────────────────────────────────────────
            tipo = seleccionar_opcion(
                "Tipo de prenda  [OBLIGATORIO]",
                list(TIPOS_PRENDA),
                obligatorio=True,
            )
            if tipo is None:
                print("\n  Solicitud cancelada.")
                break

            # ── Atributos opcionales ──────────────────────────────────────
            color = seleccionar_opcion("Color           [opcional]",
                                       list(COLORES), obligatorio=False)
            tela  = seleccionar_opcion("Tipo de tela    [opcional]",
                                       list(TIPOS_TELA), obligatorio=False)
            talla = seleccionar_opcion("Talla           [opcional]",
                                       list(TALLAS), obligatorio=False)
            fit   = seleccionar_opcion("Fit             [opcional]",
                                       list(FITS), obligatorio=False)

            # ── Mostrar resumen de la búsqueda ────────────────────────────
            criterios = [tipo]
            if color: criterios.append(color)
            if tela:  criterios.append(tela)
            if talla: criterios.append(talla)
            if fit:   criterios.append(fit)
            print(f"\n  Buscando: {' · '.join(criterios)} ...")

            # ── Buscar en la base de datos ────────────────────────────────
            r = sistema.extraer_por_caracteristicas(tipo, color, tela, talla, fit)

            if not r.ok:
                print(f"\n  ✗ {r.mensaje}")
            else:
                per    = r.datos["perchero"]
                slot   = r.datos["slot"]
                nombre = r.datos["prenda"].nombre

                print(f"\n  ✓ Prenda encontrada: '{nombre}'")
                print(f"    Ubicación: perchero {per}, slot {slot}")
                print(f"    Moviendo motores...")

                rmotor = driver.presentar(per, slot)
                if rmotor.ok:
                    print(f"  ✓ Motor en posición. Retira la prenda '{nombre}'.")
                else:
                    print(f"  ⚠ Error de motor: {rmotor.mensaje}")
                    print("    La prenda fue extraída lógicamente.")
                    print("    Ajusta la posición del perchero manualmente si es necesario.")

                # Guardar estado actualizado para que la GUI lo refleje
                sistema.guardar_estado(ARCHIVO_ESTADO)
                print(f"  ✓ Estado actualizado en '{os.path.basename(ARCHIVO_ESTADO)}'.")

            # ── Continuar o salir ─────────────────────────────────────────
            print()
            try:
                continuar = input("  ¿Extraer otra prenda? [s/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                continuar = "n"

            if continuar not in ("s", "si", "sí", "y", "yes"):
                break

    except KeyboardInterrupt:
        print("\n\n  Interrupción por teclado.")
    finally:
        driver.cerrar()

    print(f"\n{SEP_ANCHO}")
    print("  Sesión de extracción finalizada.")
    print(SEP_ANCHO)


if __name__ == "__main__":
    main()

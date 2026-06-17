"""
extraccion_caract.py — Mecanismo DISTINTO a la interfaz gráfica para
extraer prendas por características (requerimiento PDF §III).

═══════════════════════════════════════════════════════════════════════
  En Raspberry Pi: usa la pantalla OLED + encoder EC11 + 3 botones.
  En PC (--mock):  usa menú de texto en terminal (fallback automático).
═══════════════════════════════════════════════════════════════════════

Flujo en modo OLED (Raspberry Pi):
  1. Carga estado desde estado.txt.
  2. Conecta al ESP32 por I2C.
  3. Activa el menú OLED (oled_caract.py) controlado por:
       Encoder CW/CCW → Siguiente / Anterior
       SEL  (GPIO22)  → Seleccionar ítem
       BACK (GPIO23)  → Atrás / cancelar
       CONF (GPIO24)  → Confirmar extracción
  4. Al confirmar: mueve ESP32, extrae la prenda de la BD, guarda estado.
  5. Pregunta si se quiere otra extracción.

Flujo en modo terminal (--mock o sin OLED):
  Menú de texto numerado (comportamiento original).

Uso:
    python3 extraccion_caract.py           # OLED en Raspberry Pi
    python3 extraccion_caract.py --mock    # terminal (sin hardware)
    python3 extraccion_caract.py --cli     # forzar terminal aunque haya OLED
"""

import os
import sys
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from Ropero import (
    Sistema, ARCHIVO_ESTADO,
    TIPOS_PRENDA, COLORES, TIPOS_TELA, TALLAS, FITS,
)
from comunicacion import ESP32Driver
from entrada import Entrada, Boton, ES_RASPBERRY


# ─────────────────────────────────────────
# Helpers de consola (modo CLI)
# ─────────────────────────────────────────

SEP       = "─" * 52
SEP_ANCHO = "═" * 52


def _titulo(texto: str):
    print(f"\n{SEP_ANCHO}\n  {texto}\n{SEP_ANCHO}")


def _seccion(texto: str):
    print(f"\n  {texto}\n  {SEP}")


def seleccionar_opcion(titulo: str, opciones: list,
                       obligatorio: bool = True) -> object:
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
# Modo OLED (Raspberry Pi)
# ─────────────────────────────────────────

def _modo_oled(sistema: Sistema, driver: ESP32Driver):
    """Lanza el menú OLED controlado por encoder + botones."""
    import threading
    from oled_caract import (
        OledMenuState, get_callbacks, _display_loop, _Fase,
        I2C_PORT, I2C_ADDRESS, OLED_WIDTH, OLED_HEIGHT,
    )

    continuar = True

    while continuar:
        _titulo("EXTRACCIÓN POR CARACTERÍSTICAS — OLED")
        print("  Usa el encoder y los botones en la pantalla OLED.")
        print("  BACK en el menú principal cancela la sesión.")

        # ── Inicializar OLED ──────────────────────────────────────────────
        try:
            from luma.core.interface.serial import i2c as luma_i2c
            from luma.oled.device import ssd1306
            from PIL import ImageFont
            serial = luma_i2c(port=I2C_PORT, address=I2C_ADDRESS)
            device = ssd1306(serial, width=OLED_WIDTH, height=OLED_HEIGHT)
            try:
                font_sm = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 10)
                font_hd = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 10)
            except IOError:
                font_sm = ImageFont.load_default()
                font_hd = font_sm
            oled_ok = True
        except Exception as e:
            print(f"⚠ OLED no disponible: {e}")
            oled_ok = False

        # ── Estado del menú ───────────────────────────────────────────────
        extraccion_result = {}   # comunicación entre callbacks e hilo principal

        def on_extraccion(prenda, perchero_id, slot_idx):
            extraccion_result["prenda"]     = prenda
            extraccion_result["perchero"]   = perchero_id
            extraccion_result["slot"]       = slot_idx

        state = OledMenuState(
            sistema,
            opciones_tipo   = TIPOS_PRENDA,
            opciones_color  = COLORES,
            opciones_tela   = TIPOS_TELA,
            opciones_talla  = TALLAS,
            opciones_fit    = FITS,
        )
        state.on_extraccion = on_extraccion

        # ── Entrada física (GPIO) ─────────────────────────────────────────
        entrada = Entrada()   # sin tk_root → usa lgpio directo
        cbs = get_callbacks(state)
        entrada.on(Boton.SIGUIENTE, cbs["siguiente"])
        entrada.on(Boton.ANTERIOR,  cbs["anterior"])
        entrada.on(Boton.SI,        cbs["si"])
        entrada.on(Boton.NO,        cbs["no"])
        entrada.on(Boton.CONF,      cbs["conf"])

        # ── Hilo de renderizado ───────────────────────────────────────────
        if oled_ok:
            t = threading.Thread(
                target=_display_loop,
                args=(device, state, font_sm, font_hd, None),
                daemon=True,
            )
            t.start()

        # ── Esperar fin de menú ───────────────────────────────────────────
        try:
            while state.running:
                time.sleep(0.05)
        except KeyboardInterrupt:
            state.running = False

        entrada.cleanup()

        # Cerrar OLED
        if oled_ok:
            try:
                from luma.core.render import canvas as luma_canvas
                with luma_canvas(device) as draw:
                    draw.rectangle((0, 0, 127, 63), fill="black")
                device.hide()
            except Exception:
                pass

        # ── Procesar resultado ────────────────────────────────────────────
        if state.fase == _Fase.DONE and extraccion_result:
            prenda     = extraccion_result["prenda"]
            perchero   = extraccion_result["perchero"]
            slot       = extraccion_result["slot"]
            estado_esp = (perchero - 1) * 5 + slot + 1

            print(f"\n  ✓ Prenda seleccionada: '{prenda.nombre}'")
            print(f"    Ubicación: perchero {perchero}, slot {slot} → estado {estado_esp}")
            print(f"    Moviendo motor...")

            rmotor = driver.ir_a_estado(estado_esp)
            if rmotor.ok:
                print(f"  ✓ Motor en posición. Retira la prenda '{prenda.nombre}'.")
            else:
                print(f"  ⚠ Error de motor: {rmotor.mensaje}")

            # Extraer de la BD y guardar estado
            r = sistema.extraer_por_nombre(prenda.nombre)
            if r.ok:
                sistema.guardar_estado(ARCHIVO_ESTADO)
                print(f"  ✓ Estado actualizado en '{os.path.basename(ARCHIVO_ESTADO)}'.")
            else:
                print(f"  ⚠ Error al extraer de BD: {r.mensaje}")

            # RETIRAR (no HOME): volver al perchero 1 sin homing completo
            print("  Esperando confirmación del usuario...")
            input("  Presiona Enter cuando hayas retirado la prenda: ")
            driver.retirar()
            print("  ✓ Motor en reposo.")

        elif state.fase == _Fase.CANCEL:
            print("\n  Sesión de extracción cancelada.")

        # ── ¿Otra extracción? ─────────────────────────────────────────────
        try:
            resp = input("\n  ¿Extraer otra prenda? [s/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            resp = "n"
        continuar = resp in ("s", "si", "sí", "y", "yes")


# ─────────────────────────────────────────
# Modo terminal (fallback CLI)
# ─────────────────────────────────────────

def _modo_cli(sistema: Sistema, driver: ESP32Driver):
    """Menú de texto numerado (modo original, sin OLED)."""
    try:
        while True:
            _titulo("NUEVA SOLICITUD DE EXTRACCIÓN")
            print("  Tipo es obligatorio. Demás atributos son opcionales (0 = omitir).")

            tipo = seleccionar_opcion("Tipo de prenda  [OBLIGATORIO]",
                                      list(TIPOS_PRENDA), obligatorio=True)
            if tipo is None:
                print("\n  Solicitud cancelada.")
                break

            color = seleccionar_opcion("Color           [opcional]",
                                       list(COLORES), obligatorio=False)
            tela  = seleccionar_opcion("Tipo de tela    [opcional]",
                                       list(TIPOS_TELA), obligatorio=False)
            talla = seleccionar_opcion("Talla           [opcional]",
                                       list(TALLAS), obligatorio=False)
            fit   = seleccionar_opcion("Fit             [opcional]",
                                       list(FITS), obligatorio=False)

            # Buscar todas las coincidencias en perchero
            candidatas = []
            for prenda in sistema.prendas():
                per = sistema._perchero_que_contiene(prenda.id)
                if per is None:
                    continue
                if prenda.coincide(tipo, color, tela, talla, fit):
                    candidatas.append((prenda, per))

            if not candidatas:
                print("\n  ✗ No hay ninguna prenda disponible que coincida.")
            else:
                if len(candidatas) == 1:
                    prenda, per_obj = candidatas[0]
                    print(f"\n  Una prenda coincide: '{prenda.nombre}' — seleccionada automáticamente.")
                else:
                    print(f"\n  Se encontraron {len(candidatas)} prendas:")
                    for i, (p, _) in enumerate(candidatas, 1):
                        print(f"    {i}. {p.nombre}  ({p.tipo}, {p.color}, {p.talla})")
                    while True:
                        try:
                            n = int(input("\n  → Elige prenda (número): "))
                            if 1 <= n <= len(candidatas):
                                break
                        except (ValueError, EOFError):
                            pass
                        print(f"  ✗ Ingresa un número entre 1 y {len(candidatas)}.")
                    prenda, per_obj = candidatas[n - 1]

                slot       = per_obj.slot_de(prenda.id)
                estado_esp = (per_obj.id - 1) * 5 + slot + 1

                print(f"\n  ✓ Prenda: '{prenda.nombre}' — perchero {per_obj.id}, slot {slot} → estado {estado_esp}")
                print(f"    Moviendo motor...")

                rmotor = driver.ir_a_estado(estado_esp)
                if rmotor.ok:
                    print(f"  ✓ Motor en posición.")
                else:
                    print(f"  ⚠ Error de motor: {rmotor.mensaje}")

                r = sistema.extraer_por_nombre(prenda.nombre)
                if r.ok:
                    sistema.guardar_estado(ARCHIVO_ESTADO)
                    print(f"  ✓ Estado actualizado.")

                input("  Presiona Enter cuando hayas retirado la prenda: ")
                driver.retirar()
                print("  ✓ Motor en reposo.")

            try:
                cont = input("\n  ¿Extraer otra prenda? [s/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                cont = "n"
            if cont not in ("s", "si", "sí", "y", "yes"):
                break

    except KeyboardInterrupt:
        print("\n\n  Interrupción por teclado.")


# ─────────────────────────────────────────
# Main
# ─────────────────────────────────────────

def parsear_args(argv: list) -> tuple[bool, bool]:
    forzar_mock = "--mock" in argv
    forzar_cli  = "--cli"  in argv
    return forzar_mock, forzar_cli


def main():
    forzar_mock, forzar_cli = parsear_args(sys.argv[1:])

    _titulo("EXTRACCIÓN POR CARACTERÍSTICAS")
    print("  Sistema de Percheros Inteligentes — MT-7003")

    # ── Cargar estado ─────────────────────────────────────────────────────
    sistema = Sistema()
    if not os.path.exists(ARCHIVO_ESTADO):
        print(f"✗ No se encontró el archivo de estado: '{ARCHIVO_ESTADO}'")
        sys.exit(1)
    r = sistema.cargar_estado(ARCHIVO_ESTADO)
    if not r.ok:
        print(f"✗ Error cargando estado: {r.mensaje}")
        sys.exit(1)
    total     = len(sistema.prendas())
    en_per    = sum(1 for p in sistema.prendas()
                    if sistema._perchero_que_contiene(p.id) is not None)
    print(f"✓ Estado cargado — {total} prendas, {en_per} en percheros.")

    # ── Conectar ESP32 ────────────────────────────────────────────────────
    driver = ESP32Driver(forzar_mock=forzar_mock)
    if driver.es_mock:
        print("⚠  Usando modo MOCK — los motores no se moverán físicamente.")
    else:
        rp = driver.ping()
        print(("✓ " if rp.ok else "✗ ") + f"ESP32 PING: {rp.mensaje}")

    # ── Elegir modo ───────────────────────────────────────────────────────
    usar_oled = ES_RASPBERRY and not forzar_cli and not forzar_mock
    print(f"  Modo: {'OLED + encoder' if usar_oled else 'terminal CLI'}")

    try:
        if usar_oled:
            _modo_oled(sistema, driver)
        else:
            _modo_cli(sistema, driver)
    finally:
        driver.retirar()   # RETIRAR al salir (no HOME; HOME es solo al cerrar la GUI)
        driver.cerrar()

    print(f"\n{SEP_ANCHO}\n  Sesión de extracción finalizada.\n{SEP_ANCHO}")


if __name__ == "__main__":
    main()

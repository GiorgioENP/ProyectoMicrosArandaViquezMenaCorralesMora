"""
interfaz.py — GUI del Sistema de Percheros Inteligentes (Tkinter).

Controles:
  · Encoder CW/CCW → Siguiente / Anterior
  · SEL  (GPIO22)  → Sí / seleccionar / confirmar
  · BACK (GPIO4)   → Un paso atrás / cancelar
  · ABORT (GPIO10) → Abortar y volver al menú principal (no activo durante movimiento)

Cambios v3:
  - CONF renombrado a ABORT. ABORT ya no duplica SI; vuelve al menú principal
    desde cualquier punto salvo durante movimiento de motor.
    Excepción: en la fase de nombre de prenda nueva, ABORT = "Completado".
  - _info_filtrado: corregido bucle infinito al omitir todos los filtros.
    El Back desde el resultado final vuelve al menú principal.
  - _flujo_extraer_caract (OLED): misma corrección propagada.
  - Información → "Prendas conocidas" con tabla completa y columna
    de ubicación (P1/P2/P3 o NO).
  - _cerrar: envía HOME_STEPPER (solo mueve stepper a disco 0).
  - Tecla Space = ABORT, Delete = NO/BACK, Enter = SI.
"""

from __future__ import annotations
import threading
import tkinter as tk
from typing import Callable, Optional, Any

from Ropero import (
    Sistema, Resultado,
    TIPOS_PRENDA, COLORES, TIPOS_TELA, TALLAS, FITS,
    NUM_PERCHEROS, SLOTS_POR_PERCHERO, ARCHIVO_ESTADO,
)
from entrada import Entrada, Boton, ES_RASPBERRY
from comunicacion import ESP32Driver
import oled_caract as oled


# ─────────────────────────────────────────
# Estilo
# ─────────────────────────────────────────

COLOR_FONDO        = "#1e1e2e"
COLOR_PANEL        = "#2a2a3e"
COLOR_TEXTO        = "#e0e0e0"
COLOR_RESALTADO    = "#7c5cff"
COLOR_OK           = "#4caf50"
COLOR_ERROR        = "#f44336"
COLOR_BOTON        = "#3a3a52"
COLOR_BOTON_ACTIVO = "#5050a0"
COLOR_ABORT        = "#8b3a3a"

ANCHO  = 800
ALTO   = 480
ESCALA = 1.0


def _fs(nombre: str, tam: int, *estilo) -> tuple:
    return (nombre, max(9, int(tam * ESCALA))) + estilo


# ─────────────────────────────────────────
# Clase base Pantalla
# ─────────────────────────────────────────

class Pantalla:
    titulo: str = ""

    def dibujar(self, panel: tk.Frame) -> None:
        raise NotImplementedError

    def siguiente(self): pass
    def anterior(self):  pass
    def si(self):        pass
    def no(self):        pass
    # abort lo gestiona Interfaz directamente; las pantallas no lo definen


# ─────────────────────────────────────────
# Selector de lista
# ─────────────────────────────────────────

class Selector(Pantalla):
    def __init__(self, titulo: str, opciones: list[Any],
                 on_seleccion: Callable[[Any], None],
                 on_cancelar: Callable[[], None],
                 etiqueta: Callable[[Any], str] = str,
                 vacio_msg: str = "(no hay opciones)"):
        self.titulo    = titulo
        self._opciones = list(opciones)
        self._on_sel   = on_seleccion
        self._on_cancel = on_cancelar
        self._etiqueta = etiqueta
        self._vacio_msg = vacio_msg
        self._cursor   = 0
        self._items_widgets: list[tk.Label] = []

    def dibujar(self, panel):
        if not self._opciones:
            tk.Label(panel, text=self._vacio_msg,
                     font=_fs("Helvetica", 18), bg=COLOR_PANEL,
                     fg=COLOR_TEXTO).pack(expand=True)
            return
        contenedor = tk.Frame(panel, bg=COLOR_PANEL)
        contenedor.pack(expand=True, fill="both", padx=40, pady=20)
        self._items_widgets = []
        for opcion in self._opciones:
            lbl = tk.Label(contenedor,
                           text="  " + self._etiqueta(opcion),
                           font=_fs("Helvetica", 16),
                           bg=COLOR_PANEL, fg=COLOR_TEXTO, anchor="w")
            lbl.pack(fill="x", pady=2)
            self._items_widgets.append(lbl)
        self._refrescar_cursor()

    def _refrescar_cursor(self):
        for i, lbl in enumerate(self._items_widgets):
            if i == self._cursor:
                lbl.config(text=" ▶ " + self._etiqueta(self._opciones[i]),
                           bg=COLOR_RESALTADO, fg="white")
            else:
                lbl.config(text="    " + self._etiqueta(self._opciones[i]),
                           bg=COLOR_PANEL, fg=COLOR_TEXTO)

    def siguiente(self):
        if not self._opciones: return
        self._cursor = (self._cursor + 1) % len(self._opciones)
        self._refrescar_cursor()

    def anterior(self):
        if not self._opciones: return
        self._cursor = (self._cursor - 1) % len(self._opciones)
        self._refrescar_cursor()

    def si(self):
        if not self._opciones:
            self._on_cancel(); return
        self._on_sel(self._opciones[self._cursor])

    def no(self):
        self._on_cancel()


# ─────────────────────────────────────────
# Confirmación Sí / No
# ─────────────────────────────────────────

class Confirmacion(Pantalla):
    def __init__(self, titulo: str, mensaje: str,
                 on_si: Callable[[], None], on_no: Callable[[], None]):
        self.titulo   = titulo
        self._mensaje = mensaje
        self._on_si   = on_si
        self._on_no   = on_no

    def dibujar(self, panel):
        tk.Label(panel, text=self._mensaje,
                 font=_fs("Helvetica", 16),
                 bg=COLOR_PANEL, fg=COLOR_TEXTO,
                 wraplength=ANCHO - 80, justify="center").pack(
                     expand=True, padx=40, pady=20)
        tk.Label(panel,
                 text="Sí/SEL = confirmar    ·    No/BACK = cancelar",
                 font=_fs("Helvetica", 13, "italic"),
                 bg=COLOR_PANEL, fg="#aaaacc").pack(pady=(0, 30))

    def si(self): self._on_si()
    def no(self): self._on_no()


# ─────────────────────────────────────────
# Mensaje de información / resultado
# ─────────────────────────────────────────

class Mensaje(Pantalla):
    def __init__(self, titulo: str, texto: str,
                 on_aceptar: Callable[[], None], es_error: bool = False):
        self.titulo    = titulo
        self._texto    = texto
        self._on_ok    = on_aceptar
        self._es_error = es_error

    def dibujar(self, panel):
        color = COLOR_ERROR if self._es_error else COLOR_OK
        tk.Label(panel,
                 text=("✗  " if self._es_error else "✓  ") + self._texto,
                 font=_fs("Helvetica", 16),
                 bg=COLOR_PANEL, fg=color,
                 wraplength=ANCHO - 80, justify="center").pack(
                     expand=True, padx=40, pady=20)
        tk.Label(panel, text="Sí/SEL = aceptar",
                 font=_fs("Helvetica", 12, "italic"),
                 bg=COLOR_PANEL, fg="#aaaacc").pack(pady=(0, 30))

    def si(self): self._on_ok()
    def no(self): self._on_ok()


# ─────────────────────────────────────────
# Teclado virtual lineal
# ─────────────────────────────────────────

# Teclado QWERTY — filas visuales
# La navegación encoder avanza/retrocede por todas las teclas en orden
# de lectura (fila 0 izq→der, fila 1 izq→der, …).  La disposición
# solo mejora la legibilidad visual en pantalla.
_QWERTY_FILAS: list[list[str]] = [
    list("1234567890-_"),
    list("qwertyuiop"),
    list("asdfghjkl"),
    list("zxcvbnm"),
    [" ", "⌫", "✓", "✗"],
]
# Lista plana para indexar con el cursor
TECLAS: list[str] = [t for fila in _QWERTY_FILAS for t in fila]


class TecladoVirtual(Pantalla):
    """
    Teclado QWERTY on-screen para ingresar el nombre de una prenda.

    Navegación:
      Encoder CW/CCW → avanza/retrocede entre todas las teclas (orden lectura).
      SEL            → inserta la tecla / confirma ✓ / cancela ✗ / borra ⌫.
      BACK           → borra el último carácter (o cancela si vacío).
      ABORT          → "Completado": acepta el texto actual sin navegar a ✓.
    """
    titulo = "Ingresar nombre"

    def __init__(self, titulo: str,
                 on_terminar: Callable[[str], None],
                 on_cancelar: Callable[[], None],
                 on_abort_completar: Optional[Callable[[str], None]] = None,
                 inicial: str = ""):
        self.titulo         = titulo
        self._on_done       = on_terminar
        self._on_cancel     = on_cancelar
        self._on_abort_comp = on_abort_completar or on_terminar
        self._texto         = inicial
        self._cursor        = 0
        self._lbl_texto: Optional[tk.Label]           = None
        self._lbl_teclas: dict[int, tk.Label]         = {}  # índice_plano → Label

    def abort_como_completar(self) -> bool:
        if self._texto.strip():
            self._on_abort_comp(self._texto.strip())
            return True
        return False

    def dibujar(self, panel):
        # ── Campo de texto ─────────────────────────────────────────────────
        self._lbl_texto = tk.Label(
            panel, text=self._texto + "│",
            font=_fs("Courier", 22, "bold"),
            bg=COLOR_PANEL, fg=COLOR_RESALTADO)
        self._lbl_texto.pack(pady=(12, 4))

        tk.Label(panel,
                 text="◀ ▶ mover  ·  SEL = insertar/confirmar  ·  BACK = borrar  ·  ABORT = completar",
                 font=_fs("Helvetica", 10, "italic"),
                 bg=COLOR_PANEL, fg="#aaaacc").pack()

        # ── Teclado QWERTY ─────────────────────────────────────────────────
        teclado_frame = tk.Frame(panel, bg=COLOR_PANEL)
        teclado_frame.pack(pady=10)

        self._lbl_teclas = {}
        idx_plano = 0

        for fila in _QWERTY_FILAS:
            fila_frame = tk.Frame(teclado_frame, bg=COLOR_PANEL)
            fila_frame.pack(pady=2)
            for tecla in fila:
                txt  = "␣" if tecla == " " else tecla
                # Teclas especiales más anchas
                ancho = 3 if tecla in ("⌫", "✓", "✗") else (4 if tecla == " " else 2)
                lbl  = tk.Label(
                    fila_frame,
                    text=txt,
                    font=_fs("Helvetica", 16, "bold"),
                    bg=COLOR_PANEL,
                    fg=COLOR_TEXTO,
                    width=ancho,
                    pady=5,
                    relief="flat",
                    bd=1,
                )
                lbl.pack(side="left", padx=2)
                self._lbl_teclas[idx_plano] = lbl
                idx_plano += 1

        self._refrescar()

    def _refrescar(self):
        if self._lbl_texto is not None:
            self._lbl_texto.config(text=self._texto + "│")
        for i, lbl in self._lbl_teclas.items():
            tecla = TECLAS[i]
            if i == self._cursor:
                # Resaltar tecla activa
                lbl.config(bg=COLOR_RESALTADO, fg="white", relief="raised")
            elif tecla in ("✓", "✗", "⌫"):
                lbl.config(bg="#3a3a52", fg="#cccccc", relief="flat")
            else:
                lbl.config(bg=COLOR_PANEL, fg=COLOR_TEXTO, relief="flat")

    def siguiente(self):
        self._cursor = (self._cursor + 1) % len(TECLAS)
        self._refrescar()

    def anterior(self):
        self._cursor = (self._cursor - 1) % len(TECLAS)
        self._refrescar()

    def si(self):
        tecla = TECLAS[self._cursor]
        if tecla == "✓":
            if self._texto.strip():
                self._on_done(self._texto.strip())
            return
        if tecla == "✗":
            self._on_cancel(); return
        if tecla == "⌫":
            self._texto = self._texto[:-1]
            self._refrescar(); return
        self._texto += tecla
        self._refrescar()

    def no(self):
        if self._texto:
            self._texto = self._texto[:-1]
            self._refrescar()
        else:
            self._on_cancel()


# ─────────────────────────────────────────
# Tabla de prendas conocidas
# ─────────────────────────────────────────

class TablaPrendasConocidas(Pantalla):
    """
    Muestra TODAS las prendas conocidas con sus características y ubicación.
    Columna 'Ubicación': P1/P2/P3 si está en perchero, NO si no.
    """
    titulo = "Prendas conocidas"

    def __init__(self, sistema: Sistema, on_volver: Callable[[], None]):
        self._sis       = sistema
        self._on_volver = on_volver

    def dibujar(self, panel):
        # Encabezados
        COLS   = ["Nombre", "Tipo", "Color", "Tela", "Talla", "Fit", "Ubicación"]
        WIDTHS = [18, 16, 8, 10, 6, 10, 9]

        contenedor = tk.Frame(panel, bg=COLOR_PANEL)
        contenedor.pack(expand=True, fill="both", padx=10, pady=10)

        # Canvas con scrollbar vertical
        canvas = tk.Canvas(contenedor, bg=COLOR_PANEL, highlightthickness=0)
        scrollbar = tk.Scrollbar(contenedor, orient="vertical",
                                 command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=COLOR_PANEL)
        canvas.create_window((0, 0), window=inner, anchor="nw")

        def _cfg(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _cfg)

        # Fila de encabezados
        for c, (col, w) in enumerate(zip(COLS, WIDTHS)):
            tk.Label(inner, text=col.center(w),
                     font=_fs("Courier", 11, "bold"),
                     bg=COLOR_RESALTADO, fg="white",
                     padx=4, pady=3, anchor="center",
                     relief="flat").grid(row=0, column=c, sticky="nsew", padx=1, pady=1)

        # Filas de prendas
        todas = list(self._sis.prendas())
        if not todas:
            tk.Label(inner, text="No hay prendas registradas.",
                     font=_fs("Helvetica", 14),
                     bg=COLOR_PANEL, fg=COLOR_TEXTO).grid(
                         row=1, column=0, columnspan=len(COLS), pady=10)
        else:
            for r, p in enumerate(todas, start=1):
                per_obj = self._sis._perchero_que_contiene(p.id)
                if per_obj is not None:
                    ubic = f"P{per_obj.id}"
                    ubic_fg = COLOR_OK
                else:
                    ubic = "NO"
                    ubic_fg = "#aaaacc"

                celdas = [p.nombre, p.tipo, p.color, p.tela, p.talla, p.fit]
                bg_row = "#242436" if r % 2 == 0 else COLOR_PANEL

                for c, (val, w) in enumerate(zip(celdas, WIDTHS)):
                    txt = str(val)
                    if len(txt) > w:
                        txt = txt[:w - 1] + "~"
                    tk.Label(inner, text=txt,
                             font=_fs("Courier", 10),
                             bg=bg_row, fg=COLOR_TEXTO,
                             padx=4, pady=2, anchor="w").grid(
                                 row=r, column=c, sticky="nsew", padx=1, pady=0)

                # Columna Ubicación
                tk.Label(inner, text=ubic,
                         font=_fs("Courier", 10, "bold"),
                         bg=bg_row, fg=ubic_fg,
                         padx=4, pady=2, anchor="center").grid(
                             row=r, column=len(COLS) - 1,
                             sticky="nsew", padx=1, pady=0)

        tk.Label(panel,
                 text="Sí/SEL o No/BACK = volver",
                 font=_fs("Helvetica", 11, "italic"),
                 bg=COLOR_PANEL, fg="#aaaacc").pack(pady=(4, 6))

    def si(self):   self._on_volver()
    def no(self):   self._on_volver()
    def siguiente(self): pass
    def anterior(self):  pass


# ─────────────────────────────────────────
# Tabla de características disponibles
# ─────────────────────────────────────────

class TablaCaracteristicas(Pantalla):
    """
    Tabla informativa estática con todas las opciones válidas para
    cada característica. No modifica ningún dato.
    """
    titulo = "Características disponibles"

    def __init__(self, on_volver: Callable[[], None]):
        self._on_volver = on_volver

    def dibujar(self, panel):
        contenedor = tk.Frame(panel, bg=COLOR_PANEL)
        contenedor.pack(expand=True, fill="both", padx=12, pady=10)

        # Encabezados y datos de cada columna
        columnas = [
            ("Tipo",   list(TIPOS_PRENDA)),
            ("Color",  list(COLORES)),
            ("Tela",   list(TIPOS_TELA)),
            ("Talla",  list(TALLAS)),
            ("Fit",    list(FITS)),
        ]

        for col_idx, (encabezado, opciones) in enumerate(columnas):
            col_frame = tk.Frame(contenedor, bg=COLOR_PANEL)
            col_frame.pack(side="left", expand=True, fill="both", padx=4)

            # Encabezado
            tk.Label(col_frame,
                     text=encabezado,
                     font=_fs("Helvetica", 13, "bold"),
                     bg=COLOR_RESALTADO, fg="white",
                     pady=5, padx=6, anchor="center").pack(fill="x", pady=(0, 3))

            # Opciones
            for i, op in enumerate(opciones):
                bg_celda = "#242436" if i % 2 == 0 else COLOR_PANEL
                tk.Label(col_frame,
                         text=op,
                         font=_fs("Helvetica", 12),
                         bg=bg_celda, fg=COLOR_TEXTO,
                         pady=4, padx=6, anchor="w").pack(fill="x")

        tk.Label(panel,
                 text="SEL / BACK = volver",
                 font=_fs("Helvetica", 11, "italic"),
                 bg=COLOR_PANEL, fg="#aaaacc").pack(pady=(6, 4))

    def si(self):        self._on_volver()
    def no(self):        self._on_volver()
    def siguiente(self): pass
    def anterior(self):  pass


# ─────────────────────────────────────────
# Diagrama de los 3 percheros
# ─────────────────────────────────────────

class DiagramaPercheros(Pantalla):
    titulo = "Prendas actuales"

    def __init__(self, sistema: Sistema, on_volver: Callable[[], None]):
        self._sis       = sistema
        self._on_volver = on_volver

    def dibujar(self, panel):
        contenedor = tk.Frame(panel, bg=COLOR_PANEL)
        contenedor.pack(expand=True, fill="both", padx=20, pady=20)
        for per in self._sis.percheros():
            col = tk.Frame(contenedor, bg=COLOR_FONDO, bd=2, relief="ridge")
            col.pack(side="left", expand=True, fill="both", padx=10, pady=5)
            tk.Label(col, text=f"Perchero {per.id}",
                     font=_fs("Helvetica", 14, "bold"),
                     bg=COLOR_FONDO, fg=COLOR_RESALTADO).pack(pady=(8, 4))
            for i, pid in enumerate(per.slots):
                if pid is None:
                    txt = f"slot {i}: ─"; fg = "#777777"
                else:
                    p   = self._sis.buscar_prenda_por_id(pid)
                    txt = f"slot {i}: {p.nombre if p else '?'}"; fg = COLOR_TEXTO
                tk.Label(col, text=txt, font=_fs("Courier", 12),
                         bg=COLOR_FONDO, fg=fg, anchor="w").pack(
                             fill="x", padx=8, pady=2)
        no_alm = self._sis.prendas_conocidas_no_almacenadas()
        if no_alm:
            tk.Label(panel,
                     text="Conocidas sin perchero: " +
                          ", ".join(p.nombre for p in no_alm),
                     font=_fs("Helvetica", 11, "italic"),
                     bg=COLOR_PANEL, fg="#aaaacc",
                     wraplength=ANCHO - 80).pack(pady=(0, 10))

    def si(self):        self._on_volver()
    def no(self):        self._on_volver()
    def siguiente(self): pass
    def anterior(self):  pass


# ─────────────────────────────────────────
# Pantalla de espera OLED
# ─────────────────────────────────────────

class EsperaOled(Pantalla):
    titulo = "Extraer por características"

    def dibujar(self, panel):
        tk.Label(panel,
                 text="🖥  Use la pantalla OLED\npara seleccionar la prenda.",
                 font=_fs("Helvetica", 20, "bold"),
                 bg=COLOR_PANEL, fg=COLOR_RESALTADO,
                 justify="center").pack(expand=True)
        tk.Label(panel,
                 text="Encoder = navegar   ·   SEL = elegir\n"
                      "BACK = atrás        ·   ABORT = cancelar todo",
                 font=_fs("Helvetica", 13, "italic"),
                 bg=COLOR_PANEL, fg="#aaaacc").pack(pady=(0, 30))


# ─────────────────────────────────────────
# Controlador principal
# ─────────────────────────────────────────

class Interfaz:
    def __init__(self, sistema: Sistema, driver: Optional[ESP32Driver] = None):
        self.sistema = sistema
        self.driver  = driver

        # Flag: True mientras el motor está en movimiento; ABORT bloqueado
        self._motor_activo = False

        self.root = tk.Tk()
        self.root.title("Sistema de Percheros Inteligentes")
        self.root.configure(bg=COLOR_FONDO)

        self.root.update_idletasks()
        ancho_real = self.root.winfo_screenwidth()
        alto_real  = self.root.winfo_screenheight()

        global ANCHO, ALTO, ESCALA
        if ES_RASPBERRY:
            self.root.attributes("-fullscreen", True)
            ANCHO = ancho_real
            ALTO  = alto_real
        else:
            self.root.geometry(f"{ANCHO}x{ALTO}")

        ESCALA = max(1.0, min(alto_real / 480.0, ancho_real / 800.0, 2.0))

        self.entrada = Entrada(self.root)
        self._conectar_entrada_gui()

        # Layout
        self._header = tk.Label(self.root, text="",
                                font=_fs("Helvetica", 18, "bold"),
                                bg=COLOR_FONDO, fg=COLOR_TEXTO, pady=10)
        self._header.pack(fill="x")

        self._panel = tk.Frame(self.root, bg=COLOR_PANEL)
        self._panel.pack(expand=True, fill="both", padx=10, pady=5)

        self._barra = tk.Frame(self.root, bg=COLOR_FONDO)
        self._barra.pack(fill="x", side="bottom", pady=8)
        self._crear_botones_simulados(self._barra)

        self._stack: list[Pantalla]       = []
        self._actual: Optional[Pantalla]  = None
        # Guardar referencia al TecladoVirtual activo si lo hay
        self._teclado_activo: Optional[TecladoVirtual] = None

        self.root.protocol("WM_DELETE_WINDOW", self._cerrar)
        self._mostrar_menu_principal()

    # ─── conexión de entrada ──────────────────────────────────────────────

    def _conectar_entrada_gui(self):
        self.entrada.on(Boton.SI,        lambda: self._delegar("si"))
        self.entrada.on(Boton.NO,        lambda: self._delegar("no"))
        self.entrada.on(Boton.SIGUIENTE, lambda: self._delegar("siguiente"))
        self.entrada.on(Boton.ANTERIOR,  lambda: self._delegar("anterior"))
        self.entrada.on(Boton.ABORT,     self._abort)

    def _conectar_entrada_oled(self, cbs: dict):
        self.entrada.on(Boton.SIGUIENTE, cbs["siguiente"])
        self.entrada.on(Boton.ANTERIOR,  cbs["anterior"])
        self.entrada.on(Boton.SI,        cbs["si"])
        self.entrada.on(Boton.NO,        cbs["no"])
        # ABORT durante OLED también cancela (equivale a BACK en menú raíz OLED)
        self.entrada.on(Boton.ABORT,     cbs["no"])

    # ─── ABORT ───────────────────────────────────────────────────────────

    def _abort(self):
        """Vuelve al menú principal desde cualquier lugar.

        Excepciones:
          · Si el motor está activo, se ignora.
          · Si la pantalla activa es un TecladoVirtual con texto, actúa
            como "Completado" (avanza con el texto actual).
        """
        if self._motor_activo:
            return   # no hacer nada mientras se mueve el perchero

        # Si hay un teclado activo con texto → ABORT = Completado
        if (self._teclado_activo is not None
                and self._teclado_activo is self._actual):
            self._teclado_activo.abort_como_completar()
            return

        # En cualquier otro caso → menú principal
        self._reset_a_menu()

    # ─── manejo de pantallas ──────────────────────────────────────────────

    def _delegar(self, metodo: str):
        if self._actual is not None:
            getattr(self._actual, metodo)()

    def _push(self, pantalla: Pantalla):
        if self._actual is not None:
            self._stack.append(self._actual)
        self._mostrar(pantalla)

    def _pop(self):
        if self._stack:
            self._mostrar(self._stack.pop())
        else:
            self._mostrar_menu_principal()

    def _reemplazar(self, pantalla: Pantalla):
        self._mostrar(pantalla)

    def _reset_a_menu(self):
        self._stack.clear()
        self._teclado_activo = None
        self._mostrar_menu_principal()

    def _mostrar(self, pantalla: Pantalla):
        for w in self._panel.winfo_children():
            w.destroy()
        self._actual = pantalla
        # Rastrear teclado activo
        if isinstance(pantalla, TecladoVirtual):
            self._teclado_activo = pantalla
        else:
            self._teclado_activo = None
        self._header.config(text=pantalla.titulo)
        pantalla.dibujar(self._panel)

    def _crear_botones_simulados(self, barra):
        for boton, label, color in [
            (Boton.ANTERIOR,  "◀ Ant",   COLOR_BOTON),
            (Boton.NO,        "BACK",    COLOR_BOTON),
            (Boton.SI,        "SEL/Sí",  COLOR_BOTON),
            (Boton.SIGUIENTE, "Sig ▶",   COLOR_BOTON),
            (Boton.ABORT,     "ABORT",   COLOR_ABORT),
        ]:
            b = tk.Button(barra, text=label,
                          font=_fs("Helvetica", 12, "bold"),
                          bg=color, fg="white",
                          activebackground=COLOR_BOTON_ACTIVO,
                          activeforeground="white",
                          padx=12, pady=10, bd=0,
                          command=lambda b=boton: self.entrada.disparar(b))
            b.pack(side="left", expand=True, fill="x", padx=4)

    def _cerrar(self):
        """Cierre limpio: envía HOME_STEPPER al ESP32 y destruye la ventana."""
        if self.driver is not None:
            print("  Enviando HOME_STEPPER al ESP32 (fin de sesión)...")
            self.driver.home_stepper()   # nuevo método en comunicacion.py
        self.entrada.cleanup()
        self.root.destroy()

    def correr(self):
        self.root.mainloop()

    # ─────────────────────────────────────────
    # Menú principal
    # ─────────────────────────────────────────

    def _mostrar_menu_principal(self):
        opciones = [
            ("Colocar prenda",  self._flujo_colocar),
            ("Extraer prenda",  self._flujo_extraer),
            ("Información",     self._flujo_info),
            ("Eliminar prenda", self._flujo_eliminar),
            ("Guardar estado",  self._flujo_guardar),
            ("Cargar estado",   self._flujo_cargar),
            ("Salir",           self._cerrar),
        ]
        sel = Selector(
            titulo="Menú principal",
            opciones=opciones,
            etiqueta=lambda op: op[0],
            on_seleccion=lambda op: op[1](),
            # BACK desde el menú principal también envía HOME_STEPPER y cierra
            on_cancelar=self._cerrar,
        )
        self._stack.clear()
        self._teclado_activo = None
        self._mostrar(sel)

    # ─── COLOCAR ─────────────────────────────────────────────────────────

    def _flujo_colocar(self):
        opciones = [("Prenda nueva",  self._flujo_colocar_nueva),
                    ("Prenda previa", self._flujo_colocar_previa)]
        self._push(Selector(
            titulo="Colocar prenda",
            opciones=opciones,
            etiqueta=lambda op: op[0],
            on_seleccion=lambda op: op[1](),
            on_cancelar=self._pop,
        ))

    def _flujo_colocar_nueva(self):
        # Las closures de paso se pasan on_cancelar=self._pop para BACK normal.
        # ABORT en el teclado = Completado; en los selectores siguientes = abort.
        def paso2(nombre):
            self._sel_caracteristica("Tipo", TIPOS_PRENDA,
                lambda tipo: paso3(nombre, tipo))
        def paso3(nombre, tipo):
            self._sel_caracteristica("Color", COLORES,
                lambda color: paso4(nombre, tipo, color))
        def paso4(nombre, tipo, color):
            self._sel_caracteristica("Tela", TIPOS_TELA,
                lambda tela: paso5(nombre, tipo, color, tela))
        def paso5(nombre, tipo, color, tela):
            self._sel_caracteristica("Talla", TALLAS,
                lambda talla: paso6(nombre, tipo, color, tela, talla))
        def paso6(nombre, tipo, color, tela, talla):
            self._sel_caracteristica("Fit", FITS,
                lambda fit: paso7(nombre, tipo, color, tela, talla, fit))
        def paso7(nombre, tipo, color, tela, talla, fit):
            self._sel_perchero(
                lambda per: paso8(nombre, tipo, color, tela, talla, fit, per))
        def paso8(nombre, tipo, color, tela, talla, fit, per):
            resumen = (f"Confirmar:\n\nNombre: {nombre}\n"
                       f"{tipo} · {color} · {tela} · {talla} · {fit}\n"
                       f"Perchero: {per}")
            def ejecutar():
                r = self.sistema.colocar_prenda_nueva(
                    nombre, tipo, color, tela, talla, fit, per)
                self._mostrar_resultado(r)
            self._push(Confirmacion(
                titulo="Confirmar colocación",
                mensaje=resumen,
                on_si=ejecutar,
                on_no=self._pop,
            ))

        tv = TecladoVirtual(
            titulo="Nombre único de la nueva prenda",
            on_terminar=paso2,
            on_cancelar=self._pop,
            on_abort_completar=paso2,   # ABORT = Completado en esta fase
        )
        self._push(tv)

    def _flujo_colocar_previa(self):
        previas = self.sistema.prendas_conocidas_no_almacenadas()
        if not previas:
            self._mostrar_resultado(Resultado.error(
                "No hay prendas previas (todas están en algún perchero)"))
            return

        def elegida(prenda):
            def por_perchero(per_id):
                resumen = (f"Confirmar:\n\nColocar '{prenda.nombre}'\n"
                           f"en perchero {per_id}")
                def ejecutar():
                    r = self.sistema.colocar_prenda_previa(prenda.nombre, per_id)
                    self._mostrar_resultado(r)
                self._push(Confirmacion(
                    titulo="Confirmar colocación",
                    mensaje=resumen,
                    on_si=ejecutar,
                    on_no=self._pop,
                ))
            self._sel_perchero(por_perchero)

        self._push(Selector(
            titulo="Seleccionar prenda previa",
            opciones=previas,
            etiqueta=lambda p: f"{p.nombre}  ({p.tipo}, {p.color})",
            on_seleccion=elegida,
            on_cancelar=self._pop,
        ))

    # ─── EXTRAER ─────────────────────────────────────────────────────────

    def _flujo_extraer(self):
        opciones = [("Por nombre único",    self._flujo_extraer_nombre),
                    ("Por características", self._flujo_extraer_caract)]
        self._push(Selector(
            titulo="Extraer prenda",
            opciones=opciones,
            etiqueta=lambda op: op[0],
            on_seleccion=lambda op: op[1](),
            on_cancelar=self._pop,
        ))

    def _flujo_extraer_nombre(self):
        prendas_en_per = [
            p for p in self.sistema.prendas()
            if self.sistema._perchero_que_contiene(p.id) is not None
        ]
        if not prendas_en_per:
            self._mostrar_resultado(Resultado.error(
                "No hay ninguna prenda en los percheros"))
            return

        def elegida(prenda):
            r = self.sistema.extraer_por_nombre(prenda.nombre)
            self._mostrar_resultado(r)

        self._push(Selector(
            titulo="Extraer por nombre",
            opciones=prendas_en_per,
            etiqueta=lambda p: f"{p.nombre}  ({p.tipo}, {p.color})",
            on_seleccion=elegida,
            on_cancelar=self._pop,
        ))

    def _flujo_extraer_caract(self):
        """
        Lanza el menú OLED en un hilo secundario.
        Corrección de bug: el flujo de filtros tiene una condición de parada
        clara (todos los atributos procesados exactamente una vez).
        """
        self._push(EsperaOled())

        def _on_extraccion_oled(prenda, perchero_id, slot_idx):
            r = self.sistema.extraer_por_nombre(prenda.nombre)
            self.root.after(0, lambda: self._finalizar_extraccion_oled(r))

        def _on_cancelar_oled():
            self.root.after(0, self._pop)

        def _hilo_oled():
            from oled_caract import OledMenuState, get_callbacks, _display_loop
            from oled_caract import _Fase
            from Ropero import TIPOS_PRENDA, COLORES, TIPOS_TELA, TALLAS, FITS
            import time

            try:
                from luma.core.interface.serial import i2c as luma_i2c
                from luma.oled.device import ssd1306
                from PIL import ImageFont
                serial = luma_i2c(port=oled.I2C_PORT, address=oled.I2C_ADDRESS)
                device = ssd1306(serial,
                                 width=oled.OLED_WIDTH,
                                 height=oled.OLED_HEIGHT)
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
                device = None

            state = OledMenuState(
                self.sistema,
                opciones_tipo   = TIPOS_PRENDA,
                opciones_color  = COLORES,
                opciones_tela   = TIPOS_TELA,
                opciones_talla  = TALLAS,
                opciones_fit    = FITS,
            )
            state.on_extraccion = _on_extraccion_oled

            cbs = get_callbacks(state)
            self.root.after(0, lambda: self._conectar_entrada_oled(cbs))

            if oled_ok:
                t = threading.Thread(
                    target=_display_loop,
                    args=(device, state, font_sm, font_hd, None),
                    daemon=True,
                )
                t.start()

            while state.running:
                time.sleep(0.05)

            if oled_ok:
                try:
                    from luma.core.render import canvas as luma_canvas
                    with luma_canvas(device) as draw:
                        draw.rectangle((0, 0, 127, 63), fill="black")
                    device.hide()
                except Exception:
                    pass

            self.root.after(0, self._conectar_entrada_gui)

            if state.fase == _Fase.CANCEL:
                self.root.after(0, _on_cancelar_oled)

        threading.Thread(target=_hilo_oled, daemon=True).start()

    def _finalizar_extraccion_oled(self, r: Resultado):
        self._stack.clear()
        self._mostrar_resultado(r)

    # ─── INFORMACIÓN ─────────────────────────────────────────────────────

    def _flujo_info(self):
        opciones = [
            ("Prendas actuales",                     self._info_actuales),
            ("Prendas conocidas",                    self._info_conocidas),
            ("Informe filtrado por características", self._info_filtrado),
            ("Características",                      self._info_caracteristicas),
        ]
        self._push(Selector(
            titulo="Información",
            opciones=opciones,
            etiqueta=lambda op: op[0],
            on_seleccion=lambda op: op[1](),
            on_cancelar=self._pop,
        ))

    def _info_actuales(self):
        self._push(DiagramaPercheros(self.sistema, self._pop))

    def _info_conocidas(self):
        """Tabla de todas las prendas conocidas con columna de ubicación."""
        self._push(TablaPrendasConocidas(self.sistema, self._pop))

    def _info_caracteristicas(self):
        """Tabla estática de todas las opciones por característica."""
        self._push(TablaCaracteristicas(self._pop))

    def _info_filtrado(self):
        """
        Informe filtrado por características.

        Corrección de bug: se itera sobre los 5 atributos con un índice explícito.
        Cuando attr_idx == len(attrs), se muestran los resultados y se termina.
        El bucle NO se llama de nuevo tras mostrar la lista; BACK desde el
        resultado lleva al menú principal (no a otra pregunta de filtro).
        """
        attrs    = ["Tipo", "Color", "Tela", "Talla", "Fit"]
        dominios = [TIPOS_PRENDA, COLORES, TIPOS_TELA, TALLAS, FITS]

        def paso(attr_idx: int, valores: list):
            # ── Todos los atributos procesados: mostrar resultado ──────────
            if attr_idx >= len(attrs):
                tipo, color, tela, talla, fit = valores
                resultado = self.sistema.informe_por_caracteristicas(
                    tipo, color, tela, talla, fit)
                if not resultado:
                    self._push(Mensaje(
                        titulo="Resultado",
                        texto="Ninguna prenda coincide con el filtro.",
                        on_aceptar=self._pop,   # volver al menú de info
                        es_error=False,
                    ))
                else:
                    # Mostrar lista; BACK / SEL vuelven al menú principal
                    self._push(Selector(
                        titulo=f"Resultado ({len(resultado)} prendas)",
                        opciones=resultado,
                        etiqueta=lambda p: f"{p.nombre}  ·  {p.tipo} {p.color} {p.talla}",
                        on_seleccion=lambda _p: self._reset_a_menu(),
                        on_cancelar=self._reset_a_menu,
                    ))
                return   # <── salida del recursivo; no se vuelve a llamar

            nombre  = attrs[attr_idx]
            dominio = dominios[attr_idx]

            def si_filtrar():
                self._sel_caracteristica(
                    nombre, dominio,
                    lambda v: paso(attr_idx + 1, valores + [v]))

            def no_filtrar():
                paso(attr_idx + 1, valores + [None])

            self._push(Confirmacion(
                titulo=f"¿Filtrar por {nombre.lower()}?",
                mensaje=f"¿Deseas filtrar por {nombre.lower()}?",
                on_si=si_filtrar,
                on_no=no_filtrar,
            ))

        paso(0, [])

    # ─── ELIMINAR ────────────────────────────────────────────────────────

    def _flujo_eliminar(self):
        prendas = self.sistema.prendas()
        if not prendas:
            self._mostrar_resultado(Resultado.error("No hay prendas en el sistema"))
            return

        def elegida(prenda):
            def confirmar():
                r = self.sistema.eliminar_prenda(prenda.nombre)
                self._mostrar_resultado(r)
            self._push(Confirmacion(
                titulo="Confirmar eliminación",
                mensaje=f"¿Eliminar definitivamente '{prenda.nombre}'?",
                on_si=confirmar,
                on_no=self._pop,
            ))

        self._push(Selector(
            titulo="Eliminar prenda",
            opciones=prendas,
            etiqueta=lambda p: f"{p.nombre}  ({p.tipo}, {p.color})",
            on_seleccion=elegida,
            on_cancelar=self._pop,
        ))

    # ─── GUARDAR / CARGAR ────────────────────────────────────────────────

    def _flujo_guardar(self):
        def confirmar():
            r = self.sistema.guardar_estado(ARCHIVO_ESTADO)
            self._mostrar_resultado(r)
        self._push(Confirmacion(
            titulo="Guardar estado",
            mensaje="¿Guardar el estado actual del sistema?",
            on_si=confirmar,
            on_no=self._pop,
        ))

    def _flujo_cargar(self):
        def confirmar():
            r = self.sistema.cargar_estado(ARCHIVO_ESTADO)
            self._mostrar_resultado(r)
        self._push(Confirmacion(
            titulo="Cargar estado",
            mensaje="¿Cargar el estado guardado?\n⚠ Reemplazará el estado actual.",
            on_si=confirmar,
            on_no=self._pop,
        ))

    # ─── helpers ─────────────────────────────────────────────────────────

    def _sel_caracteristica(self, titulo: str, opciones, on_seleccion):
        self._push(Selector(
            titulo=titulo,
            opciones=list(opciones),
            on_seleccion=on_seleccion,
            on_cancelar=self._pop,
        ))

    def _sel_perchero(self, on_seleccion):
        self._push(Selector(
            titulo="Seleccionar perchero",
            opciones=list(range(1, NUM_PERCHEROS + 1)),
            etiqueta=lambda n: f"Perchero {n}",
            on_seleccion=on_seleccion,
            on_cancelar=self._pop,
        ))

    def _mostrar_resultado(self, r: Resultado):
        """
        Muestra el resultado de una operación y mueve el motor si corresponde.
        Bloquea ABORT durante el movimiento (_motor_activo = True).
        RETIRAR se envía cuando el usuario confirma (SEL/Sí) tras el movimiento.
        """
        hubo_movimiento = False

        if (r.ok and self.driver is not None
                and isinstance(r.datos, dict)
                and "perchero" in r.datos and "slot" in r.datos):
            per    = r.datos["perchero"]
            slot   = r.datos["slot"]
            estado = (per - 1) * 5 + slot + 1

            self._motor_activo = True
            rmotor = self.driver.ir_a_estado(estado)
            self._motor_activo = False

            hubo_movimiento = True
            if rmotor.ok:
                r = Resultado(True, r.mensaje + "\nPrenda presentada.", r.datos)
            else:
                r = Resultado(True,
                              r.mensaje + f"\n⚠ Error mecánico: {rmotor.mensaje}",
                              r.datos)

        on_aceptar = self._retirar_y_reset if hubo_movimiento else self._reset_a_menu

        self._reemplazar(Mensaje(
            titulo="Resultado",
            texto=r.mensaje,
            on_aceptar=on_aceptar,
            es_error=not r.ok,
        ))

    def _retirar_y_reset(self):
        """El usuario confirmó; enviar RETIRAR y volver al menú."""
        if self.driver is not None:
            self.driver.retirar()
        self._reset_a_menu()

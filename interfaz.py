"""
interfaz.py — GUI del Sistema de Percheros Inteligentes (Tkinter).

Diseñada para operarse con SOLO 4 botones (Sí, No, Siguiente, Anterior).
Funciona igual en Raspberry Pi (con botones GPIO, pantalla HDMI o DSI) y en
Windows (con teclas y botones clickeables en pantalla).

SOPORTE HDMI:
  En Raspberry Pi la ventana se pone en fullscreen automáticamente, sea cual
  sea la resolución del monitor HDMI (p.ej. 1920×1080). Las fuentes y los
  márgenes escalan con la resolución real detectada en tiempo de ejecución.

Estructura:
  - Pantalla (clase base): contrato de toda pantalla.
  - Selector, Confirmacion, Mensaje, TecladoVirtual, DiagramaPercheros:
        pantallas reutilizables.
  - Interfaz: el controlador. Mantiene un stack de pantallas para
        que el botón "No" funcione como "atrás" en cualquier punto.
"""

from __future__ import annotations
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Optional, Any

from Ropero import (
    Sistema, Resultado,
    TIPOS_PRENDA, COLORES, TIPOS_TELA, TALLAS, FITS,
    NUM_PERCHEROS, SLOTS_POR_PERCHERO, ARCHIVO_ESTADO,
)
from entrada import Entrada, Boton, ES_RASPBERRY
from comunicacion import ESP32Driver


# ─────────────────────────────────────────
# Estilo (paleta)
# ─────────────────────────────────────────

COLOR_FONDO        = "#1e1e2e"
COLOR_PANEL        = "#2a2a3e"
COLOR_TEXTO        = "#e0e0e0"
COLOR_RESALTADO    = "#7c5cff"
COLOR_OK           = "#4caf50"
COLOR_ERROR        = "#f44336"
COLOR_BOTON        = "#3a3a52"
COLOR_BOTON_ACTIVO = "#5050a0"

# Tamaño de diseño base (Pi 7" DSI / HDMI 800×480).
# Interfaz.__init__ actualiza estos valores con la resolución real.
ANCHO  = 800
ALTO   = 480
ESCALA = 1.0   # factor de escala calculado desde resolución real


def _fs(nombre: str, tam: int, *estilo) -> tuple:
    """Devuelve una tupla de fuente escalada a la resolución real.

    Uso: _fs("Helvetica", 16, "bold")  →  ("Helvetica", 20, "bold") en 1080p
    """
    return (nombre, max(9, int(tam * ESCALA))) + estilo


# ─────────────────────────────────────────
# Clase base Pantalla
# ─────────────────────────────────────────

class Pantalla:
    """Contrato común. Cada pantalla decide qué hace cada botón."""

    titulo: str = ""

    def dibujar(self, panel: tk.Frame) -> None:
        raise NotImplementedError

    def siguiente(self): pass
    def anterior(self):  pass
    def si(self):        pass
    def no(self):        pass


# ─────────────────────────────────────────
# Selector de lista
# ─────────────────────────────────────────

class Selector(Pantalla):
    """Lista navegable: el usuario mueve el cursor con Anterior/Siguiente,
    confirma con Sí, vuelve atrás con No."""

    def __init__(self, titulo: str, opciones: list[Any],
                 on_seleccion: Callable[[Any], None],
                 on_cancelar: Callable[[], None],
                 etiqueta: Callable[[Any], str] = str,
                 vacio_msg: str = "(no hay opciones)"):
        self.titulo       = titulo
        self._opciones    = list(opciones)
        self._on_sel      = on_seleccion
        self._on_cancel   = on_cancelar
        self._etiqueta    = etiqueta
        self._vacio_msg   = vacio_msg
        self._cursor      = 0
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
            lbl = tk.Label(contenedor, text="  " + self._etiqueta(opcion),
                           font=_fs("Helvetica", 16), bg=COLOR_PANEL,
                           fg=COLOR_TEXTO, anchor="w")
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
            self._on_cancel()
            return
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
        tk.Label(panel, text=self._mensaje, font=_fs("Helvetica", 16),
                 bg=COLOR_PANEL, fg=COLOR_TEXTO,
                 wraplength=ANCHO - 80, justify="center").pack(
                     expand=True, padx=40, pady=20)
        tk.Label(panel, text="Sí = confirmar    ·    No = cancelar",
                 font=_fs("Helvetica", 13, "italic"),
                 bg=COLOR_PANEL, fg="#aaaacc").pack(pady=(0, 30))

    def si(self): self._on_si()
    def no(self): self._on_no()


# ─────────────────────────────────────────
# Mensaje de información (un solo botón: Sí = OK)
# ─────────────────────────────────────────

class Mensaje(Pantalla):
    def __init__(self, titulo: str, texto: str,
                 on_aceptar: Callable[[], None], es_error: bool = False):
        self.titulo   = titulo
        self._texto   = texto
        self._on_ok   = on_aceptar
        self._es_error = es_error

    def dibujar(self, panel):
        color = COLOR_ERROR if self._es_error else COLOR_OK
        tk.Label(panel, text=("✗  " if self._es_error else "✓  ") + self._texto,
                 font=_fs("Helvetica", 16), bg=COLOR_PANEL, fg=color,
                 wraplength=ANCHO - 80, justify="center").pack(
                     expand=True, padx=40, pady=20)
        tk.Label(panel, text="Sí = aceptar",
                 font=_fs("Helvetica", 12, "italic"),
                 bg=COLOR_PANEL, fg="#aaaacc").pack(pady=(0, 30))

    def si(self): self._on_ok()
    def no(self): self._on_ok()


# ─────────────────────────────────────────
# Teclado virtual lineal (para nombres únicos)
# ─────────────────────────────────────────

# Layout de un único renglón. Los símbolos finales son funcionales:
#   ⌫  = backspace, ✓ = terminar, ✗ = cancelar
TECLAS = list("abcdefghijklmnopqrstuvwxyz0123456789-_") + [" ", "⌫", "✓", "✗"]


class TecladoVirtual(Pantalla):
    """Permite ingresar un texto navegando una sola fila de caracteres.

    Anterior/Siguiente: mueven el cursor por las teclas.
    Sí: selecciona la tecla actual (agrega letra, o ejecuta acción especial).
    No: borra el último carácter del texto (atajo rápido para corregir).
    """

    titulo = "Ingresar nombre"

    def __init__(self, titulo: str,
                 on_terminar: Callable[[str], None],
                 on_cancelar: Callable[[], None],
                 inicial: str = ""):
        self.titulo     = titulo
        self._on_done   = on_terminar
        self._on_cancel = on_cancelar
        self._texto     = inicial
        self._cursor    = 0
        self._lbl_texto: Optional[tk.Label] = None
        self._lbl_teclas: list[tk.Label]    = []

    def dibujar(self, panel):
        # Texto que se está componiendo
        self._lbl_texto = tk.Label(
            panel, text=self._texto + "│",
            font=_fs("Courier", 22, "bold"),
            bg=COLOR_PANEL, fg=COLOR_RESALTADO,
        )
        self._lbl_texto.pack(pady=(20, 10))

        # Indicaciones
        tk.Label(panel,
                 text="◀ ▶ mover cursor   ·   Sí = seleccionar   ·   No = borrar",
                 font=_fs("Helvetica", 11, "italic"),
                 bg=COLOR_PANEL, fg="#aaaacc").pack()

        # Fila de teclas (con scroll si no caben)
        canvas = tk.Canvas(panel, bg=COLOR_PANEL, highlightthickness=0,
                           height=int(70 * ESCALA))
        canvas.pack(fill="x", padx=20, pady=20)
        frame = tk.Frame(canvas, bg=COLOR_PANEL)
        canvas.create_window((0, 0), window=frame, anchor="nw")

        self._lbl_teclas = []
        for tecla in TECLAS:
            txt = tecla if tecla != " " else "␣"
            lbl = tk.Label(frame, text=f" {txt} ",
                           font=_fs("Helvetica", 18, "bold"),
                           bg=COLOR_PANEL, fg=COLOR_TEXTO,
                           padx=4, pady=4)
            lbl.pack(side="left", padx=2)
            self._lbl_teclas.append(lbl)

        frame.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))
        self._canvas = canvas
        self._refrescar()

    def _refrescar(self):
        if self._lbl_texto is not None:
            self._lbl_texto.config(text=self._texto + "│")
        for i, lbl in enumerate(self._lbl_teclas):
            if i == self._cursor:
                lbl.config(bg=COLOR_RESALTADO, fg="white")
                try:
                    x_min     = lbl.winfo_x()
                    ancho_lbl = lbl.winfo_width()
                    ancho_canvas = self._canvas.winfo_width()
                    bbox = self._canvas.bbox("all")
                    if bbox and bbox[2] > 0:
                        frac = max(0, x_min - ancho_canvas // 2) / bbox[2]
                        self._canvas.xview_moveto(min(1.0, frac))
                except Exception:
                    pass
            else:
                lbl.config(bg=COLOR_PANEL, fg=COLOR_TEXTO)

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
            self._on_cancel()
            return
        if tecla == "⌫":
            self._texto = self._texto[:-1]
            self._refrescar()
            return
        self._texto += tecla
        self._refrescar()

    def no(self):
        if self._texto:
            self._texto = self._texto[:-1]
            self._refrescar()
        else:
            self._on_cancel()


# ─────────────────────────────────────────
# Diagrama de los 3 percheros
# ─────────────────────────────────────────

class DiagramaPercheros(Pantalla):
    """Muestra los 3 percheros con sus 5 slots cada uno. Cumple la
    rúbrica 'Información: Prendas Actuales' (5 pts) del PDF."""

    titulo = "Prendas actuales"

    def __init__(self, sistema: Sistema, on_volver: Callable[[], None]):
        self._sis      = sistema
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
                    txt = f"slot {i}: ─"
                    fg  = "#777777"
                else:
                    # Usar el método público para obtener la prenda
                    p   = self._sis.buscar_prenda_por_id(pid)
                    txt = f"slot {i}: {p.nombre if p else '?'}"
                    fg  = COLOR_TEXTO
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
# Controlador principal
# ─────────────────────────────────────────

class Interfaz:
    """Controlador maestro. Crea la ventana, mantiene el stack de
    pantallas y conecta los botones a la pantalla activa.

    SOPORTE HDMI:
      En Raspberry Pi se activa fullscreen automáticamente. Se detecta la
      resolución real de la pantalla y se actualizan las variables globales
      ANCHO, ALTO y ESCALA para que todas las pantallas usen tamaños correctos.
    """

    def __init__(self, sistema: Sistema, driver: Optional[ESP32Driver] = None):
        self.sistema = sistema
        self.driver  = driver

        self.root = tk.Tk()
        self.root.title("Sistema de Percheros Inteligentes")
        self.root.configure(bg=COLOR_FONDO)

        # ── Detectar resolución real y calcular escala ─────────────────────
        # update() es necesario para que winfo_screen* devuelva valores reales
        self.root.update_idletasks()
        ancho_real = self.root.winfo_screenwidth()
        alto_real  = self.root.winfo_screenheight()

        # Actualizar los globales para que todas las Pantallas hereden el
        # tamaño correcto (los módulos Python comparten el mismo objeto de módulo)
        global ANCHO, ALTO, ESCALA
        if ES_RASPBERRY:
            # Pantalla completa en el monitor HDMI o DSI conectado a la Pi
            self.root.attributes("-fullscreen", True)
            ANCHO  = ancho_real
            ALTO   = alto_real
        else:
            # Modo ventana en desarrollo (Windows / Linux desktop)
            self.root.geometry(f"{ANCHO}x{ALTO}")

        # Escala relativa al diseño base de 800×480.
        # Limitado a 2× para no sobredimensionar fuentes en monitores 4K.
        ESCALA = max(1.0, min(alto_real / 480.0, ancho_real / 800.0, 2.0))
        # ──────────────────────────────────────────────────────────────────

        # Entrada (GPIO o teclado)
        self.entrada = Entrada(self.root)
        self.entrada.on(Boton.SI,        lambda: self._delegar("si"))
        self.entrada.on(Boton.NO,        lambda: self._delegar("no"))
        self.entrada.on(Boton.SIGUIENTE, lambda: self._delegar("siguiente"))
        self.entrada.on(Boton.ANTERIOR,  lambda: self._delegar("anterior"))

        # Layout: header / panel / botones
        self._header = tk.Label(self.root, text="",
                                font=_fs("Helvetica", 18, "bold"),
                                bg=COLOR_FONDO, fg=COLOR_TEXTO, pady=10)
        self._header.pack(fill="x")

        self._panel = tk.Frame(self.root, bg=COLOR_PANEL)
        self._panel.pack(expand=True, fill="both", padx=10, pady=5)

        self._barra = tk.Frame(self.root, bg=COLOR_FONDO)
        self._barra.pack(fill="x", side="bottom", pady=8)
        self._crear_botones_simulados(self._barra)

        # Stack y pantalla activa
        self._stack: list[Pantalla]    = []
        self._actual: Optional[Pantalla] = None

        # Cierre limpio
        self.root.protocol("WM_DELETE_WINDOW", self._cerrar)

        # Pantalla inicial
        self._mostrar_menu_principal()

    # ─── manejo de pantallas ───────────────

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
        """Cambia la pantalla actual sin meterla en el stack."""
        self._mostrar(pantalla)

    def _reset_a_menu(self):
        self._stack.clear()
        self._mostrar_menu_principal()

    def _mostrar(self, pantalla: Pantalla):
        for w in self._panel.winfo_children():
            w.destroy()
        self._actual = pantalla
        self._header.config(text=pantalla.titulo)
        pantalla.dibujar(self._panel)

    def _crear_botones_simulados(self, barra):
        """Botones en pantalla (simulan los 4 físicos; útil en Windows)."""
        for boton, label in [
            (Boton.ANTERIOR,  "◀ Anterior"),
            (Boton.NO,        "No"),
            (Boton.SI,        "Sí"),
            (Boton.SIGUIENTE, "Siguiente ▶"),
        ]:
            b = tk.Button(barra, text=label,
                          font=_fs("Helvetica", 13, "bold"),
                          bg=COLOR_BOTON, fg="white",
                          activebackground=COLOR_BOTON_ACTIVO,
                          activeforeground="white",
                          padx=18, pady=10, bd=0,
                          command=lambda b=boton: self.entrada.disparar(b))
            b.pack(side="left", expand=True, fill="x", padx=4)

    def _cerrar(self):
        self.entrada.cleanup()
        self.root.destroy()

    def correr(self):
        self.root.mainloop()

    # ─────────────────────────────────────
    # Menú principal y flujos
    # ─────────────────────────────────────

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
            on_cancelar=self._cerrar,
        )
        self._stack.clear()
        self._mostrar(sel)

    # ─── COLOCAR ─────────────────────────

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

        self._push(TecladoVirtual(
            titulo="Nombre único de la nueva prenda",
            on_terminar=paso2,
            on_cancelar=self._pop,
        ))

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

    # ─── EXTRAER ─────────────────────────

    def _flujo_extraer(self):
        opciones = [("Por nombre único",     self._flujo_extraer_nombre),
                    ("Por características",  self._flujo_extraer_caract)]
        self._push(Selector(
            titulo="Extraer prenda",
            opciones=opciones,
            etiqueta=lambda op: op[0],
            on_seleccion=lambda op: op[1](),
            on_cancelar=self._pop,
        ))

    def _flujo_extraer_nombre(self):
        # Solo prendas que estén en algún perchero
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
        """Informa al usuario que debe usar el mecanismo de terminal (CLI)."""
        self._push(Mensaje(
            titulo="Extraer por características",
            texto=(
                "Este modo usa un mecanismo distinto a esta interfaz.\n\n"
                "Abre una terminal o conéctate por SSH y ejecuta:\n\n"
                "  python3 extraccion_caract.py\n\n"
                "Puedes especificar hasta 5 características.\n"
                "El tipo de prenda es obligatorio."
            ),
            on_aceptar=self._pop,
            es_error=False,
        ))

    # ─── INFORMACIÓN ─────────────────────

    def _flujo_info(self):
        opciones = [
            ("Diagrama de prendas actuales",          self._info_actuales),
            ("Prendas conocidas no almacenadas",      self._info_no_alm),
            ("Informe filtrado por características",  self._info_filtrado),
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

    def _info_no_alm(self):
        prendas = self.sistema.prendas_conocidas_no_almacenadas()
        if not prendas:
            self._mostrar_resultado(Resultado.exitoso(
                "Todas las prendas conocidas están en algún perchero"))
            return
        self._push(Selector(
            titulo=f"Conocidas no almacenadas ({len(prendas)})",
            opciones=prendas,
            etiqueta=lambda p: f"{p.nombre}  ·  {p.tipo} {p.color} {p.talla}",
            on_seleccion=lambda _p: self._pop(),
            on_cancelar=self._pop,
        ))

    def _info_filtrado(self):
        def paso(attr_idx, valores):
            attrs    = ["Tipo", "Color", "Tela", "Talla", "Fit"]
            dominios = [TIPOS_PRENDA, COLORES, TIPOS_TELA, TALLAS, FITS]
            if attr_idx >= len(attrs):
                r = self.sistema.informe_por_caracteristicas(*valores)
                if not r:
                    self._mostrar_resultado(Resultado.exitoso(
                        "Ninguna prenda coincide con el filtro"))
                    return
                self._push(Selector(
                    titulo=f"Resultado ({len(r)})",
                    opciones=r,
                    etiqueta=lambda p: f"{p.nombre}  ·  {p.tipo} {p.color}",
                    on_seleccion=lambda _p: self._pop(),
                    on_cancelar=self._pop,
                ))
                return

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
                mensaje=f"¿Filtrar por {nombre.lower()}?",
                on_si=si_filtrar,
                on_no=no_filtrar,
            ))

        paso(0, [])

    # ─── ELIMINAR ────────────────────────

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
                mensaje=f"¿Eliminar definitivamente la prenda '{prenda.nombre}'?",
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

    # ─── GUARDAR / CARGAR ────────────────

    def _flujo_guardar(self):
        def confirmar():
            r = self.sistema.guardar_estado(ARCHIVO_ESTADO)
            self._mostrar_resultado(r)
        self._push(Confirmacion(
            titulo="Guardar estado",
            mensaje=f"¿Guardar el estado actual del sistema?",
            on_si=confirmar,
            on_no=self._pop,
        ))

    def _flujo_cargar(self):
        def confirmar():
            r = self.sistema.cargar_estado(ARCHIVO_ESTADO)
            self._mostrar_resultado(r)
        self._push(Confirmacion(
            titulo="Cargar estado",
            mensaje="¿Cargar el estado guardado?\n⚠ Esto reemplazará el estado actual.",
            on_si=confirmar,
            on_no=self._pop,
        ))

    # ─── helpers ─────────────────────────

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
        """Muestra el resultado de una operación y vuelve al menú principal.

        Si la operación afectó un slot físico (colocar/extraer), envía al
        ESP32 el comando PRESENT para mover los motores a esa posición.
        El error mecánico se anexa al mensaje pero no invalida la operación
        lógica (la BD ya quedó consistente).
        """
        if (r.ok and self.driver is not None
                and isinstance(r.datos, dict)
                and "perchero" in r.datos and "slot" in r.datos):
            per  = r.datos["perchero"]
            slot = r.datos["slot"]
            rmotor = self.driver.presentar(per, slot)
            if rmotor.ok:
                r = Resultado(True, r.mensaje + "\nMotor en posición.", r.datos)
            else:
                r = Resultado(True,
                              r.mensaje + f"\n⚠ Error mecánico: {rmotor.mensaje}",
                              r.datos)

        self._reemplazar(Mensaje(
            titulo="Resultado",
            texto=r.mensaje,
            on_aceptar=self._reset_a_menu,
            es_error=not r.ok,
        ))

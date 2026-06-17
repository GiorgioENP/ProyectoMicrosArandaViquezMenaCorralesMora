"""
oled_caract.py — Menú OLED para "Extraer por Características".

Pantalla OLED SSD1306 128×64 controlada por encoder EC11 + 3 botones:
  · Encoder CW/CCW → Siguiente / Anterior  (navegar lista)
  · Botón SEL  (GPIO22) → seleccionar ítem / confirmar
  · Botón BACK (GPIO4)  → omitir filtro actual / volver un paso / cancelar
  · Botón ABORT (GPIO10)→ salir al menú principal desde cualquier punto

Flujo — idéntico a Información → Filtrado por características:
  ┌─ 1. Lista de TIPO       → SEL elige valor │ BACK omite (None) │ ABORT sale
  ├─ 2. Lista de COLOR      → SEL elige valor │ BACK omite (None) │ ABORT sale
  ├─ 3. Lista de TELA       → SEL elige valor │ BACK omite (None) │ ABORT sale
  ├─ 4. Lista de TALLA      → SEL elige valor │ BACK omite (None) │ ABORT sale
  ├─ 5. Lista de FIT        → SEL elige valor │ BACK omite (None) │ ABORT sale
  ├─ 6. Lista de resultados → SEL extrae prenda │ BACK reinicia │ ABORT sale
  │      · 0 resultados → pantalla de aviso 2 s → reinicia filtros
  │      · 1 resultado  → pasa directo a confirmación
  └─ 7. Confirmación        → SEL confirma extracción │ BACK cancela │ ABORT sale

ABORT en cualquier pantalla termina el menú OLED con fase=CANCEL,
lo que hace que interfaz.py vuelva al menú principal.

Diferencia con _info_filtrado:
  · Corre completamente en la OLED (no en Tkinter).
  · Al terminar los filtros muestra las prendas DISPONIBLES EN PERCHERO
    (no todas las prendas conocidas).
  · Al seleccionar una prenda llama on_extraccion() y extrae físicamente.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

# ─────────────────────────────────────────
# Constantes de pantalla
# ─────────────────────────────────────────

OLED_WIDTH   = 128
OLED_HEIGHT  = 64
I2C_ADDRESS  = 0x3C
I2C_PORT     = 1

ROW_H        = 12
HEADER_H     = 13
VISIBLE_ROWS = 4


# ─────────────────────────────────────────
# Fases del menú
# ─────────────────────────────────────────

class _Fase:
    # Fases de selección de atributos (en orden)
    TIPO   = "tipo"
    COLOR  = "color"
    TELA   = "tela"
    TALLA  = "talla"
    FIT    = "fit"
    # Resultado y acción
    RESULT  = "result"
    CONFIRM = "confirm"
    SIN_RES = "sin_result"
    # Terminales
    DONE   = "done"
    CANCEL = "cancel"

# Orden de los 5 atributos de filtrado
_ORDEN_ATTRS = [_Fase.TIPO, _Fase.COLOR, _Fase.TELA, _Fase.TALLA, _Fase.FIT]

_TITULO_FASE = {
    _Fase.TIPO:    "Tipo",
    _Fase.COLOR:   "Color",
    _Fase.TELA:    "Tela",
    _Fase.TALLA:   "Talla",
    _Fase.FIT:     "Fit",
    _Fase.RESULT:  "Resultados",
    _Fase.CONFIRM: "Confirmar",
    _Fase.SIN_RES: "Sin resultados",
}

_HINT_FASE = {
    _Fase.TIPO:    "SEL=elegir  BACK=omitir",
    _Fase.COLOR:   "SEL=elegir  BACK=omitir",
    _Fase.TELA:    "SEL=elegir  BACK=omitir",
    _Fase.TALLA:   "SEL=elegir  BACK=omitir",
    _Fase.FIT:     "SEL=elegir  BACK=omitir",
    _Fase.RESULT:  "SEL=extraer BACK=reinic",
    _Fase.CONFIRM: "SEL=OK      BACK=cancel",
}


# ─────────────────────────────────────────
# Estado del menú — thread-safe
# ─────────────────────────────────────────

class OledMenuState:
    """
    Máquina de estados del menú OLED.

    Todos los métodos de navegación (mover, sel, no, abort) deben
    llamarse con self.lock adquirido.  get_callbacks() ya se encarga
    de esto.
    """

    def __init__(self, sistema,
                 opciones_tipo, opciones_color,
                 opciones_tela, opciones_talla, opciones_fit):

        self.lock = threading.Lock()

        self._sis = sistema
        self._opts: dict[str, list] = {
            _Fase.TIPO:  list(opciones_tipo),
            _Fase.COLOR: list(opciones_color),
            _Fase.TELA:  list(opciones_tela),
            _Fase.TALLA: list(opciones_talla),
            _Fase.FIT:   list(opciones_fit),
        }

        # Valores seleccionados para cada atributo (None = omitido)
        self._filtros: dict[str, Optional[str]] = {
            _Fase.TIPO:  None,
            _Fase.COLOR: None,
            _Fase.TELA:  None,
            _Fase.TALLA: None,
            _Fase.FIT:   None,
        }

        self._fase       = _Fase.TIPO
        self._cursor     = 0
        self._scroll_off = 0

        self._resultados: list = []   # prendas en perchero que coinciden
        self._prenda_sel       = None
        self._per_id: int      = 0
        self._slot_idx: int    = 0

        self._sin_res_frames   = 0    # contador para auto-reinicio sin resultados

        self.dirty   = True
        self.running = True

        # Callback: firma on_extraccion(prenda, perchero_id, slot_idx)
        self.on_extraccion: Optional[Callable] = None

    # ── propiedades de solo lectura usadas por el renderizador ────────────

    @property
    def fase(self) -> str:
        return self._fase

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def scroll_off(self) -> int:
        return self._scroll_off

    @property
    def resultados(self):
        return self._resultados

    @property
    def prenda_sel(self):
        return self._prenda_sel

    @property
    def per_id(self) -> int:
        return self._per_id

    @property
    def slot_idx(self) -> int:
        return self._slot_idx

    def lista_actual(self) -> list[str]:
        """Lista de ítems que se muestran en la fase actual."""
        if self._fase in self._opts:
            return self._opts[self._fase]
        if self._fase == _Fase.RESULT:
            return [p.nombre for p in self._resultados]
        return []

    def titulo(self) -> str:
        return _TITULO_FASE.get(self._fase, "")

    def hint(self) -> str:
        return _HINT_FASE.get(self._fase, "")

    def confirm_lineas(self) -> list[str]:
        """Líneas informativas para la pantalla de confirmación."""
        if self._prenda_sel is None:
            return ["(ninguna)"]
        p = self._prenda_sel
        return [
            p.nombre[:18],
            f"{p.tipo} {p.color}"[:18],
            f"{p.tela} {p.talla}"[:18],
            f"P{self._per_id} slot {self._slot_idx}",
            "",
            "SEL=confirmar",
        ]

    # ── navegación ────────────────────────────────────────────────────────

    def mover(self, delta: int):
        """Encoder: mover cursor."""
        lst = self.lista_actual()
        if not lst:
            return
        n = len(lst)
        self._cursor = (self._cursor + delta) % n
        # Ajustar scroll
        if self._cursor < self._scroll_off:
            self._scroll_off = self._cursor
        elif self._cursor >= self._scroll_off + VISIBLE_ROWS:
            self._scroll_off = self._cursor - VISIBLE_ROWS + 1
        self.dirty = True

    def sel(self):
        """
        SEL: seleccionar el ítem bajo el cursor.

        · En fases de atributo: guarda el valor y avanza al siguiente.
        · En RESULT: selecciona la prenda y va a confirmación.
        · En CONFIRM: llama on_extraccion y termina con DONE.
        """
        fase = self._fase

        if fase in _ORDEN_ATTRS:
            lst = self.lista_actual()
            if lst:
                self._filtros[fase] = lst[self._cursor]
                self._avanzar()

        elif fase == _Fase.RESULT:
            if self._resultados:
                self._seleccionar_prenda(self._cursor)

        elif fase == _Fase.CONFIRM:
            if self._prenda_sel is not None and self.on_extraccion:
                self.on_extraccion(
                    self._prenda_sel, self._per_id, self._slot_idx)
            self._fase = _Fase.DONE
            self.running = False

    def no(self):
        """
        BACK:
        · En fases de atributo: omite ese atributo (None) y avanza.
        · En RESULT: reinicia los filtros desde el principio.
        · En CONFIRM: cancela y vuelve a RESULT.
        """
        fase = self._fase

        if fase in _ORDEN_ATTRS:
            self._filtros[fase] = None   # omitir
            self._avanzar()

        elif fase == _Fase.RESULT:
            self._resetear()
            self._ir(_Fase.TIPO)

        elif fase == _Fase.CONFIRM:
            self._ir(_Fase.RESULT)

    def abort(self):
        """
        ABORT: termina el menú OLED con fase=CANCEL.
        interfaz.py interpretará esto como "volver al menú principal".
        """
        self._fase = _Fase.CANCEL
        self.running = False
        self.dirty = True

    # ── lógica interna ────────────────────────────────────────────────────

    def _ir(self, fase: str):
        self._fase = fase
        self._cursor = 0
        self._scroll_off = 0
        self.dirty = True

    def _avanzar(self):
        """Pasa al siguiente atributo; si ya terminamos, busca resultados."""
        idx_actual = _ORDEN_ATTRS.index(self._fase)
        siguiente  = idx_actual + 1

        if siguiente < len(_ORDEN_ATTRS):
            self._ir(_ORDEN_ATTRS[siguiente])
        else:
            self._buscar()

    def _buscar(self):
        """Consulta el sistema y transiciona a RESULT o SIN_RES."""
        tipo  = self._filtros[_Fase.TIPO]
        color = self._filtros[_Fase.COLOR]
        tela  = self._filtros[_Fase.TELA]
        talla = self._filtros[_Fase.TALLA]
        fit   = self._filtros[_Fase.FIT]

        candidatas = []
        for prenda in self._sis.prendas():
            per_obj = self._sis._perchero_que_contiene(prenda.id)
            if per_obj is None:
                continue
            if prenda.coincide(tipo, color, tela, talla, fit):
                candidatas.append(prenda)

        self._resultados = candidatas

        if len(candidatas) == 0:
            self._sin_res_frames = 0
            self._ir(_Fase.SIN_RES)
        elif len(candidatas) == 1:
            self._seleccionar_prenda(0)   # directo a confirmación
        else:
            self._ir(_Fase.RESULT)

    def _seleccionar_prenda(self, idx: int):
        prenda  = self._resultados[idx]
        per_obj = self._sis._perchero_que_contiene(prenda.id)
        if per_obj is None:
            return
        slot = per_obj.slot_de(prenda.id)
        self._prenda_sel = prenda
        self._per_id     = per_obj.id
        self._slot_idx   = slot
        self._ir(_Fase.CONFIRM)

    def _resetear(self):
        for k in self._filtros:
            self._filtros[k] = None
        self._resultados = []
        self._prenda_sel = None
        self._cursor     = 0
        self._scroll_off = 0

    def tick_sin_resultado(self) -> bool:
        """
        Llamado por el loop de renderizado en cada frame mientras estamos
        en SIN_RES.  Devuelve True cuando hay que reiniciar (tras ~2 s).
        """
        self._sin_res_frames += 1
        if self._sin_res_frames >= 40:   # 40 × 50 ms = 2 s
            self._resetear()
            self._ir(_Fase.TIPO)
            return True
        return False


# ─────────────────────────────────────────
# Renderizado OLED
# ─────────────────────────────────────────

def _draw_frame(device, state: OledMenuState, font_sm, font_hd):
    from luma.core.render import canvas as luma_canvas  # type: ignore

    with luma_canvas(device) as draw:
        fase = state.fase

        # ── Sin resultado ────────────────────────────────────────────────
        if fase == _Fase.SIN_RES:
            draw.rectangle((0, 0, 127, 63), fill="black")
            draw.text((2,  8), "Sin coincidencias", font=font_hd, fill="white")
            draw.text((2, 24), "Volviendo...",       font=font_sm, fill="white")
            return

        # ── Encabezado (barra blanca con título negro) ────────────────────
        titulo = state.titulo()
        t = titulo if len(titulo) <= 18 else titulo[:17] + "~"
        draw.rectangle((0, 0, 127, HEADER_H - 1), fill="white")
        draw.text((2, 1), t, font=font_hd, fill="black")

        # ── Pantalla de confirmación ──────────────────────────────────────
        if fase == _Fase.CONFIRM:
            y = HEADER_H + 1
            for linea in state.confirm_lineas():
                if y + ROW_H > OLED_HEIGHT:
                    break
                l = linea if len(linea) <= 19 else linea[:18] + "~"
                draw.text((2, y), l, font=font_sm, fill="white")
                y += ROW_H
            return

        # ── Lista con scroll y hint ───────────────────────────────────────
        hint = state.hint()
        if hint:
            draw.text((0, OLED_HEIGHT - ROW_H + 1), hint,
                      font=font_sm, fill="white")

        visibles = VISIBLE_ROWS - (1 if hint else 0)
        lista    = state.lista_actual()

        for row in range(visibles):
            i = state.scroll_off + row
            if i >= len(lista):
                break
            y        = HEADER_H + row * ROW_H + 1
            selected = (i == state.cursor)
            if selected:
                draw.rectangle((0, y - 1, 127, y + ROW_H - 2), fill="white")
                prefix, color_txt = "> ", "black"
            else:
                prefix, color_txt = "  ", "white"
            label = prefix + str(lista[i])
            if len(label) > 19:
                label = label[:18] + "~"
            draw.text((2, y), label, font=font_sm, fill=color_txt)

        # Scrollbar vertical
        if len(lista) > visibles > 0:
            area_h  = OLED_HEIGHT - HEADER_H - (ROW_H if hint else 0)
            bar_h   = max(4, int(area_h * visibles / len(lista)))
            bar_top = HEADER_H + int(area_h * state.scroll_off / len(lista))
            draw.rectangle((125, HEADER_H, 127, HEADER_H + area_h - 1),
                           outline="white")
            draw.rectangle((125, bar_top, 127, bar_top + bar_h), fill="white")


def _display_loop(device, state: OledMenuState, font_sm, font_hd, _unused):
    """Hilo de renderizado dedicado."""
    while state.running:
        with state.lock:
            if state.dirty or state.fase == _Fase.SIN_RES:
                _draw_frame(device, state, font_sm, font_hd)
                state.dirty = False
                if state.fase == _Fase.SIN_RES:
                    state.tick_sin_resultado()
        time.sleep(0.05)


# ─────────────────────────────────────────
# API pública: callbacks para entrada.py
# ─────────────────────────────────────────

def get_callbacks(state: OledMenuState) -> dict:
    """
    Devuelve un dict con los 5 callbacks que interfaz.py conecta a la entrada:
        cbs["siguiente"] → encoder CW
        cbs["anterior"]  → encoder CCW
        cbs["si"]        → Botón SEL
        cbs["no"]        → Botón BACK
        cbs["abort"]     → Botón ABORT
    """
    def _locked(fn):
        def _wrapper():
            with state.lock:
                fn()
        return _wrapper

    return {
        "siguiente": _locked(lambda: state.mover(+1)),
        "anterior":  _locked(lambda: state.mover(-1)),
        "si":        _locked(state.sel),
        "no":        _locked(state.no),
        "abort":     _locked(state.abort),
    }

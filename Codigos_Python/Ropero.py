"""
Base de datos del Sistema de Percheros Inteligentes.
Proyecto MT-7003 Microprocesadores y Microcontroladores - I Sem 2026
TEC - Ingeniería Mecatrónica.

Este módulo expone tres clases:
  - Prenda:    una prenda con las 5 características obligatorias del PDF.
  - Perchero:  un perchero rotatorio con 5 slots fijos (uno por ángulo físico).
  - Sistema:   contiene 3 percheros y todas las prendas conocidas; ofrece
               los modos de uso (colocar / extraer / informar / eliminar)
               y persistencia en archivo .txt.

Cumple la rúbrica del PDF: validación de dominios, los 4 errores lógicos,
extracción por nombre y por características, reportes y estado .txt único.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import os


# ─────────────────────────────────────────
# Constantes de dominio (PDF §II)
# ─────────────────────────────────────────

TIPOS_PRENDA = ("T-Shirt", "Pantalón", "Short", "Camisa Manga Larga", "Enagua")
COLORES      = ("Blanco", "Negro", "Café", "Azul", "Gris")
TIPOS_TELA   = ("Denim", "Algodón", "Polyester", "Seda", "Lino")
TALLAS       = ("S", "M", "L", "XL", "XXL")
FITS         = ("Regular", "Skinny", "Slim", "Loose", "Oversized")

NUM_PERCHEROS      = 3   # PDF §II: máximo 3 percheros
SLOTS_POR_PERCHERO = 5   # PDF §II: hasta 5 prendas por perchero

ARCHIVO_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estado.txt")


# ─────────────────────────────────────────
# Resultado uniforme de operaciones
# ─────────────────────────────────────────

@dataclass
class Resultado:
    """Resultado uniforme de toda operación de la base de datos.

    La interfaz lee `ok` para saber si la operación tuvo éxito y `mensaje`
    para mostrarle al usuario. `datos` lleva información extra (la prenda
    afectada, el slot usado, etc.) cuando aplica.
    """
    ok: bool
    mensaje: str
    datos: object = None

    @classmethod
    def exitoso(cls, mensaje: str = "OK", datos=None) -> "Resultado":
        return cls(True, mensaje, datos)

    @classmethod
    def error(cls, mensaje: str) -> "Resultado":
        return cls(False, mensaje)


# ─────────────────────────────────────────
# Modelo: Prenda
# ─────────────────────────────────────────

class Prenda:
    """Una prenda con las 5 características obligatorias + nombre único + id.

    El nombre único lo asigna el usuario; el id lo asigna el Sistema con un
    contador interno (_proximo_id) para evitar colisiones.
    """

    def __init__(self, id: int, nombre: str, tipo: str, color: str,
                 tela: str, talla: str, fit: str):
        self._validar(tipo, color, tela, talla, fit)
        self.id = id
        self.nombre = nombre
        self.tipo = tipo
        self.color = color
        self.tela = tela
        self.talla = talla
        self.fit = fit

    @staticmethod
    def _validar(tipo: str, color: str, tela: str, talla: str, fit: str):
        """Rechaza atributos fuera de los dominios del PDF."""
        if tipo not in TIPOS_PRENDA:
            raise ValueError(f"tipo '{tipo}' inválido. Debe ser uno de {TIPOS_PRENDA}")
        if color not in COLORES:
            raise ValueError(f"color '{color}' inválido. Debe ser uno de {COLORES}")
        if tela not in TIPOS_TELA:
            raise ValueError(f"tela '{tela}' inválida. Debe ser uno de {TIPOS_TELA}")
        if talla not in TALLAS:
            raise ValueError(f"talla '{talla}' inválida. Debe ser uno de {TALLAS}")
        if fit not in FITS:
            raise ValueError(f"fit '{fit}' inválido. Debe ser uno de {FITS}")

    def coincide(self, tipo: str, color: Optional[str] = None,
                 tela: Optional[str] = None, talla: Optional[str] = None,
                 fit: Optional[str] = None) -> bool:
        """¿Coincide con la búsqueda por características? (PDF §III).

        El tipo es obligatorio (lo dice el PDF). El resto es opcional;
        cualquier atributo None se ignora (acepta cualquier valor).
        """
        if self.tipo != tipo:
            return False
        if color is not None and self.color != color: return False
        if tela  is not None and self.tela  != tela:  return False
        if talla is not None and self.talla != talla: return False
        if fit   is not None and self.fit   != fit:   return False
        return True

    def __repr__(self) -> str:
        return (f"Prenda(id={self.id}, nombre='{self.nombre}', tipo={self.tipo}, "
                f"color={self.color}, tela={self.tela}, talla={self.talla}, fit={self.fit})")


# ─────────────────────────────────────────
# Modelo: Perchero
# ─────────────────────────────────────────

class Perchero:
    """Perchero rotatorio con 5 slots fijos.

    slots[i] = id de la prenda en la posición i, o None si está vacío.
    El índice i ∈ {0,1,2,3,4} corresponde al ángulo físico
        theta_i = i * (360 / 5) = i * 72°
    que el ESP32 debe mover el motor para presentar ese slot al usuario.
    """

    def __init__(self, id: int):
        self.id = id  # 1, 2 o 3
        self.slots: list[Optional[int]] = [None] * SLOTS_POR_PERCHERO

    # ─── consultas ─────────────────────────

    def tiene_espacio(self) -> bool:
        return None in self.slots

    def primer_slot_libre(self) -> Optional[int]:
        """Índice del primer slot vacío, o None si está lleno."""
        for i, p in enumerate(self.slots):
            if p is None:
                return i
        return None

    def contiene(self, prenda_id: int) -> bool:
        return prenda_id in self.slots

    def slot_de(self, prenda_id: int) -> Optional[int]:
        try:
            return self.slots.index(prenda_id)
        except ValueError:
            return None

    def prendas_ids(self) -> list[int]:
        return [p for p in self.slots if p is not None]

    def angulo_de(self, slot_idx: int) -> float:
        """Ángulo físico que el motor debe alcanzar para mostrar ese slot."""
        return slot_idx * (360.0 / SLOTS_POR_PERCHERO)

    # ─── mutaciones ────────────────────────

    def colocar(self, prenda_id: int) -> Optional[int]:
        """Coloca la prenda en el primer slot libre.
        Devuelve el índice usado, o None si el perchero estaba lleno.
        """
        i = self.primer_slot_libre()
        if i is None:
            return None
        self.slots[i] = prenda_id
        return i

    def extraer(self, prenda_id: int) -> Optional[int]:
        """Quita la prenda del perchero. Devuelve el slot que liberó,
        o None si la prenda no estaba aquí.
        """
        i = self.slot_de(prenda_id)
        if i is None:
            return None
        self.slots[i] = None
        return i

    def __repr__(self) -> str:
        return f"Perchero(id={self.id}, slots={self.slots})"


# ─────────────────────────────────────────
# Sistema (la base de datos completa)
# ─────────────────────────────────────────

class Sistema:
    """Estado completo del sistema de Percheros Inteligentes.

    Reemplaza la antigua clase Ropero + RoperoRepo y los 3 archivos JSON.
    Una sola instancia maneja todo el estado y lo persiste en un único .txt.
    """

    def __init__(self):
        self._prendas: dict[int, Prenda] = {}
        self._percheros: list[Perchero] = [Perchero(i + 1) for i in range(NUM_PERCHEROS)]
        self._proximo_id: int = 1

    # ─── acceso ────────────────────────────

    def perchero(self, perchero_id: int) -> Perchero:
        """Devuelve el perchero con id 1..NUM_PERCHEROS (1-indexed como en el PDF)."""
        if not 1 <= perchero_id <= NUM_PERCHEROS:
            raise ValueError(f"perchero_id debe estar entre 1 y {NUM_PERCHEROS}")
        return self._percheros[perchero_id - 1]

    def prendas(self) -> list[Prenda]:
        """Todas las prendas conocidas por el sistema."""
        return list(self._prendas.values())

    def percheros(self) -> list[Perchero]:
        return self._percheros

    def nombres_unicos(self) -> list[str]:
        """Lista de nombres únicos para que la interfaz arme dropdowns."""
        return [p.nombre for p in self._prendas.values()]

    def buscar_prenda_por_id(self, prenda_id: int) -> Optional[Prenda]:
        """Devuelve la prenda con ese id, o None si no existe."""
        return self._prendas.get(prenda_id)

    def _buscar_prenda_por_nombre(self, nombre: str) -> Optional[Prenda]:
        return next((p for p in self._prendas.values() if p.nombre == nombre), None)

    def _perchero_que_contiene(self, prenda_id: int) -> Optional[Perchero]:
        return next((p for p in self._percheros if p.contiene(prenda_id)), None)

    # ─── modos de uso (PDF §III) ───────────

    def colocar_prenda_nueva(self, nombre: str, tipo: str, color: str,
                             tela: str, talla: str, fit: str,
                             perchero_id: int) -> Resultado:
        """Modo 'Colocar Prenda Nueva' (PDF §III).

        Detecta los errores lógicos #1, #2 y #3.
        """
        # Error #3: nombre único repetido
        if self._buscar_prenda_por_nombre(nombre) is not None:
            return Resultado.error(f"Ya existe una prenda con el nombre '{nombre}'")

        # Error #1: no hay espacio en todos los percheros
        if not any(p.tiene_espacio() for p in self._percheros):
            return Resultado.error("No hay espacio en ningún perchero")

        # Error #2: no hay espacio en el perchero específico
        try:
            per = self.perchero(perchero_id)
        except ValueError as e:
            return Resultado.error(str(e))
        if not per.tiene_espacio():
            return Resultado.error(f"El perchero {perchero_id} está lleno")

        # Validar dominios y crear (puede lanzar ValueError)
        try:
            prenda = Prenda(self._proximo_id, nombre, tipo, color, tela, talla, fit)
        except ValueError as e:
            return Resultado.error(str(e))

        self._prendas[prenda.id] = prenda
        self._proximo_id += 1
        slot = per.colocar(prenda.id)
        return Resultado.exitoso(
            f"Prenda '{nombre}' colocada en perchero {perchero_id} slot {slot}",
            datos={"prenda": prenda, "perchero": perchero_id, "slot": slot},
        )

    def colocar_prenda_previa(self, nombre: str, perchero_id: int) -> Resultado:
        """Modo 'Colocar Prenda Previa' (PDF §III).

        Detecta los 4 errores lógicos.
        """
        prenda = self._buscar_prenda_por_nombre(nombre)
        if prenda is None:
            return Resultado.error(f"No existe ninguna prenda llamada '{nombre}'")

        # Error #4: la prenda previa ya está guardada en un perchero
        if self._perchero_que_contiene(prenda.id) is not None:
            return Resultado.error(f"La prenda '{nombre}' ya está en un perchero")

        # Error #1
        if not any(p.tiene_espacio() for p in self._percheros):
            return Resultado.error("No hay espacio en ningún perchero")

        # Error #2
        try:
            per = self.perchero(perchero_id)
        except ValueError as e:
            return Resultado.error(str(e))
        if not per.tiene_espacio():
            return Resultado.error(f"El perchero {perchero_id} está lleno")

        slot = per.colocar(prenda.id)
        return Resultado.exitoso(
            f"Prenda '{nombre}' colocada en perchero {perchero_id} slot {slot}",
            datos={"prenda": prenda, "perchero": perchero_id, "slot": slot},
        )

    def extraer_por_nombre(self, nombre: str) -> Resultado:
        """Modo 'Extraer por Nombre Único' (PDF §III).

        Solo quita la prenda del perchero; sigue siendo prenda conocida.
        """
        prenda = self._buscar_prenda_por_nombre(nombre)
        if prenda is None:
            return Resultado.error(f"No existe ninguna prenda llamada '{nombre}'")
        per = self._perchero_que_contiene(prenda.id)
        if per is None:
            return Resultado.error(f"La prenda '{nombre}' no está en ningún perchero")
        slot = per.extraer(prenda.id)
        return Resultado.exitoso(
            f"Prenda '{nombre}' extraída del perchero {per.id} slot {slot}",
            datos={"prenda": prenda, "perchero": per.id, "slot": slot},
        )

    def extraer_por_caracteristicas(self, tipo: str, color: Optional[str] = None,
                                    tela: Optional[str] = None,
                                    talla: Optional[str] = None,
                                    fit: Optional[str] = None) -> Resultado:
        """Modo 'Extraer por Características' (PDF §III).

        Tipo es obligatorio; del 1 al 5 atributos según el PDF.
        Devuelve la primera prenda en perchero que coincida y la extrae.
        """
        for prenda in self._prendas.values():
            per = self._perchero_que_contiene(prenda.id)
            if per is None:
                continue  # solo se puede extraer lo que esté en perchero
            if prenda.coincide(tipo, color, tela, talla, fit):
                slot = per.extraer(prenda.id)
                return Resultado.exitoso(
                    f"Prenda '{prenda.nombre}' extraída del perchero {per.id} slot {slot}",
                    datos={"prenda": prenda, "perchero": per.id, "slot": slot},
                )
        return Resultado.error("No hay ninguna prenda disponible que coincida")

    def eliminar_prenda(self, nombre: str) -> Resultado:
        """Modo 'Eliminar Prenda' (PDF §IV/Interfaz).

        Solo elimina si la prenda no está en ningún perchero.
        """
        prenda = self._buscar_prenda_por_nombre(nombre)
        if prenda is None:
            return Resultado.error(f"No existe ninguna prenda llamada '{nombre}'")
        if self._perchero_que_contiene(prenda.id) is not None:
            return Resultado.error(
                f"No se puede eliminar '{nombre}': está colocada en un perchero. "
                f"Extráela primero."
            )
        del self._prendas[prenda.id]
        return Resultado.exitoso(f"Prenda '{nombre}' eliminada del sistema")

    # ─── reportes (PDF §III, Información de Prendas) ───

    def prendas_actuales_por_perchero(self) -> dict[int, list[Prenda]]:
        """Diagrama de prendas actuales en cada perchero (rúbrica 5 pts)."""
        return {
            per.id: [self._prendas[pid] for pid in per.slots if pid is not None]
            for per in self._percheros
        }

    def prendas_conocidas_no_almacenadas(self) -> list[Prenda]:
        """Prendas conocidas por el sistema que NO están en ningún perchero (rúbrica 3 pts)."""
        almacenadas = {pid for per in self._percheros for pid in per.prendas_ids()}
        return [p for p in self._prendas.values() if p.id not in almacenadas]

    def informe_por_caracteristicas(self, tipo: Optional[str] = None,
                                    color: Optional[str] = None,
                                    tela: Optional[str] = None,
                                    talla: Optional[str] = None,
                                    fit: Optional[str] = None) -> list[Prenda]:
        """Listado filtrado por cualquier combinación de las 5 características (rúbrica 5 pts).

        A diferencia de extraer_por_caracteristicas, aquí 'tipo' NO es obligatorio,
        para soportar consultas como 'todas las prendas blancas'.
        """
        def cumple(p: Prenda) -> bool:
            if tipo  is not None and p.tipo  != tipo:  return False
            if color is not None and p.color != color: return False
            if tela  is not None and p.tela  != tela:  return False
            if talla is not None and p.talla != talla: return False
            if fit   is not None and p.fit   != fit:   return False
            return True
        return [p for p in self._prendas.values() if cumple(p)]

    # ─── persistencia (PDF §III, Escritura y Carga de Estado) ───

    def guardar_estado(self, filepath: str = ARCHIVO_ESTADO) -> Resultado:
        """Guarda todo el estado del sistema en un único archivo .txt legible."""
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("# Estado del Sistema de Percheros Inteligentes\n")
                f.write("# Generado automáticamente; editar a mano puede dañar el formato.\n\n")

                f.write("[meta]\n")
                f.write(f"proximo_id={self._proximo_id}\n\n")

                f.write("[prendas]\n")
                f.write("# id|nombre|tipo|color|tela|talla|fit\n")
                for p in self._prendas.values():
                    f.write(f"{p.id}|{p.nombre}|{p.tipo}|{p.color}|"
                            f"{p.tela}|{p.talla}|{p.fit}\n")
                f.write("\n")

                for per in self._percheros:
                    f.write(f"[perchero_{per.id}]\n")
                    f.write("# slot_idx|prenda_id   (vacío si slot vacío)\n")
                    for i, pid in enumerate(per.slots):
                        f.write(f"{i}|{pid if pid is not None else ''}\n")
                    f.write("\n")
            return Resultado.exitoso(f"Estado guardado en '{filepath}'")
        except OSError as e:
            return Resultado.error(f"Error al guardar: {e}")

    def cargar_estado(self, filepath: str = ARCHIVO_ESTADO) -> Resultado:
        """Reemplaza el estado actual por el del archivo .txt."""
        if not os.path.exists(filepath):
            return Resultado.error(f"El archivo '{filepath}' no existe")

        # Estado provisional: si algo falla, no dañamos el sistema actual
        nuevas_prendas: dict[int, Prenda] = {}
        nuevos_percheros: list[Perchero] = [Perchero(i + 1) for i in range(NUM_PERCHEROS)]
        nuevo_proximo_id: int = 1
        seccion: Optional[str] = None

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for num_linea, linea in enumerate(f, start=1):
                    linea = linea.strip()
                    if not linea or linea.startswith("#"):
                        continue
                    if linea.startswith("[") and linea.endswith("]"):
                        seccion = linea[1:-1]
                        continue

                    if seccion == "meta":
                        clave, _, valor = linea.partition("=")
                        if clave.strip() == "proximo_id":
                            nuevo_proximo_id = int(valor.strip())

                    elif seccion == "prendas":
                        partes = linea.split("|")
                        if len(partes) != 7:
                            raise ValueError(
                                f"línea {num_linea}: la prenda debe tener 7 campos"
                            )
                        id_, nombre, tipo, color, tela, talla, fit = partes
                        prenda = Prenda(int(id_), nombre, tipo, color, tela, talla, fit)
                        nuevas_prendas[prenda.id] = prenda

                    elif seccion and seccion.startswith("perchero_"):
                        per_id = int(seccion.split("_")[1])
                        if not 1 <= per_id <= NUM_PERCHEROS:
                            raise ValueError(f"perchero_id {per_id} fuera de rango")
                        slot_idx, _, pid_str = linea.partition("|")
                        i = int(slot_idx)
                        if not 0 <= i < SLOTS_POR_PERCHERO:
                            raise ValueError(f"slot_idx {i} fuera de rango")
                        nuevos_percheros[per_id - 1].slots[i] = (
                            int(pid_str) if pid_str.strip() else None
                        )
            # Validar consistencia: ids referenciados existen
            for per in nuevos_percheros:
                for pid in per.prendas_ids():
                    if pid not in nuevas_prendas:
                        raise ValueError(
                            f"perchero {per.id} referencia prenda id={pid} inexistente"
                        )

            # Todo OK: reemplazamos el estado de golpe
            self._prendas = nuevas_prendas
            self._percheros = nuevos_percheros
            self._proximo_id = nuevo_proximo_id
            return Resultado.exitoso(f"Estado cargado desde '{filepath}'")

        except (OSError, ValueError) as e:
            return Resultado.error(f"Error al cargar: {e}")

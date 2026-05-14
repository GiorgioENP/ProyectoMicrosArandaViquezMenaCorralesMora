"""
main.py — Pruebas funcionales del módulo Ropero.

Ejercita cada funcionalidad exigida por el PDF y reporta éxito/fallo.
Sirve como:
  1. Verificación de que la base de datos cumple los requisitos.
  2. Ejemplo de cómo la interfaz (Tkinter, web, etc.) debe usar el Sistema.

Cuando lo corras verás:
  - Cada operación que se intenta y su resultado.
  - Comprobaciones automáticas (asserts) que truenan si algo está mal.
  - El archivo estado.txt generado.

Para ejecutar:
    python main.py
"""

from Ropero import Sistema


def imprimir_titulo(t: str):
    print(f"\n{'═' * 60}\n  {t}\n{'═' * 60}")


def imprimir_estado(sis: Sistema):
    """Muestra el 'diagrama' de prendas actuales en cada perchero."""
    print("\n┌── ESTADO ACTUAL ──")
    for per_id, prendas in sis.prendas_actuales_por_perchero().items():
        slots = sis.perchero(per_id).slots
        descripcion_slots = [
            f"[{i}]={'vacío' if pid is None else sis._prendas[pid].nombre}"
            for i, pid in enumerate(slots)
        ]
        print(f"│ Perchero {per_id}: {' '.join(descripcion_slots)}")
    no_almacenadas = sis.prendas_conocidas_no_almacenadas()
    if no_almacenadas:
        print(f"│ Conocidas sin perchero: {[p.nombre for p in no_almacenadas]}")
    else:
        print("│ Conocidas sin perchero: (ninguna)")
    print("└──────────────────")


def main():
    sis = Sistema()

    # ─────────────────────────────────────────
    imprimir_titulo("1) Colocar Prenda Nueva (modo 1 del PDF)")
    # ─────────────────────────────────────────

    r = sis.colocar_prenda_nueva("camisa-azul", "Camisa Manga Larga",
                                  "Azul", "Algodón", "M", "Regular", 1)
    print(r.mensaje); assert r.ok

    r = sis.colocar_prenda_nueva("jean-negro", "Pantalón",
                                  "Negro", "Denim", "L", "Slim", 1)
    print(r.mensaje); assert r.ok

    r = sis.colocar_prenda_nueva("short-blanco", "Short",
                                  "Blanco", "Algodón", "S", "Loose", 2)
    print(r.mensaje); assert r.ok

    imprimir_estado(sis)

    # ─────────────────────────────────────────
    imprimir_titulo("2) Detección de errores lógicos (PDF §III)")
    # ─────────────────────────────────────────

    print("\n→ Error #3: nombre único repetido")
    r = sis.colocar_prenda_nueva("camisa-azul", "T-Shirt",
                                  "Blanco", "Algodón", "S", "Regular", 3)
    print(r.mensaje); assert not r.ok

    print("\n→ Error: dominio inválido (color 'rosado' no existe)")
    r = sis.colocar_prenda_nueva("invalida", "T-Shirt",
                                  "rosado", "Algodón", "S", "Regular", 3)
    print(r.mensaje); assert not r.ok

    print("\n→ Llenar perchero 3 para probar errores #1 y #2")
    for i in range(5):
        r = sis.colocar_prenda_nueva(f"relleno3-{i}", "T-Shirt",
                                      "Gris", "Polyester", "S", "Regular", 3)
        assert r.ok
    print("Perchero 3 lleno.")

    print("\n→ Error #2: no hay espacio en el perchero específico")
    r = sis.colocar_prenda_nueva("rebote", "T-Shirt",
                                  "Gris", "Polyester", "S", "Regular", 3)
    print(r.mensaje); assert not r.ok

    print("\n→ Llenar percheros 1 y 2 para probar el error #1")
    nombres_relleno = []
    for per_id in [1, 2]:
        per = sis.perchero(per_id)
        while per.tiene_espacio():
            n = f"relleno{per_id}-{len(nombres_relleno)}"
            r = sis.colocar_prenda_nueva(n, "T-Shirt",
                                          "Gris", "Polyester", "S", "Regular", per_id)
            assert r.ok
            nombres_relleno.append(n)
    print("\n→ Error #1: no hay espacio en ningún perchero")
    r = sis.colocar_prenda_nueva("nope", "T-Shirt",
                                  "Gris", "Polyester", "S", "Regular", 1)
    print(r.mensaje); assert not r.ok

    # ─────────────────────────────────────────
    imprimir_titulo("3) Extraer por nombre y volver a colocar (prenda previa)")
    # ─────────────────────────────────────────

    r = sis.extraer_por_nombre("camisa-azul")
    print(r.mensaje); assert r.ok
    imprimir_estado(sis)

    r = sis.colocar_prenda_previa("camisa-azul", 1)
    print(r.mensaje); assert r.ok

    print("\n→ Error #4: la prenda previa ya está en un perchero")
    r = sis.colocar_prenda_previa("camisa-azul", 2)
    print(r.mensaje); assert not r.ok

    # ─────────────────────────────────────────
    imprimir_titulo("4) Extraer por características (PDF §III)")
    # ─────────────────────────────────────────

    print("\n→ Solo tipo (mínimo 1 atributo, tipo obligatorio)")
    r = sis.extraer_por_caracteristicas("Pantalón")
    print(r.mensaje); assert r.ok

    print("\n→ Tipo + color + tela + talla + fit (5 atributos)")
    r = sis.extraer_por_caracteristicas("Short", "Blanco", "Algodón", "S", "Loose")
    print(r.mensaje); assert r.ok

    print("\n→ Búsqueda sin coincidencia (Enagua roja inexistente)")
    r = sis.extraer_por_caracteristicas("Enagua")
    print(r.mensaje); assert not r.ok

    # ─────────────────────────────────────────
    imprimir_titulo("5) Reportes (Información de Prendas)")
    # ─────────────────────────────────────────

    print("\n→ Prendas conocidas no almacenadas:")
    for p in sis.prendas_conocidas_no_almacenadas():
        print(f"   · {p.nombre}  ({p.tipo} {p.color})")

    print("\n→ Informe filtrado: todas las prendas grises")
    for p in sis.informe_por_caracteristicas(color="Gris"):
        print(f"   · {p}")

    print("\n→ Informe filtrado: T-Shirt grises talla S")
    for p in sis.informe_por_caracteristicas(tipo="T-Shirt", color="Gris", talla="S"):
        print(f"   · {p.nombre}")

    # ─────────────────────────────────────────
    imprimir_titulo("6) Eliminar prenda (PDF §IV)")
    # ─────────────────────────────────────────

    print("\n→ No se puede eliminar mientras está en perchero")
    r = sis.eliminar_prenda("camisa-azul")
    print(r.mensaje); assert not r.ok

    print("\n→ La extraemos y luego sí podemos eliminar")
    sis.extraer_por_nombre("camisa-azul")
    r = sis.eliminar_prenda("camisa-azul")
    print(r.mensaje); assert r.ok

    # ─────────────────────────────────────────
    imprimir_titulo("7) Persistencia .txt (PDF §III)")
    # ─────────────────────────────────────────

    r = sis.guardar_estado("estado.txt")
    print(r.mensaje); assert r.ok

    sis2 = Sistema()
    r = sis2.cargar_estado("estado.txt")
    print(r.mensaje); assert r.ok

    # Verificación: el estado cargado coincide con el guardado
    nombres_originales  = sorted(p.nombre for p in sis.prendas())
    nombres_recuperados = sorted(p.nombre for p in sis2.prendas())
    assert nombres_originales == nombres_recuperados, "El estado cargado no coincide!"
    print(f"✓ Estado restaurado correctamente ({len(nombres_recuperados)} prendas).")

    print("\n" + "═" * 60)
    print("  TODAS LAS PRUEBAS PASARON ✓")
    print("═" * 60)


if __name__ == "__main__":
    main()

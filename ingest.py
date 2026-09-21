"""
Paso 1 — Ingesta.

Carga las 5 hojas SIN alterar un solo valor, y captura la información que
no es texto: el color de fuente, que es como el equipo marca hoy los datos
supuestos y que se perdería con pd.read_csv o con un export a CSV.

El origen (archivo .xlsx o Google Sheets en vivo) lo decide readers.py.
Este módulo no sabe ni le importa cuál se usó.

Este módulo NO normaliza, NO limpia y NO descarta filas. Eso es el paso 3.
Si tocara algo aquí, el perfilado del paso 2 mediría datos ya modificados
y perdería su valor como evidencia.

Uso:
    from ingest import cargar_todo
    hojas, marcas = cargar_todo()                  # elige el origen solo
    hojas, marcas = cargar_todo(origen="xlsx")     # fuerza el archivo
    hojas, marcas = cargar_todo(origen="sheets")   # fuerza la hoja viva
"""

from readers import obtener_lector


# ---------------------------------------------------------------------------
# Materializar la columna `assumptions`
# ---------------------------------------------------------------------------

def agregar_assumptions(hojas, marcas):
    """
    Crea la columna `assumptions` en cada hoja: una lista simple con los
    nombres de las columnas marcadas en esa fila.

        assumptions = ['Power']

    El quién / cuándo / con qué fuente NO se guarda aquí: vive en audit_log,
    que hay que construir de todos modos para el requisito 3.

    OJO PASO 3: aquí los nombres son los encabezados CRUDOS ('Power').
    Cuando el paso 3 renombre las columnas a canónicas (power_amps), tiene
    que renombrar también lo que está dentro de esta lista, con el mismo
    mapa de aliases.
    """
    por_fila = {}
    for m in marcas:
        por_fila.setdefault((m["hoja"], m["fila_excel"]), []).append(m["columna"])

    for nombre, df in hojas.items():
        if df.empty:
            continue
        df["assumptions"] = [
            por_fila.get((nombre, fila), []) for fila in df["_fila_excel"]
        ]

    return hojas


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------

def resumen_carga(hojas, marcas, lector):
    print(f"ORIGEN: {lector.descripcion()}\n")

    print("CARGA")
    for nombre, df in hojas.items():
        # -3 por las columnas de control (_hoja, _fila_excel, assumptions)
        n_cols = len(df.columns) - 3
        print(f"  {nombre:<22} {len(df):>2} filas x {n_cols:>2} columnas")

    print("\nCOLORES DE FUENTE Y RELLENOS (semillas de supuestos)")
    if not marcas:
        print("  ninguno")
    for m in marcas:
        detalle = m["color_fuente"] or f"relleno {m['relleno']}"
        print(f"  {m['hoja']}!{m['celda']}  {m['columna']} = {m['valor']!r}  {detalle}")
    print(f"  {len(marcas)} celda(s) marcada(s)")

    con_marca = sum(
        (df["assumptions"].str.len() > 0).sum()
        for df in hojas.values() if not df.empty
    )
    print(f"\nFILAS CON AL MENOS UN SUPUESTO: {con_marca}")


def cargar_todo(origen=None, verbose=True):
    lector = obtener_lector(forzar=origen)
    hojas, marcas = lector.leer()
    hojas = agregar_assumptions(hojas, marcas)
    if verbose:
        resumen_carga(hojas, marcas, lector)
    return hojas, marcas


if __name__ == "__main__":
    cargar_todo()
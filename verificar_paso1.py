"""
Paso 1 — Verificación.

Tres comprobaciones concretas sobre la ingesta. Si alguna falla, el
paso 1 está mal y no tiene sentido seguir al 2.

Uso:  python verificar_paso1.py      (o  %run verificar_paso1.py  en el notebook)
"""

import sys

from ingest import cargar_todo

# Forzamos el .xlsx: estas comprobaciones son sobre el contenido conocido
# del archivo. Para verificar el lector de Sheets, usa origen="sheets".
ORIGEN = sys.argv[1] if len(sys.argv) > 1 else "xlsx"
hojas, marcas = cargar_todo(origen=ORIGEN, verbose=False)
fallos = []


def check(nombre, condicion, detalle=""):
    print(f"  [{'OK ' if condicion else 'FALLA'}] {nombre}")
    if detalle:
        print(f"         {detalle}")
    if not condicion:
        fallos.append(nombre)


print("VERIFICACIÓN DEL PASO 1\n")

# --- 1. Las columnas fantasma llegaron -------------------------------------
# catering tiene 25 columnas y la última no tiene encabezado. Si el lector
# las descartara, estaríamos perdiendo datos en silencio sin enterarnos.
cat = hojas["catering_clientB_jfk"]
sin_encabezado = [c for c in cat.columns if c.startswith("__col_")]
check(
    "las columnas sin encabezado se conservan",
    len(sin_encabezado) >= 1,
    f"catering: {len(cat.columns) - 3} columnas de datos, "
    f"sin encabezado: {sin_encabezado}",
)

# --- 2. No se convirtieron tipos -------------------------------------------
# 'Power' debe llegar como TEXTO '400A'. Si llega como 400 (int), el lector
# está interpretando y hay que frenarlo: perderíamos la unidad.
av = hojas["av_clientC_atl"]
power = av.loc[av["_fila_excel"] == 2, "Power"].iloc[0]
check(
    "los valores llegan sin convertir",
    isinstance(power, str) and power == "400A",
    f"av!O2 Power = {power!r}  (tipo {type(power).__name__})",
)

# El valor con coma también debe seguir siendo texto
cat_power = cat.loc[cat["_fila_excel"] == 3, "Power"].iloc[0]
check(
    "el valor con coma se conserva intacto",
    isinstance(cat_power, str) and "," in cat_power,
    f"catering!Q3 Power = {cat_power!r}",
)

# --- 3. El escaneo de color encontró lo que debía --------------------------
check(
    "el escaneo encuentra exactamente 1 celda marcada",
    len(marcas) == 1,
    f"{len(marcas)} marca(s): "
    + ", ".join(f"{m['hoja']}!{m['celda']}" for m in marcas),
)

if marcas:
    m = marcas[0]
    check(
        "la marca está en av_clientC_atl!O2, columna Power",
        m["hoja"] == "av_clientC_atl" and m["celda"] == "O2" and m["columna"] == "Power",
        f"{m['hoja']}!{m['celda']} · {m['columna']} = {m['valor']!r} · {m['color_fuente']}",
    )
    # El color se normaliza a #RRGGBB en los dos lectores, para que el
    # resto del pipeline no tenga que saber de dónde vino el dato.
    check(
        "el color viene normalizado a #RRGGBB",
        m["color_fuente"] == "#0000FF",
        f"color_fuente = {m['color_fuente']}",
    )

# --- 4. La columna assumptions quedó bien construida -----------------------
fila_marcada = av.loc[av["_fila_excel"] == 2, "assumptions"].iloc[0]
check(
    "la fila marcada lista su columna en `assumptions`",
    fila_marcada == ["Power"],
    f"av fila 2 -> assumptions = {fila_marcada}",
)

otras = av.loc[av["_fila_excel"] != 2, "assumptions"]
check(
    "las demás filas tienen la lista vacía",
    all(len(a) == 0 for a in otras),
    f"{sum(len(a) == 0 for a in otras)} de {len(otras)} filas sin supuestos",
)

# --- 5. Nada se perdió en el camino ----------------------------------------
# Conteo de filas esperado por hoja (incluye las vacías: todavía no se limpia)
esperado = {
    "av_clientC_atl": 6,
    "food_clientA_nyc": 8,
    "contact_tracker": 5,
    "catering_clientB_jfk": 5,
    "target_weekly_view": 7,
}
reales = {n: len(df) for n, df in hojas.items()}
check(
    "el conteo de filas coincide con el archivo",
    reales == esperado,
    f"{reales}",
)

print()
if fallos:
    print(f"{len(fallos)} comprobación(es) fallida(s): {fallos}")
    sys.exit(1)
print("Paso 1 verificado. Listo para el paso 2.")
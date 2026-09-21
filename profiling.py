"""
Paso 2 — Perfilado de calidad.

REGLA DEL MÓDULO: aquí no se modifica un solo dato. Este paso produce
EVIDENCIA, no datos limpios. La limpieza (paso 3) se escribe después,
guiada por lo que este informe encuentre.

Si se perfilara y limpiara a la vez, al terminar no se podría decir cuánta
suciedad había. El brief pide explícitamente contar qué se encontró y qué
se decidió: separarlos es lo que hace posible esa respuesta.

Nueve perfiles:
  1. estructura        ¿está bien formada la tabla?
  2. completitud       ¿qué tan llena está?
  3. validez           ¿los valores son del tipo que dicen ser?
  4. unicidad          ¿hay cosas repetidas?
  5. outliers          ¿hay valores raros? (señalar, NO corregir)
  6. derivados         ¿puedo confiar en las columnas calculadas?
  7. notas             ¿qué hay escondido en el texto libre?
  8. cobertura         ¿en qué se parecen las hojas ENTRE SÍ?  -> prepara la unión
  9. trazabilidad      ¿target_weekly_view se reconstruye desde los datos?

Uso:
    from profiling import perfilar
    hallazgos = perfilar(hojas)
"""

import re
from pathlib import Path

import pandas as pd
import yaml

SCHEMA = yaml.safe_load(Path("config/schema.yaml").read_text(encoding="utf-8"))
CONTROL = {"_hoja", "_fila_excel", "assumptions"}

HOJAS_BUSQUEDA = [
    s for v in SCHEMA["verticals"].values() for s in v["sheets"]
]
HOJA_OBJETIVO = "target_weekly_view"


def _normalizar_encabezado(texto):
    t = str(texto).strip().lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _mapa_aliases():
    """encabezado normalizado -> campo canónico"""
    mapa = {}
    for campo, cfg in SCHEMA["fields"].items():
        for alias in cfg.get("aliases", []):
            mapa[_normalizar_encabezado(alias)] = campo
    return mapa


ALIASES = _mapa_aliases()


def _columnas_datos(df):
    return [c for c in df.columns if c not in CONTROL]


def _fila_vacia(fila, columnas):
    return all(pd.isna(fila[c]) for c in columnas)


# ===========================================================================
# 1. ESTRUCTURA
# ===========================================================================

def perfil_estructura(hojas):
    print("\n" + "=" * 70)
    print("1. ESTRUCTURA — ¿está bien formada la tabla?")
    print("=" * 70)

    hallazgos = {}
    for nombre, df in hojas.items():
        cols = _columnas_datos(df)
        sin_encabezado = [c for c in cols if c.startswith("__col_")]
        vacias_del_todo = [c for c in cols if df[c].isna().all()]
        filas_vacias = [
            int(df.loc[i, "_fila_excel"])
            for i in df.index
            if _fila_vacia(df.loc[i], cols)
        ]

        hallazgos[nombre] = {
            "filas": len(df),
            "columnas": len(cols),
            "sin_encabezado": sin_encabezado,
            "columnas_vacias": vacias_del_todo,
            "filas_vacias": filas_vacias,
        }

        print(f"\n  {nombre}")
        print(f"    {len(df)} filas x {len(cols)} columnas")
        if sin_encabezado:
            print(f"    columnas sin encabezado: {sin_encabezado}")
        if vacias_del_todo:
            print(f"    columnas 100% vacías: {vacias_del_todo}")
        if filas_vacias:
            print(f"    filas completamente vacías (fila del archivo): {filas_vacias}")
        if not (sin_encabezado or vacias_del_todo or filas_vacias):
            print("    sin anomalías estructurales")

    return hallazgos


# ===========================================================================
# 2. COMPLETITUD
# ===========================================================================

def perfil_completitud(hojas):
    print("\n" + "=" * 70)
    print("2. COMPLETITUD — ¿qué tan llena está?")
    print("=" * 70)
    print("\n  % de celdas CON dato, por columna canónica y hoja")
    print("  (— = la columna no existe en esa hoja)\n")

    tabla = {}
    for nombre in HOJAS_BUSQUEDA:
        df = hojas[nombre]
        cols = _columnas_datos(df)
        # excluir filas totalmente vacías del denominador: no son registros
        validas = df[[not _fila_vacia(df.loc[i], cols) for i in df.index]]
        for c in cols:
            canon = ALIASES.get(_normalizar_encabezado(c), c)
            pct = 0 if len(validas) == 0 else round(
                100 * validas[c].notna().sum() / len(validas)
            )
            tabla.setdefault(canon, {})[nombre] = pct

    etiquetas = {n: n.split("_")[0] for n in HOJAS_BUSQUEDA}
    ancho = max(len(c) for c in tabla) + 2
    print(f"  {'campo':<{ancho}}" + "".join(f"{etiquetas[n]:>10}" for n in HOJAS_BUSQUEDA))
    for canon in sorted(tabla, key=lambda c: -sum(tabla[c].values())):
        fila = "".join(
            f"{str(tabla[canon][n]) + '%':>10}" if n in tabla[canon] else f"{'—':>10}"
            for n in HOJAS_BUSQUEDA
        )
        print(f"  {canon:<{ancho}}{fila}")

    return tabla


# ===========================================================================
# 3. VALIDEZ
# ===========================================================================

CATEGORICOS = ["Status", "Property Type", "Sale Status"]


def perfil_validez(hojas):
    print("\n" + "=" * 70)
    print("3. VALIDEZ — ¿los valores son del tipo que dicen ser?")
    print("=" * 70)

    print("\n  Valores distintos en campos categóricos")
    categorias = {}
    for campo in CATEGORICOS:
        print(f"\n  {campo}:")
        todos = set()
        for nombre in HOJAS_BUSQUEDA:
            df = hojas[nombre]
            if campo not in df.columns:
                continue
            vals = sorted({str(v) for v in df[campo].dropna()})
            todos |= set(vals)
            print(f"    {nombre:<22} {', '.join(vals)}")
        categorias[campo] = sorted(todos)
        print(f"    >>> {len(todos)} valores distintos en total")

    print("\n  Valores que NO convierten al tipo esperado por el esquema")
    no_convierten = {}
    numericos = [
        c for c, cfg in SCHEMA["fields"].items()
        if cfg.get("type") in {"number", "integer", "money"}
    ]
    for nombre in HOJAS_BUSQUEDA:
        df = hojas[nombre]
        for c in _columnas_datos(df):
            canon = ALIASES.get(_normalizar_encabezado(c))
            if canon not in numericos:
                continue
            malos = []
            for v in df[c].dropna():
                try:
                    float(v)
                except (TypeError, ValueError):
                    malos.append(v)
            if malos:
                no_convierten.setdefault(canon, set()).update(malos)

    if not no_convierten:
        print("    ninguno")
    for canon, vals in sorted(no_convierten.items()):
        print(f"    {canon:<20} {sorted(vals)}")

    return {"categoricos": categorias, "no_convierten": no_convierten}


# ===========================================================================
# 4. UNICIDAD
# ===========================================================================

def perfil_unicidad(hojas):
    print("\n" + "=" * 70)
    print("4. UNICIDAD — ¿hay cosas repetidas?")
    print("=" * 70)

    hallazgos = {}

    print("\n  Direcciones repetidas (misma hoja)")
    hubo = False
    for nombre in HOJAS_BUSQUEDA:
        df = hojas[nombre]
        if "Address" not in df.columns:
            continue
        dirs = df["Address"].dropna()
        rep = dirs[dirs.duplicated(keep=False)]
        if len(rep):
            hubo = True
            print(f"    {nombre}: {sorted(set(rep))}")
    if not hubo:
        print("    ninguna")

    print("\n  Teléfonos en más de una fila")
    print("  (NO es error: un broker lleva varios deals. Importa para el join)")
    telefonos = {}
    for nombre in HOJAS_BUSQUEDA:
        df = hojas[nombre]
        if "Phone #" not in df.columns:
            continue
        for _, fila in df.iterrows():
            tel = fila.get("Phone #")
            if pd.isna(tel):
                continue
            telefonos.setdefault(str(tel), []).append(
                f"{nombre}!{fila['_fila_excel']} ({fila.get('Broker')})"
            )
    for tel, usos in sorted(telefonos.items()):
        if len(usos) > 1:
            print(f"    {tel}  ->  {', '.join(usos)}")
    hallazgos["telefonos"] = telefonos

    # cruce con contact_tracker: la clave de join es el teléfono, no el nombre
    print("\n  Cruce con contact_tracker (join por teléfono)")
    ct = hojas.get("contact_tracker")
    if ct is not None and not ct.empty:
        tels_ct = {}
        for _, fila in ct.iterrows():
            for col in ["Office #", "Mobile #"]:
                v = fila.get(col)
                if pd.notna(v):
                    tels_ct.setdefault(str(v), []).append(fila.get("Contact Name"))
        for tel, usos in telefonos.items():
            if tel in tels_ct:
                print(f"    {tel}")
                print(f"      en deals   : {', '.join(usos)}")
                print(f"      en tracker : {', '.join(tels_ct[tel])}")
                nombres_deal = {u.split("(")[-1].rstrip(")") for u in usos}
                if not (nombres_deal & set(tels_ct[tel])):
                    print("      >>> DISCREPANCIA: el nombre del broker no coincide")
        hallazgos["tels_tracker"] = tels_ct

    return hallazgos


# ===========================================================================
# 5. OUTLIERS
# ===========================================================================

RANGOS = {
    "days_on_market": (0, 365),
    "price_per_sf": (20, 800),
    "bldg_sf": (500, 500_000),
    "list_price": (100_000, 100_000_000),
    "miles_from_airport": (0, 50),
}


def perfil_outliers(hojas):
    print("\n" + "=" * 70)
    print("5. OUTLIERS — señalar, NO corregir")
    print("=" * 70)
    print("\n  Un dato real que 'se ve raro' sigue siendo un dato real.")
    print("  Esta sección los lista para revisión humana.\n")

    encontrados = []
    for nombre in HOJAS_BUSQUEDA:
        df = hojas[nombre]
        for c in _columnas_datos(df):
            canon = ALIASES.get(_normalizar_encabezado(c))
            if canon not in RANGOS:
                continue
            lo, hi = RANGOS[canon]
            for _, fila in df.iterrows():
                v = fila[c]
                if pd.isna(v):
                    continue
                try:
                    num = float(v)
                except (TypeError, ValueError):
                    continue
                if num < lo or num > hi:
                    encontrados.append({
                        "hoja": nombre,
                        "fila": int(fila["_fila_excel"]),
                        "campo": canon,
                        "valor": num,
                        "rango": (lo, hi),
                        "direccion": fila.get("Address"),
                    })

    if not encontrados:
        print("    ninguno")
    for e in encontrados:
        print(f"    {e['hoja']}!{e['fila']:<3} {e['campo']:<16} {e['valor']:>12,.2f}"
              f"   (rango esperado {e['rango'][0]}–{e['rango'][1]})")
        print(f"        {e['direccion']}")

    return encontrados


# ===========================================================================
# 6. DERIVADOS
# ===========================================================================

def perfil_derivados(hojas):
    print("\n" + "=" * 70)
    print("6. DERIVADOS — ¿puedo confiar en las columnas calculadas?")
    print("=" * 70)

    checks = [
        ("List Price/SF", "List Price", "Bldg SF"),
        ("List Price/Lot SF", "List Price", "Lot SF"),
    ]
    discrepancias = []
    total = 0

    for nombre in HOJAS_BUSQUEDA:
        df = hojas[nombre]
        for col_calc, col_num, col_den in checks:
            if not {col_calc, col_num, col_den} <= set(df.columns):
                continue
            for _, fila in df.iterrows():
                try:
                    origen = float(fila[col_calc])
                    num = float(fila[col_num])
                    den = float(fila[col_den])
                except (TypeError, ValueError):
                    continue
                if den == 0:
                    continue
                total += 1
                calculado = num / den
                if abs(calculado - origen) / max(abs(origen), 1e-9) > 0.01:
                    discrepancias.append({
                        "hoja": nombre,
                        "fila": int(fila["_fila_excel"]),
                        "campo": col_calc,
                        "origen": origen,
                        "calculado": round(calculado, 2),
                    })

    print(f"\n  {total} valores calculados verificados")
    if not discrepancias:
        print("  0 discrepancias — las columnas calculadas del origen son confiables")
        print("  (aun así se recalculan en el paso 3: es gratis y elimina la duda)")
    for d in discrepancias:
        print(f"    {d['hoja']}!{d['fila']} {d['campo']}: "
              f"origen {d['origen']} vs calculado {d['calculado']}")

    return discrepancias


# ===========================================================================
# 7. NOTAS
# ===========================================================================

PATRONES_NOTAS = {
    "monto": r"\$\s?[\d.,]+\s?[MmKk]?",
    "fecha": r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    "plazo_dias": r"\b\d+\s*day\b",
    "incertidumbre": r"\b(may |not |awaiting|no good|looking|yet|draft|tbd|approx|est\b|~)",
    "arriendo": r"\b(lease|rent|/yr|free rent)\b",
    "precio_negociado": r"\b(agreed|best and final|countered|responded|offer)\b",
}


def perfil_notas(hojas):
    print("\n" + "=" * 70)
    print("7. NOTAS — ¿qué hay escondido en el texto libre?")
    print("=" * 70)
    print("\n  No extrae nada todavía (eso es el paso 4): solo dimensiona.\n")

    conteos = {k: 0 for k in PATRONES_NOTAS}
    total_notas = 0
    ejemplos = {k: [] for k in PATRONES_NOTAS}

    for nombre in HOJAS_BUSQUEDA:
        df = hojas[nombre]
        if "Notes" not in df.columns:
            continue
        for _, fila in df.iterrows():
            nota = fila.get("Notes")
            if pd.isna(nota):
                continue
            total_notas += 1
            for clave, patron in PATRONES_NOTAS.items():
                if re.search(patron, str(nota), re.IGNORECASE):
                    conteos[clave] += 1
                    if len(ejemplos[clave]) < 2:
                        ejemplos[clave].append(
                            f"{nombre}!{int(fila['_fila_excel'])}: {nota}"
                        )

    print(f"  {total_notas} notas con contenido\n")
    for clave, n in sorted(conteos.items(), key=lambda x: -x[1]):
        print(f"    {clave:<20} {n:>2} notas")
        for e in ejemplos[clave]:
            print(f"        {e}")

    return {"total": total_notas, "conteos": conteos}


# ===========================================================================
# 8. COBERTURA ENTRE HOJAS  -> prepara la unión
# ===========================================================================

def perfil_cobertura(hojas):
    print("\n" + "=" * 70)
    print("8. COBERTURA — ¿en qué se parecen las hojas ENTRE SÍ?")
    print("=" * 70)
    print("\n  Esta matriz es el insumo directo de la unificación (paso 5).")
    print("  Demuestra que el esquema canónico salió de comparar las hojas.\n")

    matriz = {}
    for nombre in HOJAS_BUSQUEDA:
        for c in _columnas_datos(hojas[nombre]):
            canon = ALIASES.get(_normalizar_encabezado(c), f"(sin mapeo) {c}")
            matriz.setdefault(canon, set()).add(nombre)

    tier_de = {c: cfg.get("tier", "?") for c, cfg in SCHEMA["fields"].items()}
    etiquetas = {n: n.split("_")[0] for n in HOJAS_BUSQUEDA}
    ancho = max(len(c) for c in matriz) + 2

    print(f"  {'campo':<{ancho}}" + "".join(f"{etiquetas[n]:>10}" for n in HOJAS_BUSQUEDA)
          + "   tier")
    orden = sorted(matriz, key=lambda c: (-len(matriz[c]), c))
    for canon in orden:
        marcas = "".join(
            f"{'X' if n in matriz[canon] else '—':>10}" for n in HOJAS_BUSQUEDA
        )
        print(f"  {canon:<{ancho}}{marcas}   {tier_de.get(canon, '?')}")

    en_todas = [c for c in matriz if len(matriz[c]) == len(HOJAS_BUSQUEDA)]
    en_una = [c for c in matriz if len(matriz[c]) == 1]
    sin_mapeo = [c for c in matriz if c.startswith("(sin mapeo)")]

    print(f"\n  en las 3 hojas: {len(en_todas)}   en una sola: {len(en_una)}")
    if sin_mapeo:
        print(f"  SIN MAPEO EN EL ESQUEMA: {sin_mapeo}")
        print("  (no se descartan: van a `extras` en el paso 3)")

    return {"matriz": {k: sorted(v) for k, v in matriz.items()}, "sin_mapeo": sin_mapeo}


# ===========================================================================
# 9. TRAZABILIDAD DE LA VISTA SEMANAL
# ===========================================================================

MAPA_WEEKLY = {
    "Vertical": "vertical (derivado del nombre de la hoja)",
    "Market": "market (columna Metro)",
    "Address": "address",
    "Status": "status (normalizado)",
    "$": "list_price",
    "SF": "bldg_sf",
    "$/SF": "price_per_sf",
    "Lot Size": "lot_sf",
    "Notes": "notes (recortada)",
}


def perfil_trazabilidad(hojas):
    print("\n" + "=" * 70)
    print("9. TRAZABILIDAD — ¿target_weekly_view se reconstruye desde los datos?")
    print("=" * 70)
    print("\n  Si algo de la vista semanal NO se puede derivar, hay trabajo")
    print("  manual escondido — justo lo que el brief quiere eliminar.\n")

    tw = hojas[HOJA_OBJETIVO]
    cols_tw = [c for c in _columnas_datos(tw) if not c.startswith("__col_")]

    print("  Columnas de la vista semanal -> campo de origen")
    sin_origen = []
    for c in cols_tw:
        origen = MAPA_WEEKLY.get(c)
        if origen:
            print(f"    {c:<12} -> {origen}")
        else:
            sin_origen.append(c)
            print(f"    {c:<12} -> SIN ORIGEN IDENTIFICADO")

    # --- rastrear cada fila ---
    print("\n  Filas de la vista semanal -> fila de origen")
    indice = {}
    for nombre in HOJAS_BUSQUEDA:
        df = hojas[nombre]
        for _, fila in df.iterrows():
            addr = fila.get("Address")
            if pd.notna(addr):
                indice[str(addr).strip().lower()] = f"{nombre}!{int(fila['_fila_excel'])}"

    filas_tw = tw[[not _fila_vacia(tw.loc[i], cols_tw) for i in tw.index]]
    sin_rastro = []
    for _, fila in filas_tw.iterrows():
        addr = fila.get("Address")
        clave = str(addr).strip().lower() if pd.notna(addr) else ""
        if clave in indice:
            print(f"    {fila['Vertical']:<20} {str(addr):<24} -> {indice[clave]}")
        elif clave in {"-", ""}:
            print(f"    {fila['Vertical']:<20} {'(sin dirección)':<24} -> "
                  f"fila de Sourcing, se rastrea por vertical+mercado")
        else:
            sin_rastro.append(addr)
            print(f"    {fila['Vertical']:<20} {str(addr):<24} -> SIN ORIGEN")

    # --- selección ---
    total_origen = sum(
        len(hojas[n][[not _fila_vacia(hojas[n].loc[i], _columnas_datos(hojas[n]))
                      for i in hojas[n].index]])
        for n in HOJAS_BUSQUEDA
    )
    print(f"\n  {len(filas_tw)} filas en la vista semanal vs "
          f"{total_origen} registros en las hojas de origen")
    print("  >>> La vista semanal es un SUBCONJUNTO: alguien decide qué mostrar.")
    print("      Ese criterio de selección es una decisión de la VISTA (paso 6),")
    print("      no del dataset. Se documenta aquí, se decide allá.")

    veredicto = not sin_origen and not sin_rastro
    print(f"\n  VEREDICTO: {'todo derivable desde el dataset unificado' if veredicto else 'HAY HUECOS'}")
    if sin_origen:
        print(f"    columnas sin origen: {sin_origen}")
    if sin_rastro:
        print(f"    filas sin origen: {sin_rastro}")

    return {"sin_origen": sin_origen, "sin_rastro": sin_rastro,
            "filas_weekly": len(filas_tw), "filas_origen": total_origen}


# ===========================================================================
# Orquestador
# ===========================================================================

def perfilar(hojas):
    print("\n" + "#" * 70)
    print("#  PASO 2 — PERFILADO DE CALIDAD")
    print("#  No se modifica ningún dato. Esto produce evidencia.")
    print("#" * 70)

    hallazgos = {
        "estructura": perfil_estructura(hojas),
        "completitud": perfil_completitud(hojas),
        "validez": perfil_validez(hojas),
        "unicidad": perfil_unicidad(hojas),
        "outliers": perfil_outliers(hojas),
        "derivados": perfil_derivados(hojas),
        "notas": perfil_notas(hojas),
        "cobertura": perfil_cobertura(hojas),
        "trazabilidad": perfil_trazabilidad(hojas),
    }

    print("\n" + "#" * 70)
    print("#  FIN DEL PERFILADO — ningún dato fue modificado")
    print("#" * 70)
    return hallazgos


def generar_reporte_md(hojas, destino="reports/perfilado.md"):
    """
    Guarda el perfilado completo en Markdown.

    Este archivo es el insumo directo del process log: se escribe solo,
    mientras trabajas, en vez de tener que reconstruirlo al final.
    """
    import io
    from contextlib import redirect_stdout
    from datetime import date

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        hallazgos = perfilar(hojas)

    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        f"# Perfilado de calidad de datos\n\n"
        f"Generado automáticamente el {date.today().isoformat()} "
        f"por `profiling.py` (paso 2).\n\n"
        f"Ningún dato fue modificado para producir este informe.\n\n"
        f"```\n{buffer.getvalue()}\n```\n",
        encoding="utf-8",
    )
    print(f"Reporte guardado en {ruta}")
    return hallazgos


if __name__ == "__main__":
    from ingest import cargar_todo

    hojas, _ = cargar_todo(verbose=False)
    hallazgos = perfilar(hojas)
    generar_reporte_md(hojas)
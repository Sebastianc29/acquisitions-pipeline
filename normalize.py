"""
Paso 3 — Normalización.

Objetivo: que las tres hojas hablen el mismo idioma. Al terminar, un filtro
por 'Sourcing' devuelve las tres filas y `Power` es un número calculable.

Sigue siendo POR HOJA. La unión es el paso 5.

Principios:
  - Nada se descarta en silencio. Todo lo eliminado o no reconocido queda
    en el load_report, con su motivo.
  - Todo valor transformado conserva su original en un campo _raw.
  - Los outliers NO se corrigen: un dato real que se ve raro sigue siendo real.
  - No se imputa nada. Un hueco sigue siendo un hueco.

Estructura:
  A. Normalización de encabezados
  B. Normalización de texto (genérica, para TODAS las columnas)
  C. Parseo de tipos
  D. Recálculo de derivados
  E. Limpieza estructural
  F. Validación posterior (integridad, validez, unicidad, completitud,
     outliers y reglas de negocio) sobre los datos YA normalizados

Uso:
    from normalize import normalizar
    limpias, reporte = normalizar(hojas)
"""

import re
from collections import defaultdict
from pathlib import Path

import pandas as pd
import yaml

SCHEMA = yaml.safe_load(Path("config/schema.yaml").read_text(encoding="utf-8"))
CONTROL = {"_hoja", "_fila_excel", "assumptions"}
HOJAS_BUSQUEDA = [s for v in SCHEMA["verticals"].values() for s in v["sheets"]]
VERTICAL_DE_HOJA = {
    s: clave for clave, v in SCHEMA["verticals"].items() for s in v["sheets"]
}


# ===========================================================================
# Reporte de carga
# ===========================================================================

class LoadReport:
    """Todo lo que el ETL no entendió o decidió quitar queda aquí."""

    def __init__(self):
        self.eventos = defaultdict(list)

    def add(self, categoria, detalle):
        self.eventos[categoria].append(detalle)

    def imprimir(self):
        print("\n" + "=" * 70)
        print("LOAD REPORT — lo que no encajó")
        print("=" * 70)
        orden = [
            "status_sin_mapeo", "valor_no_parseable", "columna_sin_mapeo",
            "columna_vacia_eliminada", "fila_vacia_eliminada",
            "variante_texto_detectada", "derivado_recalculado",
        ]
        claves = orden + [k for k in self.eventos if k not in orden]
        for clave in claves:
            items = self.eventos.get(clave, [])
            marca = "  " if not items else ">>"
            print(f"\n{marca} {clave}: {len(items)}")
            for i in items[:12]:
                print(f"     {i}")
            if len(items) > 12:
                print(f"     ... y {len(items) - 12} más")

    def criticos(self):
        """Eventos que exigen intervención humana."""
        return (
            len(self.eventos["status_sin_mapeo"])
            + len(self.eventos["valor_no_parseable"])
        )


# ===========================================================================
# A. ENCABEZADOS
# ===========================================================================

def clave_encabezado(texto):
    t = str(texto).strip().lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


ALIASES = {
    clave_encabezado(a): campo
    for campo, cfg in SCHEMA["fields"].items()
    for a in cfg.get("aliases", [])
}


def normalizar_encabezados(df, hoja, reporte):
    """
    'Clear Height' y 'Clear height' -> clear_height_min (mismo campo).
    Lo que no está en el esquema NO se descarta: se marca para `extras`.
    """
    renombres, sin_mapeo = {}, []
    for c in df.columns:
        if c in CONTROL:
            continue
        canon = ALIASES.get(clave_encabezado(c))
        if canon:
            renombres[c] = canon
        elif not c.startswith("__col_"):
            sin_mapeo.append(c)
            reporte.add("columna_sin_mapeo", f"{hoja}: '{c}' -> extras")

    df = df.rename(columns=renombres)
    return df, renombres, sin_mapeo


# ===========================================================================
# B. NORMALIZACIÓN DE TEXTO (genérica, todas las columnas)
# ===========================================================================

def limpiar_texto(valor):
    """
    Se aplica a TODO valor de texto, en todas las columnas:
    quita espacios al inicio/fin, colapsa espacios internos y elimina
    caracteres invisibles. NO toca mayúsculas — eso rompería direcciones
    ('7500 NW 25th St') y nombres propios.
    """
    if not isinstance(valor, str):
        return valor
    t = valor.replace(" ", " ").replace("​", "")
    t = re.sub(r"\s+", " ", t).strip()
    return t if t else None


def normalizar_textos(df):
    for c in df.columns:
        if c in CONTROL:
            continue
        df[c] = df[c].map(limpiar_texto)
    return df


# --- categóricos: aquí sí se unifica el casing -----------------------------

def normalizar_status(valor, reporte, hoja, fila):
    """
    13 variantes -> 7 estados canónicos, vía el status_map del esquema.
    Dos tiempos: normalizar el texto, luego recorrer los patrones EN ORDEN.

    Si NO matchea ningún patrón no se manda a 'Otro': se deja en None y se
    reporta. Es la red que atrapa el status raro de la tab 171.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    norm = clave_encabezado(valor)
    for canonico, cfg in SCHEMA["status_map"].items():
        for patron in cfg["patterns"]:
            if re.match(patron, norm):
                return canonico
    reporte.add("status_sin_mapeo", f"{hoja}!{fila}: '{valor}'")
    return None


CATEGORICOS_SIMPLES = ["property_type", "sale_status"]


def normalizar_categorico_simple(valor):
    """'industrial' / 'INDUSTRIAL' / 'Industrial' -> 'Industrial'."""
    if not isinstance(valor, str):
        return valor
    return valor.strip().title()


# --- detección de variantes en texto libre ---------------------------------

def detectar_variantes_texto(df, hoja, reporte):
    """
    Busca valores que probablemente sean el MISMO, escritos distinto:
    'Matt M.' vs 'Matt M', 'Industrial' vs 'industrial'.

    Los REPORTA, no los fusiona. Unificar nombres propios automáticamente
    es peligroso: 'Matt M.' y 'Matt Miller' podrían ser dos personas.
    Los enums del esquema sí se unifican solos porque su catálogo es cerrado.
    """
    columnas_texto = [
        c for c in df.columns
        if c not in CONTROL and df[c].map(lambda v: isinstance(v, str)).any()
    ]
    for c in columnas_texto:
        if c in {"notes", "address"}:      # texto libre: no aplica
            continue
        grupos = defaultdict(set)
        for v in df[c].dropna():
            if isinstance(v, str):
                grupos[re.sub(r"[^a-z0-9]", "", v.lower())].add(v)
        for clave, variantes in grupos.items():
            if len(variantes) > 1:
                reporte.add(
                    "variante_texto_detectada",
                    f"{hoja}.{c}: {sorted(variantes)} (¿el mismo valor?)",
                )


# ===========================================================================
# C. PARSEO DE TIPOS
# ===========================================================================

def parsear_power(valor, reporte, hoja, fila):
    """
    '2,400A' -> 2400 ; '400A' -> 400
    0 es válido (lote sin acometida). Un dato ausente va vacío, NUNCA 0.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    texto = str(valor).strip().upper().replace(",", "")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*A?$", texto)
    if m:
        return int(float(m.group(1)))
    reporte.add("valor_no_parseable", f"{hoja}!{fila} power: '{valor}'")
    return None


def parsear_clear_height(valor, reporte, hoja, fila):
    """
    14.6      -> (14.6, 14.6)   escalar: min = max
    '15-51'   -> (15.0, 51.0)   rango: secciones con alturas distintas

    Devuelve (min, max, sospechoso). Un rango con max/min > 2 se marca
    como sospechoso: 51 pies libres en una nave de 17.000 SF es inusual y
    huele a error de digitación en el origen. Se conserva y se marca;
    NO se inventa un valor 'corregido'.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None, None, False
    if isinstance(valor, (int, float)):
        return float(valor), float(valor), False

    texto = str(valor).strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)$", texto)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        sospechoso = hi / lo > 2 if lo else True
        if sospechoso:
            reporte.add(
                "valor_sospechoso",
                f"{hoja}!{fila} clear_height: '{valor}' -> {lo}–{hi} "
                f"(rango inusualmente amplio, se conserva y se marca)",
            )
        return lo, hi, sospechoso

    try:
        v = float(texto)
        return v, v, False
    except ValueError:
        reporte.add("valor_no_parseable", f"{hoja}!{fila} clear_height: '{valor}'")
        return None, None, False


def parsear_telefono(valor):
    """Formato único para que el join con contact_tracker sea fiable."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    digitos = re.sub(r"\D", "", str(valor))
    if len(digitos) == 10:
        return f"{digitos[:3]}-{digitos[3:6]}-{digitos[6:]}"
    if len(digitos) == 11 and digitos[0] == "1":
        d = digitos[1:]
        return f"{d[:3]}-{d[3:6]}-{d[6:]}"
    return str(valor).strip()


def parsear_numero(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = re.sub(r"[,$\s]", "", str(valor))
    try:
        return float(texto)
    except ValueError:
        return None


# ===========================================================================
# Clasificación del registro
# ===========================================================================

# Señales de que la fila describe un inmueble concreto, aunque le falte
# la dirección.
SENALES_DE_SITIO = [
    "address", "list_price", "bldg_sf", "lot_sf", "zoning",
    "power_amps", "broker", "property_type", "miles_from_airport",
]


def clasificar_registro(fila):
    """
    site          -> un inmueble concreto
    market_search -> una búsqueda de mercado, sin inmueble todavía

    La primera versión usaba solo la dirección, y la validación posterior
    la tumbó: catering!5 (Westbury) no tiene dirección pero sí 16.072 SF y
    US$4.018.000. Eso no es una búsqueda de mercado: es un sitio real al
    que le falta la dirección.

    La distinción importa porque decide si un hueco es 'todavía no aplica'
    o 'falta un dato que debería estar' (paso 4.5).
    """
    for campo in SENALES_DE_SITIO:
        v = fila.get(campo)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        if isinstance(v, str) and v.strip() in {"", "-"}:
            continue
        return "site"
    return "market_search"


# ===========================================================================
# D. DERIVADOS
# ===========================================================================

def recalcular_derivados(df, hoja, reporte):
    """
    Recalcula $/SF y $/Lot SF. El perfilado verificó que el origen es
    confiable, pero recalcular es gratis y elimina la duda.

    Es CÁLCULO, no imputación: solo se deriva si ambos operandos existen.
    """
    pares = [
        ("price_per_sf", "list_price", "bldg_sf"),
        ("price_per_lot_sf", "list_price", "lot_sf"),
    ]
    for destino, num, den in pares:
        if not {num, den} <= set(df.columns):
            continue
        nuevos = []
        for _, fila in df.iterrows():
            a, b = fila.get(num), fila.get(den)
            if a is None or b in (None, 0) or pd.isna(a) or pd.isna(b):
                nuevos.append(None)
                continue
            calculado = round(float(a) / float(b), 2)
            anterior = fila.get(destino)
            if anterior is not None and not pd.isna(anterior):
                if abs(calculado - float(anterior)) / max(abs(float(anterior)), 1e-9) > 0.01:
                    reporte.add(
                        "derivado_recalculado",
                        f"{hoja}!{int(fila['_fila_excel'])} {destino}: "
                        f"origen {anterior} -> calculado {calculado}",
                    )
            nuevos.append(calculado)
        df[destino] = nuevos
    return df


# ===========================================================================
# E. LIMPIEZA ESTRUCTURAL
# ===========================================================================

def eliminar_vacios(df, hoja, reporte):
    """
    Elimina columnas 100% vacías y filas 100% vacías.

    NO es descartar en silencio: el criterio es estricto (cero valores),
    el perfilado ya lo verificó, y cada eliminación queda en el reporte.
    Si una columna tuviera un solo valor, el criterio no se cumple y se queda.
    """
    cols = [c for c in df.columns if c not in CONTROL]

    vacias = [c for c in cols if df[c].isna().all()]
    for c in vacias:
        reporte.add("columna_vacia_eliminada", f"{hoja}: '{c}' (0 valores)")
    df = df.drop(columns=vacias)

    cols = [c for c in df.columns if c not in CONTROL]
    a_quitar = []
    for i in df.index:
        if all(pd.isna(df.loc[i, c]) for c in cols):
            a_quitar.append(i)
            reporte.add(
                "fila_vacia_eliminada",
                f"{hoja}!{int(df.loc[i, '_fila_excel'])} (0 valores)",
            )
    return df.drop(index=a_quitar).reset_index(drop=True)


# ===========================================================================
# Normalización de una hoja
# ===========================================================================

def normalizar_hoja(df, hoja, reporte):
    df = df.copy()

    # --- E1: quitar vacíos antes de trabajar ---
    df = eliminar_vacios(df, hoja, reporte)

    # --- A: encabezados ---
    df, renombres, sin_mapeo = normalizar_encabezados(df, hoja, reporte)

    # columnas sin mapeo -> extras (nada se pierde)
    if sin_mapeo:
        df["extras"] = [
            {c: fila[c] for c in sin_mapeo if pd.notna(fila[c])}
            for _, fila in df.iterrows()
        ]
        df = df.drop(columns=sin_mapeo)
    else:
        df["extras"] = [{} for _ in range(len(df))]

    # --- B: texto ---
    df = normalizar_textos(df)
    detectar_variantes_texto(df, hoja, reporte)

    # --- C: tipos ---
    if "status" in df.columns:
        df["status_raw"] = df["status"]
        df["status"] = [
            normalizar_status(v, reporte, hoja, int(f))
            for v, f in zip(df["status"], df["_fila_excel"])
        ]

    if "power_amps" in df.columns:
        df["power_raw"] = df["power_amps"]
        df["power_amps"] = [
            parsear_power(v, reporte, hoja, int(f))
            for v, f in zip(df["power_amps"], df["_fila_excel"])
        ]

    if "clear_height_min" in df.columns:
        crudos = list(df["clear_height_min"])
        minimos, maximos, sospechosos = [], [], []
        for v, f in zip(crudos, df["_fila_excel"]):
            lo, hi, sosp = parsear_clear_height(v, reporte, hoja, int(f))
            minimos.append(lo)
            maximos.append(hi)
            sospechosos.append(sosp)
        df["clear_height_min"] = minimos
        df["clear_height_max"] = maximos
        for i, (crudo, sosp) in enumerate(zip(crudos, sospechosos)):
            if sosp:
                df.at[i, "extras"] = {**df.at[i, "extras"],
                                      "clear_height_raw": crudo,
                                      "clear_height_dudoso": True}

    if "broker_phone" in df.columns:
        df["broker_phone"] = df["broker_phone"].map(parsear_telefono)

    for c in CATEGORICOS_SIMPLES:
        if c in df.columns:
            df[c] = df[c].map(normalizar_categorico_simple)

    numericos = [
        c for c, cfg in SCHEMA["fields"].items()
        if cfg.get("type") in {"number", "integer", "money"}
        and c in df.columns
        and c not in {"power_amps", "clear_height_min", "clear_height_max"}
    ]
    for c in numericos:
        df[c] = df[c].map(parsear_numero)

    # --- D: derivados ---
    df = recalcular_derivados(df, hoja, reporte)

    # --- metadatos ---
    df["vertical"] = VERTICAL_DE_HOJA.get(hoja)
    df["source_sheet"] = hoja
    df["record_type"] = [
        clasificar_registro(fila) for _, fila in df.iterrows()
    ]

    # --- assumptions: ['Power'] -> ['power_amps'] ---
    if "assumptions" in df.columns:
        df["assumptions"] = [
            [ALIASES.get(clave_encabezado(c), c) for c in lista]
            for lista in df["assumptions"]
        ]

    return df


# ===========================================================================
# F. VALIDACIÓN POSTERIOR
# ===========================================================================

def validar(limpias):
    """
    Vuelve a correr las dimensiones de calidad sobre los datos YA
    normalizados, y añade reglas de negocio. El paso 2 midió la suciedad;
    esto comprueba que la limpieza hizo lo que debía y que el resultado
    tiene sentido inmobiliario.
    """
    print("\n" + "=" * 70)
    print("VALIDACIÓN POSTERIOR — sobre los datos ya normalizados")
    print("=" * 70)

    problemas = []

    # --- INTEGRIDAD ---
    print("\nINTEGRIDAD")
    canonicos = set(SCHEMA["status_map"])
    for hoja, df in limpias.items():
        fuera = {s for s in df["status"].dropna() if s not in canonicos}
        nulos = df["status"].isna().sum()
        estado = "OK" if not fuera and not nulos else "REVISAR"
        print(f"  [{estado}] {hoja}: status dentro del catálogo canónico")
        if fuera:
            problemas.append(f"{hoja}: status fuera de catálogo {fuera}")
        if nulos:
            problemas.append(f"{hoja}: {nulos} status sin normalizar")

    for hoja, df in limpias.items():
        incoherentes = df[
            (df["record_type"] == "market_search") & df["list_price"].notna()
        ] if "list_price" in df.columns else df.iloc[0:0]
        estado = "OK" if len(incoherentes) == 0 else "REVISAR"
        print(f"  [{estado}] {hoja}: ninguna búsqueda de mercado tiene precio")
        for _, f in incoherentes.iterrows():
            problemas.append(
                f"{hoja}!{int(f['_fila_excel'])}: market_search con precio"
            )

    # Sitios a los que les falta la dirección: no es incoherencia, es un
    # dato faltante. Se avisa porque afecta la clave del deal (paso 5).
    for hoja, df in limpias.items():
        if "address" not in df.columns:
            continue
        sin_dir = df[(df["record_type"] == "site") & df["address"].isna()]
        estado = "OK" if len(sin_dir) == 0 else "AVISO"
        print(f"  [{estado}] {hoja}: los sitios tienen dirección")
        for _, f in sin_dir.iterrows():
            print(f"         {hoja}!{int(f['_fila_excel'])} "
                  f"({f.get('city')}, {f.get('status')}): sitio real sin dirección "
                  f"-> la clave se derivará de city+cliente+precio")

    # --- VALIDEZ ---
    print("\nVALIDEZ")
    for hoja, df in limpias.items():
        malos = []
        for c in ["power_amps", "bldg_sf", "lot_sf", "list_price", "days_on_market"]:
            if c not in df.columns:
                continue
            for _, f in df.iterrows():
                v = f.get(c)
                if v is not None and not pd.isna(v) and not isinstance(v, (int, float)):
                    malos.append(f"{c}={v!r}")
        estado = "OK" if not malos else "REVISAR"
        print(f"  [{estado}] {hoja}: campos numéricos son numéricos")
        problemas.extend(f"{hoja}: {m}" for m in malos)

    # --- UNICIDAD ---
    print("\nUNICIDAD")
    for hoja, df in limpias.items():
        sitios = df[df["record_type"] == "site"]
        dup = sitios["address"].dropna()
        dup = dup[dup.duplicated(keep=False)]
        estado = "OK" if len(dup) == 0 else "REVISAR"
        print(f"  [{estado}] {hoja}: sin direcciones duplicadas")
        if len(dup):
            problemas.append(f"{hoja}: direcciones duplicadas {sorted(set(dup))}")

    # --- COMPLETITUD ---
    print("\nCOMPLETITUD (campos que SIEMPRE deben existir)")
    obligatorios = ["client", "vertical", "market", "status", "record_type"]
    for hoja, df in limpias.items():
        faltan = {c: int(df[c].isna().sum()) for c in obligatorios
                  if c in df.columns and df[c].isna().any()}
        estado = "OK" if not faltan else "REVISAR"
        print(f"  [{estado}] {hoja}: identificación completa en las {len(df)} filas")
        for c, n in faltan.items():
            problemas.append(f"{hoja}: {n} filas sin {c}")

    # --- OUTLIERS (se conservan; se listan) ---
    print("\nOUTLIERS (conservados, marcados para revisión humana)")
    rangos = {"price_per_sf": (20, 800), "days_on_market": (0, 365)}
    for hoja, df in limpias.items():
        for c, (lo, hi) in rangos.items():
            if c not in df.columns:
                continue
            for _, f in df.iterrows():
                v = f.get(c)
                if v is None or pd.isna(v):
                    continue
                if v < lo or v > hi:
                    print(f"  [INFO] {hoja}!{int(f['_fila_excel'])} "
                          f"{c}={v:,.2f} fuera de {lo}–{hi} · {f.get('address')}")

    # --- REGLAS DE NEGOCIO ---
    print("\nREGLAS DE NEGOCIO")

    def regla(nombre, incumplimientos):
        estado = "OK" if not incumplimientos else "REVISAR"
        print(f"  [{estado}] {nombre}")
        for i in incumplimientos:
            print(f"         {i}")
        problemas.extend(incumplimientos)

    # R1: precios y superficies positivos
    malos = []
    for hoja, df in limpias.items():
        for c in ["list_price", "bldg_sf", "lot_sf"]:
            if c not in df.columns:
                continue
            for _, f in df.iterrows():
                v = f.get(c)
                if v is not None and not pd.isna(v) and v <= 0:
                    malos.append(f"{hoja}!{int(f['_fila_excel'])} {c}={v}")
    regla("precios y superficies son positivos", malos)

    # R2: coherencia de derivados tras el recálculo
    malos = []
    for hoja, df in limpias.items():
        for _, f in df.iterrows():
            p, s, pps = f.get("list_price"), f.get("bldg_sf"), f.get("price_per_sf")
            if None in (p, s, pps) or pd.isna(p) or pd.isna(s) or pd.isna(pps) or s == 0:
                continue
            if abs(p / s - pps) > 0.02:
                malos.append(f"{hoja}!{int(f['_fila_excel'])} $/SF incoherente")
    regla("$/SF coincide con precio/superficie", malos)

    # R3: edificio más grande que el lote -> multipiso, o error
    avisos = []
    for hoja, df in limpias.items():
        if not {"bldg_sf", "lot_sf"} <= set(df.columns):
            continue
        for _, f in df.iterrows():
            b, l = f.get("bldg_sf"), f.get("lot_sf")
            if None in (b, l) or pd.isna(b) or pd.isna(l):
                continue
            if b > l:
                avisos.append(
                    f"{hoja}!{int(f['_fila_excel'])} {f.get('address')}: "
                    f"edificio {b:,.0f} SF > lote {l:,.0f} SF "
                    f"(implica más de un piso — plausible, se conserva)"
                )
    regla("superficie construida vs. lote", avisos)

    # R4: clear_height_min <= max
    malos = []
    for hoja, df in limpias.items():
        if "clear_height_max" not in df.columns:
            continue
        for _, f in df.iterrows():
            lo, hi = f.get("clear_height_min"), f.get("clear_height_max")
            if None in (lo, hi) or pd.isna(lo) or pd.isna(hi):
                continue
            if lo > hi:
                malos.append(f"{hoja}!{int(f['_fila_excel'])} min>max")
    regla("clear_height min <= max", malos)

    # R5: teléfonos con formato uniforme
    malos = []
    for hoja, df in limpias.items():
        if "broker_phone" not in df.columns:
            continue
        for _, f in df.iterrows():
            v = f.get("broker_phone")
            if v is None or pd.isna(v):
                continue
            if not re.match(r"^\d{3}-\d{3}-\d{4}$", str(v)):
                malos.append(f"{hoja}!{int(f['_fila_excel'])} tel='{v}'")
    regla("teléfonos en formato 000-000-0000", malos)

    # R6: campos de vertical solo en su vertical
    malos = []
    for campo, cfg in SCHEMA["fields"].items():
        if cfg.get("tier") != "vertical":
            continue
        permitidos = set(cfg.get("applies_to", []))
        for hoja, df in limpias.items():
            if campo not in df.columns:
                continue
            v = VERTICAL_DE_HOJA.get(hoja)
            if v not in permitidos and df[campo].notna().any():
                malos.append(f"{hoja}: tiene {campo} pero no aplica a '{v}'")
    regla("campos de vertical solo donde aplican", malos)

    print("\n" + "-" * 70)
    if problemas:
        print(f"{len(problemas)} punto(s) para revisar")
    else:
        print("Todas las validaciones pasaron.")
    return problemas


# ===========================================================================
# Orquestador
# ===========================================================================

def normalizar(hojas, verbose=True):
    reporte = LoadReport()
    limpias = {}

    if verbose:
        print("\n" + "#" * 70)
        print("#  PASO 3 — NORMALIZACIÓN")
        print("#" * 70)
        print("\nNORMALIZACIÓN POR HOJA")

    for hoja in HOJAS_BUSQUEDA:
        antes_filas = len(hojas[hoja])
        antes_cols = len([c for c in hojas[hoja].columns if c not in CONTROL])
        df = normalizar_hoja(hojas[hoja], hoja, reporte)
        limpias[hoja] = df

        if verbose:
            estados = sorted({s for s in df["status"].dropna()})
            print(f"\n  {hoja}")
            print(f"    filas    {antes_filas} -> {len(df)}")
            print(f"    columnas {antes_cols} -> "
                  f"{len([c for c in df.columns if c not in CONTROL])}")
            print(f"    status   {df['status_raw'].nunique()} variantes -> "
                  f"{len(estados)} canónicos: {', '.join(estados)}")
            if "power_amps" in df.columns:
                vals = [int(v) for v in df["power_amps"].dropna()]
                if vals:
                    print(f"    power_amps parseados: {vals}")

    if verbose:
        reporte.imprimir()
        validar(limpias)
        print("\n" + "#" * 70)
        print("#  FIN DEL PASO 3")
        print("#" * 70)

    return limpias, reporte


if __name__ == "__main__":
    from ingest import cargar_todo

    hojas, _ = cargar_todo(verbose=False)
    normalizar(hojas)
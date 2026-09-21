"""
Paso 4.5 — Clasificación de nulos.

Los huecos de este dataset NO son aleatorios: los explican tres dimensiones.
Cada celda vacía cae en uno de dos cubos:

  not_yet_known  -> no aplica (aún o nunca). Se muestra "—". No cuenta.
      por etapa            un Sourcing todavía no tiene inmueble
      por tipo de propiedad un terreno no tiene superficie construida
      por tipo de operación un arriendo no tiene precio de venta

  missing        -> debería estar y no está. Es una ALERTA, y en la app
                    se puede llenar como supuesto.

NO SE IMPUTA NADA. Ni promedios, ni medianas, ni valores generados por un
modelo. Un hueco sigue siendo un hueco; lo que cambia es que ahora sabemos
cuáles importan.

El valor real de este paso: los mismos nulos que parecían un problema de
limpieza se convierten en el contenido más útil de la vista semanal
("3 deals avanzados con superficie sin confirmar").

Uso:
    from nulls import clasificar_nulos
    clasificadas, alertas = clasificar_nulos(enriquecidas)
"""

from pathlib import Path

import pandas as pd
import yaml

SCHEMA = yaml.safe_load(Path("config/schema.yaml").read_text(encoding="utf-8"))
EXPECTATIVAS = SCHEMA["expectations"]
ORDEN_FUNNEL = SCHEMA["funnel"]["order"]

CONTROL = {
    "_hoja", "_fila_excel", "assumptions", "extras", "extraccion",
    "status_raw", "power_raw", "null_reason", "completitud", "alertas",
    "campos_esperados", "source_sheet", "record_type", "deal_type",
}

# Campos que NACEN de la extracción de notas (paso 4). Que estén vacíos no
# es un hueco de datos: significa que la nota no mencionaba ese detalle.
# Reportarlos como "no aplica" en cada fila solo genera ruido.
OPCIONALES = {
    c for c, cfg in SCHEMA["fields"].items() if cfg.get("tier") == "extracted"
} | {"plazos", "deposit", "rent_year", "current_price"}


def _etiqueta(fila):
    """
    Nombre legible del registro. NaN es TRUTHY en Python, así que
    `fila.get("address") or "..."` imprime 'nan' en vez del respaldo.
    """
    addr = fila.get("address")
    if not _vacio(addr):
        return str(addr)
    ciudad = fila.get("city")
    ciudad = str(ciudad) if not _vacio(ciudad) else "sin ciudad"
    return f"{ciudad} (sin dirección)"


def _vacio(v):
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    if isinstance(v, str) and v.strip() in {"", "-"}:
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


# ===========================================================================
# Reglas de aplicabilidad
# ===========================================================================

def campos_esperados(fila):
    """Qué campos DEBERÍAN tener valor en esta fila, según su etapa."""
    etapa = fila.get("status")
    if etapa is None or etapa not in EXPECTATIVAS["by_stage"]:
        return set()
    cfg = EXPECTATIVAS["by_stage"][etapa]
    esperados = set(cfg.get("expected", []))

    # Los campos de identificación se esperan siempre
    esperados |= {"client", "vertical", "market", "status"}

    # --- restar lo que no aplica por tipo de propiedad ---
    tipo = fila.get("property_type")
    if tipo and tipo in EXPECTATIVAS["by_property_type"]:
        esperados -= set(EXPECTATIVAS["by_property_type"][tipo]["not_applicable"])

    # --- restar/sumar por tipo de operación ---
    operacion = fila.get("deal_type")
    if operacion and operacion in EXPECTATIVAS["by_deal_type"]:
        cfg_op = EXPECTATIVAS["by_deal_type"][operacion]
        esperados -= set(cfg_op.get("not_applicable", []))
        esperados |= set(cfg_op.get("expected", []))

    # --- una búsqueda de mercado no tiene inmueble ---
    if fila.get("record_type") == "market_search":
        esperados &= {"client", "vertical", "market", "status", "notes"}

    return esperados


def motivo_no_aplica(campo, fila):
    """Por qué este campo no aplica a esta fila. Texto para la UI."""
    if fila.get("record_type") == "market_search":
        return "búsqueda de mercado: todavía no hay inmueble"

    tipo = fila.get("property_type")
    if tipo in EXPECTATIVAS["by_property_type"]:
        if campo in EXPECTATIVAS["by_property_type"][tipo]["not_applicable"]:
            return f"es un terreno ({tipo}): no hay edificio que medir"

    operacion = fila.get("deal_type")
    if operacion in EXPECTATIVAS["by_deal_type"]:
        if campo in EXPECTATIVAS["by_deal_type"][operacion].get("not_applicable", []):
            return "es un arriendo: los campos de venta no aplican"

    etapa = fila.get("status")
    if etapa in EXPECTATIVAS["by_stage"]:
        if campo in EXPECTATIVAS["by_stage"][etapa].get("not_yet_known", []):
            return f"etapa {etapa}: todavía no se conoce"

    return "no aplica en esta etapa"


# ===========================================================================
# Gravedad de las alertas
# ===========================================================================

CAMPOS_CRITICOS = {"list_price", "bldg_sf", "address", "broker", "key_dates"}


def gravedad(campo, etapa):
    """
    Mientras más avanzado el deal, más grave el hueco.
    Un Sourcing sin precio es normal; un In Contract sin precio es una alarma.
    """
    if etapa not in ORDEN_FUNNEL:
        return "BAJA"
    avance = ORDEN_FUNNEL.index(etapa) / max(len(ORDEN_FUNNEL) - 1, 1)
    critico = campo in CAMPOS_CRITICOS
    if avance >= 0.7 and critico:
        return "ALTA"
    if avance >= 0.4 or critico:
        return "MEDIA"
    return "BAJA"


# ===========================================================================
# Clasificación
# ===========================================================================

def clasificar_fila(fila, columnas):
    esperados = campos_esperados(fila)
    razones, faltantes = {}, []

    for campo in columnas:
        if campo in CONTROL:
            continue
        if not _vacio(fila.get(campo)):
            continue
        if campo in OPCIONALES and campo not in esperados:
            razones[campo] = {
                "tipo": "opcional",
                "motivo": "la nota no menciona este dato",
            }
            continue
        if campo in esperados:
            razones[campo] = {"tipo": "missing", "motivo": "debería tener valor"}
            faltantes.append(campo)
        else:
            razones[campo] = {
                "tipo": "not_yet_known",
                "motivo": motivo_no_aplica(campo, fila),
            }

    # Un campo esperado cuya COLUMNA no existe en esta hoja también falta.
    # food_clientA_nyc no tiene columna 'broker' ni 'property_type', pero a
    # partir de Pre LOI deberían existir. Sin esto, el porcentaje de
    # completitud los descontaba sin decir cuáles eran.
    for campo in esperados:
        if campo not in columnas and campo not in razones:
            razones[campo] = {
                "tipo": "missing",
                "motivo": "la hoja de origen no captura este campo",
            }
            faltantes.append(campo)

    presentes = [c for c in esperados if c in columnas and not _vacio(fila.get(c))]
    completitud = (
        round(100 * len(presentes) / len(esperados)) if esperados else 100
    )
    return razones, faltantes, completitud, sorted(esperados)


def clasificar_nulos(enriquecidas, verbose=True):
    clasificadas, alertas = {}, []

    if verbose:
        print("\n" + "#" * 70)
        print("#  PASO 4.5 — CLASIFICACIÓN DE NULOS")
        print("#  Sin imputación: ningún hueco se rellena.")
        print("#" * 70)

    for hoja, df in enriquecidas.items():
        df = df.copy()
        columnas = list(df.columns)
        col_razon, col_compl, col_esp = [], [], []

        for _, fila in df.iterrows():
            razones, faltantes, completitud, esperados = clasificar_fila(fila, columnas)
            col_razon.append(razones)
            col_compl.append(completitud)
            col_esp.append(esperados)

            for campo in faltantes:
                alertas.append({
                    "gravedad": gravedad(campo, fila.get("status")),
                    "hoja": hoja,
                    "fila": int(fila["_fila_excel"]),
                    "deal": _etiqueta(fila),
                    "etapa": fila.get("status"),
                    "campo": campo,
                    "completitud": completitud,
                })

        df["null_reason"] = col_razon
        df["completitud"] = col_compl
        df["campos_esperados"] = col_esp
        clasificadas[hoja] = df

    orden = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
    alertas.sort(key=lambda a: (orden[a["gravedad"]], -a["completitud"]))

    if verbose:
        _imprimir(clasificadas, alertas)

    return clasificadas, alertas


# ===========================================================================
# Salida
# ===========================================================================

def _imprimir(clasificadas, alertas):
    print("\nCLASIFICACIÓN POR REGISTRO")

    for hoja, df in clasificadas.items():
        print(f"\n  ── {hoja} " + "─" * (56 - len(hoja)))
        for _, fila in df.iterrows():
            etiqueta = _etiqueta(fila)
            contexto = [fila.get("status") or "sin etapa"]
            if fila.get("record_type") == "market_search":
                contexto.append("búsqueda de mercado")
            if fila.get("property_type"):
                contexto.append(str(fila["property_type"]))
            if fila.get("deal_type") == "lease":
                contexto.append("arriendo")

            print(f"\n    {hoja.split('_')[0]}!{int(fila['_fila_excel'])}  "
                  f"{etiqueta}   [{' · '.join(contexto)}]")

            razones = fila["null_reason"]
            faltan = [c for c, r in razones.items() if r["tipo"] == "missing"]
            no_aplican = [c for c, r in razones.items() if r["tipo"] == "not_yet_known"]

            if faltan:
                sin_columna = [
                    c for c in faltan
                    if razones[c]["motivo"].startswith("la hoja de origen")
                ]
                en_blanco = [c for c in faltan if c not in sin_columna]
                if en_blanco:
                    print(f"      FALTA      {', '.join(sorted(en_blanco))}")
                if sin_columna:
                    print(f"      FALTA      {', '.join(sorted(sin_columna))}")
                    print(f"                 └─ la hoja de origen no captura "
                          f"estos campos")
            if no_aplican:
                # agrupar por motivo para no repetir la explicación
                por_motivo = {}
                for c in no_aplican:
                    por_motivo.setdefault(razones[c]["motivo"], []).append(c)
                for motivo, campos in por_motivo.items():
                    muestra = ", ".join(sorted(campos)[:6])
                    extra = f" (+{len(campos) - 6})" if len(campos) > 6 else ""
                    print(f"      no aplica  {muestra}{extra}")
                    print(f"                 └─ {motivo}")
            print(f"      completitud {fila['completitud']}% "
                  f"({len(fila['campos_esperados'])} campos esperados)")

    # -------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("ALERTAS PARA LA VISTA SEMANAL")
    print("=" * 70)
    print("\n  Estos son los huecos que SÍ importan, ordenados por gravedad.")
    print("  En la app se pueden llenar como supuesto, con nombre y fecha.\n")

    if not alertas:
        print("  ninguna")
    for a in alertas:
        print(f"  [{a['gravedad']:<5}] {a['hoja'].split('_')[0]}!{a['fila']:<3} "
              f"{a['etapa']:<16} falta {a['campo']:<14} · {a['deal']}")

    por_gravedad = {}
    for a in alertas:
        por_gravedad[a["gravedad"]] = por_gravedad.get(a["gravedad"], 0) + 1
    print(f"\n  total: {len(alertas)} "
          f"({', '.join(f'{v} {k}' for k, v in por_gravedad.items())})")

    # -------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("COMPLETITUD POR DEAL (solo cuenta los campos esperados en su etapa)")
    print("=" * 70 + "\n")
    filas = []
    for hoja, df in clasificadas.items():
        for _, f in df.iterrows():
            filas.append((
                f["completitud"],
                f"{hoja.split('_')[0]}!{int(f['_fila_excel'])}",
                f.get("status") or "?",
                _etiqueta(f),
            ))
    for compl, ref, etapa, deal in sorted(filas):
        barra = "█" * round(compl / 10) + "░" * (10 - round(compl / 10))
        print(f"  {barra} {compl:>3}%  {ref:<8} {etapa:<16} {deal}")

    print("\n" + "#" * 70)
    print("#  FIN DEL PASO 4.5 — ningún hueco fue rellenado")
    print("#" * 70)


if __name__ == "__main__":
    from extract import extraer_de_notas
    from ingest import cargar_todo
    from normalize import normalizar

    hojas, _ = cargar_todo(verbose=False)
    limpias, _ = normalizar(hojas, verbose=False)
    enriquecidas, _ = extraer_de_notas(limpias, verbose=False)
    clasificar_nulos(enriquecidas)
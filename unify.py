"""
Paso 5 — Unificación.

Las tres hojas se vuelven UNA tabla. Esta es la fail condition del brief:
"Two disconnected tools (one for each use case) is a fail condition".

Produce:
  deals      una fila por registro, esquema canónico, columnas que no
             aplican en null (con su motivo ya clasificado en el paso 4.5)
  contacts   contact_tracker, con el enlace a deals por teléfono
  enlaces    cruce deal <-> contacto, marcando discrepancias sin resolverlas

deal_id: hash determinístico del CONTENIDO, nunca del orden de filas.
Si se derivara del índice, al reordenar el Sheet las confirmaciones y
ediciones de los usuarios quedarían pegadas al deal equivocado, en silencio.

Uso:
    from unify import unificar
    deals, contacts, enlaces, reporte = unificar(clasificadas, hojas)
"""

import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

SCHEMA = yaml.safe_load(Path("config/schema.yaml").read_text(encoding="utf-8"))

# Orden de columnas del dataset unificado: identidad, núcleo, extendidos,
# vertical, extraídos, metadatos.
COLUMNAS_SALIDA = [
    "deal_id", "record_type", "source_sheet", "source_row",
    "client", "vertical", "market", "city", "state", "address",
    "status", "status_raw",
    "list_price", "current_price", "bldg_sf", "lot_sf",
    "price_per_sf", "price_per_lot_sf",
    "deal_type", "rent_year", "rent_per_sf", "deposit",
    "property_type", "sale_status", "days_on_market",
    "zoning", "clear_height_min", "clear_height_max",
    "dh_doors", "gl_doors", "power_amps", "power_raw",
    "miles_from_airport",
    "broker", "broker_phone",
    "notes", "key_dates", "plazos",
    "assumptions", "null_reason", "completitud", "campos_esperados",
    "extras",
]

COLUMNAS_JSON = {
    "key_dates", "plazos", "assumptions", "null_reason",
    "campos_esperados", "extras",
}


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
# deal_id
# ===========================================================================

def generar_deal_id(fila, reporte):
    """
    Hash determinístico con cadena de respaldo, de lo más estable a lo menos:

      1. hoja + cliente + dirección          (lo normal)
      2. hoja + cliente + ciudad + precio    (sitio sin dirección: catering!5)
      3. hoja + cliente + ciudad + estado    (búsqueda de mercado con ciudad)
      4. hoja + cliente + nota               (último recurso: av!7, sin ciudad)

    El nivel usado se guarda en el reporte: si mañana alguien corrige una
    dirección con typo, el id cambia, y hay que poder re-vincular a mano.
    """
    hoja = fila.get("source_sheet") or fila.get("_hoja")
    cliente = fila.get("client")

    if not _vacio(fila.get("address")):
        partes = [hoja, cliente, str(fila["address"]).lower()]
        nivel = "direccion"
    elif not _vacio(fila.get("city")) and not _vacio(fila.get("list_price")):
        partes = [hoja, cliente, str(fila["city"]).lower(), str(fila["list_price"])]
        nivel = "ciudad+precio"
    elif not _vacio(fila.get("city")):
        partes = [hoja, cliente, str(fila["city"]).lower(),
                  str(fila.get("state") or "").lower()]
        nivel = "ciudad+estado"
    else:
        partes = [hoja, cliente, str(fila.get("notes") or "").lower()[:80]]
        nivel = "nota"

    crudo = "|".join(str(p) for p in partes)
    deal_id = hashlib.sha256(crudo.encode()).hexdigest()[:12]
    reporte.setdefault("claves", []).append({
        "deal_id": deal_id,
        "nivel": nivel,
        "origen": f"{hoja}!{int(fila['_fila_excel'])}",
        "base": crudo,
    })
    return deal_id


# ===========================================================================
# Unificación de deals
# ===========================================================================

def unificar_deals(clasificadas, reporte):
    filas = []

    for hoja, df in clasificadas.items():
        for _, fila in df.iterrows():
            registro = {}

            for col in COLUMNAS_SALIDA:
                if col in {"deal_id", "source_row"}:
                    continue
                valor = fila.get(col)
                registro[col] = None if _vacio(valor) else valor

            registro["source_sheet"] = hoja
            registro["source_row"] = int(fila["_fila_excel"])
            registro["deal_id"] = generar_deal_id(fila, reporte)
            filas.append(registro)

    deals = pd.DataFrame(filas, columns=COLUMNAS_SALIDA)

    # --- colisiones de id: dos filas distintas con la misma clave ---
    dup = deals["deal_id"][deals["deal_id"].duplicated(keep=False)]
    for did in sorted(set(dup)):
        afectadas = deals[deals["deal_id"] == did]
        reporte.setdefault("colisiones", []).append({
            "deal_id": did,
            "filas": [f"{r['source_sheet']}!{r['source_row']}"
                      for _, r in afectadas.iterrows()],
        })

    return deals


# ===========================================================================
# Contactos
# ===========================================================================

def normalizar_telefono(valor):
    import re
    if _vacio(valor):
        return None
    d = re.sub(r"\D", "", str(valor))
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return f"{d[:3]}-{d[3:6]}-{d[6:]}" if len(d) == 10 else str(valor).strip()


def unificar_contactos(hojas, deals, reporte):
    ct = hojas.get("contact_tracker")
    if ct is None or ct.empty:
        return pd.DataFrame(), pd.DataFrame()

    contactos, indice_tel = [], {}
    for _, fila in ct.iterrows():
        oficina = normalizar_telefono(fila.get("Office #"))
        movil = normalizar_telefono(fila.get("Mobile #"))
        nombre = fila.get("Contact Name")
        cid = hashlib.sha256(
            f"{nombre}|{fila.get('Email')}".encode()
        ).hexdigest()[:12]

        contactos.append({
            "contact_id": cid,
            "name": nombre,
            "title": fila.get("Title"),
            "email": None if _vacio(fila.get("Email")) else fila.get("Email"),
            "office_phone": oficina,
            "mobile_phone": movil,
            "emailed": bool(fila.get("Emailed?")),
            "linkedin_dm": bool(fila.get("LinkedIn DM?")),
            "helpful_reply": bool(fila.get("Helpful Reply?")),
            "notes": None if _vacio(fila.get("Notes")) else fila.get("Notes"),
            "source_row": int(fila["_fila_excel"]),
        })
        for tel in (oficina, movil):
            if tel:
                indice_tel.setdefault(tel, []).append((cid, nombre))

    # --- enlace deal <-> contacto, por TELÉFONO (no por nombre) ---
    enlaces = []
    for _, deal in deals.iterrows():
        tel = deal.get("broker_phone")
        if _vacio(tel) or tel not in indice_tel:
            continue
        for cid, nombre_contacto in indice_tel[tel]:
            discrepancia = (
                not _vacio(deal.get("broker"))
                and str(deal["broker"]).strip().lower()
                != str(nombre_contacto).strip().lower()
            )
            enlaces.append({
                "deal_id": deal["deal_id"],
                "contact_id": cid,
                "matched_on": "phone",
                "phone": tel,
                "broker_en_deal": deal.get("broker"),
                "nombre_en_tracker": nombre_contacto,
                "name_mismatch": discrepancia,
            })
            if discrepancia:
                reporte.setdefault("discrepancias_nombre", []).append(
                    f"{deal['source_sheet']}!{deal['source_row']}: "
                    f"el deal dice '{deal['broker']}' y el tracker "
                    f"'{nombre_contacto}' para el teléfono {tel}"
                )

    return pd.DataFrame(contactos), pd.DataFrame(enlaces)


# ===========================================================================
# Orquestador
# ===========================================================================

def unificar(clasificadas, hojas, verbose=True):
    reporte = {}

    deals = unificar_deals(clasificadas, reporte)
    contacts, enlaces = unificar_contactos(hojas, deals, reporte)

    if verbose:
        _imprimir(deals, contacts, enlaces, reporte)

    return deals, contacts, enlaces, reporte


def _imprimir(deals, contacts, enlaces, reporte):
    print("\n" + "#" * 70)
    print("#  PASO 5 — UNIFICACIÓN")
    print("#  Tres hojas -> un dataset")
    print("#" * 70)

    print(f"\nDATASET UNIFICADO: {len(deals)} registros x {len(deals.columns)} columnas")

    print("\n  por vertical")
    for v, n in deals["vertical"].value_counts().items():
        display = SCHEMA["verticals"].get(v, {}).get("display", v)
        print(f"    {display:<22} {n}")

    print("\n  por etapa del funnel")
    orden = SCHEMA["funnel"]["order"] + SCHEMA["funnel"]["terminal"]
    conteo = deals["status"].value_counts().to_dict()
    for etapa in orden:
        if etapa in conteo:
            display = SCHEMA["status_map"][etapa]["display"]
            print(f"    {display:<22} {conteo[etapa]}")

    print("\n  por tipo de registro")
    for t, n in deals["record_type"].value_counts().items():
        print(f"    {t:<22} {n}")

    # --- claves ---
    print("\nCLAVES (deal_id determinístico)")
    niveles = {}
    for c in reporte.get("claves", []):
        niveles[c["nivel"]] = niveles.get(c["nivel"], 0) + 1
    for nivel, n in niveles.items():
        print(f"    {nivel:<16} {n} registros")
    if niveles.get("nota"):
        print("    (nivel 'nota' = último recurso: sin dirección ni ciudad)")

    colisiones = reporte.get("colisiones", [])
    print(f"\n  colisiones de id: {len(colisiones)}")
    for c in colisiones:
        print(f"    {c['deal_id']}: {c['filas']}")

    # --- contactos ---
    print(f"\nCONTACTOS: {len(contacts)}   ENLACES A DEALS: {len(enlaces)}")
    for d in reporte.get("discrepancias_nombre", []):
        print(f"    DISCREPANCIA: {d}")
        print("      -> se MUESTRA en la app, no se resuelve a la fuerza")

    # --- la prueba que importa ---
    print("\n" + "=" * 70)
    print("PRUEBA DE UNIFICACIÓN")
    print("=" * 70)
    print("\n  Una sola consulta sobre UNA tabla responde las dos vistas:")
    print("\n  (a) operacional — filtrar por etapa:")
    avanzados = deals[deals["status"].isin(
        ["negotiating_loi", "negotiating_psa", "in_contract"]
    )]
    print(f"      {len(avanzados)} deals en negociación o contrato")

    print("\n  (b) semanal — agrupar por vertical:")
    for v, grupo in deals.groupby("vertical"):
        display = SCHEMA["verticals"].get(v, {}).get("display", v)
        sitios = grupo[grupo["record_type"] == "site"]
        valor = sitios["list_price"].sum()
        print(f"      {display:<22} {len(grupo)} registros · "
              f"${valor:,.0f} en pipeline")

    print("\n  Misma tabla, dos preguntas. Eso es la unificación.")


if __name__ == "__main__":
    from extract import extraer_de_notas
    from ingest import cargar_todo
    from normalize import normalizar
    from nulls import clasificar_nulos

    hojas, _ = cargar_todo(verbose=False)
    limpias, _ = normalizar(hojas, verbose=False)
    enriquecidas, _ = extraer_de_notas(limpias, verbose=False)
    clasificadas, _ = clasificar_nulos(enriquecidas, verbose=False)
    unificar(clasificadas, hojas)
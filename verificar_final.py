"""
End-to-end verification, before submitting.

Runs the whole chain and then checks the one thing that actually decides
whether this project did its job:

    can the weekly review be reproduced, field by field, from the unified
    dataset — without anyone retyping a row?

`target_weekly_view` is the sheet the team rebuilds by hand every week. If
what we generate matches it cell for cell, the manual step is dead. If it
does not, we have built something adjacent to what they asked for.

    python verificar_final.py            uses the available source
    python verificar_final.py --xlsx     forces the local file
"""

import sys

import pandas as pd

fallos, avisos = [], []


def check(nombre, ok, detalle=""):
    print(f"  [{'OK ' if ok else 'FALLA'}] {nombre}")
    if detalle:
        print(f"         {detalle}")
    if not ok:
        fallos.append(nombre)
    return ok


def aviso(texto):
    print(f"  [AVISO] {texto}")
    avisos.append(texto)


def titulo(n, texto):
    print(f"\n{'=' * 70}\n{n}. {texto}\n{'=' * 70}")


ORIGEN = "xlsx" if "--xlsx" in sys.argv else None

# ===========================================================================
titulo(1, "SCHEMA")

import yaml  # noqa: E402

SCHEMA = yaml.safe_load(open("config/schema.yaml", encoding="utf-8"))
import subprocess  # noqa: E402

r = subprocess.run([sys.executable, "validate_schema.py"],
                   capture_output=True, text=True)
check("el esquema canónico es válido", r.returncode == 0,
      r.stdout.strip().splitlines()[-1] if r.stdout else r.stderr[:200])

# ===========================================================================
titulo(2, "PIPELINE COMPLETO")

import db  # noqa: E402
import etl  # noqa: E402

conn, info = etl.correr(origen=ORIGEN, verbose=False)
check("el ETL corre de punta a punta", info["deals"] > 0,
      f"{info['deals']} registros desde '{info['origen']}' "
      f"en {info['segundos']:.1f}s")
check("cero eventos críticos en la carga", info["criticos"] == 0,
      f"{info['criticos']} status sin mapear o valores no parseables")

deals = db.leer_deals(conn)
estado = db.resumen(conn)
print(f"         tablas: {estado}")

# ===========================================================================
titulo(3, "INTEGRIDAD DEL DATASET UNIFICADO")

canonicos = set(SCHEMA["status_map"])
check("todos los status están en el catálogo canónico",
      set(deals["status"].dropna()) <= canonicos,
      f"{sorted(set(deals['status'].dropna()))}")

check("los deal_id son únicos",
      deals["deal_id"].nunique() == len(deals),
      f"{len(deals)} filas, {deals['deal_id'].nunique()} ids")

check("cada registro sabe de qué hoja y fila salió",
      deals["source_sheet"].notna().all() and deals["source_row"].notna().all())

obligatorios = ["client", "vertical", "market", "status", "record_type"]
faltan = {c: int(deals[c].isna().sum()) for c in obligatorios
          if deals[c].isna().any()}
check("los campos de identificación están completos", not faltan,
      "sin huecos" if not faltan else f"faltan: {faltan}")

numericos = ["list_price", "bldg_sf", "lot_sf", "price_per_sf"]
malos = []
for c in numericos:
    serie = deals[c].dropna()
    malos += [f"{c}={v!r}" for v in serie if not isinstance(v, (int, float))]
check("los campos numéricos son numéricos", not malos, "; ".join(malos[:5]))

# ===========================================================================
titulo(4, "LA PRUEBA QUE IMPORTA — weekly review vs target_weekly_view")

from ingest import cargar_todo  # noqa: E402

hojas, _ = cargar_todo(origen=ORIGEN, verbose=False)
tw = hojas["target_weekly_view"]

VERT = {k: v["display"] for k, v in SCHEMA["verticals"].items()}
STAGE = {k: v["display"] for k, v in SCHEMA["status_map"].items()}


def limpio(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, str) and v.strip() in {"", "-"}:
        return None
    return v


def numero(v):
    v = limpio(v)
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


# índice del dataset unificado, por dirección normalizada
indice = {}
for _, f in deals.iterrows():
    if limpio(f.get("address")):
        indice[str(f["address"]).strip().lower()] = f

filas_tw = [f for _, f in tw.iterrows()
            if limpio(f.get("Vertical")) is not None]
print(f"\n  {len(filas_tw)} filas en la hoja objetivo\n")

CAMPOS = [
    ("Vertical", lambda d: VERT.get(d["vertical"], d["vertical"]), False),
    ("Market", lambda d: limpio(d.get("market")), False),
    ("Status", lambda d: STAGE.get(d["status"], d["status"]), False),
    ("$", lambda d: numero(d.get("list_price")), True),
    ("SF", lambda d: numero(d.get("bldg_sf")), True),
    ("$/SF", lambda d: numero(d.get("price_per_sf")), True),
    ("Lot Size", lambda d: numero(d.get("lot_sf")), True),
]

comparadas = discrepancias = sin_origen = 0

for f in filas_tw:
    direccion = limpio(f.get("Address"))
    etiqueta = direccion or f"{f['Vertical']} (fila sin dirección)"

    if direccion is None:
        print(f"  ~ {etiqueta}")
        print(f"      fila de Sourcing: la hoja objetivo la muestra con '-'")
        continue

    deal = indice.get(str(direccion).strip().lower())
    if deal is None:
        sin_origen += 1
        print(f"  ✗ {etiqueta}: NO existe en el dataset unificado")
        continue

    problemas = []
    for nombre, extraer, es_num in CAMPOS:
        esperado = numero(f.get(nombre)) if es_num else limpio(f.get(nombre))
        obtenido = extraer(deal)
        if esperado is None and obtenido is None:
            continue
        if es_num and esperado is not None and obtenido is not None:
            if abs(esperado - obtenido) <= max(0.02, abs(esperado) * 0.001):
                continue
        elif str(esperado).strip().lower() == str(obtenido).strip().lower():
            continue
        problemas.append(f"{nombre}: hoja='{esperado}' vs generado='{obtenido}'")

    comparadas += 1
    if problemas:
        discrepancias += 1
        print(f"  ✗ {etiqueta}")
        for p in problemas:
            print(f"      {p}")
    else:
        print(f"  ✓ {etiqueta}  ({len(CAMPOS)} campos idénticos)")

print()
check("cada fila de la hoja objetivo existe en el dataset", sin_origen == 0,
      f"{sin_origen} sin origen")
check("cada campo coincide con la hoja hecha a mano", discrepancias == 0,
      f"{comparadas} filas comparadas x {len(CAMPOS)} campos, "
      f"{discrepancias} con diferencias")

# --- la vista generada no pierde deals ---
sitios = deals[deals["record_type"] == "site"]
activos = sitios[~sitios["status"].isin(["closed", "on_hold"])]
if len(activos) > len(filas_tw):
    print(f"\n  La vista generada muestra {len(activos)} inmuebles activos "
          f"contra {len(filas_tw)} de la hoja hecha a mano.")
    print("  Es intencional: la hoja manual es un subconjunto curado. "
          "Nadie decide\n  qué se oculta.")

# ===========================================================================
titulo(5, "SUPUESTOS Y BLOQUEO")

import auth  # noqa: E402

marcados = sum(
    1 for _, f in deals.iterrows()
    for _ in (f.get("assumptions") or [])
)
check("la señal de color del origen sobrevivió al pipeline", marcados > 0,
      f"{marcados} valores marcados como supuesto")

check("un viewer no puede confirmar",
      not auth.puede({"role": "viewer"}, "confirm"))
check("un analyst puede asumir pero no confirmar",
      auth.puede({"role": "analyst"}, "add_assumption")
      and not auth.puede({"role": "analyst"}, "confirm"))
check("un lead puede confirmar y desbloquear",
      auth.puede({"role": "lead"}, "confirm")
      and auth.puede({"role": "lead"}, "unlock"))

alcances = {r: auth.alcance_auditoria({"role": r, "team": "X"})
            for r in ["viewer", "analyst", "lead", "admin"]}
check("el panel de auditoría se limita según el rol",
      alcances == {"viewer": "propio", "analyst": "propio",
                   "lead": "equipo", "admin": "todos"},
      str(alcances))

# --- supervivencia del trabajo humano ---
objetivo = deals[deals["address"].notna()].iloc[0]
db.registrar_override(conn, objetivo["deal_id"], "broker", "VERIFICACIÓN",
                      "test", "lead", is_assumption=False, source="prueba")
etl.correr(origen=ORIGEN, verbose=False)
efectivos = db.leer_deals(conn)
fila = efectivos[efectivos["deal_id"] == objetivo["deal_id"]].iloc[0]
check("una edición sobrevive a un refresco completo",
      fila["broker"] == "VERIFICACIÓN")
crudo = conn.execute("SELECT broker FROM deals WHERE deal_id = ?",
                     (objetivo["deal_id"],)).fetchone()["broker"]
check("la tabla espejo del Sheet no se contamina",
      crudo != "VERIFICACIÓN", f"deals.broker = {crudo!r}")
conn.execute("UPDATE deal_overrides SET active=0 WHERE set_by='test'")
conn.execute("DELETE FROM audit_log WHERE user_id='test'")
conn.commit()

# ===========================================================================
titulo(6, "BARRERA ANTI-ALUCINACIÓN")

import insights  # noqa: E402

ctx = insights.build_context(deals)
real = ctx["totals"]["pipeline_value_usd"]

ok, marcados_ai = insights.verify([
    {"title": "cifra real", "figures": [real], "body": "", "evidence": []},
    {"title": "cifra inventada", "figures": [999_888_777], "body": "",
     "evidence": []},
], ctx)
check("una cifra real se acepta", len(ok) == 1,
      f"aceptada: {ok[0]['title'] if ok else '—'}")
check("una cifra inventada se rechaza", len(marcados_ai) == 1,
      f"rechazada: {marcados_ai[0]['unverified_figures'] if marcados_ai else '—'}")

# ===========================================================================
titulo(7, "ENTREGABLES")

from pathlib import Path  # noqa: E402

for archivo, que_es in [
    ("README.md", "documentación y credenciales"),
    ("FLOWCHART.md", "flowchart del caso"),
    ("PROCESS_LOG.md", "process log"),
    ("NEXT_STEPS.md", "siguientes pasos"),
    ("requirements.txt", "dependencias"),
    (".streamlit/config.toml", "tema"),
    ("reports/perfilado.md", "reporte de calidad"),
]:
    check(f"{archivo} — {que_es}", Path(archivo).exists())

gitignore = Path(".gitignore").read_text(encoding="utf-8") \
    if Path(".gitignore").exists() else ""
for secreto in [".env", "google_credentials.json", "auth.yaml",
                "sheets.json", "secrets.toml"]:
    check(f"{secreto} está en .gitignore", secreto in gitignore)

if "https://" not in Path("README.md").read_text(encoding="utf-8"):
    aviso("el README no tiene la URL de la app desplegada")

# ===========================================================================
print("\n" + "=" * 70)
if fallos:
    print(f"{len(fallos)} COMPROBACIÓN(ES) FALLIDA(S)")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)

print("TODO VERIFICADO")
print("=" * 70)
print("\nLa weekly review se reproduce campo por campo desde el dataset")
print("unificado. El paso manual de la hoja a las slides ya no es necesario.")
if avisos:
    print(f"\n{len(avisos)} aviso(s) no bloqueante(s):")
    for a in avisos:
        print(f"  - {a}")
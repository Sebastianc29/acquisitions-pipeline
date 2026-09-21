"""
Paso 5 — Verificación.

Comprueba las cuatro propiedades que hacen que la base sirva:

  1. UNIFICACIÓN   una sola tabla responde las dos vistas
  2. ESTABILIDAD   los deal_id no cambian entre corridas
  3. SUPERVIVENCIA el ETL reescribe `deals` sin borrar el trabajo humano
  4. TRAZABILIDAD  cada registro sabe de qué hoja y fila salió

La 3 es la crítica: si falla, un refresco programado borraría las
confirmaciones del equipo y nadie volvería a confiar en la herramienta.

Uso:  python verificar_paso5.py
"""

import sys

import db
import etl

fallos = []


def check(nombre, condicion, detalle=""):
    print(f"  [{'OK ' if condicion else 'FALLA'}] {nombre}")
    if detalle:
        print(f"         {detalle}")
    if not condicion:
        fallos.append(nombre)


print("VERIFICACIÓN DEL PASO 5\n")
print("Corrida 1 del ETL...")
conn, info1 = etl.correr(verbose=False)

# =========================================================================
print("\n1. UNIFICACIÓN")
deals = db.leer_deals(conn)
check(
    "una sola tabla con todos los verticales",
    set(deals["vertical"].dropna()) == {"food", "av", "catering"},
    f"{len(deals)} registros · verticales: {sorted(set(deals['vertical'].dropna()))}",
)
check(
    "la misma tabla responde la vista operacional",
    len(deals[deals["status"].isin(["negotiating_psa", "in_contract"])]) > 0,
    f"{len(deals[deals['status'].isin(['negotiating_psa', 'in_contract'])])} "
    f"deals en negociación avanzada o contrato",
)
check(
    "la misma tabla responde la vista semanal",
    deals.groupby("vertical").size().shape[0] == 3,
    "agrupable por vertical sin ninguna consulta adicional",
)

# =========================================================================
print("\n2. TRAZABILIDAD")
check(
    "cada registro conoce su hoja y fila de origen",
    deals["source_sheet"].notna().all() and deals["source_row"].notna().all(),
    f"ej: {deals.iloc[0]['source_sheet']}!{int(deals.iloc[0]['source_row'])}",
)
enlaces = conn.execute(
    "SELECT * FROM deal_contacts WHERE name_mismatch = 1"
).fetchall()
check(
    "la discrepancia de nombre se conserva, no se resuelve",
    len(enlaces) >= 1,
    f"{len(enlaces)} enlace(s) marcados: "
    + "; ".join(f"'{e['broker_en_deal']}' vs '{e['nombre_en_tracker']}'"
               for e in enlaces),
)

# =========================================================================
print("\n3. ESTABILIDAD DE LOS deal_id")
ids1 = set(deals["deal_id"])

print("\nCorrida 2 del ETL (mismo origen, sin cambios)...")
conn, info2 = etl.correr(verbose=False)
deals2 = db.leer_deals(conn)
ids2 = set(deals2["deal_id"])

check(
    "los deal_id son idénticos entre corridas",
    ids1 == ids2,
    f"{len(ids1)} ids · {len(ids1 & ids2)} coinciden · "
    f"{len(ids1 ^ ids2)} difieren",
)
check(
    "no hay ids duplicados",
    len(ids2) == len(deals2),
    f"{len(deals2)} filas, {len(ids2)} ids únicos",
)

# =========================================================================
print("\n4. SUPERVIVENCIA DEL TRABAJO HUMANO")
print("   (la propiedad crítica: un refresco no puede borrar ediciones)\n")

objetivo = deals2[deals2["address"].notna()].iloc[0]
did = objetivo["deal_id"]
valor_antes = objetivo.get("broker")

db.registrar_override(
    conn, did, "broker", "Confirmado: Ana Restrepo",
    user_id="ana", role="lead", is_assumption=False,
    source="llamada telefónica 21/09",
)
print(f"   Simulando: un usuario confirma 'broker' en {objetivo['address']}")

efectivos = db.leer_deals(conn)
fila = efectivos[efectivos["deal_id"] == did].iloc[0]
check(
    "la edición se ve en la lectura efectiva",
    fila["broker"] == "Confirmado: Ana Restrepo",
    f"antes: {valor_antes!r} -> ahora: {fila['broker']!r}",
)

print("\nCorrida 3 del ETL (después de la edición)...")
conn, _ = etl.correr(verbose=False)

overrides = conn.execute(
    "SELECT COUNT(*) FROM deal_overrides WHERE active = 1"
).fetchone()[0]
check(
    "el override sobrevive a la reescritura de `deals`",
    overrides >= 1,
    f"{overrides} override(s) activo(s) después del ETL",
)

efectivos = db.leer_deals(conn)
fila = efectivos[efectivos["deal_id"] == did].iloc[0]
check(
    "el valor editado sigue visible tras el refresco",
    fila["broker"] == "Confirmado: Ana Restrepo",
    f"broker = {fila['broker']!r}",
)

crudo = conn.execute(
    "SELECT broker FROM deals WHERE deal_id = ?", (did,)
).fetchone()["broker"]
check(
    "la tabla `deals` conserva el valor del Sheet, sin contaminar",
    crudo != "Confirmado: Ana Restrepo",
    f"deals.broker = {crudo!r} (espejo del origen) · "
    f"la edición vive en deal_overrides",
)

auditoria = conn.execute(
    "SELECT COUNT(*) FROM audit_log WHERE action IN ('assume','confirm')"
).fetchone()[0]
check(
    "la edición quedó registrada en el audit_log",
    auditoria >= 1,
    f"{auditoria} evento(s) de edición · alimenta el dashboard del requisito 3",
)

# =========================================================================
print("\n5. REQUISITO 3 — usuarios y actividad")
estado = db.resumen(conn)
check(
    "más de 20 usuarios con roles distintos",
    estado["users"] > 20,
    f"{estado['users']} usuarios",
)
roles = conn.execute(
    "SELECT role, COUNT(*) n FROM users GROUP BY role ORDER BY n DESC"
).fetchall()
print("         " + " · ".join(f"{r['role']}: {r['n']}" for r in roles))
check(
    "hay historial de actividad para el dashboard",
    estado["audit_log"] > 50,
    f"{estado['audit_log']} eventos registrados",
)

# =========================================================================
# limpieza: el override de prueba no debe quedar en la base del demo
conn.execute("UPDATE deal_overrides SET active = 0 WHERE set_by = 'ana'")
conn.execute("DELETE FROM audit_log WHERE user_id = 'ana'")
conn.commit()
print("\n  (override de prueba desactivado: la base queda limpia)")

print()
if fallos:
    print(f"{len(fallos)} comprobación(es) fallida(s): {fallos}")
    sys.exit(1)
print("Paso 5 verificado. La base está lista para la app.")
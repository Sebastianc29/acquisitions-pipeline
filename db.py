"""
Paso 5 — Base de datos.

DECISIÓN CENTRAL DEL DISEÑO: el trabajo humano NO vive en la tabla `deals`.

  deals            se reescribe entera en cada corrida del ETL.
                   Es un espejo del Google Sheet.
  deal_overrides   ediciones, supuestos y confirmaciones hechos en la app.
                   El ETL nunca la toca.

El valor que ve el usuario es deals + overrides encima. Por eso un refresco
programado (GitHub Actions cada mañana) puede reescribir `deals` sin borrar
nada de lo que la gente hizo. Si la columna estuviera en `deals`, cada
actualización destruiría el trabajo del equipo — y nadie volvería a confiar
en la herramienta.

Cuando el Sheet cambia un campo que tiene override activo, NO se resuelve
en silencio: se registra en `conflicts` y un humano decide.

Tablas:
  deals           espejo del Sheet (reescrita por el ETL)
  deal_overrides  capa humana (intacta entre corridas)
  conflicts       el Sheet cambió algo que un humano había editado
  contacts        contact_tracker
  deal_contacts   enlace por teléfono, con discrepancias marcadas
  users           usuarios y roles (requisito 3)
  audit_log       logins y cambios (requisito 3)
  etl_runs        historia de ejecuciones
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DB_PATH = Path("data/deals.db")

ESQUEMA_SQL = """
CREATE TABLE IF NOT EXISTS deals (
    deal_id           TEXT PRIMARY KEY,
    record_type       TEXT,
    source_sheet      TEXT,
    source_row        INTEGER,
    client            TEXT,
    vertical          TEXT,
    market            TEXT,
    city              TEXT,
    state             TEXT,
    address           TEXT,
    status            TEXT,
    status_raw        TEXT,
    list_price        REAL,
    current_price     REAL,
    bldg_sf           REAL,
    lot_sf            REAL,
    price_per_sf      REAL,
    price_per_lot_sf  REAL,
    deal_type         TEXT,
    rent_year         REAL,
    rent_per_sf       REAL,
    deposit           REAL,
    property_type     TEXT,
    sale_status       TEXT,
    days_on_market    REAL,
    zoning            TEXT,
    clear_height_min  REAL,
    clear_height_max  REAL,
    dh_doors          REAL,
    gl_doors          REAL,
    power_amps        INTEGER,
    power_raw         TEXT,
    miles_from_airport REAL,
    broker            TEXT,
    broker_phone      TEXT,
    notes             TEXT,
    key_dates         TEXT,
    plazos            TEXT,
    assumptions       TEXT,
    null_reason       TEXT,
    completitud       INTEGER,
    campos_esperados  TEXT,
    extras            TEXT,
    synced_at         TEXT
);

-- Capa humana. El ETL NUNCA escribe aquí.
CREATE TABLE IF NOT EXISTS deal_overrides (
    override_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id        TEXT NOT NULL,
    field          TEXT NOT NULL,
    value          TEXT,
    is_assumption  INTEGER NOT NULL DEFAULT 1,
    source         TEXT,
    set_by         TEXT NOT NULL,
    set_at         TEXT NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_over_deal ON deal_overrides(deal_id, field, active);

-- El Sheet cambió un campo que un humano había editado.
CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id       TEXT NOT NULL,
    field         TEXT NOT NULL,
    valor_sheet   TEXT,
    valor_humano  TEXT,
    detected_at   TEXT NOT NULL,
    resolved      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS contacts (
    contact_id     TEXT PRIMARY KEY,
    name           TEXT,
    title          TEXT,
    email          TEXT,
    office_phone   TEXT,
    mobile_phone   TEXT,
    emailed        INTEGER,
    linkedin_dm    INTEGER,
    helpful_reply  INTEGER,
    notes          TEXT,
    source_row     INTEGER
);

CREATE TABLE IF NOT EXISTS deal_contacts (
    deal_id            TEXT,
    contact_id         TEXT,
    matched_on         TEXT,
    phone              TEXT,
    broker_en_deal     TEXT,
    nombre_en_tracker  TEXT,
    name_mismatch      INTEGER
);

CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT,
    role        TEXT NOT NULL,
    team        TEXT,
    created_at  TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    user_id       TEXT,
    role          TEXT,
    action        TEXT NOT NULL,
    deal_id       TEXT,
    field         TEXT,
    value_before  TEXT,
    value_after   TEXT,
    source        TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS etl_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    origen      TEXT,
    n_deals     INTEGER,
    n_contacts  INTEGER,
    conflictos  INTEGER,
    reporte     TEXT
);
"""

COLUMNAS_JSON = {
    "key_dates", "plazos", "assumptions", "null_reason",
    "campos_esperados", "extras",
}


def conectar(path=DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(ESQUEMA_SQL)
    return conn


def _serializar(df):
    """Las columnas compuestas van a SQLite como JSON."""
    out = df.copy()
    for c in COLUMNAS_JSON:
        if c in out.columns:
            out[c] = out[c].map(
                lambda v: None if v is None or (isinstance(v, float) and pd.isna(v))
                else json.dumps(v, ensure_ascii=False, default=str)
            )
    return out


# ===========================================================================
# Detección de conflictos
# ===========================================================================

def detectar_conflictos(conn, deals_nuevos):
    """
    Un conflicto es: el Sheet cambió un campo que alguien había editado
    en la app. No se resuelve automáticamente — se registra y se muestra.
    """
    overrides = conn.execute(
        "SELECT deal_id, field, value FROM deal_overrides WHERE active = 1"
    ).fetchall()
    if not overrides:
        return 0

    anteriores = {
        r["deal_id"]: dict(r)
        for r in conn.execute("SELECT * FROM deals").fetchall()
    }
    nuevos = {r["deal_id"]: r for _, r in deals_nuevos.iterrows()}

    ahora = datetime.now().isoformat(timespec="seconds")
    detectados = 0

    for o in overrides:
        did, campo = o["deal_id"], o["field"]
        if did not in anteriores or did not in nuevos:
            continue
        antes = anteriores[did].get(campo)
        ahora_sheet = nuevos[did].get(campo)
        if str(antes) != str(ahora_sheet):
            conn.execute(
                "INSERT INTO conflicts (deal_id, field, valor_sheet, "
                "valor_humano, detected_at) VALUES (?,?,?,?,?)",
                (did, campo, str(ahora_sheet), o["value"], ahora),
            )
            detectados += 1

    return detectados


# ===========================================================================
# Escritura
# ===========================================================================

def escribir(deals, contacts, enlaces, reporte, origen, path=DB_PATH):
    conn = conectar(path)
    ahora = datetime.now().isoformat(timespec="seconds")

    conflictos = detectar_conflictos(conn, deals)

    # deals, contacts y deal_contacts SE REEMPLAZAN: son espejo del origen.
    # deal_overrides, audit_log y users NO se tocan: son la capa humana.
    deals = _serializar(deals)
    deals["synced_at"] = ahora

    conn.execute("DELETE FROM deals")
    conn.execute("DELETE FROM contacts")
    conn.execute("DELETE FROM deal_contacts")

    deals.to_sql("deals", conn, if_exists="append", index=False)
    if len(contacts):
        contacts.to_sql("contacts", conn, if_exists="append", index=False)
    if len(enlaces):
        enlaces.to_sql("deal_contacts", conn, if_exists="append", index=False)

    conn.execute(
        "INSERT INTO etl_runs (ts, origen, n_deals, n_contacts, conflictos, reporte)"
        " VALUES (?,?,?,?,?,?)",
        (ahora, origen, len(deals), len(contacts), conflictos,
         json.dumps(reporte, ensure_ascii=False, default=str)),
    )
    conn.execute(
        "INSERT INTO audit_log (ts, user_id, role, action, source)"
        " VALUES (?,?,?,?,?)",
        (ahora, "etl", "system", "etl_run", origen),
    )
    conn.commit()
    return conn, conflictos


# ===========================================================================
# Lectura efectiva  (deals + overrides encima)
# ===========================================================================

def leer_deals(conn, solo_confirmados=False):
    """
    Lo que ve el usuario: el espejo del Sheet con las ediciones humanas
    aplicadas encima. Esta función es la ÚNICA fuente de las dos vistas.
    """
    deals = pd.read_sql_query("SELECT * FROM deals", conn)
    if deals.empty:
        return deals

    for c in COLUMNAS_JSON:
        if c in deals.columns:
            deals[c] = deals[c].map(
                lambda v: json.loads(v) if isinstance(v, str) and v else None
            )

    overrides = pd.read_sql_query(
        "SELECT * FROM deal_overrides WHERE active = 1 ORDER BY set_at", conn
    )

    deals = deals.set_index("deal_id")
    for _, o in overrides.iterrows():
        did, campo = o["deal_id"], o["field"]
        if did not in deals.index or campo not in deals.columns:
            continue
        if solo_confirmados and o["is_assumption"]:
            continue
        valor = o["value"]
        try:
            valor = json.loads(valor)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        deals.at[did, campo] = valor
        if o["is_assumption"]:
            marcas = list(deals.at[did, "assumptions"] or [])
            if campo not in marcas:
                marcas.append(campo)
            deals.at[did, "assumptions"] = marcas

    return deals.reset_index()


# ===========================================================================
# Semilla de usuarios (requisito 3)
# ===========================================================================

NOMBRES = [
    ("Ana Restrepo", "lead"), ("Marco Villa", "analyst"),
    ("Priya Nair", "analyst"), ("Tom Becker", "viewer"),
    ("Sofía Duarte", "analyst"), ("James Ford", "viewer"),
    ("Lena Okoye", "lead"), ("Diego Salas", "analyst"),
    ("Hannah Weiss", "viewer"), ("Omar Haddad", "analyst"),
    ("Clara Jensen", "lead"), ("Ravi Menon", "analyst"),
    ("Beatriz Lima", "viewer"), ("Nils Andersen", "analyst"),
    ("Yuki Tanaka", "viewer"), ("Pablo Ferrer", "analyst"),
    ("Grace Mwangi", "lead"), ("Ian Corbett", "viewer"),
    ("Sara Kowalski", "analyst"), ("Felipe Cardona", "admin"),
    ("Noor Rahman", "viewer"), ("Victor Ulloa", "analyst"),
]
EQUIPOS = ["Acquisitions", "Research", "Capital Markets"]


def sembrar_usuarios(conn, forzar=False):
    """
    El brief pide asumir >20 usuarios con roles distintos. Estos son datos
    SEMILLA, no reales: quedan marcados con source='seed' en el audit_log
    para que nadie los confunda con actividad verdadera.
    """
    existentes = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if existentes and not forzar:
        return existentes

    import hashlib
    import random

    random.seed(42)          # reproducible: el demo se ve igual siempre
    base = datetime.now() - timedelta(days=30)

    for i, (nombre, rol) in enumerate(NOMBRES):
        uid = hashlib.sha256(nombre.encode()).hexdigest()[:10]
        correo = (nombre.lower().replace(" ", ".")
                  .replace("í", "i").replace("á", "a").replace("ó", "o")
                  + "@acme-re.com")
        conn.execute(
            "INSERT OR REPLACE INTO users (user_id, name, email, role, team,"
            " created_at, active) VALUES (?,?,?,?,?,?,1)",
            (uid, nombre, correo, rol, EQUIPOS[i % len(EQUIPOS)],
             (base - timedelta(days=random.randint(0, 200))).isoformat(
                 timespec="seconds")),
        )
        # actividad de login repartida en los últimos 30 días
        for _ in range(random.randint(1, 12)):
            ts = base + timedelta(
                days=random.randint(0, 29),
                hours=random.randint(7, 19),
                minutes=random.randint(0, 59),
            )
            conn.execute(
                "INSERT INTO audit_log (ts, user_id, role, action, source)"
                " VALUES (?,?,?,?,?)",
                (ts.isoformat(timespec="seconds"), uid, rol, "login", "seed"),
            )

    conn.commit()
    return len(NOMBRES)


# ===========================================================================
# Escritura de ediciones desde la app
# ===========================================================================

def registrar_override(conn, deal_id, field, value, user_id, role,
                       is_assumption=True, source=None):
    """
    La app llama esto cuando alguien edita o asume un valor.
    Escribe en la capa humana y deja rastro en el audit_log.
    """
    ahora = datetime.now().isoformat(timespec="seconds")
    anterior = conn.execute(
        "SELECT value FROM deal_overrides WHERE deal_id=? AND field=? AND active=1",
        (deal_id, field),
    ).fetchone()

    conn.execute(
        "UPDATE deal_overrides SET active=0 WHERE deal_id=? AND field=? AND active=1",
        (deal_id, field),
    )
    conn.execute(
        "INSERT INTO deal_overrides (deal_id, field, value, is_assumption,"
        " source, set_by, set_at) VALUES (?,?,?,?,?,?,?)",
        (deal_id, field, json.dumps(value, default=str), int(is_assumption),
         source, user_id, ahora),
    )
    conn.execute(
        "INSERT INTO audit_log (ts, user_id, role, action, deal_id, field,"
        " value_before, value_after, source) VALUES (?,?,?,?,?,?,?,?,?)",
        (ahora, user_id, role,
         "assume" if is_assumption else "confirm",
         deal_id, field,
         anterior["value"] if anterior else None,
         json.dumps(value, default=str), source),
    )
    conn.commit()


def resumen(conn):
    def n(tabla):
        return conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]

    return {
        "deals": n("deals"),
        "overrides": n("deal_overrides"),
        "conflicts": conn.execute(
            "SELECT COUNT(*) FROM conflicts WHERE resolved=0"
        ).fetchone()[0],
        "contacts": n("contacts"),
        "users": n("users"),
        "audit_log": n("audit_log"),
        "etl_runs": n("etl_runs"),
    }
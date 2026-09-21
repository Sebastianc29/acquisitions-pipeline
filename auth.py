"""
Autenticación y permisos.

Login propio, ~80 líneas, sin dependencias externas.

Por qué no streamlit-authenticator ni Auth0: lo que el brief evalúa es el
MODELO DE PERMISOS y el dashboard de uso, no el proveedor de identidad.
Un IdP externo resolvería la parte que no se evalúa y añadiría tenant,
callbacks y una dependencia que puede romper el deploy. Decisión de alcance
bajo timebox; queda en el process log.

Contraseñas: PBKDF2-HMAC-SHA256 con sal por usuario (hashlib, stdlib).
No se guarda ninguna contraseña en claro, ni en el YAML ni en la base.

Roles y lo que puede cada uno sale de config/schema.yaml (sección `roles`),
así que cambiar permisos no exige tocar código.
"""

import hashlib
import os
import secrets
from datetime import datetime
from pathlib import Path

import yaml

AUTH_PATH = Path("config/auth.yaml")
SCHEMA = yaml.safe_load(Path("config/schema.yaml").read_text(encoding="utf-8"))
ROLES = SCHEMA["roles"]

ITERACIONES = 200_000


# ===========================================================================
# Hashing
# ===========================================================================

def hashear(password, sal=None):
    sal = sal or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), sal.encode(), ITERACIONES)
    return f"{sal}${dk.hex()}"


def verificar(password, guardado):
    try:
        sal, _ = guardado.split("$", 1)
    except (ValueError, AttributeError):
        return False
    return secrets.compare_digest(hashear(password, sal), guardado)


# ===========================================================================
# Archivo de credenciales
# ===========================================================================

def cargar_auth():
    """
    Los usuarios salen de settings.py: st.secrets si está desplegado,
    config/auth.yaml si es local. Solo contienen hashes.
    """
    import settings
    return settings.auth_users() or {}


def guardar_auth(datos):
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_PATH.write_text(
        yaml.safe_dump(datos, allow_unicode=True, sort_keys=True), encoding="utf-8"
    )


def sembrar_credenciales(conn, password_demo=None):
    """
    Crea config/auth.yaml a partir de la tabla `users`.

    Para el demo todos los usuarios comparten una contraseña, que se imprime
    una sola vez y se guarda SOLO como hash. En producción esto sería un
    correo de activación; aquí es una decisión explícita de alcance.
    """
    password_demo = password_demo or os.environ.get("DEMO_PASSWORD", "profood2026")
    datos = cargar_auth()

    filas = conn.execute(
        "SELECT user_id, name, email, role, team FROM users WHERE active = 1"
    ).fetchall()

    nuevos = 0
    for f in filas:
        if f["email"] in datos:
            continue
        datos[f["email"]] = {
            "user_id": f["user_id"],
            "name": f["name"],
            "role": f["role"],
            "team": f["team"],
            "password": hashear(password_demo),
        }
        nuevos += 1

    guardar_auth(datos)
    return nuevos, password_demo


# ===========================================================================
# Sesión
# ===========================================================================

def autenticar(email, password, conn=None):
    """Devuelve el dict del usuario, o None. Registra el intento."""
    datos = cargar_auth()
    registro = datos.get((email or "").strip().lower())

    exito = bool(registro) and verificar(password, registro["password"])

    if conn is not None:
        conn.execute(
            "INSERT INTO audit_log (ts, user_id, role, action, source)"
            " VALUES (?,?,?,?,?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                registro["user_id"] if registro else None,
                registro["role"] if registro else None,
                "login" if exito else "login_failed",
                "app",
            ),
        )
        conn.commit()

    if not exito:
        return None
    return {
        "user_id": registro["user_id"],
        "email": email.strip().lower(),
        "name": registro["name"],
        "role": registro["role"],
        "team": registro.get("team"),
    }


# ===========================================================================
# Permisos
# ===========================================================================

def puede(usuario, accion):
    """¿Este rol puede hacer esta acción? El catálogo vive en schema.yaml."""
    if not usuario:
        return False
    permisos = ROLES.get(usuario["role"], {}).get("can", [])
    return "*" in permisos or accion in permisos


def alcance_auditoria(usuario):
    """
    Hasta dónde ve este usuario en el panel de auditoría.

    El brief dice "logins by person who is logged in": un panel de
    auditoría que cualquiera puede leer entero contradiría el propio
    modelo de permisos. Así que el dashboard respeta el rol de quien mira.
    """
    rol = usuario["role"]
    if rol == "admin":
        return "todos"
    if rol == "lead":
        return "equipo"
    return "propio"
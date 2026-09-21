"""
Diagnóstico de la conexión a Google Sheets.

Revisa los requisitos EN ORDEN y se detiene en el primero que falla,
diciéndote qué hacer. Los errores de la API de Google son crípticos
(un 404 casi nunca significa "no existe"), así que aquí se traducen.

Uso:  python probar_sheets.py
"""

import json
import sys
from pathlib import Path

CRED = Path("config/google_credentials.json")
SHEETS_CFG = Path("config/sheets.json")

paso = 0


def titulo(texto):
    global paso
    paso += 1
    print(f"\n[{paso}] {texto}")


def morir(problema, solucion):
    print(f"\n  FALLA: {problema}")
    print(f"  QUÉ HACER: {solucion}")
    sys.exit(1)


print("DIAGNÓSTICO DE CONEXIÓN A GOOGLE SHEETS")

# ---------------------------------------------------------------------------
titulo("Librerías instaladas")
try:
    from google.oauth2.service_account import Credentials  # noqa: F401
    from googleapiclient.discovery import build  # noqa: F401
    from googleapiclient.errors import HttpError
except ImportError as e:
    morir(
        f"falta una librería ({e.name})",
        "pip install google-api-python-client google-auth",
    )
print("  OK")

# ---------------------------------------------------------------------------
titulo("Archivo de credenciales")
if not CRED.exists():
    morir(
        f"no existe {CRED}",
        "Descarga el JSON de la service account (GCP -> Cuentas de servicio -> "
        "Claves -> Agregar clave -> JSON) y guárdalo con ESE nombre exacto.",
    )

try:
    cred = json.loads(CRED.read_text(encoding="utf-8"))
except json.JSONDecodeError:
    morir("el archivo no es JSON válido", "Vuelve a descargar la clave.")

email_robot = cred.get("client_email")
if not email_robot:
    morir(
        "el JSON no tiene client_email",
        "Descargaste el archivo equivocado. Necesitas la clave de una "
        "SERVICE ACCOUNT, no un OAuth client ID.",
    )

print(f"  OK  proyecto: {cred.get('project_id')}")
print(f"\n  >>> EMAIL DEL ROBOT (compártele la hoja como Lector):")
print(f"  >>> {email_robot}")

# ---------------------------------------------------------------------------
titulo("ID de la hoja")
spreadsheet_id = None
if SHEETS_CFG.exists():
    spreadsheet_id = json.loads(SHEETS_CFG.read_text(encoding="utf-8")).get("spreadsheet_id")

if not spreadsheet_id:
    morir(
        f"no hay spreadsheet_id en {SHEETS_CFG}",
        'Crea config/sheets.json con: {"spreadsheet_id": "el_id_de_tu_copia"}\n'
        "  El ID está en la URL, entre /d/ y /edit",
    )

if "/" in spreadsheet_id or "http" in spreadsheet_id:
    morir(
        "pegaste la URL completa, no el ID",
        "El ID es SOLO el pedazo entre /d/ y /edit, sin barras.",
    )
print(f"  OK  {spreadsheet_id}")

# ---------------------------------------------------------------------------
titulo("Llamada a la API")
from readers import LectorSheets, LectorXlsx  # noqa: E402

lector = LectorSheets(spreadsheet_id, CRED)
try:
    hojas, marcas = lector.leer()
except HttpError as e:
    codigo = e.resp.status
    if codigo == 404:
        morir(
            "404 — la API no encuentra la hoja",
            f"Casi siempre significa que NO compartiste la hoja con el robot.\n"
            f"  Abre tu copia -> Compartir -> pega {email_robot} -> Lector.\n"
            f"  (Si ya lo hiciste, revisa que el ID sea el de TU copia.)",
        )
    if codigo == 403:
        morir(
            "403 — permiso denegado",
            "Falta habilitar la Sheets API en ESTE proyecto "
            f"({cred.get('project_id')}).\n"
            "  GCP -> busca 'Google Sheets API' -> Habilitar.\n"
            "  Verifica arriba que el proyecto seleccionado sea el correcto.",
        )
    morir(f"HTTP {codigo}: {e}", "Mándame el mensaje completo.")
except Exception as e:
    if "invalid_grant" in str(e) or "JWT" in str(e):
        morir(
            "el token fue rechazado",
            "Suele ser el reloj del PC desfasado. Windows: Configuración -> "
            "Hora e idioma -> Sincronizar ahora. Luego reintenta.",
        )
    morir(f"{type(e).__name__}: {e}", "Mándame el mensaje completo.")

print(f"  OK  {len(hojas)} hojas leídas")

# ---------------------------------------------------------------------------
titulo("Contenido leído desde la hoja viva")
for nombre, df in hojas.items():
    print(f"  {nombre:<22} {len(df):>2} filas x {len(df.columns) - 2:>2} columnas")

print("\n  Celdas con formato marcado:")
if not marcas:
    print("    NINGUNA")
else:
    for m in marcas:
        detalle = m["color_fuente"] or f"relleno {m['relleno']}"
        print(f"    {m['hoja']}!{m['celda']}  {m['columna']} = {m['valor']!r}  {detalle}")

# ---------------------------------------------------------------------------
titulo("Comparación contra el .xlsx (los dos lectores deben coincidir)")
try:
    hojas_x, marcas_x = LectorXlsx(
        Path("data") / "MAKE A COPY_DevOps Case Study Data.xlsx"
    ).leer()
except FileNotFoundError:
    print("  (omitido: no está el .xlsx local)")
    hojas_x = None

if hojas_x is not None:
    problemas = []

    faltan = set(hojas_x) - set(hojas)
    sobran = set(hojas) - set(hojas_x)
    if faltan:
        problemas.append(f"hojas que la API no trajo: {sorted(faltan)}")
    if sobran:
        problemas.append(f"hojas nuevas en la copia: {sorted(sobran)}")

    for nombre in set(hojas) & set(hojas_x):
        if len(hojas[nombre]) != len(hojas_x[nombre]):
            problemas.append(
                f"{nombre}: {len(hojas[nombre])} filas en vivo vs "
                f"{len(hojas_x[nombre])} en el archivo"
            )

    claves_api = {(m["hoja"], m["celda"], m["columna"]) for m in marcas}
    claves_xlsx = {(m["hoja"], m["celda"], m["columna"]) for m in marcas_x}
    if claves_api != claves_xlsx:
        problemas.append(
            f"marcas de color distintas.\n"
            f"      en vivo: {sorted(claves_api)}\n"
            f"      archivo: {sorted(claves_xlsx)}"
        )

    if problemas:
        print("  DIFERENCIAS (no siempre son errores: tu copia puede haber cambiado)")
        for p in problemas:
            print(f"    - {p}")
    else:
        print("  OK  los dos lectores devuelven exactamente lo mismo")

print("\n" + "=" * 60)
print("CONEXIÓN FUNCIONANDO.")
print("Desde ahora cargar_todo() usa la hoja viva automáticamente.")
print("Para forzar el archivo:  cargar_todo(origen='xlsx')")
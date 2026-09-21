"""
Paso 1 — Lectores de origen.

Dos implementaciones detrás de la MISMA interfaz:

    LectorXlsx    -> lee el archivo descargado (funciona hoy, sin credenciales)
    LectorSheets  -> lee la hoja viva vía Google Sheets API (requiere service account)

Ambos devuelven exactamente lo mismo:

    leer() -> (hojas, marcas)
        hojas : {nombre_hoja: DataFrame}  valores TAL CUAL, sin normalizar
        marcas: [{hoja, celda, fila_excel, columna, valor, color_fuente, relleno}]

El resto del pipeline no sabe cuál de los dos se usó. Cambiar de origen
no cambia una línea de los pasos 2 al 6.

Por qué no basta con exportar a CSV: el CSV pierde el formato, y el color
de fuente es justamente como el equipo marca hoy los datos supuestos.
"""

import json
import os
from pathlib import Path

import pandas as pd

FILA_ENCABEZADO = 1

# Negro y "sin color" en ARGB de openpyxl
NEGROS_ARGB = {None, "FF000000", "00000000"}


# ---------------------------------------------------------------------------
# Lógica de color COMPARTIDA por los dos lectores.
# Si esto viviera duplicado, los dos orígenes podrían discrepar sobre qué
# cuenta como supuesto, que es el peor bug posible en este proyecto.
# ---------------------------------------------------------------------------

def _rgb_floats_a_hex(color):
    """
    La Sheets API entrega los colores como floats 0..1
    ({'red': 0, 'green': 0, 'blue': 1}), no en hexadecimal.
    Las claves ausentes valen 0.
    """
    if not color:
        return None
    r = int(round(color.get("red", 0) * 255))
    g = int(round(color.get("green", 0) * 255))
    b = int(round(color.get("blue", 0) * 255))
    return f"#{r:02X}{g:02X}{b:02X}"


def es_fuente_marcada(hex_color):
    """Una fuente cuenta como marca si no es negro ni 'sin color'."""
    if hex_color is None:
        return False
    return hex_color.upper() not in {"#000000"}


def es_relleno_marcado(hex_color):
    """Un relleno cuenta como marca si no es blanco ni transparente."""
    if hex_color is None:
        return False
    return hex_color.upper() not in {"#FFFFFF", "#000000"}


def _argb_a_hex(argb):
    """'FF0000FF' (ARGB de openpyxl) -> '#0000FF'."""
    if argb is None or argb in NEGROS_ARGB:
        return None
    s = str(argb)
    return f"#{s[-6:].upper()}"


def _armar_dataframe(nombre_hoja, filas):
    """
    Construye el DataFrame a partir de una matriz de valores crudos.
    Compartido por los dos lectores para que la forma de salida sea idéntica.

    - Las columnas sin encabezado se conservan como __col_N (no se descartan:
      el brief penaliza explícitamente perder datos en silencio).
    - _hoja y _fila_excel permiten rastrear de dónde salió cada registro.
    """
    if not filas:
        return pd.DataFrame()

    crudos = filas[0]
    encabezados = [
        str(h) if h is not None else f"__col_{i}"
        for i, h in enumerate(crudos, start=1)
    ]

    datos = filas[1:]
    # Rellenar filas cortas para que todas tengan el mismo ancho
    ancho = len(encabezados)
    datos = [list(f) + [None] * (ancho - len(f)) for f in datos]

    df = pd.DataFrame(datos, columns=encabezados)
    df.insert(0, "_fila_excel", range(2, 2 + len(df)))
    df.insert(0, "_hoja", nombre_hoja)
    return df


# ---------------------------------------------------------------------------
# Lector 1 — archivo .xlsx  (funciona sin credenciales)
# ---------------------------------------------------------------------------

class LectorXlsx:

    nombre = "xlsx"

    def __init__(self, path):
        self.path = Path(path)

    def descripcion(self):
        return f"archivo local: {self.path.name}"

    def leer(self):
        from openpyxl import load_workbook

        wb_valores = load_workbook(self.path, data_only=True)
        hojas = {
            ws.title: _armar_dataframe(ws.title, list(ws.iter_rows(values_only=True)))
            for ws in wb_valores.worksheets
        }

        wb_formato = load_workbook(self.path)
        marcas = []
        for ws in wb_formato.worksheets:
            encabezados = {c.column: c.value for c in ws[FILA_ENCABEZADO]}

            for fila in ws.iter_rows(min_row=FILA_ENCABEZADO + 1):
                for celda in fila:
                    if celda.value is None:
                        continue

                    fuente = celda.font.color
                    color = None
                    if fuente is not None and fuente.type == "rgb":
                        color = _argb_a_hex(fuente.rgb)

                    relleno = None
                    f = celda.fill
                    if f is not None and f.patternType and f.fgColor is not None:
                        if f.fgColor.type == "rgb":
                            relleno = _argb_a_hex(f.fgColor.rgb)

                    if es_fuente_marcada(color) or es_relleno_marcado(relleno):
                        marcas.append({
                            "hoja": ws.title,
                            "celda": celda.coordinate,
                            "fila_excel": celda.row,
                            "columna": encabezados.get(celda.column) or f"__col_{celda.column}",
                            "valor": celda.value,
                            "color_fuente": color if es_fuente_marcada(color) else None,
                            "relleno": relleno if es_relleno_marcado(relleno) else None,
                        })

        return hojas, marcas


# ---------------------------------------------------------------------------
# Lector 2 — Google Sheets en vivo
# ---------------------------------------------------------------------------

class LectorSheets:
    """
    Lee la hoja viva. Necesita:
      - google-api-python-client y google-auth instalados
      - un JSON de service account
      - que el Sheet esté COMPARTIDO con el email de esa service account

    Usa includeGridData=True porque es la única forma de obtener el formato.
    gspread a secas devuelve solo valores y perderíamos los colores, que es
    exactamente lo que vinimos a rescatar.
    """

    nombre = "sheets"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

    def __init__(self, spreadsheet_id, credentials_path=None,
                 credentials_info=None):
        """
        credentials_info: el JSON ya cargado como dict (Streamlit Cloud).
        credentials_path: el archivo en disco (local).
        Se prefiere el dict; así el deploy no necesita filesystem.
        """
        self.spreadsheet_id = spreadsheet_id
        self.credentials_path = Path(credentials_path) if credentials_path else None
        self.credentials_info = credentials_info

    def descripcion(self):
        return f"Google Sheets en vivo: {self.spreadsheet_id[:12]}..."

    def _servicio(self):
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        if self.credentials_info:
            creds = Credentials.from_service_account_info(
                self.credentials_info, scopes=self.SCOPES)
        else:
            creds = Credentials.from_service_account_file(
                str(self.credentials_path), scopes=self.SCOPES)
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    @staticmethod
    def _valor_de_celda(celda):
        """effectiveValue conserva el tipo original, como hace openpyxl."""
        ev = celda.get("effectiveValue")
        if not ev:
            return None
        if "numberValue" in ev:
            return ev["numberValue"]
        if "stringValue" in ev:
            return ev["stringValue"]
        if "boolValue" in ev:
            return ev["boolValue"]
        return celda.get("formattedValue")

    @staticmethod
    def _color_fuente(celda):
        fmt = celda.get("effectiveFormat", {}).get("textFormat", {})
        # La API tiene dos formas según la antigüedad del documento
        color = fmt.get("foregroundColor")
        if color is None:
            estilo = fmt.get("foregroundColorStyle", {})
            color = estilo.get("rgbColor")
        return _rgb_floats_a_hex(color) if color is not None else None

    @staticmethod
    def _relleno(celda):
        fmt = celda.get("effectiveFormat", {})
        color = fmt.get("backgroundColor")
        if color is None:
            color = fmt.get("backgroundColorStyle", {}).get("rgbColor")
        return _rgb_floats_a_hex(color) if color is not None else None

    def leer(self):
        campos = (
            "sheets(properties(title),"
            "data(rowData(values("
            "effectiveValue,formattedValue,"
            "effectiveFormat(textFormat(foregroundColor,foregroundColorStyle),"
            "backgroundColor,backgroundColorStyle)))))"
        )
        respuesta = (
            self._servicio()
            .spreadsheets()
            .get(spreadsheetId=self.spreadsheet_id, includeGridData=True, fields=campos)
            .execute()
        )

        hojas, marcas = {}, []

        for hoja in respuesta.get("sheets", []):
            titulo = hoja["properties"]["title"]
            bloques = hoja.get("data", [])
            row_data = bloques[0].get("rowData", []) if bloques else []

            # --- valores ---
            filas = [
                [self._valor_de_celda(c) for c in fila.get("values", [])]
                for fila in row_data
            ]
            hojas[titulo] = _armar_dataframe(titulo, filas)

            # --- formato ---
            if not row_data:
                continue
            encabezados = [
                self._valor_de_celda(c) for c in row_data[0].get("values", [])
            ]

            for i, fila in enumerate(row_data[1:], start=FILA_ENCABEZADO + 1):
                for j, celda in enumerate(fila.get("values", [])):
                    valor = self._valor_de_celda(celda)
                    if valor is None:
                        continue

                    color = self._color_fuente(celda)
                    relleno = self._relleno(celda)

                    if es_fuente_marcada(color) or es_relleno_marcado(relleno):
                        columna = (
                            encabezados[j]
                            if j < len(encabezados) and encabezados[j] is not None
                            else f"__col_{j + 1}"
                        )
                        marcas.append({
                            "hoja": titulo,
                            "celda": f"{_letra_columna(j + 1)}{i}",
                            "fila_excel": i,
                            "columna": columna,
                            "valor": valor,
                            "color_fuente": color if es_fuente_marcada(color) else None,
                            "relleno": relleno if es_relleno_marcado(relleno) else None,
                        })

        return hojas, marcas


def _letra_columna(n):
    """1 -> A, 15 -> O. Para que las coordenadas sean idénticas a las del .xlsx."""
    letras = ""
    while n > 0:
        n, resto = divmod(n - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


# ---------------------------------------------------------------------------
# Selección automática del lector
# ---------------------------------------------------------------------------

DATA_PATH = Path("data") / "MAKE A COPY_DevOps Case Study Data.xlsx"
CREDENCIALES = Path("config") / "google_credentials.json"


def obtener_lector(forzar=None):
    """
    Elige el lector disponible:
      1. Si hay credenciales de Google e ID de hoja -> Sheets en vivo
      2. Si no -> el .xlsx local

    Las credenciales salen de settings.py, que mira primero st.secrets
    (Streamlit Cloud), después el entorno y por último los archivos
    locales. Así el mismo código corre en tu máquina y desplegado.

    forzar='xlsx' o 'sheets' salta la detección.
    """
    import settings

    if forzar == "xlsx":
        return LectorXlsx(DATA_PATH)

    info = settings.google_credentials()
    spreadsheet_id = settings.spreadsheet_id()

    if forzar == "sheets" or (info and spreadsheet_id):
        if not spreadsheet_id:
            raise RuntimeError(
                "Falta el ID de la hoja: pon SPREADSHEET_ID en los secrets "
                "o crea config/sheets.json con {'spreadsheet_id': '...'}")
        if not info:
            raise RuntimeError(
                "Faltan las credenciales de Google: añade la tabla "
                "[gcp_service_account] a los secrets o el archivo "
                "config/google_credentials.json")
        return LectorSheets(spreadsheet_id, credentials_info=info)

    return LectorXlsx(DATA_PATH)
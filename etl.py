"""
ETL completo — un solo comando.

    python etl.py                 usa el origen disponible (Sheets si hay
                                  credenciales, si no el .xlsx)
    python etl.py --xlsx          fuerza el archivo local
    python etl.py --sheets        fuerza la hoja viva
    python etl.py --sin-llm       solo regex en la extracción de notas
    python etl.py --silencioso    solo el resumen final

Encadena los pasos 1 a 5:

    Google Sheets / .xlsx
        |  1. ingesta          valores crudos + colores de fuente
        |  2. (perfilado)      se corre aparte: no modifica datos
        |  3. normalización    status, tipos, encabezados, derivados
        |  4. extracción       notas -> columnas (regex + LLM)
        |  4.5 nulos           not_yet_known vs missing
        |  5. unificación      3 hojas -> 1 dataset
        v
    data/deals.db

La app llama a `correr()` desde su botón de Actualizar. Nadie fuera del
desarrollador escribe un comando.

IMPORTANTE: este ETL reescribe `deals`, pero NUNCA toca `deal_overrides`.
Las ediciones, supuestos y confirmaciones hechos en la app sobreviven a
cada actualización. Ver db.py.
"""

import sys
import time
from datetime import datetime

import db
from extract import extraer_de_notas
from ingest import cargar_todo
from normalize import normalizar
from nulls import clasificar_nulos
from unify import unificar


def correr(origen=None, usar_llm=True, verbose=True):
    t0 = time.time()
    pasos = []

    def paso(n, nombre):
        if verbose:
            print(f"  [{n}] {nombre}...", end=" ", flush=True)
        return time.time()

    def fin(t, detalle=""):
        seg = time.time() - t
        pasos.append(seg)
        if verbose:
            print(f"ok ({seg:.1f}s) {detalle}")

    if verbose:
        print("\n" + "=" * 70)
        print(f"ETL — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70 + "\n")

    t = paso(1, "Ingesta")
    hojas, marcas = cargar_todo(origen=origen, verbose=False)
    fin(t, f"{len(hojas)} hojas, {len(marcas)} celda(s) marcada(s) por color")

    t = paso(3, "Normalización")
    limpias, reporte_carga = normalizar(hojas, verbose=False)
    criticos = reporte_carga.criticos()
    fin(t, f"{criticos} evento(s) crítico(s)")

    t = paso(4, "Extracción de notas")
    enriquecidas, reporte_extr = extraer_de_notas(
        limpias, usar_llm=usar_llm, verbose=False
    )
    fin(t, f"{len(reporte_extr['rechazos'])} rechazado(s)")

    t = paso(4.5, "Clasificación de nulos")
    clasificadas, alertas = clasificar_nulos(enriquecidas, verbose=False)
    altas = sum(1 for a in alertas if a["gravedad"] == "ALTA")
    fin(t, f"{len(alertas)} alerta(s), {altas} de gravedad alta")

    t = paso(5, "Unificación")
    deals, contacts, enlaces, reporte_unif = unificar(
        clasificadas, hojas, verbose=False
    )
    fin(t, f"{len(deals)} registros")

    t = paso(6, "Escritura en SQLite")
    etiqueta_origen = origen or ("sheets" if _hay_credenciales() else "xlsx")
    conn, conflictos = db.escribir(
        deals, contacts, enlaces,
        {
            "claves": reporte_unif.get("claves", []),
            "colisiones": reporte_unif.get("colisiones", []),
            "discrepancias": reporte_unif.get("discrepancias_nombre", []),
            "alertas": alertas,
            "rechazos_llm": reporte_extr["rechazos"],
        },
        etiqueta_origen,
    )
    n_usuarios = db.sembrar_usuarios(conn)
    fin(t, f"{conflictos} conflicto(s)")

    total = time.time() - t0
    estado = db.resumen(conn)

    if verbose:
        print("\n" + "-" * 70)
        print(f"BASE: data/deals.db   ({total:.1f}s)")
        print("-" * 70)
        for k, v in estado.items():
            print(f"  {k:<14} {v}")

        if criticos:
            print(f"\n  ATENCIÓN: {criticos} evento(s) crítico(s) en la carga.")
            print("  Corre `python normalize.py` para ver el detalle.")
        if conflictos:
            print(f"\n  ATENCIÓN: {conflictos} conflicto(s) entre el Sheet y "
                  f"ediciones humanas.")
            print("  Se muestran en la app para que alguien decida. "
                  "Nada se resolvió solo.")
        if altas:
            print(f"\n  {altas} alerta(s) de gravedad ALTA para la vista semanal.")

        print(f"\n  Origen: {etiqueta_origen}   Usuarios en la base: {n_usuarios}")
        print()

    return conn, {
        "deals": len(deals),
        "alertas": alertas,
        "conflictos": conflictos,
        "criticos": criticos,
        "segundos": total,
        "origen": etiqueta_origen,
    }


def _hay_credenciales():
    from pathlib import Path
    return (Path("config/google_credentials.json").exists()
            and Path("config/sheets.json").exists())


if __name__ == "__main__":
    args = sys.argv[1:]
    origen = "xlsx" if "--xlsx" in args else "sheets" if "--sheets" in args else None
    correr(
        origen=origen,
        usar_llm="--sin-llm" not in args,
        verbose="--silencioso" not in args,
    )
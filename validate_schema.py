"""
Paso 0 — Validación del esquema canónico.

Verifica que schema.yaml esté bien formado y sea utilizable, ANTES de que
una sola línea del ETL dependa de él. Se ejecuta como celda de notebook o
como script:  python validate_schema.py

No lee el .xlsx: solo valida la configuración contra sí misma, más una
prueba con los valores de status reales encontrados en el archivo.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

SCHEMA_PATH = Path("config/schema.yaml")

# Los 13 valores de status crudos que existen realmente en el .xlsx.
# Si el mapeo no los cubre todos, el filtro de la app mostraría duplicados.
STATUS_CRUDOS = [
    "In Contract", "Negotiating PSA", "nego PSA", "Negotiating LOI",
    "Sourcing", "sourcing", "SOURCING", "In Pipeline", "in pipeline",
    "pre loi", "Under Contract", "On Hold", "Listed",
]


def normalizar(texto):
    """Normalización previa al match: minúsculas, sin puntuación, sin espacios dobles."""
    t = str(texto).strip().lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def mapear_status(crudo, status_map):
    """Recorre los patrones EN ORDEN; el primero que matchea gana."""
    norm = normalizar(crudo)
    for canonico, cfg in status_map.items():
        for patron in cfg["patterns"]:
            if re.match(patron, norm):
                return canonico
    return None


def main():
    errores, avisos = [], []

    # ---- 1. El archivo carga -------------------------------------------
    if not SCHEMA_PATH.exists():
        print(f"ERROR: no existe {SCHEMA_PATH}")
        sys.exit(1)
    schema = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    print(f"schema.yaml v{schema['meta']['version']} cargado\n")

    fields = schema["fields"]
    status_map = schema["status_map"]

    # ---- 2. Inventario de campos por tier -------------------------------
    por_tier = defaultdict(list)
    for nombre, cfg in fields.items():
        por_tier[cfg.get("tier", "sin_tier")].append(nombre)

    print("CAMPOS POR TIER")
    for tier in ["core", "extended", "vertical", "derived", "extracted"]:
        nombres = sorted(por_tier.get(tier, []))
        print(f"  {tier:<10} {len(nombres):>2}  {', '.join(nombres)}")
    if "sin_tier" in por_tier:
        errores.append(f"campos sin tier: {por_tier['sin_tier']}")
    print()

    # ---- 3. Ningún alias apunta a dos campos distintos ------------------
    dueno_de_alias = {}
    for nombre, cfg in fields.items():
        for alias in cfg.get("aliases", []):
            clave = normalizar(alias)
            # Dos casings del mismo encabezado apuntando al MISMO campo es
            # justamente lo que queremos ('Clear Height' / 'Clear height').
            # Solo es colisión si el dueño es otro campo.
            if clave in dueno_de_alias and dueno_de_alias[clave] != nombre:
                errores.append(
                    f"alias '{alias}' reclamado por '{dueno_de_alias[clave]}' y '{nombre}'"
                )
            dueno_de_alias[clave] = nombre
    colisiones = [e for e in errores if e.startswith("alias")]
    print(f"ALIASES: {len(dueno_de_alias)} únicos, {len(colisiones)} colisiones")

    # 'Clear Height' y 'Clear height' deben resolver al mismo campo
    a, b = normalizar("Clear Height"), normalizar("Clear height")
    if a == b and a in dueno_de_alias:
        print(f"  'Clear Height' == 'Clear height' -> {dueno_de_alias[a]}")
    else:
        errores.append("el casing de Clear Height no se resuelve")
    print()

    # ---- 4. Todos los regex compilan ------------------------------------
    for canonico, cfg in status_map.items():
        for patron in cfg["patterns"]:
            try:
                re.compile(patron)
            except re.error as e:
                errores.append(f"regex inválido en {canonico}: {patron} ({e})")

    # ---- 5. Los 13 status reales del archivo se mapean -------------------
    print("MAPEO DE STATUS (valores reales del .xlsx)")
    resultado = {}
    for crudo in STATUS_CRUDOS:
        canonico = mapear_status(crudo, status_map)
        resultado[crudo] = canonico
        if canonico is None:
            errores.append(f"status sin mapeo: '{crudo}'")
        display = status_map[canonico]["display"] if canonico else "SIN MAPEO"
        print(f"  {crudo:<18} -> {display}")

    distintos = sorted({v for v in resultado.values() if v})
    print(f"\n  {len(STATUS_CRUDOS)} valores crudos -> {len(distintos)} estados canónicos")

    # La prueba que importa: las 3 formas de Sourcing son una sola
    formas_sourcing = {resultado[s] for s in ["Sourcing", "sourcing", "SOURCING"]}
    if len(formas_sourcing) == 1:
        print("  'Sourcing' / 'sourcing' / 'SOURCING' colapsan en un solo valor")
    else:
        errores.append("las variantes de Sourcing no colapsan")

    if resultado["nego PSA"] == resultado["Negotiating PSA"]:
        print("  'nego PSA' == 'Negotiating PSA'")
    else:
        errores.append("'nego PSA' no equivale a 'Negotiating PSA'")

    if resultado["Under Contract"] == resultado["In Contract"]:
        print("  'Under Contract' == 'In Contract'")
    print()

    # ---- 6. Coherencia del funnel ---------------------------------------
    orden = schema["funnel"]["order"] + schema["funnel"]["terminal"]
    for canonico in status_map:
        if canonico not in orden:
            errores.append(f"'{canonico}' no aparece en funnel.order ni en terminal")
    print(f"FUNNEL: {len(schema['funnel']['order'])} etapas + "
          f"{len(schema['funnel']['terminal'])} terminales")
    print(f"  {' -> '.join(schema['funnel']['order'])}")
    print()

    # ---- 7. La matriz de expectativas solo cita campos existentes --------
    exp = schema["expectations"]
    citados = set()
    for etapa, cfg in exp["by_stage"].items():
        if etapa not in status_map:
            errores.append(f"expectations.by_stage cita etapa inexistente: {etapa}")
        citados |= set(cfg.get("expected", [])) | set(cfg.get("not_yet_known", []))
    for _, cfg in exp["by_property_type"].items():
        citados |= set(cfg.get("not_applicable", []))
    for _, cfg in exp["by_deal_type"].items():
        citados |= set(cfg.get("not_applicable", [])) | set(cfg.get("expected", []))

    fantasmas = sorted(citados - set(fields))
    if fantasmas:
        errores.append(f"expectations cita campos que no existen: {fantasmas}")
    print(f"EXPECTATIVAS: {len(exp['by_stage'])} etapas cubiertas, "
          f"{len(citados)} campos citados, {len(fantasmas)} inexistentes")

    sin_cubrir = sorted(set(status_map) - set(exp["by_stage"]))
    if sin_cubrir:
        avisos.append(f"etapas sin matriz de expectativas: {sin_cubrir}")
    print()

    # ---- 8. Verticales y roles ------------------------------------------
    hojas_busqueda = [s for v in schema["verticals"].values() for s in v["sheets"]]
    print(f"VERTICALES: {len(schema['verticals'])} "
          f"({', '.join(v['display'] for v in schema['verticals'].values())})")
    print(f"  hojas de búsqueda: {len(hojas_busqueda)} de {len(schema['meta']['sheets'])} totales")
    print(f"ROLES: {', '.join(schema['roles'])}")
    print()

    # ---- Veredicto -------------------------------------------------------
    for a in avisos:
        print(f"AVISO: {a}")
    if errores:
        print(f"\n{len(errores)} ERROR(ES):")
        for e in errores:
            print(f"  - {e}")
        sys.exit(1)
    print("Esquema válido. Listo para el paso 1.")


if __name__ == "__main__":
    main()
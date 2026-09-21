"""
Paso 4 — Extracción de notas.

Saca a columnas lo que hoy vive enterrado en texto libre:
precio negociado, tipo de operación (venta/arriendo), fechas clave,
plazos y señales de incertidumbre.

DOS MOTORES:
  regex  -> lo que tiene patrón inequívoco (fechas, plazos, monto único)
  LLM    -> lo ambiguo, que el regex no puede resolver:
            "closing 11/25, deposit $250,000"
                -> $250.000 es un DEPÓSITO, no un precio
            "countered at $3.25M, we responded at $3.05M"
                -> hay dos montos; el vigente es el nuestro

REGLA ANTI-ALUCINACIÓN (la parte importante):
  El modelo devuelve, con cada dato, el fragmento literal de la nota de
  donde lo sacó. El código verifica que ese fragmento exista TAL CUAL en
  la nota, y que todo monto extraído coincida con un monto realmente
  escrito en el texto. Lo que no pasa la verificación se DESCARTA y se
  reporta. El modelo no puede colar un número inventado aunque quiera.

CACHÉ:
  Las respuestas se guardan en cache/notas_extraidas.json indexadas por el
  hash de la nota. Correr el ETL veinte veces cuesta una llamada por nota,
  y el resultado es reproducible para el demo.

SIN API KEY:
  Funciona igual, solo con regex. Lo ambiguo queda sin extraer y sale en el
  reporte como "requiere revisión manual".

Uso:
    from extract import extraer_de_notas
    enriquecidas, reporte = extraer_de_notas(limpias)
"""

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd

CACHE_PATH = Path("cache/notas_extraidas.json")
ANIO_ASUMIDO = date.today().year


# ===========================================================================
# Utilidades de texto
# ===========================================================================

def _normalizar(texto):
    return re.sub(r"\s+", " ", str(texto)).strip().lower()


def _vacio(v):
    """
    None y NaN son la misma cosa aquí. pandas convierte None en NaN al
    construir la columna, y `x is not None` deja pasar los NaN — por eso
    hay que preguntarlo explícitamente.
    """
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def montos_en_texto(nota):
    """
    Todos los montos escritos en la nota, ya convertidos a número.
    Es la lista blanca contra la que se valida lo que devuelva el LLM:
    si un monto no está aquí, no estaba en la nota.

    '$3.25M' -> 3250000 ; '$250,000' -> 250000 ; '$4M/yr' -> 4000000
    """
    encontrados = {}
    for m in re.finditer(r"\$\s?([\d][\d,]*(?:\.\d+)?)\s*([MmKk])?", str(nota)):
        numero = float(m.group(1).replace(",", ""))
        sufijo = (m.group(2) or "").lower()
        if sufijo == "m":
            numero *= 1_000_000
        elif sufijo == "k":
            numero *= 1_000
        encontrados[m.group(0).strip()] = numero
    return encontrados


# ===========================================================================
# MOTOR 1 — regex (patrones inequívocos)
# ===========================================================================

PATRON_FECHA = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
PATRON_PLAZO = re.compile(r"\b(\d+)\s*day\s*(dd|close|diligence)?\b", re.I)
PATRON_ARRIENDO = re.compile(r"\b(lease|/yr|per year|free rent|rent)\b", re.I)
PATRON_INCERTIDUMBRE = re.compile(
    r"(have not|has not|not yet|no strong|no good|awaiting|may require|"
    r"looking for|draft|tbd|approx|estimated|~)", re.I
)


def extraer_con_regex(nota, contexto):
    """Solo lo que no admite interpretación."""
    salida = {"fuente": "regex", "fragmentos": {}}

    # --- fechas ---
    fechas = []
    for m in PATRON_FECHA.finditer(nota):
        mes, dia, anio = int(m.group(1)), int(m.group(2)), m.group(3)
        if mes > 12 or dia > 31:
            continue
        if anio:
            anio = int(anio) if int(anio) > 100 else 2000 + int(anio)
            asumido = False
        else:
            anio = ANIO_ASUMIDO
            asumido = True
        # contexto inmediato: qué palabra precede a la fecha
        inicio = max(0, m.start() - 25)
        etiqueta = "fecha"
        previo = nota[inicio:m.start()].lower()
        if "clos" in previo:
            etiqueta = "closing"
        elif "dd" in previo or "diligence" in previo:
            etiqueta = "due_diligence"
        fechas.append({
            "tipo": etiqueta,
            "fecha": f"{anio:04d}-{mes:02d}-{dia:02d}",
            "anio_asumido": asumido,
            "fragmento": m.group(0),
        })
    if fechas:
        salida["key_dates"] = fechas

    # --- plazos ---
    plazos = []
    for m in PATRON_PLAZO.finditer(nota):
        plazos.append({
            "dias": int(m.group(1)),
            "concepto": (m.group(2) or "").lower() or "sin especificar",
            "fragmento": m.group(0),
        })
    if plazos:
        salida["plazos"] = plazos

    # --- tipo de operación ---
    if PATRON_ARRIENDO.search(nota):
        salida["deal_type"] = "lease"
        salida["fragmentos"]["deal_type"] = PATRON_ARRIENDO.search(nota).group(0)

    # --- incertidumbre ---
    m = PATRON_INCERTIDUMBRE.search(nota)
    if m:
        salida["incertidumbre"] = True
        salida["fragmentos"]["incertidumbre"] = m.group(0)

    # --- precio: SOLO si hay un monto y no hay ambigüedad ---
    montos = montos_en_texto(nota)
    ambiguo = len(montos) > 1 or bool(
        re.search(r"\b(deposit|rent|/yr|escrow|earnest)\b", nota, re.I)
    )
    if len(montos) == 1 and not ambiguo:
        frag, valor = next(iter(montos.items()))
        salida["current_price"] = valor
        salida["fragmentos"]["current_price"] = frag
    elif montos:
        salida["requiere_llm"] = list(montos)

    return salida


# ===========================================================================
# MOTOR 2 — LLM (desambiguación)
# ===========================================================================

SISTEMA = """Eres un extractor de datos para un pipeline inmobiliario.

REGLAS ABSOLUTAS:
1. Solo extraes información que esté LITERALMENTE escrita en la nota.
2. Nunca estimas, infieres ni completas valores. Si algo no está, es null.
3. Con cada dato devuelves el fragmento EXACTO de la nota de donde lo
   sacaste, copiado carácter por carácter.
4. Respondes únicamente con JSON, sin texto alrededor ni bloques de código.

Distinciones que importan:
- current_price es el precio VIGENTE de la negociación: el último valor
  ofrecido o acordado. No es el precio de lista, ni un depósito, ni una
  renta anual, ni una contraoferta ya superada.
- Si la nota dice "countered at X, we responded at Y", el vigente es Y.
- Un depósito (deposit, earnest money, escrow) NUNCA es current_price.
- Una renta anual ("$4M/yr rent") NUNCA es current_price: va en rent_year
  y deal_type es "lease"."""

ESQUEMA = """{
  "current_price": <número o null>,
  "current_price_fragmento": "<texto exacto o null>",
  "deposit": <número o null>,
  "deposit_fragmento": "<texto exacto o null>",
  "rent_year": <número o null>,
  "rent_year_fragmento": "<texto exacto o null>",
  "deal_type": "sale" | "lease" | null,
  "deal_type_fragmento": "<texto exacto o null>",
  "incertidumbre": true | false,
  "incertidumbre_fragmento": "<texto exacto o null>"
}"""


def _cliente_y_modelo():
    """Elige el modelo consultando la API, sin hardcodear identificadores."""
    try:
        import anthropic
        from dotenv import load_dotenv
    except ImportError:
        return None, None

    load_dotenv()
    import settings
    clave = settings.get("ANTHROPIC_API_KEY")
    if not clave:
        return None, None

    cliente = anthropic.Anthropic(api_key=clave)
    modelo = settings.get("ANTHROPIC_MODEL")
    if not modelo:
        try:
            ids = [m.id for m in cliente.models.list(limit=50).data]
        except Exception:
            return None, None
        candidatos = ([m for m in ids if "haiku" in m.lower()]
                      or [m for m in ids if "sonnet" in m.lower()] or ids)
        modelo = candidatos[0]
    return cliente, modelo


def extraer_con_llm(nota, contexto, cliente, modelo):
    ctx = ", ".join(f"{k}={v}" for k, v in contexto.items() if v is not None)
    prompt = (
        f"Contexto de la fila (NO extraigas de aquí, solo para desambiguar): {ctx}\n\n"
        f"Nota: {nota}\n\n"
        f"Devuelve exactamente este JSON:\n{ESQUEMA}"
    )
    respuesta = cliente.messages.create(
        model=modelo,
        max_tokens=600,
        system=SISTEMA,
        messages=[{"role": "user", "content": prompt}],
    )
    # Los modelos con razonamiento extendido devuelven un ThinkingBlock ANTES
    # del bloque de texto, así que content[0].text revienta. Se concatenan
    # todos los bloques de texto en vez de asumir que el primero lo es.
    bloques = [b.text for b in respuesta.content
               if getattr(b, "type", None) == "text"]
    if not bloques:
        bloques = [getattr(b, "text", "") for b in respuesta.content]
    texto = "".join(bloques).strip()
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ValueError(f"respuesta sin JSON: {texto[:120]}")
    return json.loads(m.group())


# ===========================================================================
# VERIFICACIÓN ANTI-ALUCINACIÓN
# ===========================================================================

def verificar(datos_llm, nota, reporte, origen):
    """
    Acepta un dato solo si:
      a) su fragmento existe LITERALMENTE en la nota, y
      b) si es un monto, ese monto está entre los escritos en la nota.

    Lo que no pasa se descarta y se reporta. Esta función es la razón por la
    que se puede usar un LLM en un pipeline de datos sin perder confianza.
    """
    nota_norm = _normalizar(nota)
    montos = set(montos_en_texto(nota).values())
    limpio = {"fuente": "llm", "fragmentos": {}}

    campos = [
        ("current_price", True), ("deposit", True), ("rent_year", True),
        ("deal_type", False), ("incertidumbre", False),
    ]

    for campo, es_monto in campos:
        valor = datos_llm.get(campo)
        if valor is None or valor is False:
            continue

        # 'sale' es el valor POR DEFECTO de deal_type: no afirma nada, así
        # que no hay nada que respaldar con un fragmento. Solo 'lease' es
        # una afirmación que cambia cómo se interpreta la fila (los campos
        # de venta pasan a no aplicar), y esa sí exige evidencia.
        # Sin esta distinción, el contador de rechazos se llena de ruido y
        # deja de servir para detectar alucinaciones de verdad.
        if campo == "deal_type" and valor == "sale":
            continue

        fragmento = datos_llm.get(f"{campo}_fragmento")

        if not fragmento:
            reporte.append(f"{origen}: '{campo}' sin fragmento de origen -> descartado")
            continue

        if _normalizar(fragmento) not in nota_norm:
            reporte.append(
                f"{origen}: '{campo}' cita \"{fragmento}\" que NO está en la nota "
                f"-> descartado"
            )
            continue

        if es_monto and float(valor) not in montos:
            reporte.append(
                f"{origen}: '{campo}'={valor} no coincide con ningún monto "
                f"escrito {sorted(montos)} -> descartado"
            )
            continue

        limpio[campo] = valor
        limpio["fragmentos"][campo] = fragmento

    return limpio


# ===========================================================================
# Caché
# ===========================================================================

def _cargar_cache():
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _guardar_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _hash(nota):
    return hashlib.sha256(_normalizar(nota).encode()).hexdigest()[:16]


# ===========================================================================
# Orquestador
# ===========================================================================

def extraer_de_notas(limpias, usar_llm=True, verbose=True):
    cliente, modelo = _cliente_y_modelo() if usar_llm else (None, None)
    cache = _cargar_cache()
    rechazos, sin_resolver = [], []
    llamadas, desde_cache = 0, 0

    if verbose:
        print("\n" + "#" * 70)
        print("#  PASO 4 — EXTRACCIÓN DE NOTAS")
        print("#" * 70)
        print(f"\nMotor: regex" + (f" + LLM ({modelo})" if cliente else
                                   " solamente (sin API key)"))

    enriquecidas = {}

    for hoja, df in limpias.items():
        df = df.copy()
        cols_nuevas = {
            "current_price": [], "deposit": [], "rent_year": [],
            "deal_type": [], "key_dates": [], "plazos": [],
            "extraccion": [],
        }

        for _, fila in df.iterrows():
            nota = fila.get("notes")
            origen = f"{hoja}!{int(fila['_fila_excel'])}"

            if _vacio(nota):
                for c in cols_nuevas:
                    cols_nuevas[c].append(None if c != "extraccion" else {})
                continue

            # --- regex ---
            resultado = extraer_con_regex(nota, {})

            # --- LLM solo si hace falta ---
            necesita_llm = "requiere_llm" in resultado
            if necesita_llm and cliente:
                clave = _hash(nota)
                if clave in cache:
                    datos = cache[clave]
                    desde_cache += 1
                else:
                    contexto = {
                        "list_price": fila.get("list_price"),
                        "status": fila.get("status"),
                    }
                    try:
                        datos = extraer_con_llm(nota, contexto, cliente, modelo)
                        cache[clave] = datos
                        llamadas += 1
                    except Exception as e:
                        rechazos.append(f"{origen}: fallo del LLM ({e})")
                        datos = {}
                verificado = verificar(datos, nota, rechazos, origen)
                for k, v in verificado.items():
                    if k == "fragmentos":
                        resultado.setdefault("fragmentos", {}).update(v)
                    else:
                        resultado[k] = v
            elif necesita_llm:
                sin_resolver.append(
                    f"{origen}: {len(resultado['requiere_llm'])} montos ambiguos "
                    f"{resultado['requiere_llm']} -> requiere revisión manual"
                )

            for c in cols_nuevas:
                if c == "extraccion":
                    cols_nuevas[c].append({
                        "fragmentos": resultado.get("fragmentos", {}),
                        "fuente": resultado.get("fuente"),
                    })
                else:
                    cols_nuevas[c].append(resultado.get(c))

        for c, valores in cols_nuevas.items():
            df[c] = valores

        # deal_type por defecto: venta
        df["deal_type"] = df["deal_type"].fillna("sale")

        # todo lo extraído nace como supuesto
        nuevas_marcas = []
        for _, fila in df.iterrows():
            marcas = list(fila.get("assumptions") or [])
            for campo in ["current_price", "deposit", "rent_year", "key_dates"]:
                if not _vacio(fila.get(campo)):
                    marcas.append(campo)
            nuevas_marcas.append(marcas)
        df["assumptions"] = nuevas_marcas

        # La extracción puede cambiar la naturaleza de una fila: food!9 no
        # tiene dirección ni superficie, pero la nota describe un borrador
        # de arriendo por $4M/año. Eso es un inmueble concreto, no una
        # búsqueda de mercado. Se reclasifica ahora que existen los campos.
        from normalize import clasificar_registro
        df["record_type"] = [clasificar_registro(f) for _, f in df.iterrows()]

        enriquecidas[hoja] = df

    _guardar_cache(cache)

    if verbose:
        _imprimir(enriquecidas, rechazos, sin_resolver, llamadas, desde_cache)

    return enriquecidas, {"rechazos": rechazos, "sin_resolver": sin_resolver}


def _imprimir(enriquecidas, rechazos, sin_resolver, llamadas, desde_cache):
    print("\nDATOS EXTRAÍDOS (cada uno con su fragmento de origen)")

    total_notas = 0
    conteos = {"current_price": 0, "deposit": 0, "rent_year": 0,
               "key_dates": 0, "plazos": 0, "lease": 0}

    for hoja, df in enriquecidas.items():
        for _, fila in df.iterrows():
            nota = fila.get("notes")
            if _vacio(nota):
                continue
            total_notas += 1

            hallazgos = []
            frags = (fila.get("extraccion") or {}).get("fragmentos", {})

            if not _vacio(fila.get("current_price")):
                conteos["current_price"] += 1
                lista = fila.get("list_price")
                delta = ""
                if not _vacio(lista) and lista:
                    pct = (fila["current_price"] - lista) / lista * 100
                    delta = f"  (lista {lista:,.0f} · {pct:+.1f}%)"
                hallazgos.append(
                    f"current_price = {fila['current_price']:,.0f}"
                    f'   <- "{frags.get("current_price", "")}"{delta}'
                )
            if not _vacio(fila.get("deposit")):
                conteos["deposit"] += 1
                hallazgos.append(
                    f"deposit       = {fila['deposit']:,.0f}"
                    f'   <- "{frags.get("deposit", "")}"'
                )
            if not _vacio(fila.get("rent_year")):
                conteos["rent_year"] += 1
                hallazgos.append(
                    f"rent_year     = {fila['rent_year']:,.0f}"
                    f'   <- "{frags.get("rent_year", "")}"'
                )
            if fila.get("deal_type") == "lease":
                conteos["lease"] += 1
                hallazgos.append(f"deal_type     = lease")
            for f in (fila.get("key_dates") or []):
                conteos["key_dates"] += 1
                marca = " [año asumido]" if f["anio_asumido"] else ""
                hallazgos.append(
                    f'{f["tipo"]:<13} = {f["fecha"]}   <- "{f["fragmento"]}"{marca}'
                )
            for p in (fila.get("plazos") or []):
                conteos["plazos"] += 1
                hallazgos.append(
                    f'plazo {p["concepto"]:<7} = {p["dias"]} días'
                    f'   <- "{p["fragmento"]}"'
                )

            if hallazgos:
                print(f'\n  {hoja}!{int(fila["_fila_excel"])}  "{nota}"')
                for h in hallazgos:
                    print(f"    {h}")

    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    print(f"  {total_notas} notas procesadas")
    for k, v in conteos.items():
        print(f"  {v:>2} {k}")
    print(f"\n  llamadas al LLM: {llamadas}   servidas desde caché: {desde_cache}")

    print(f"\n  RECHAZADOS por no verificarse en el texto: {len(rechazos)}")
    for r in rechazos:
        print(f"    {r}")
    if not rechazos:
        print("    (0 — todo lo extraído se puede señalar dentro de la nota)")

    if sin_resolver:
        print(f"\n  SIN RESOLVER (requieren revisión manual): {len(sin_resolver)}")
        for s in sin_resolver:
            print(f"    {s}")

    print("\n" + "#" * 70)
    print("#  FIN DEL PASO 4")
    print("#" * 70)


if __name__ == "__main__":
    from ingest import cargar_todo
    from normalize import normalizar

    hojas, _ = cargar_todo(verbose=False)
    limpias, _ = normalizar(hojas, verbose=False)
    extraer_de_notas(limpias)
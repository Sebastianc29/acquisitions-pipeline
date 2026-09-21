"""
Diagnóstico de la API de Anthropic (previo al paso 4).

Revisa los requisitos EN ORDEN y se detiene en el primero que falla.
No hardcodea el nombre del modelo: lo consulta a la API, así no se queda
desactualizado cuando salgan modelos nuevos.

Uso:  python probar_api.py
"""

import os
import sys
from pathlib import Path

paso = 0


def titulo(texto):
    global paso
    paso += 1
    print(f"\n[{paso}] {texto}")


def morir(problema, solucion):
    print(f"\n  FALLA: {problema}")
    print(f"  QUÉ HACER: {solucion}")
    sys.exit(1)


print("DIAGNÓSTICO DE LA API DE ANTHROPIC")

# ---------------------------------------------------------------------------
titulo("Librerías instaladas")
try:
    import anthropic
    from dotenv import load_dotenv
except ImportError as e:
    morir(f"falta una librería ({e.name})", "pip install anthropic python-dotenv")
print(f"  OK  anthropic {anthropic.__version__}")

# ---------------------------------------------------------------------------
titulo("Clave en el entorno")
load_dotenv()
clave = os.environ.get("ANTHROPIC_API_KEY")

if not clave:
    morir(
        "no hay ANTHROPIC_API_KEY",
        "Crea un archivo .env en la raíz del proyecto con:\n"
        "      ANTHROPIC_API_KEY=sk-ant-...\n"
        "  y verifica que .env esté en tu .gitignore.",
    )
if not clave.startswith("sk-ant-"):
    morir(
        "la clave no tiene el formato esperado",
        "Debe empezar por 'sk-ant-'. Revisa que copiaste la clave completa "
        "y sin comillas ni espacios.",
    )
if not Path(".gitignore").exists() or ".env" not in Path(".gitignore").read_text(
    encoding="utf-8"
):
    print("  AVISO: .env no aparece en .gitignore — añádelo antes de subir a GitHub")
print(f"  OK  clave cargada ({clave[:12]}...{clave[-4:]})")

# ---------------------------------------------------------------------------
titulo("Modelos disponibles en tu cuenta")
cliente = anthropic.Anthropic(api_key=clave)

try:
    modelos = [m.id for m in cliente.models.list(limit=50).data]
except anthropic.AuthenticationError:
    morir(
        "la clave fue rechazada (401)",
        "La clave está mal copiada o fue revocada. Crea otra en "
        "console.anthropic.com -> API Keys.",
    )
except anthropic.APIConnectionError as e:
    morir(f"no se pudo conectar: {e}", "Revisa tu conexión o un proxy/firewall.")
except Exception as e:
    morir(f"{type(e).__name__}: {e}", "Mándame el mensaje completo.")

for m in modelos[:12]:
    print(f"    {m}")
if len(modelos) > 12:
    print(f"    ... y {len(modelos) - 12} más")

# Para extraer datos de 18 notas cortas, el modelo más pequeño sobra.
# Preferimos Haiku por costo; si no hay, el primero de la lista.
candidatos = [m for m in modelos if "haiku" in m.lower()] or \
             [m for m in modelos if "sonnet" in m.lower()] or modelos
MODELO = candidatos[0]
print(f"\n  >>> Modelo elegido para el paso 4: {MODELO}")

# ---------------------------------------------------------------------------
titulo("Llamada de prueba sobre una nota real del caso")
NOTA = "partial cooler, toured, countered at $3.25M, we responded at $3.05M"
print(f'  nota: "{NOTA}"')

PROMPT = f"""De esta nota de un negocio inmobiliario, extrae el precio vigente
de la negociación (el más reciente ofrecido por nosotros), en dólares y sin
formato. Responde SOLO con un JSON: {{"precio": <número o null>, "fragmento":
"<el texto exacto de la nota de donde lo sacaste>"}}

Nota: {NOTA}"""

try:
    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=200,
        messages=[{"role": "user", "content": PROMPT}],
    )
except anthropic.RateLimitError:
    morir("429 — límite de uso", "Espera un minuto y reintenta.")
except Exception as e:
    msg = str(e)
    if "credit" in msg.lower() or "billing" in msg.lower():
        morir(
            "la cuenta no tiene crédito",
            "Entra a console.anthropic.com -> Billing y carga saldo "
            "(el mínimo suele ser US$5). Este ejercicio gasta centavos.",
        )
    morir(f"{type(e).__name__}: {e}", "Mándame el mensaje completo.")

texto = respuesta.content[0].text.strip()
print(f"\n  respuesta:\n    {texto}")

uso = respuesta.usage
print(f"\n  tokens: {uso.input_tokens} entrada / {uso.output_tokens} salida")
print(f"  estimado para las 18 notas del caso: "
      f"~{(uso.input_tokens + uso.output_tokens) * 18:,} tokens (centavos)")

# ---------------------------------------------------------------------------
titulo("Verificación anti-alucinación")
# La regla del paso 4: todo dato extraído debe poder señalarse DENTRO de la
# nota original. Si el fragmento no está literalmente ahí, se descarta.
import json
import re

try:
    datos = json.loads(re.search(r"\{.*\}", texto, re.S).group())
except Exception:
    print("  AVISO: la respuesta no vino como JSON limpio.")
    print("  No es bloqueante: el paso 4 fuerza el formato con más precisión.")
    datos = {}

fragmento = (datos.get("fragmento") or "").strip()
if fragmento and fragmento.lower() in NOTA.lower():
    print(f'  OK  el fragmento "{fragmento}" existe literalmente en la nota')
    print(f"      precio extraído: {datos.get('precio')}")
    if datos.get("precio") == 3050000:
        print("      >>> correcto: eligió $3.05M (nuestra respuesta), "
              "no $3.25M (la contraoferta)")
        print("      Ese es exactamente el caso que un regex no puede resolver.")
elif fragmento:
    print(f'  ATENCIÓN: el fragmento "{fragmento}" NO está en la nota')
    print("  En el paso 4 un dato así se descarta automáticamente.")

print("\n" + "=" * 60)
print("API FUNCIONANDO. Listo para construir el paso 4.")
print(f"Modelo: {MODELO}")
"""Quick check: what does the catering tab actually look like right now?"""
from ingest import cargar_todo

hojas, _ = cargar_todo(verbose=False)

for nombre, df in hojas.items():
    cols = [c for c in df.columns if c not in {"_hoja", "_fila_excel", "assumptions"}]
    print(f"\n{nombre}  ({len(df)} filas)")
    print("  encabezados:", cols[:6], "...")

df = hojas["catering_clientB_jfk"]
print("\n--- catering, primeras 3 columnas de datos ---")
cols = [c for c in df.columns if c not in {"_hoja", "_fila_excel", "assumptions"}][:3]
print(df[["_fila_excel"] + cols].to_string(index=False))
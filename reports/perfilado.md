# Perfilado de calidad de datos

Generado automáticamente el 2026-09-21 por `profiling.py` (paso 2).

Ningún dato fue modificado para producir este informe.

```

######################################################################
#  PASO 2 — PERFILADO DE CALIDAD
#  No se modifica ningún dato. Esto produce evidencia.
######################################################################

======================================================================
1. ESTRUCTURA — ¿está bien formada la tabla?
======================================================================

  av_clientC_atl
    6 filas x 21 columnas
    sin anomalías estructurales

  food_clientA_nyc
    8 filas x 13 columnas
    columnas sin encabezado: ['__col_12', '__col_13']
    columnas 100% vacías: ['__col_12', '__col_13']

  contact_tracker
    5 filas x 9 columnas
    sin anomalías estructurales

  catering_clientB_jfk
    5 filas x 25 columnas
    columnas sin encabezado: ['__col_25']
    columnas 100% vacías: ['__col_25']
    filas completamente vacías (fila del archivo): [6]

  target_weekly_view
    7 filas x 11 columnas
    columnas sin encabezado: ['__col_10', '__col_11']
    columnas 100% vacías: ['__col_10', '__col_11']
    filas completamente vacías (fila del archivo): [8]

======================================================================
2. COMPLETITUD — ¿qué tan llena está?
======================================================================

  % de celdas CON dato, por columna canónica y hoja
  (— = la columna no existe en esa hoja)

  campo                     food        av  catering
  client                    100%      100%      100%
  market                    100%      100%      100%
  status                    100%      100%      100%
  notes                     100%      100%      100%
  city                       62%       83%      100%
  list_price                 50%       50%      100%
  address                    62%       50%       75%
  bldg_sf                    50%       33%      100%
  price_per_sf               50%       33%      100%
  state                        —       83%      100%
  lot_sf                     25%       50%      100%
  price_per_lot_sf           25%       50%      100%
  broker                       —       50%      100%
  property_type                —       50%      100%
  sale_status                  —       50%      100%
  clear_height_min             —       33%      100%
  broker_phone                 —       50%       75%
  power_amps                   —       33%       75%
  gl_doors                     —         —      100%
  dh_doors                     —       33%       50%
  zoning                       —       50%       25%
  days_on_market               —       50%       25%
  miles_from_airport           —         —       25%
  rent_per_sf                  —         —       25%
  __col_12                    0%         —         —
  __col_13                    0%         —         —
  __col_25                     —         —        0%

======================================================================
3. VALIDEZ — ¿los valores son del tipo que dicen ser?
======================================================================

  Valores distintos en campos categóricos

  Status:
    food_clientA_nyc       Listed, Negotiating PSA, On Hold, Sourcing, Under Contract, nego PSA, pre loi
    av_clientC_atl         In Contract, Negotiating LOI, Negotiating PSA, SOURCING, Sourcing, sourcing
    catering_clientB_jfk   In Pipeline, Negotiating LOI, in pipeline
    >>> 13 valores distintos en total

  Property Type:
    av_clientC_atl         Industrial, Land, Retail
    catering_clientB_jfk   Industrial
    >>> 3 valores distintos en total

  Sale Status:
    av_clientC_atl         Active
    catering_clientB_jfk   Active
    >>> 1 valores distintos en total

  Valores que NO convierten al tipo esperado por el esquema
    clear_height_min     ['15-51']
    power_amps           ['2,000A', '2,400A', '200A', '3,500A', '400A']

======================================================================
4. UNICIDAD — ¿hay cosas repetidas?
======================================================================

  Direcciones repetidas (misma hoja)
    ninguna

  Teléfonos en más de una fila
  (NO es error: un broker lleva varios deals. Importa para el join)
    203-555-5365  ->  catering_clientB_jfk!4 (Matt M.), catering_clientB_jfk!5 (Matt M.)
    310-555-0192  ->  av_clientC_atl!3 (Dana Wu), av_clientC_atl!4 (Dana Wu)

  Cruce con contact_tracker (join por teléfono)
    404-555-0110
      en deals   : av_clientC_atl!2 (Sam Ortiz)
      en tracker : James Ford
      >>> DISCREPANCIA: el nombre del broker no coincide

======================================================================
5. OUTLIERS — señalar, NO corregir
======================================================================

  Un dato real que 'se ve raro' sigue siendo un dato real.
  Esta sección los lista para revisión humana.

    av_clientC_atl!3   price_per_sf         1,138.70   (rango esperado 20–800)
        5861 Washington Blvd
    catering_clientB_jfk!2   days_on_market         423.00   (rango esperado 0–365)
        9213 183rd St

======================================================================
6. DERIVADOS — ¿puedo confiar en las columnas calculadas?
======================================================================

  38 valores calculados verificados
  0 discrepancias — las columnas calculadas del origen son confiables
  (aun así se recalculan en el paso 3: es gratis y elimina la duda)

======================================================================
7. NOTAS — ¿qué hay escondido en el texto libre?
======================================================================

  No extrae nada todavía (eso es el paso 4): solo dimensiona.

  18 notas con contenido

    incertidumbre         6 notas
        food_clientA_nyc!4: looking for additional options near JFK, no strong candidates yet
        food_clientA_nyc!9: received lease draft, $4M/yr rent, 90 day DD, 12 months free rent
    monto                 5 notas
        food_clientA_nyc!5: partial cooler, toured, countered at $3.25M, we responded at $3.05M
        food_clientA_nyc!6: closing 11/25, deposit $250,000
    plazo_dias            3 notas
        food_clientA_nyc!2: full cooler, negotiating PSA - 60 day dd, 30 day close
        food_clientA_nyc!3: partial cooler - 60 day dd, 30 day close
    precio_negociado      3 notas
        food_clientA_nyc!5: partial cooler, toured, countered at $3.25M, we responded at $3.05M
        av_clientC_atl!3: agreed to $3M, negotiating final PSA terms
    fecha                 2 notas
        food_clientA_nyc!6: closing 11/25, deposit $250,000
        av_clientC_atl!2: DD date 9/18, kicked off environmental review
    arriendo              1 notas
        food_clientA_nyc!9: received lease draft, $4M/yr rent, 90 day DD, 12 months free rent

======================================================================
8. COBERTURA — ¿en qué se parecen las hojas ENTRE SÍ?
======================================================================

  Esta matriz es el insumo directo de la unificación (paso 5).
  Demuestra que el esquema canónico salió de comparar las hojas.

  campo                       food        av  catering   tier
  address                        X         X         X   core
  bldg_sf                        X         X         X   core
  city                           X         X         X   core
  client                         X         X         X   core
  list_price                     X         X         X   core
  lot_sf                         X         X         X   core
  market                         X         X         X   core
  notes                          X         X         X   core
  price_per_lot_sf               X         X         X   derived
  price_per_sf                   X         X         X   derived
  status                         X         X         X   core
  broker                         —         X         X   extended
  broker_phone                   —         X         X   extended
  clear_height_min               —         X         X   extended
  days_on_market                 —         X         X   extended
  dh_doors                       —         X         X   extended
  power_amps                     —         X         X   extended
  property_type                  —         X         X   extended
  sale_status                    —         X         X   extended
  state                          —         X         X   core
  zoning                         —         X         X   extended
  (sin mapeo) __col_12           X         —         —   ?
  (sin mapeo) __col_13           X         —         —   ?
  (sin mapeo) __col_25           —         —         X   ?
  gl_doors                       —         —         X   vertical
  miles_from_airport             —         —         X   vertical
  rent_per_sf                    —         —         X   vertical

  en las 3 hojas: 11   en una sola: 6
  SIN MAPEO EN EL ESQUEMA: ['(sin mapeo) __col_12', '(sin mapeo) __col_13', '(sin mapeo) __col_25']
  (no se descartan: van a `extras` en el paso 3)

======================================================================
9. TRAZABILIDAD — ¿target_weekly_view se reconstruye desde los datos?
======================================================================

  Si algo de la vista semanal NO se puede derivar, hay trabajo
  manual escondido — justo lo que el brief quiere eliminar.

  Columnas de la vista semanal -> campo de origen
    Vertical     -> vertical (derivado del nombre de la hoja)
    Market       -> market (columna Metro)
    Address      -> address
    Status       -> status (normalizado)
    $            -> list_price
    SF           -> bldg_sf
    $/SF         -> price_per_sf
    Lot Size     -> lot_sf
    Notes        -> notes (recortada)

  Filas de la vista semanal -> fila de origen
    Food                 7500 NW 25th St          -> food_clientA_nyc!2
    Food                 3330 NW 60th St          -> food_clientA_nyc!3
    Food                 (sin dirección)          -> fila de Sourcing, se rastrea por vertical+mercado
    Autonomous Vehicle   635 Angier Ave           -> av_clientC_atl!2
    Autonomous Vehicle   5861 Washington Blvd     -> av_clientC_atl!3
    Airline Catering     9213 183rd St            -> catering_clientB_jfk!2

  6 filas en la vista semanal vs 18 registros en las hojas de origen
  >>> La vista semanal es un SUBCONJUNTO: alguien decide qué mostrar.
      Ese criterio de selección es una decisión de la VISTA (paso 6),
      no del dataset. Se documenta aquí, se decide allá.

  VEREDICTO: todo derivable desde el dataset unificado

######################################################################
#  FIN DEL PERFILADO — ningún dato fue modificado
######################################################################

```

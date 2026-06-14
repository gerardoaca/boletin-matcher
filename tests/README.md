# Suite de regresión — Boletin Matcher

Tests automáticos que protegen contra dos regresiones críticas y cubren el
matcher contra fixtures reales (4 boletines en `input/boletines/` + el
listado de producción `input/listados/listado_clientes.csv`).

## Qué protegen

1. **Commit `dfd7c41` — parche quirúrgico `juzgado_conflicto`.** Antes de
   este parche, cuando el detector de headers no encontraba ningún juzgado
   en la hoja, el matcher degradaba TODOS los matches a `REVISION` y el
   total de validadas caía de 6 a 0. El test
   `test_no_regresion_juzgado_conflicto` asegura que sigan apareciendo
   exactamente 6 validadas contra el set real.

2. **Bug #3 — `es_actor_reservado("")` devolvía `True`.** Causaba que
   listados con celdas de actor vacías fueran tratados como reservados y
   cero matches pasaran. Los tests en `test_normalizer.py` y
   `test_listado_loader.py` fijan el comportamiento correcto.

## Cómo correr

```bash
cd /Users/gca/Desktop/boletines-app
source .venv/bin/activate
pip install pytest      # solo la primera vez
pytest tests/ -v
```

### Skipping de tests lentos

Los tests con marca `regresion` cargan los 4 PDFs reales con PyMuPDF —
toman ~10-30 s. Para iterar rápido sobre unitarios:

```bash
pytest tests/ -v -k "not regresion"
```

Para correr **solo** el smoke de regresión:

```bash
pytest tests/ -v -m regresion
```

## Estructura

```
tests/
├── __init__.py
├── conftest.py                  # fixtures: repo_root, listado_real, boletines_pdfs
├── test_normalizer.py           # es_actor_reservado, expedientes, tokens
├── test_listado_loader.py       # carga CSV real, coherencia reservado/desconocido
├── test_matcher_regresion.py    # 6 validadas end-to-end
└── README.md
```

## Si algún test falla

- **`test_no_regresion_juzgado_conflicto`** falla → probablemente alguien
  removió la guarda `juzgado_detectado` en `core/matcher.py`. Revisar
  diff contra el commit `dfd7c41`.
- **`test_es_actor_reservado_vacio_no_es_reservado`** falla → regresó el
  Bug #3 en `core/normalizer.py::es_actor_reservado`.
- **Un `test_validacion_esperada[...]` falla** → cambió el contenido de
  un boletín, del listado o de la lógica de extracción. Verificar
  manualmente cuál de los 6 matches se rompió e investigar.

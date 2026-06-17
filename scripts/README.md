# scripts/

Scripts de operaciones para Boletin Matcher.

## `daily_healthcheck.py`

Health check diario invocado por cron a las 07:00 CST. Ejecuta 10 verificaciones
y deja reporte en `logs/healthcheck/YYYY-MM-DD.md`.

### Uso

```bash
cd /Users/gca/Desktop/boletines-app
source .venv/bin/activate

# Ejecución completa (incluye llamada de prueba a Claude Haiku 4.5)
python3 scripts/daily_healthcheck.py

# Sin llamada IA (no consume tokens; útil para dev/CI)
python3 scripts/daily_healthcheck.py --no-ia

# Con trazas extendidas a stderr
python3 scripts/daily_healthcheck.py --verbose
```

### Exit codes

| Código | Significado |
|---|---|
| `0` | Todo OK, o solo fallaron checks no-críticos (8, 9, 10) |
| `1` | Falló al menos un check crítico (1, 3, 5, 6) |
| `2` | Error fatal antes de poder ejecutar los checks |

### Checks

| # | Check | Crítico | Timeout |
|---|---|---|---|
| 1 | URL pública `/_stcore/health` responde | ✅ | 30s |
| 2 | App pública carga HTML del login | | 30s |
| 3 | `ANTHROPIC_API_KEY` válida (1 mensaje a Haiku 4.5) | ✅ | implícito |
| 4 | Sintaxis Python en todo el repo (`ast.parse`) | | n/a |
| 5 | `pytest tests/` pasa | ✅ | 15 min |
| 6 | Smoke test end-to-end (6 validadas exactas) | ✅ | n/a |
| 7 | OCR disponible (`ocrmypdf` + `tesseract`) | | 10s |
| 8 | Espacio en `output/` ≥ 500 MB | | n/a |
| 9 | `git status` limpio | | 15s |
| 10 | HEAD sincronizado con remoto | | 30s |

Críticos → exit 1. No-críticos → exit 0 con warning en stdout.

### Cron sugerido

```cron
# Health check diario Boletin Matcher — 07:00 CST
0 7 * * * cd /Users/gca/Desktop/boletines-app && source .venv/bin/activate && python3 scripts/daily_healthcheck.py >> logs/healthcheck/cron.log 2>&1
```

### Salida

- **stdout**: una línea por check + verdict final + ruta del reporte.
- **`logs/healthcheck/YYYY-MM-DD.md`**: reporte markdown con tabla,
  detalles de fallas (con traceback si aplica) y métricas leídas de
  `logs/audit.jsonl` (runs últimos 30d, última validada, costo IA acumulado).

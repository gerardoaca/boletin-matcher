# Boletines Judiciales — Buscador de Coincidencias

App para encontrar coincidencias exactas entre el listado de clientes de un despacho jurídico y los boletines judiciales diarios (federales y de la CDMX).

## Metodología

1. **Extracción** del PDF hoja por hoja (PyMuPDF) con OCR automático para boletines escaneados (`ocrmypdf`).
2. **Normalización**: expedientes a formato canónico `NNNN/AAAA`, nombres a mayúsculas sin acentos, soporte de separadores `/` y `-`.
3. **Matching** con doble llave:
   - **Ruta A — actor por tokens**: cada parte del actor del listado, separada por *Y/coma*, se valida si todos sus tokens significativos aparecen en la ventana del expediente.
   - **Ruta A2 — cliente como parte**: si el cliente aparece como actor o demandado en el bloque.
   - **Ruta B — reservado/sucesión**: cuando el actor es genérico (`SUCESIÓN`, `RESERVADO`, `***`), valida con expediente + juzgado de la sección.
   - **Filtro anti-homónimo**: si ni el actor ni el cliente del listado aparecen en la hoja completa, se descarta silenciosamente.
4. **Transcripción con Claude Haiku 4.5** del bloque validado: extrae síntesis literal, juzgado y tipo de acuerdo (sin inventar datos).
5. **Reporte `documento.md`** con número de hoja, líneas, hash SHA1 del bloque y bloque literal para auditoría.

## Estructura

```
boletines-app/
├── app.py                      # interfaz Streamlit
├── core/
│   ├── extractor.py            # PDF → ventanas con expediente
│   ├── normalizer.py           # canonización, tokens
│   ├── listado_loader.py       # Excel/CSV/PDF → registros
│   ├── matcher.py              # motor con doble llave
│   ├── transcriber.py          # Claude Haiku 4.5
│   ├── reporter.py             # documento.md
│   └── ocr.py                  # ocrmypdf wrapper
├── packages.txt                # apt packages para Streamlit Cloud
├── requirements.txt
├── .streamlit/config.toml
└── input/, output/             # datos locales (gitignored)
```

## Uso local

```bash
cd boletines-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install ocrmypdf tesseract tesseract-lang   # solo si trabajas con PDFs escaneados
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
streamlit run app.py
```

## Despliegue en Streamlit Community Cloud

1. Push del repo a GitHub.
2. Ir a https://streamlit.io/cloud → **New app** → seleccionar repo y `app.py`.
3. **Advanced settings → Secrets** → pegar:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
4. Deploy. La URL pública será `https://<app>.streamlit.app`.
5. `packages.txt` se aplica automáticamente (instala tesseract con español + ocrmypdf).

## Listado de clientes — formato

| Columna | Acepta nombres | Obligatoria |
|---|---|---|
| `cliente` | cliente, asignado, responsable | recomendada |
| `actor` | actor, demandante, promovente, parte actora | recomendada |
| `expediente` | expediente, juicio, no expediente, exp | **sí** |
| `juzgado` | juzgado, tribunal, autoridad | **sí para reservados** |

Para **expedientes con actor reservado** (sucesiones, datos protegidos), poner el actor como `***`, `RESERVADO`, `SUCESION INTESTAMENTARIA` o `SUCESION TESTAMENTARIA`. La columna juzgado se vuelve obligatoria para esos casos.

## Privacidad

- `.gitignore` excluye TODOS los datos de clientes reales (PDFs, Excel, CSV, imágenes en `input/listados/` e `input/boletines/`).
- La API key NUNCA se sube al repo. Use `.env` local o Streamlit Secrets.
- El listado real se carga vía UI cada vez (o se persiste localmente con la opción "Guardar este listado como predeterminado").

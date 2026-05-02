"""Interfaz Streamlit para procesar boletines judiciales contra listado de clientes."""
import os
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from core.extractor import extraer_hojas
from core.listado_loader import cargar_listado
from core.matcher import buscar_coincidencias
from core.transcriber import enriquecer
from core.reporter import generar_md
from core.ocr import (
    pdf_tiene_texto,
    aplicar_ocr,
    herramientas_ocr_disponibles,
)

load_dotenv()

st.set_page_config(page_title="Boletines Judiciales — Coincidencias", layout="wide")
st.title("Buscador de coincidencias en boletines judiciales")
st.caption("Matching exacto expediente + actor (o expediente + juzgado para reservados).")

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

col1, col2 = st.columns(2)
with col1:
    boletin_file = st.file_uploader(
        "Boletín judicial del día (PDF)", type=["pdf"], key="boletin"
    )
with col2:
    listado_file = st.file_uploader(
        "Listado de clientes (Excel, CSV o PDF)",
        type=["xlsx", "xls", "csv", "pdf"],
        key="listado",
    )

col3, col4 = st.columns(2)
with col3:
    omitir_ia = st.checkbox(
        "Omitir transcripción con IA",
        value=False,
        help="Por defecto la app usa Claude Haiku 4.5 para extraer síntesis literal y juzgado de cada bloque validado.",
    )
with col4:
    guardar_listado = st.checkbox(
        "Guardar este listado como predeterminado",
        value=False,
        help="Sobrescribe input/listados/listado_clientes.csv (el archivo del repo).",
    )
ocr_auto = st.checkbox(
    "Aplicar OCR automáticamente si el PDF no tiene texto seleccionable",
    value=True,
)
ocr_ok, ocr_msg = herramientas_ocr_disponibles()
if not ocr_ok:
    st.info(f"ℹ️ {ocr_msg}")

if st.button("Procesar", type="primary", disabled=not (boletin_file and listado_file)):
    if not os.environ.get("ANTHROPIC_API_KEY") and not omitir_ia:
        st.error("Falta ANTHROPIC_API_KEY. Configúrala en el archivo .env o marca 'Omitir transcripción con IA'.")
        st.stop()

    with tempfile.TemporaryDirectory() as tmp:
        bol_path = Path(tmp) / boletin_file.name
        bol_path.write_bytes(boletin_file.getvalue())
        lis_path = Path(tmp) / listado_file.name
        lis_path.write_bytes(listado_file.getvalue())

        if guardar_listado:
            destino = Path(__file__).parent / "input" / "listados" / listado_file.name
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(listado_file.getvalue())
            st.info(f"📌 Listado guardado en `{destino.name}` para próximas ejecuciones.")

        with st.status("Procesando…", expanded=True) as status:
            st.write("Cargando listado de clientes…")
            try:
                listado = cargar_listado(str(lis_path))
            except Exception as e:
                st.error(f"Error cargando listado: {e}")
                st.stop()
            st.write(f"  → {len(listado)} registros con expediente válido.")

            bol_path_final = str(bol_path)
            if ocr_auto:
                if pdf_tiene_texto(bol_path_final):
                    st.write("PDF tiene texto seleccionable → no requiere OCR.")
                else:
                    ok, msg = herramientas_ocr_disponibles()
                    if not ok:
                        st.error(msg)
                        st.stop()
                    st.write("PDF escaneado detectado → aplicando OCR (puede tardar varios minutos)…")
                    try:
                        bol_path_final = aplicar_ocr(bol_path_final, idioma="spa")
                        st.write(f"  → OCR aplicado: `{bol_path_final}`")
                    except Exception as e:
                        st.error(f"Falló OCR: {e}")
                        st.stop()

            st.write("Extrayendo hojas del boletín…")
            hojas = extraer_hojas(bol_path_final)
            st.write(f"  → {len(hojas)} hojas extraídas.")

            st.write("Buscando coincidencias (matching determinista)…")
            validadas, revision = buscar_coincidencias(hojas, listado)
            st.write(f"  → {len(validadas)} validadas, {len(revision)} para revisión.")

            if not omitir_ia and validadas:
                st.write(f"Transcribiendo síntesis con Claude ({len(validadas)} bloques)…")
                enriquecidas = enriquecer(validadas)
            else:
                enriquecidas = [{
                    "coincidencia": c,
                    "juzgado_boletin": "",
                    "sintesis": c.bloque_texto[:400],
                    "tipo_acuerdo": "",
                    "confianza": "n/a",
                } for c in validadas]

            fecha = datetime.now().strftime("%Y-%m-%d_%H%M")
            md = generar_md(
                boletin_nombre=boletin_file.name,
                fecha_proceso=fecha,
                enriquecidas=enriquecidas,
                revision=revision,
            )
            out_path = OUTPUT_DIR / f"{fecha}_{Path(boletin_file.name).stem}.md"
            out_path.write_text(md, encoding="utf-8")
            status.update(label="Listo", state="complete")

    st.success(f"Documento generado: `{out_path}`")
    st.download_button(
        "Descargar documento.md",
        data=md,
        file_name=out_path.name,
        mime="text/markdown",
    )
    st.markdown("---")
    st.markdown(md)

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
from core.page_renderer import renderizar_hoja_con_resaltado

load_dotenv(override=True)

st.set_page_config(page_title="Boletines Judiciales — Coincidencias", layout="wide")


# ═══════════════════════════════════════════════════════════════
# AUTENTICACIÓN (contraseña compartida)
# ═══════════════════════════════════════════════════════════════
def _obtener_password_correcto() -> str | None:
    """Lee la contraseña esperada de st.secrets o env var APP_PASSWORD."""
    # 1. Variable de entorno (uso local con .env)
    pw = os.environ.get("APP_PASSWORD")
    if pw:
        return pw.strip()
    # 2. Streamlit Cloud secrets — probar varias formas
    try:
        if "APP_PASSWORD" in st.secrets:
            return str(st.secrets["APP_PASSWORD"]).strip()
    except Exception:
        pass
    try:
        return str(st.secrets.APP_PASSWORD).strip()
    except Exception:
        pass
    return None


def _diagnostico_auth() -> str:
    """Diagnóstico de la configuración de autenticación (sin exponer la contraseña)."""
    info = []
    pw_env = os.environ.get("APP_PASSWORD", "")
    info.append(f"- APP_PASSWORD en env: {'sí (largo ' + str(len(pw_env)) + ')' if pw_env else 'no'}")
    try:
        keys = list(st.secrets.keys()) if hasattr(st.secrets, "keys") else []
        info.append(f"- Claves en st.secrets: {keys}")
        if "APP_PASSWORD" in keys:
            v = str(st.secrets["APP_PASSWORD"])
            info.append(f"- APP_PASSWORD en secrets: sí (largo {len(v)})")
        else:
            info.append("- APP_PASSWORD en secrets: NO")
    except Exception as e:
        info.append(f"- st.secrets falló: {type(e).__name__}: {e}")
    return "\n".join(info)


def _verificar_password() -> bool:
    """Bloquea la app hasta que el usuario ingrese la contraseña correcta."""
    pw_esperado = _obtener_password_correcto()
    # Si NO hay contraseña configurada → app abierta (modo legacy)
    if not pw_esperado:
        return True

    if st.session_state.get("autenticado"):
        return True

    st.markdown("## 🔒 Acceso restringido")
    st.markdown("Esta aplicación requiere contraseña para entrar.")
    with st.form("login_form", clear_on_submit=False):
        pw_intento = st.text_input("Contraseña", type="password", key="pw_input")
        submitted = st.form_submit_button("Entrar")
        if submitted:
            if pw_intento.strip() == pw_esperado:
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta")

    with st.expander("🔧 Diagnóstico (solo para depurar)"):
        st.code(_diagnostico_auth(), language="text")
    return False


if not _verificar_password():
    st.stop()


# ═══════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════
st.title("Buscador de coincidencias en boletines judiciales")
st.caption("Matching exacto expediente + actor (o expediente + juzgado para reservados).")
if st.sidebar.button("Cerrar sesión"):
    st.session_state.pop("autenticado", None)
    st.rerun()

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Contadores en session_state para poder "borrar" el archivo del uploader
# (Streamlit no tiene botón X nativo; reseteamos cambiando el key del widget)
if "boletin_uploader_id" not in st.session_state:
    st.session_state.boletin_uploader_id = 0
if "listado_uploader_id" not in st.session_state:
    st.session_state.listado_uploader_id = 0

col1, col2 = st.columns(2)
with col1:
    boletin_file = st.file_uploader(
        "Boletín judicial del día (PDF, hasta 400 MB)",
        type=["pdf"],
        key=f"boletin_{st.session_state.boletin_uploader_id}",
    )
    if boletin_file is not None:
        if st.button("🗑️ Borrar boletín cargado", key="del_boletin"):
            st.session_state.boletin_uploader_id += 1
            st.rerun()

with col2:
    listado_file = st.file_uploader(
        "Listado de clientes (Excel, CSV o PDF)",
        type=["xlsx", "xls", "csv", "pdf"],
        key=f"listado_{st.session_state.listado_uploader_id}",
    )
    if listado_file is not None:
        if st.button("🗑️ Borrar listado cargado", key="del_listado"):
            st.session_state.listado_uploader_id += 1
            st.rerun()

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

            # Generar PNG con resaltado amarillo de cada coincidencia validada
            if validadas:
                st.write(f"Generando hojas con resaltado amarillo ({len(validadas)})…")
                fecha_corta = datetime.now().strftime("%Y%m%d_%H%M%S")
                imgs_dir = OUTPUT_DIR / "imgs" / f"{fecha_corta}_{Path(boletin_file.name).stem}"
                for item in enriquecidas:
                    c = item["coincidencia"]
                    nombre_png = imgs_dir / f"hoja_{c.pagina_impresa or c.hoja}_exp_{c.expediente.replace('/', '-')}.png"
                    ruta = renderizar_hoja_con_resaltado(
                        pdf_path=bol_path_final,
                        page_index_0based=c.hoja - 1,
                        expediente=c.expediente,
                        actor=c.actor_listado,
                        cliente=c.cliente,
                        salida_png=nombre_png,
                    )
                    if ruta:
                        # Ruta relativa al .md de salida (ambos en output/)
                        item["imagen_hoja"] = str(ruta.relative_to(OUTPUT_DIR))

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

"""Interfaz Streamlit para procesar boletines judiciales contra listado de clientes."""
import hmac
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from core.extractor import extraer_hojas
from core.listado_loader import cargar_listado
from core.matcher import buscar_coincidencias
from core.transcriber import enriquecer, costo_total
from core.reporter import generar_md
from core.ocr import (
    pdf_tiene_texto,
    aplicar_ocr,
    herramientas_ocr_disponibles,
)
from core.page_renderer import renderizar_hoja_con_resaltado
from core.zip_packager import empaquetar_reporte_zip
from core.audit_log import registrar_run, leer_runs

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
    # FAIL-CLOSED: si no hay password configurada, no abrimos la app.
    if not pw_esperado:
        st.error("APP_PASSWORD no configurada. Contacte al administrador.")
        st.stop()

    if st.session_state.get("autenticado"):
        # Diagnóstico opcional SOLO post-auth y bajo flag de debug explícito.
        if os.environ.get("DEBUG_AUTH") == "1":
            with st.sidebar.expander("🔧 Diagnóstico auth (DEBUG_AUTH=1)"):
                st.code(_diagnostico_auth(), language="text")
        return True

    # Rate limiting con backoff exponencial por sesión.
    intentos_fallidos = st.session_state.get("intentos_fallidos", 0)
    bloqueado_hasta = st.session_state.get("bloqueado_hasta", 0.0)
    ahora = time.time()
    restante = int(bloqueado_hasta - ahora)

    st.markdown("## 🔒 Acceso restringido")
    st.markdown("Esta aplicación requiere contraseña para entrar.")

    bloqueado = restante > 0
    if bloqueado:
        st.warning(
            f"Demasiados intentos fallidos. Espere {restante} segundo(s) antes de reintentar."
        )

    with st.form("login_form", clear_on_submit=False):
        pw_intento = st.text_input("Contraseña", type="password", key="pw_input")
        submitted = st.form_submit_button("Entrar", disabled=bloqueado)
        if submitted and not bloqueado:
            intento_bytes = pw_intento.strip().encode("utf-8")
            esperado_bytes = pw_esperado.encode("utf-8")
            if hmac.compare_digest(intento_bytes, esperado_bytes):
                st.session_state["autenticado"] = True
                st.session_state["intentos_fallidos"] = 0
                st.session_state["bloqueado_hasta"] = 0.0
                st.rerun()
            else:
                intentos_fallidos += 1
                st.session_state["intentos_fallidos"] = intentos_fallidos
                if intentos_fallidos >= 3:
                    espera = min(300, 3 ** (intentos_fallidos - 2))
                    st.session_state["bloqueado_hasta"] = time.time() + espera
                    st.error(
                        f"Contraseña incorrecta. Bloqueado por {espera} segundo(s) "
                        f"tras {intentos_fallidos} intentos fallidos."
                    )
                else:
                    st.error("Contraseña incorrecta")

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

with st.sidebar.expander("🗂️ Últimos runs (auditoría)", expanded=False):
    _ultimos = leer_runs(n=5)
    if not _ultimos:
        st.caption("Sin runs registrados todavía.")
    else:
        # Mostramos del más reciente al más viejo, sólo columnas clave.
        _tabla = [
            {
                "ts_utc": r.get("ts_utc", "")[:19],
                "boletin": r.get("boletin", "")[:32],
                "val": r.get("validadas"),
                "rev": r.get("revision"),
                "err_ia": r.get("errores_ia"),
                "usd": r.get("costo_usd"),
                "s": r.get("duracion_s"),
                "git": r.get("git_sha", ""),
            }
            for r in reversed(_ultimos)
        ]
        st.dataframe(_tabla, hide_index=True, use_container_width=True)

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

        _t0_run = time.time()
        errores_ia = 0
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
                progreso_ia = st.empty()
                def _cb(idx, total, errores):
                    progreso_ia.write(
                        f"  → {idx}/{total} transcritos"
                        + (f" — ⚠️ {errores} con error IA" if errores else "")
                    )
                enriquecidas = enriquecer(validadas, progress_cb=_cb)
                errores_ia = sum(1 for e in enriquecidas if e.get("confianza") == "error_api")
                if errores_ia:
                    st.warning(
                        f"⚠️ {errores_ia}/{len(enriquecidas)} bloques fallaron en la "
                        "transcripción IA. Esos bloques mostrarán el texto crudo del "
                        "boletín en lugar de la síntesis. El error específico aparece "
                        "en el reporte para cada caso."
                    )
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
                        # Ruta absoluta para que el zip_packager localice el PNG
                        item["imagen_hoja_abs"] = str(ruta)

            fecha = datetime.now().strftime("%Y-%m-%d_%H%M")
            md = generar_md(
                boletin_nombre=boletin_file.name,
                fecha_proceso=fecha,
                enriquecidas=enriquecidas,
                revision=revision,
            )
            out_path = OUTPUT_DIR / f"{fecha}_{Path(boletin_file.name).stem}.md"
            out_path.write_text(md, encoding="utf-8")

            # Totales de uso/costo de la IA (0.0 si se omitió IA)
            _totales_ia = costo_total(enriquecidas) if (not omitir_ia and validadas) else {
                "tokens_in": 0, "tokens_out": 0, "costo_usd": 0.0, "errores": 0,
            }
            registrar_run(
                boletin_nombre=boletin_file.name,
                boletin_path=Path(bol_path_final),
                listado_path=Path(lis_path),
                validadas=len(validadas),
                revision=len(revision),
                errores_ia=errores_ia,
                costo_usd=_totales_ia["costo_usd"],
                duracion_s=time.time() - _t0_run,
                metadata={
                    "omitir_ia": bool(omitir_ia),
                    "ocr_auto": bool(ocr_auto),
                    "tokens_in": _totales_ia["tokens_in"],
                    "tokens_out": _totales_ia["tokens_out"],
                },
            )

            # Mostrar costo al usuario después del éxito
            if _totales_ia["costo_usd"] > 0:
                st.info(
                    f"💰 Costo IA de este run: ${_totales_ia['costo_usd']:.4f} USD "
                    f"({_totales_ia['tokens_in']:,} tokens in + "
                    f"{_totales_ia['tokens_out']:,} tokens out)"
                )

            status.update(label="Listo", state="complete")

    st.success(f"Documento generado: `{out_path}`")

    # ZIP con .md + imágenes embebidas (rutas válidas al abrirlo fuera del repo)
    imgs_abs = [
        Path(item["imagen_hoja_abs"])
        for item in enriquecidas
        if item.get("imagen_hoja_abs")
    ]
    try:
        zip_bytes = empaquetar_reporte_zip(out_path, imgs_abs)
        st.download_button(
            "📦 Descargar reporte completo (ZIP con imágenes)",
            data=zip_bytes,
            file_name=f"{out_path.stem}.zip",
            mime="application/zip",
            type="primary",
        )
    except Exception as e:
        st.warning(f"No se pudo generar el ZIP: {e}")

    st.download_button(
        "📄 Descargar solo documento.md (sin imágenes)",
        data=md,
        file_name=out_path.name,
        mime="text/markdown",
    )
    st.markdown("---")
    st.markdown(md)

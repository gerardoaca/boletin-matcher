"""Transcripción de síntesis del asunto y juzgado usando Claude."""
import os
import json
from anthropic import Anthropic
from .matcher import Coincidencia

MODEL = "claude-haiku-4-5"

PROMPT_SISTEMA = """Eres un asistente legal que extrae información literal de bloques de boletines judiciales mexicanos. NUNCA inventes datos. Si un dato no aparece, devuelve cadena vacía.

Te paso un bloque de texto de una hoja del boletín y debes devolver JSON con:
- "juzgado": el órgano jurisdiccional ante el cual se ventila el asunto, tal como aparece literalmente
- "sintesis": síntesis literal o lo más cercano a literal del asunto/acuerdo/notificación contenido en el bloque (máx 400 caracteres, sin reformular más allá de quitar saltos de línea)
- "tipo_acuerdo": tipo de acuerdo o notificación si aparece (ej. "AUTO", "SENTENCIA", "EMPLAZAMIENTO"), vacío si no
- "confianza": "alta" si todos los datos están claros, "media" si hay ambigüedad, "baja" si el bloque es ilegible

Devuelve SOLO el JSON, sin texto adicional."""


def _client() -> Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        # Soporte para Streamlit secrets
        try:
            import streamlit as st
            key = st.secrets.get("ANTHROPIC_API_KEY")
        except Exception:
            pass
    if not key:
        raise RuntimeError(
            "Falta ANTHROPIC_API_KEY: configúralo en .env (local) o "
            "en Streamlit Cloud → Settings → Secrets."
        )
    return Anthropic(api_key=key)


def transcribir_bloque(bloque: str) -> dict:
    client = _client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=PROMPT_SISTEMA,
        messages=[{"role": "user", "content": f"Bloque del boletín:\n\n{bloque}"}],
    )
    raw = resp.content[0].text.strip()
    # Limpiar code fences si vinieran
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "juzgado": "",
            "sintesis": bloque[:400],
            "tipo_acuerdo": "",
            "confianza": "baja",
        }


def enriquecer(coincidencias: list[Coincidencia]) -> list[dict]:
    salidas = []
    for c in coincidencias:
        datos = transcribir_bloque(c.bloque_texto)
        salidas.append({
            "coincidencia": c,
            "juzgado_boletin": datos.get("juzgado", ""),
            "sintesis": datos.get("sintesis", ""),
            "tipo_acuerdo": datos.get("tipo_acuerdo", ""),
            "confianza": datos.get("confianza", "media"),
        })
    return salidas

"""Transcripción de síntesis del asunto y juzgado usando Claude."""
import os
import json
from anthropic import Anthropic
from .matcher import Coincidencia

MODEL = "claude-haiku-4-5"

PROMPT_SISTEMA = """Eres un asistente legal que extrae información literal de bloques de boletines judiciales mexicanos. NUNCA inventes datos. Si un dato no aparece, devuelve cadena vacía.

Te paso un bloque de texto que contiene MÚLTIPLES entradas del boletín y un EXPEDIENTE OBJETIVO. Debes localizar EXACTAMENTE la entrada que contiene ese expediente (no otras) y devolver JSON con:

- "entrada_literal": la entrada COMPLETA tal como aparece en el bloque, comenzando desde el nombre del actor hasta el "Acdo." / "Sent." / terminador. SOLO la entrada que contiene el expediente objetivo.
- "juzgado": el órgano jurisdiccional ante el cual se ventila el asunto, tal como aparece literalmente. Vacío si no aparece.
- "sintesis": resumen MUY breve (máx 200 chars) de la entrada del expediente objetivo.
- "tipo_acuerdo": tipo de acuerdo (ej. "AUTO", "SENTENCIA", "EMPLAZAMIENTO", "AUDIENCIA"), vacío si no.
- "confianza": "alta" si encontraste la entrada exacta del expediente; "media" si hay ambigüedad; "baja" si el expediente no aparece en el bloque.

Devuelve SOLO el JSON, sin texto adicional ni markdown."""


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


def transcribir_bloque(bloque: str, expediente: str = "") -> dict:
    client = _client()
    user_msg = (
        f"EXPEDIENTE OBJETIVO: {expediente}\n\n"
        f"BLOQUE DEL BOLETÍN:\n\n{bloque}"
    ) if expediente else f"Bloque del boletín:\n\n{bloque}"
    resp = client.messages.create(
        model=MODEL,
        max_tokens=900,
        system=PROMPT_SISTEMA,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "entrada_literal": bloque[:500],
            "juzgado": "",
            "sintesis": bloque[:200],
            "tipo_acuerdo": "",
            "confianza": "baja",
        }


def enriquecer(coincidencias: list[Coincidencia]) -> list[dict]:
    salidas = []
    for c in coincidencias:
        datos = transcribir_bloque(c.bloque_texto, expediente=c.expediente)
        salidas.append({
            "coincidencia": c,
            "entrada_literal": datos.get("entrada_literal", ""),
            "juzgado_boletin": datos.get("juzgado", ""),
            "sintesis": datos.get("sintesis", ""),
            "tipo_acuerdo": datos.get("tipo_acuerdo", ""),
            "confianza": datos.get("confianza", "media"),
        })
    return salidas

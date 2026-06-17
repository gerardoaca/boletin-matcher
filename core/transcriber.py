"""Transcripción de síntesis del asunto y juzgado usando Claude.

Robustez:
- Retry con backoff exponencial ante errores transitorios (429, 529, 5xx).
- Captura específica de BadRequestError para reportar la causa real.
- Si un bloque falla, se registra el error y se continúa con los siguientes
  (no se aborta todo el run).
- Fallback determinista: si la API falla definitivamente, se devuelve el
  inicio del bloque como entrada_literal/síntesis con confianza='error_api'.
"""
import os
import json
import time
from anthropic import Anthropic
from anthropic import (
    APIError,
    APIStatusError,
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    RateLimitError,
)
from .matcher import Coincidencia

# Usar el id dateado: más estable entre versiones del SDK que el alias corto.
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 900
MAX_BLOQUE_CHARS = 8000  # truncar bloques absurdamente grandes
MAX_REINTENTOS = 3

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
        try:
            import streamlit as st
            if "ANTHROPIC_API_KEY" in st.secrets:
                key = str(st.secrets["ANTHROPIC_API_KEY"])
        except Exception:
            pass
    if not key:
        raise RuntimeError(
            "Falta ANTHROPIC_API_KEY: configúralo en .env (local) o "
            "en Streamlit Cloud → Settings → Secrets."
        )
    return Anthropic(api_key=key)


def _fallback(bloque: str, motivo: str, confianza: str = "baja") -> dict:
    """Estructura de salida cuando la IA no puede o falla."""
    return {
        "entrada_literal": bloque[:500],
        "juzgado": "",
        "sintesis": bloque[:200],
        "tipo_acuerdo": "",
        "confianza": confianza,
        "error": motivo or "",
    }


def transcribir_bloque(bloque: str, expediente: str = "") -> dict:
    """Llama a Claude para extraer datos estructurados del bloque.

    Devuelve siempre un dict válido. En caso de error definitivo, devuelve
    el fallback con la causa en la clave 'error' y confianza='error_api'.
    """
    # Defensa contra bloques absurdamente grandes
    bloque_recortado = bloque[:MAX_BLOQUE_CHARS]
    user_msg = (
        f"EXPEDIENTE OBJETIVO: {expediente}\n\n"
        f"BLOQUE DEL BOLETÍN:\n\n{bloque_recortado}"
    ) if expediente else f"Bloque del boletín:\n\n{bloque_recortado}"

    try:
        client = _client()
    except RuntimeError as e:
        return _fallback(bloque, f"config: {e}", confianza="error_api")

    ultima_excepcion: str = ""
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=PROMPT_SISTEMA,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:].strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                # Respuesta sin JSON parseable — devolver fallback con texto crudo
                return _fallback(bloque, f"json_parse: {e}", confianza="baja")

        except BadRequestError as e:
            # Error definitivo en la request (no tiene sentido reintentar)
            return _fallback(
                bloque,
                f"BadRequest ({e.status_code if hasattr(e, 'status_code') else '?'}): {str(e)[:160]}",
                confianza="error_api",
            )

        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            # Errores transitorios → backoff y reintentar
            ultima_excepcion = f"{type(e).__name__}: {str(e)[:120]}"
            if intento < MAX_REINTENTOS:
                espera = 2 ** intento  # 2s, 4s, 8s
                time.sleep(espera)
                continue
            return _fallback(bloque, f"transitorio: {ultima_excepcion}", confianza="error_api")

        except APIStatusError as e:
            # 5xx — reintentable
            status = getattr(e, "status_code", 0)
            ultima_excepcion = f"HTTP {status}: {str(e)[:120]}"
            if 500 <= status < 600 and intento < MAX_REINTENTOS:
                time.sleep(2 ** intento)
                continue
            return _fallback(bloque, ultima_excepcion, confianza="error_api")

        except APIError as e:
            # Cualquier otro error de la API
            ultima_excepcion = f"{type(e).__name__}: {str(e)[:120]}"
            if intento < MAX_REINTENTOS:
                time.sleep(2 ** intento)
                continue
            return _fallback(bloque, ultima_excepcion, confianza="error_api")

        except Exception as e:
            # Algo no contemplado — no morir, registrar y devolver fallback
            return _fallback(
                bloque,
                f"inesperado ({type(e).__name__}): {str(e)[:120]}",
                confianza="error_api",
            )

    return _fallback(bloque, ultima_excepcion or "agotaron reintentos", confianza="error_api")


def enriquecer(coincidencias: list[Coincidencia], progress_cb=None) -> list[dict]:
    """Procesa cada coincidencia y reporta progreso/errores.

    progress_cb es una función opcional callable(idx, total, errores_hasta_ahora).
    """
    salidas = []
    errores = 0
    total = len(coincidencias)
    for i, c in enumerate(coincidencias, start=1):
        datos = transcribir_bloque(c.bloque_texto, expediente=c.expediente)
        if datos.get("confianza") == "error_api":
            errores += 1
        salidas.append({
            "coincidencia": c,
            "entrada_literal": datos.get("entrada_literal", ""),
            "juzgado_boletin": datos.get("juzgado", ""),
            "sintesis": datos.get("sintesis", ""),
            "tipo_acuerdo": datos.get("tipo_acuerdo", ""),
            "confianza": datos.get("confianza", "media"),
            "error_ia": datos.get("error", ""),
        })
        if progress_cb is not None:
            try:
                progress_cb(i, total, errores)
            except Exception:
                pass
    return salidas

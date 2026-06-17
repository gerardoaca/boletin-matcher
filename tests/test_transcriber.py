"""Tests del transcriber con anthropic SDK mockeado.

Cubre:
- Happy path (JSON válido)
- JSON inválido → fallback
- BadRequest → fallback definitivo (sin reintento)
- RateLimit transitorio: recupera, agota reintentos
- APITimeoutError → reintenta
- Bloque grande truncado
- _client falla sin API key → fallback config
- Code fence ```json eliminado
- enriquecer() continúa pese a errores
- enriquecer() callback de progreso

Notas:
- Las excepciones del SDK Anthropic (RateLimitError, BadRequestError, etc.)
  tienen constructores complicados (esperan httpx.Response). Para no acoplar
  los tests al detalle interno, creamos subclases dummy que heredan de las
  excepciones reales y se pueden instanciar con un mensaje plano.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Asegurar import del paquete core (conftest del repo también lo hace)
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anthropic import (  # noqa: E402
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    RateLimitError,
)

from core import transcriber  # noqa: E402
from core.matcher import Coincidencia  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Dummy exception classes (las reales requieren request/response httpx)
# ─────────────────────────────────────────────────────────────────────────────
class _DummyBadRequest(BadRequestError):
    def __init__(self, msg: str = "bad request"):
        Exception.__init__(self, msg)
        self.status_code = 400
        self.message = msg

    def __str__(self):
        return self.args[0] if self.args else ""


class _DummyRateLimit(RateLimitError):
    def __init__(self, msg: str = "rate limited"):
        Exception.__init__(self, msg)
        self.status_code = 429
        self.message = msg

    def __str__(self):
        return self.args[0] if self.args else ""


class _DummyTimeout(APITimeoutError):
    def __init__(self, msg: str = "timeout"):
        Exception.__init__(self, msg)
        self.message = msg

    def __str__(self):
        return self.args[0] if self.args else ""


class _DummyConnError(APIConnectionError):
    def __init__(self, msg: str = "conn error"):
        Exception.__init__(self, msg)
        self.message = msg

    def __str__(self):
        return self.args[0] if self.args else ""


class _DummyAPIError(APIError):
    def __init__(self, msg: str = "api error"):
        Exception.__init__(self, msg)
        self.message = msg

    def __str__(self):
        return self.args[0] if self.args else ""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _mock_response(text: str) -> MagicMock:
    """Construye un objeto similar al que devuelve client.messages.create."""
    resp = MagicMock()
    block = MagicMock()
    block.text = text
    resp.content = [block]
    return resp


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Acelera los reintentos: no esperar de verdad."""
    monkeypatch.setattr(transcriber.time, "sleep", lambda s: None)


@pytest.fixture
def mock_client(monkeypatch):
    """Sustituye _client() por un mock; devuelve el messages.create mock."""
    fake_client = MagicMock()
    monkeypatch.setattr(transcriber, "_client", lambda: fake_client)
    return fake_client.messages.create


# ─────────────────────────────────────────────────────────────────────────────
# Tests: transcribir_bloque
# ─────────────────────────────────────────────────────────────────────────────
def test_transcribir_bloque_happy_path(mock_client):
    mock_client.return_value = _mock_response(
        '{"entrada_literal":"X","juzgado":"J1","sintesis":"s","tipo_acuerdo":"AUTO","confianza":"alta"}'
    )
    out = transcriber.transcribir_bloque("bloque", expediente="123/2024")
    assert out["confianza"] == "alta"
    assert out["juzgado"] == "J1"
    assert mock_client.call_count == 1


def test_transcribir_bloque_json_invalido(mock_client):
    mock_client.return_value = _mock_response("esto no es json válido")
    out = transcriber.transcribir_bloque("bloque corto", expediente="1/2024")
    assert out["confianza"] == "baja"
    assert "json_parse" in out["error"]
    # No reintenta en JSON inválido
    assert mock_client.call_count == 1


def test_transcribir_bloque_bad_request(mock_client):
    mock_client.side_effect = _DummyBadRequest("modelo inexistente")
    out = transcriber.transcribir_bloque("bloque", expediente="1/2024")
    assert out["confianza"] == "error_api"
    assert "BadRequest" in out["error"]
    # NO reintenta BadRequest
    assert mock_client.call_count == 1


def test_transcribir_bloque_rate_limit_recupera(mock_client):
    """Primer call falla con RateLimit, segundo OK → devuelve OK."""
    mock_client.side_effect = [
        _DummyRateLimit("slow down"),
        _mock_response('{"entrada_literal":"X","juzgado":"J","sintesis":"s","tipo_acuerdo":"A","confianza":"alta"}'),
    ]
    out = transcriber.transcribir_bloque("bloque", expediente="9/2024")
    assert out["confianza"] == "alta"
    assert mock_client.call_count == 2  # confirma que SÍ reintentó


def test_transcribir_bloque_rate_limit_agota_reintentos(mock_client):
    mock_client.side_effect = _DummyRateLimit("too many")
    out = transcriber.transcribir_bloque("bloque", expediente="9/2024")
    assert out["confianza"] == "error_api"
    assert "transitorio" in out["error"] or "RateLimit" in out["error"]
    assert mock_client.call_count == transcriber.MAX_REINTENTOS


def test_transcribir_bloque_timeout(mock_client):
    """APITimeoutError → reintenta y al final fallback."""
    mock_client.side_effect = _DummyTimeout("timed out")
    out = transcriber.transcribir_bloque("bloque")
    assert out["confianza"] == "error_api"
    assert mock_client.call_count == transcriber.MAX_REINTENTOS


def test_transcribir_bloque_truncado(mock_client):
    """Bloque de 20000 chars → la llamada recibe bloque truncado a MAX_BLOQUE_CHARS."""
    mock_client.return_value = _mock_response(
        '{"entrada_literal":"x","juzgado":"","sintesis":"","tipo_acuerdo":"","confianza":"alta"}'
    )
    bloque_grande = "A" * 20000
    transcriber.transcribir_bloque(bloque_grande, expediente="1/2024")
    kwargs = mock_client.call_args.kwargs
    user_content = kwargs["messages"][0]["content"]
    # No debe contener 20000 As — el truncamiento aplica a MAX_BLOQUE_CHARS
    assert "A" * 20000 not in user_content
    # Como minimo no debe exceder el max + el preámbulo del mensaje (con buffer)
    assert user_content.count("A") <= transcriber.MAX_BLOQUE_CHARS


def test_transcribir_bloque_sin_api_key(monkeypatch):
    """Si _client lanza RuntimeError, devuelve fallback con 'config:'."""
    def _falla():
        raise RuntimeError("Falta ANTHROPIC_API_KEY")
    monkeypatch.setattr(transcriber, "_client", _falla)
    out = transcriber.transcribir_bloque("bloque", expediente="1/2024")
    assert out["confianza"] == "error_api"
    assert "config:" in out["error"]


def test_transcribir_bloque_codefence_json(mock_client):
    """La respuesta envuelta en ```json ... ``` debe parsear OK."""
    payload = '```json\n{"entrada_literal":"X","juzgado":"J","sintesis":"s","tipo_acuerdo":"A","confianza":"alta"}\n```'
    mock_client.return_value = _mock_response(payload)
    out = transcriber.transcribir_bloque("bloque", expediente="1/2024")
    assert out["confianza"] == "alta"
    assert out["juzgado"] == "J"


def test_transcribir_bloque_conn_error_reintenta(mock_client):
    """APIConnectionError también es transitorio → reintenta."""
    mock_client.side_effect = _DummyConnError("conn refused")
    out = transcriber.transcribir_bloque("bloque")
    assert out["confianza"] == "error_api"
    assert mock_client.call_count == transcriber.MAX_REINTENTOS


# ─────────────────────────────────────────────────────────────────────────────
# Tests: enriquecer
# ─────────────────────────────────────────────────────────────────────────────
def _coincidencia(expediente="1/2024", bloque="bloque texto"):
    return Coincidencia(
        expediente=expediente,
        actor_listado="ACTOR",
        cliente="CLIENTE",
        juzgado_listado="JUZ",
        hoja=1,
        pagina_impresa="1",
        linea_inicio=0,
        linea_fin=10,
        bloque_texto=bloque,
        ruta_validacion="test",
    )


def test_enriquecer_continua_con_errores(monkeypatch):
    """3 coincidencias, la segunda falla con error_api → resultado tiene 3 items."""
    calls = {"n": 0}

    def fake_transcribir(bloque, expediente=""):
        calls["n"] += 1
        if calls["n"] == 2:
            return {
                "entrada_literal": "",
                "juzgado": "",
                "sintesis": "",
                "tipo_acuerdo": "",
                "confianza": "error_api",
                "error": "BadRequest: x",
            }
        return {
            "entrada_literal": "OK",
            "juzgado": "J",
            "sintesis": "s",
            "tipo_acuerdo": "AUTO",
            "confianza": "alta",
        }

    monkeypatch.setattr(transcriber, "transcribir_bloque", fake_transcribir)

    coincidencias = [_coincidencia(f"{i}/2024", f"bloque {i}") for i in range(3)]
    out = transcriber.enriquecer(coincidencias)

    assert len(out) == 3
    assert out[1]["confianza"] == "error_api"
    assert out[1]["error_ia"] == "BadRequest: x"
    assert out[0]["confianza"] == "alta"
    assert out[2]["confianza"] == "alta"


def test_enriquecer_callback_progreso(monkeypatch):
    """cb se llama N veces con idx/total/errores correctos."""
    def fake_transcribir(bloque, expediente=""):
        # primera coincidencia error, segunda OK
        if expediente.startswith("ERR"):
            return {"confianza": "error_api", "error": "x"}
        return {"confianza": "alta"}

    monkeypatch.setattr(transcriber, "transcribir_bloque", fake_transcribir)

    coincidencias = [
        _coincidencia("ERR/2024", "b"),
        _coincidencia("OK/2024", "b"),
    ]
    eventos = []
    transcriber.enriquecer(coincidencias, progress_cb=lambda i, t, e: eventos.append((i, t, e)))

    assert eventos == [(1, 2, 1), (2, 2, 1)]


def test_enriquecer_callback_excepcion_no_rompe(monkeypatch):
    """Si el callback lanza, enriquecer no debe abortar."""
    monkeypatch.setattr(
        transcriber,
        "transcribir_bloque",
        lambda b, expediente="": {"confianza": "alta"},
    )
    coincidencias = [_coincidencia()]

    def cb_malo(i, t, e):
        raise ValueError("¡crash!")

    out = transcriber.enriquecer(coincidencias, progress_cb=cb_malo)
    assert len(out) == 1

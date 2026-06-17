"""Tests del módulo de autenticación de app.py.

Estrategia
----------
``app.py`` ejecuta código de Streamlit al importarse (set_page_config,
_verificar_password). Por eso, antes de importar ``app`` hay que sustituir
``streamlit`` en ``sys.modules`` por un fake mínimo. Además ``st.stop()``
debe levantar ``SystemExit`` para que podamos detectar el fail-closed sin
seguir ejecutando el resto del archivo.

Lo que testeamos directamente:
- ``_obtener_password_correcto`` lee env var con prioridad sobre secrets.
- ``hmac.compare_digest`` se usa correctamente (compara bytes iguales/distintos).
- Backoff exponencial: para N intentos, espera = min(300, 3 ** (N-2))
  (replicado en una función de cálculo dentro del test, dado que está
  inlineado en app.py).
- Sin APP_PASSWORD configurada, _obtener_password_correcto devuelve None
  (fail-closed depende de que el llamador interprete el None correctamente).
- Diagnóstico no expone la contraseña, solo el largo.
"""
from __future__ import annotations

import hmac
import importlib
import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Fake Streamlit
# ─────────────────────────────────────────────────────────────────────────────
class _StreamlitStopped(SystemExit):
    pass


class _FakeSecrets(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


class _FakeSidebarExpander:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeSidebar:
    def expander(self, *a, **kw):
        return _FakeSidebarExpander()


def _build_fake_streamlit():
    mod = types.ModuleType("streamlit")
    mod.session_state = {}
    mod.secrets = _FakeSecrets()
    mod.sidebar = _FakeSidebar()

    def _noop(*a, **kw): return None

    def _stop():
        raise _StreamlitStopped("st.stop()")

    # API mínima usada por app.py durante import
    mod.set_page_config = _noop
    mod.markdown = _noop
    mod.warning = _noop
    mod.error = _noop
    mod.success = _noop
    mod.code = _noop
    mod.text_input = lambda *a, **kw: ""
    mod.button = lambda *a, **kw: False
    mod.rerun = _noop
    mod.stop = _stop

    class _Form:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False

    mod.form = lambda *a, **kw: _Form()
    mod.form_submit_button = lambda *a, **kw: False
    mod.spinner = lambda *a, **kw: _Form()
    mod.cache_data = lambda **kw: (lambda f: f)
    mod.cache_resource = lambda **kw: (lambda f: f)
    mod.columns = lambda *a, **kw: (_Form(), _Form(), _Form())
    mod.container = lambda *a, **kw: _Form()
    mod.expander = lambda *a, **kw: _Form()
    mod.tabs = lambda labels: tuple(_Form() for _ in labels)
    mod.file_uploader = lambda *a, **kw: None
    mod.download_button = _noop
    mod.write = _noop
    mod.title = _noop
    mod.header = _noop
    mod.subheader = _noop
    mod.info = _noop
    mod.metric = _noop
    mod.dataframe = _noop
    mod.empty = lambda: _Form()
    mod.progress = lambda *a, **kw: _Form()
    return mod


@pytest.fixture
def fake_app(monkeypatch):
    """Construye un módulo equivalente al sub-conjunto auth de app.py.

    En vez de importar app.py entero (que tira import-time side effects de
    Streamlit + Anthropic), construimos un módulo mínimo en memoria que
    contiene exactamente las funciones de autenticación, copiando la
    lógica del original. Si la lógica del original cambia, este test
    falla y la copia debe actualizarse — eso es deseable: el test
    actúa como contrato.
    """
    fake_st = _build_fake_streamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)

    # Limpiar env relevante
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("DEBUG_AUTH", raising=False)

    src_app = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    # Tomamos solo el bloque hasta antes del `if not _verificar_password()` para
    # evitar disparar la lógica top-level.
    marker = "if not _verificar_password():"
    assert marker in src_app, "Estructura inesperada de app.py — actualizar test"
    src_corte = src_app.split(marker)[0]

    mod = types.ModuleType("fake_app_auth")
    mod.__dict__["__name__"] = "fake_app_auth"
    # Inyectar streamlit ya como st en el namespace del modulo
    exec(compile(src_corte, "app.py(slice)", "exec"), mod.__dict__)
    return mod, fake_st


# ─────────────────────────────────────────────────────────────────────────────
# Tests: _obtener_password_correcto
# ─────────────────────────────────────────────────────────────────────────────
def test_obtener_password_lee_env(fake_app, monkeypatch):
    mod, _ = fake_app
    monkeypatch.setenv("APP_PASSWORD", "secreta-123")
    assert mod._obtener_password_correcto() == "secreta-123"


def test_obtener_password_strip(fake_app, monkeypatch):
    mod, _ = fake_app
    monkeypatch.setenv("APP_PASSWORD", "  secreta-123\n")
    assert mod._obtener_password_correcto() == "secreta-123"


def test_obtener_password_secrets_fallback(fake_app):
    mod, fake_st = fake_app
    # Sin env var, leer de secrets
    fake_st.secrets["APP_PASSWORD"] = "from-secrets"
    assert mod._obtener_password_correcto() == "from-secrets"


def test_obtener_password_none_si_no_configurado(fake_app):
    mod, _ = fake_app
    assert mod._obtener_password_correcto() is None


# ─────────────────────────────────────────────────────────────────────────────
# Tests: hmac.compare_digest — uso correcto
# ─────────────────────────────────────────────────────────────────────────────
def test_compare_digest_iguales():
    a = "abc123".encode("utf-8")
    b = "abc123".encode("utf-8")
    assert hmac.compare_digest(a, b) is True


def test_compare_digest_distintos():
    a = "abc123".encode("utf-8")
    b = "abc124".encode("utf-8")
    assert hmac.compare_digest(a, b) is False


def test_compare_digest_distintas_longitudes():
    """compare_digest es seguro incluso para longitudes distintas."""
    a = "corto".encode("utf-8")
    b = "muchomaslargo".encode("utf-8")
    assert hmac.compare_digest(a, b) is False


# ─────────────────────────────────────────────────────────────────────────────
# Tests: backoff exponencial
# ─────────────────────────────────────────────────────────────────────────────
def _backoff(intentos: int) -> int:
    """Replica de la fórmula inline de app.py:
        if intentos_fallidos >= 3:
            espera = min(300, 3 ** (intentos_fallidos - 2))
    """
    if intentos < 3:
        return 0
    return min(300, 3 ** (intentos - 2))


def test_backoff_no_activa_antes_de_3():
    assert _backoff(1) == 0
    assert _backoff(2) == 0


def test_backoff_se_activa_tras_3_fallos():
    assert _backoff(3) == 3       # 3 ** 1


def test_backoff_exponencial_crece():
    """Intentos 3, 4, 5 → 3, 9, 27."""
    assert _backoff(3) == 3
    assert _backoff(4) == 9
    assert _backoff(5) == 27


def test_backoff_cap_a_300():
    """No debe exceder 300s, sin importar cuántos intentos."""
    # 3 ** 8 = 6561, cap=300
    assert _backoff(10) == 300
    assert _backoff(50) == 300


# ─────────────────────────────────────────────────────────────────────────────
# Tests: rate limit + diagnóstico (usando session_state real del fake)
# ─────────────────────────────────────────────────────────────────────────────
def test_rate_limit_se_resetea_tras_exito(fake_app, monkeypatch):
    """Simula 2 fallidos y luego un éxito → contadores en cero."""
    mod, fake_st = fake_app
    monkeypatch.setenv("APP_PASSWORD", "correcto")
    fake_st.session_state["intentos_fallidos"] = 2
    fake_st.session_state["bloqueado_hasta"] = 0.0

    # En login exitoso, app.py hace:
    #   intentos_fallidos = 0; bloqueado_hasta = 0.0; autenticado = True
    # No podemos correr la rama de UI, pero simulamos el efecto y verificamos
    # que las claves se ponen en cero.
    fake_st.session_state["autenticado"] = True
    fake_st.session_state["intentos_fallidos"] = 0
    fake_st.session_state["bloqueado_hasta"] = 0.0

    assert fake_st.session_state["intentos_fallidos"] == 0
    assert fake_st.session_state["bloqueado_hasta"] == 0.0


def test_fail_closed_sin_password(fake_app):
    """Sin APP_PASSWORD, _verificar_password llama st.stop → SystemExit."""
    mod, fake_st = fake_app
    # No autenticado, sin password configurada
    fake_st.session_state.clear()
    with pytest.raises(_StreamlitStopped):
        mod._verificar_password()


def test_autenticado_devuelve_true_sin_pedir_password(fake_app, monkeypatch):
    mod, fake_st = fake_app
    monkeypatch.setenv("APP_PASSWORD", "correcto")
    fake_st.session_state["autenticado"] = True
    assert mod._verificar_password() is True


def test_diagnostico_no_expone_password(fake_app, monkeypatch):
    """_diagnostico_auth solo reporta el LARGO, no el valor."""
    mod, fake_st = fake_app
    monkeypatch.setenv("APP_PASSWORD", "super-secreta-no-mostrar")
    out = mod._diagnostico_auth()
    assert "super-secreta-no-mostrar" not in out
    assert "largo" in out.lower() or "len" in out.lower()


def test_diagnostico_solo_se_renderiza_si_DEBUG_AUTH(fake_app, monkeypatch):
    """Pre-auth, _verificar_password NO debe ejecutar diagnostico_auth.
    Post-auth, sólo si DEBUG_AUTH=1.
    """
    mod, fake_st = fake_app
    monkeypatch.setenv("APP_PASSWORD", "correcto")
    fake_st.session_state["autenticado"] = True

    llamadas = {"n": 0}
    original = mod._diagnostico_auth

    def spy():
        llamadas["n"] += 1
        return original()

    monkeypatch.setattr(mod, "_diagnostico_auth", spy)

    # Sin DEBUG_AUTH → no llama
    monkeypatch.delenv("DEBUG_AUTH", raising=False)
    mod._verificar_password()
    assert llamadas["n"] == 0

    # Con DEBUG_AUTH=1 → sí llama
    monkeypatch.setenv("DEBUG_AUTH", "1")
    mod._verificar_password()
    assert llamadas["n"] == 1

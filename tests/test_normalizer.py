"""Tests unitarios para core/normalizer.py.

Cubre dos clases de regresiones:
  1. Bug #3 — `es_actor_reservado("")` debe ser False (antes era True y
     mataba todos los matches con celdas actor vacías).
  2. Comportamiento canónico de normalización de expedientes y matching
     de nombres por tokens (orden libre).
"""
from __future__ import annotations

import pytest

from core.normalizer import (
    alguna_parte_en_texto,
    es_actor_reservado,
    extraer_expedientes,
    normalizar_expediente,
    normalizar_texto,
    tokens_significativos,
)


# --------------------------------------------------------------------------- #
# es_actor_reservado — protege contra Bug #3
# --------------------------------------------------------------------------- #

class TestEsActorReservado:
    def test_es_actor_reservado_vacio_no_es_reservado(self):
        """Bug #3: cadena vacía NO debe ser reservada."""
        assert es_actor_reservado("") is False

    def test_es_actor_reservado_none_no_es_reservado(self):
        """Bug #3: None NO debe ser reservado."""
        assert es_actor_reservado(None) is False

    def test_es_actor_reservado_whitespace_no_es_reservado(self):
        """Solo espacios tampoco es reservado — es desconocido."""
        assert es_actor_reservado("   ") is False
        assert es_actor_reservado("\t\n ") is False

    @pytest.mark.parametrize(
        "valor",
        [
            "***",
            "* * *",
            "RESERVADO",
            "Reservado",
            "CONFIDENCIAL",
            "SECRETO",
            "NOMBRE RESERVADO",
            "PROTEGIDO",
            "SUCESION INTESTAMENTARIA",
            "SUCESIÓN INTESTAMENTARIA",
            "SUCESION TESTAMENTARIA",
            "SUCESIÓN TESTAMENTARIA",
            "INTESTADO",
            "TESTAMENTARIA",
        ],
    )
    def test_es_actor_reservado_explicito(self, valor):
        assert es_actor_reservado(valor) is True, (
            f"Esperaba que '{valor}' fuera reservado"
        )

    @pytest.mark.parametrize(
        "valor",
        [
            "JUAN PEREZ",
            "Juan Pérez García",
            "MARÍA GUADALUPE VELÁZQUEZ CARRANZA",
            "CLOUD OCR MÉXICO, S.A. DE C.V.",
            "STRATEGIC CAPITAL AGENCY S.A.P.I. DE C.V.",
        ],
    )
    def test_es_actor_reservado_nombre_normal(self, valor):
        assert es_actor_reservado(valor) is False, (
            f"Esperaba que '{valor}' NO fuera reservado"
        )


# --------------------------------------------------------------------------- #
# normalizar_expediente — canonización
# --------------------------------------------------------------------------- #

class TestNormalizarExpediente:
    @pytest.mark.parametrize(
        "raw,esperado",
        [
            ("1141/2024", "1141/2024"),
            ("813/24", "0813/2024"),
            ("813/49", "0813/2049"),
            # corte: 50→ 1950 (umbral del normalizer)
            ("813/50", "0813/1950"),
            ("170 - 2026", "0170/2026"),
            ("488/2023", "0488/2023"),
            ("21/1991", "0021/1991"),
        ],
    )
    def test_normalizar_expediente_canonico(self, raw, esperado):
        assert normalizar_expediente(raw) == esperado

    def test_normalizar_expediente_vacio(self):
        assert normalizar_expediente("") is None
        assert normalizar_expediente(None) is None

    def test_normalizar_expediente_invalido(self):
        # Sin año plausible: devuelve None
        assert normalizar_expediente("abc def") is None


# --------------------------------------------------------------------------- #
# extraer_expedientes — múltiples
# --------------------------------------------------------------------------- #

class TestExtraerExpedientes:
    def test_extraer_expedientes_multiple(self):
        texto = (
            "Expediente 488/2023 y también 1017/2025, así como el viejo "
            "21/1991 y el más reciente 170/2026."
        )
        encontrados = extraer_expedientes(texto)
        # canónicos
        assert "0488/2023" in encontrados
        assert "1017/2025" in encontrados
        assert "0021/1991" in encontrados
        assert "0170/2026" in encontrados
        assert len(encontrados) == 4

    def test_extraer_expedientes_dedup(self):
        encontrados = extraer_expedientes("488/2023 y otra vez 488/2023")
        assert encontrados == ["0488/2023"]


# --------------------------------------------------------------------------- #
# tokens_significativos
# --------------------------------------------------------------------------- #

class TestTokensSignificativos:
    def test_tokens_significativos_filtra_stopwords(self):
        toks = tokens_significativos(
            "JUAN DE LA PEÑA Y SOCIEDAD ANÓNIMA DE CAPITAL VARIABLE"
        )
        # Sin DE / LA / Y / SOCIEDAD / ANONIMA / CAPITAL / VARIABLE
        assert toks == {"JUAN", "PENA"}

    def test_tokens_significativos_sin_acentos_y_upper(self):
        toks = tokens_significativos("Pérez Michel Valeria")
        assert toks == {"PEREZ", "MICHEL", "VALERIA"}

    def test_tokens_significativos_vacio(self):
        assert tokens_significativos("") == set()
        assert tokens_significativos(None) == set()


# --------------------------------------------------------------------------- #
# alguna_parte_en_texto — matching orden libre
# --------------------------------------------------------------------------- #

class TestAlgunaParteEnTexto:
    def test_orden_libre(self):
        """El listado trae 'LEONOR AMELIA VILLALOBOS BEDOLLA' y el boletín
        lo escribe como 'VILLALOBOS BEDOLLA LEONOR AMELIA'. Debe matchear."""
        nombre = "LEONOR AMELIA VILLALOBOS BEDOLLA"
        texto = normalizar_texto(
            "...parte actora VILLALOBOS BEDOLLA LEONOR AMELIA promueve..."
        )
        assert alguna_parte_en_texto(nombre, texto) is True

    def test_falta_token_no_matchea(self):
        nombre = "LEONOR AMELIA VILLALOBOS BEDOLLA"
        # falta BEDOLLA
        texto = normalizar_texto("VILLALOBOS LEONOR AMELIA")
        assert alguna_parte_en_texto(nombre, texto) is False

    def test_multiples_partes_separadas_por_y(self):
        nombre = "ALFREDO HIDALGO TAPIA y MIGUEL GUADARRAMA VÁZQUEZ"
        texto = normalizar_texto(
            "...demandado MIGUEL GUADARRAMA VAZQUEZ apersonado..."
        )
        # solo basta una de las partes
        assert alguna_parte_en_texto(nombre, texto) is True

"""Tests de regresión end-to-end del matcher.

Estos tests son LENTOS (cargan los 4 boletines reales con PyMuPDF). Se
pueden saltar con `pytest -k "not regresion"`.

Protegen dos regresiones críticas:
  1. Commit dfd7c41 (parche quirúrgico juzgado_conflicto): si el detector
     de headers no encuentra juzgado en la hoja, NO debemos degradar a
     REVISION. Antes de ese parche, este caso mandaba todos los matches
     reales a REVISION y bajaba el total de 6 a 0.
  2. Bug #3 (es_actor_reservado("")=True): celdas actor vacías o que no
     sean explícitamente reservadas no deben filtrarse silenciosamente.

Si el total de validadas baja de 6, este test FALLA y debe investigarse
antes de mergear.
"""
from __future__ import annotations

import pytest

from core.extractor import extraer_hojas
from core.matcher import buscar_coincidencias


# Los 6 matches conocidos contra los 4 boletines + listado_clientes.csv.
# Cada entry: (substring_del_nombre_pdf, expediente_canonico, cliente_substring,
#              ruta_validacion, hoja_pdf_1based)
MATCHES_ESPERADOS = [
    ("20032026", "0488/2023", "CLOUD OCR", "A_actor", 170),
    ("20032026", "1017/2025", "MARTÍNEZ GORDILLO", "A_actor", 367),
    ("24032026", "1017/2025", "MARTÍNEZ GORDILLO", "A_actor", 245),
    ("Óscar Hernández", "0479/2018", "MALDONADO SALCEDO", "A_actor", 63),
    ("Óscar Hernández", "0168/2026", "ENRIQUE ESPÍNOLA", "A_cliente", 133),
    ("Óscar Hernández", "0429/2025", "LETRAS AL AIRE", "A_actor", 217),
]


@pytest.fixture(scope="module")
def todas_las_coincidencias(boletines_pdfs, listado_real):
    """Corre el pipeline completo (extractor + matcher) sobre los 4 boletines
    y devuelve (validadas, revision) con metadata del PDF de origen.

    Caché a nivel de módulo para no reparsear los PDFs en cada test.
    """
    validadas_all = []
    revision_all = []
    for pdf in boletines_pdfs:
        hojas = extraer_hojas(str(pdf))
        v, r = buscar_coincidencias(hojas, listado_real)
        for c in v:
            c.extras["__pdf_name"] = pdf.name
        for c in r:
            c.extras["__pdf_name"] = pdf.name
        validadas_all.extend(v)
        revision_all.extend(r)
    return validadas_all, revision_all


@pytest.mark.regresion
def test_no_regresion_juzgado_conflicto(todas_las_coincidencias):
    """REGRESIÓN COMMIT dfd7c41 — debe haber exactamente 6 validadas.

    Si este número baja, probablemente se reintrodujo el bug que mandaba
    a REVISION los matches cuyo bloque no tenía juzgado detectado.
    """
    validadas, _revision = todas_las_coincidencias
    assert len(validadas) == 6, (
        f"Se esperaban 6 validadas, vinieron {len(validadas)}.\n"
        + "\n".join(
            f"  - {c.extras.get('__pdf_name', '?')} | exp={c.expediente} "
            f"cliente={c.cliente!r} ruta={c.ruta_validacion} hoja={c.hoja}"
            for c in validadas
        )
    )


@pytest.mark.regresion
@pytest.mark.parametrize(
    "pdf_sub,expediente,cliente_sub,ruta,hoja",
    MATCHES_ESPERADOS,
    ids=[f"{e}-{r}" for _, e, _, r, _ in MATCHES_ESPERADOS],
)
def test_validacion_esperada(
    todas_las_coincidencias, pdf_sub, expediente, cliente_sub, ruta, hoja
):
    """Cada uno de los 6 matches conocidos debe estar presente con
    expediente, cliente, ruta y hoja correctos."""
    validadas, _ = todas_las_coincidencias
    matches = [
        c for c in validadas
        if c.expediente == expediente
        and pdf_sub in c.extras.get("__pdf_name", "")
    ]
    assert matches, (
        f"No se encontró ninguna validación para expediente {expediente} "
        f"en el PDF que contiene '{pdf_sub}'"
    )
    # Debe haber exactamente una validación que coincida también en
    # cliente y ruta.
    finos = [
        c for c in matches
        if cliente_sub.upper() in (c.cliente or "").upper()
        and c.ruta_validacion == ruta
        and c.hoja == hoja
    ]
    assert finos, (
        f"Hay {len(matches)} validación(es) para {expediente} en "
        f"{pdf_sub}, pero ninguna cumple cliente~{cliente_sub!r}, "
        f"ruta={ruta}, hoja={hoja}. Validaciones encontradas: "
        + ", ".join(
            f"(cliente={c.cliente!r}, ruta={c.ruta_validacion}, hoja={c.hoja})"
            for c in matches
        )
    )


@pytest.mark.regresion
def test_no_validadas_extra(todas_las_coincidencias):
    """Si aparecen MÁS de 6 validadas, alguna pasó el filtro
    incorrectamente — también es regresión (matching demasiado laxo)."""
    validadas, _ = todas_las_coincidencias
    if len(validadas) > 6:
        extras = [
            f"  exp={c.expediente} cli={c.cliente!r} ruta={c.ruta_validacion} "
            f"pdf={c.extras.get('__pdf_name')} hoja={c.hoja}"
            for c in validadas
        ]
        pytest.fail(
            f"Aparecieron {len(validadas)} validadas (>6). Posible "
            f"matching demasiado laxo. Listado:\n" + "\n".join(extras)
        )

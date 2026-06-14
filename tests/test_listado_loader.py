"""Tests del cargador de listados.

Cubre:
  - Carga del CSV real de producción.
  - Coherencia entre `actor_reservado`, `actor_desconocido` y el contenido
    de la celda actor.
"""
from __future__ import annotations

import io

from core.listado_loader import cargar_csv, _df_a_registros
from core.normalizer import RESERVADO_TOKENS, normalizar_texto


def test_cargar_csv_real_cuenta_registros(listado_csv_path):
    registros = cargar_csv(str(listado_csv_path))
    # 20 filas de datos (21 líneas: 1 header + 20 datos)
    assert len(registros) == 20, (
        f"Se esperaban 20 registros en el CSV real, vinieron {len(registros)}"
    )


def test_csv_real_dos_actores_reservados(listado_real):
    """SUCESIÓN INTESTAMENTARIA y SUCESIÓN TESTAMENTARIA son los únicos
    reservados explícitos del listado de producción."""
    reservados = [r for r in listado_real if r.actor_reservado]
    assert len(reservados) == 2, (
        f"Se esperaban exactamente 2 actores reservados, vinieron "
        f"{len(reservados)}: {[r.raw.get('actor') for r in reservados]}"
    )


def test_csv_real_coherencia_reservado(listado_real):
    """Si actor_reservado=True el campo `actor` normalizado debe estar vacío
    y el original debe ser uno de los tokens de RESERVADO_TOKENS (tras
    normalizar)."""
    for r in listado_real:
        if not r.actor_reservado:
            continue
        # El campo `actor` se vacía cuando hay reservado o desconocido.
        assert r.actor == "", (
            f"Registro reservado fila {r.fila_origen} dejó actor='{r.actor}'"
        )
        # El original debe matchear algún token reservado tras normalizar.
        original_norm = normalizar_texto(r.raw.get("actor", ""))
        assert original_norm in RESERVADO_TOKENS or original_norm == "", (
            f"Reservado pero el original normalizado '{original_norm}' "
            f"no está en RESERVADO_TOKENS"
        )


def test_csv_real_ningun_registro_falsamente_reservado(listado_real):
    """Bug #3 explícito: ningún registro con actor no vacío y no-reservado
    en RESERVADO_TOKENS puede aparecer marcado como reservado."""
    for r in listado_real:
        actor_raw = (r.raw.get("actor") or "").strip()
        if not actor_raw:
            continue  # vacío: tratado por test_actor_desconocido_solo_si_vacio
        actor_norm = normalizar_texto(actor_raw)
        if actor_norm in RESERVADO_TOKENS:
            continue  # legítimamente reservado
        assert r.actor_reservado is False, (
            f"Bug #3 regresó: fila {r.fila_origen} con actor='{actor_raw}' "
            f"fue marcado como reservado"
        )


def test_actor_desconocido_solo_si_celda_vacia():
    """`actor_desconocido` se prende solo cuando la celda actor está vacía
    (o whitespace), y nunca a la vez que `actor_reservado`."""
    import pandas as pd
    df = pd.DataFrame(
        [
            {"cliente": "C1", "actor": "JUAN PEREZ", "expediente": "1/2024", "juzgado": ""},
            {"cliente": "C2", "actor": "", "expediente": "2/2024", "juzgado": ""},
            {"cliente": "C3", "actor": "   ", "expediente": "3/2024", "juzgado": ""},
            {"cliente": "C4", "actor": "***", "expediente": "4/2024", "juzgado": ""},
            {"cliente": "C5", "actor": "SUCESIÓN INTESTAMENTARIA", "expediente": "5/2024", "juzgado": ""},
        ]
    )
    regs = _df_a_registros(df.fillna(""))
    by_exp = {r.expediente: r for r in regs}

    # actor normal
    r1 = by_exp["0001/2024"]
    assert r1.actor_reservado is False
    assert r1.actor_desconocido is False
    assert r1.actor != ""

    # vacío puro
    r2 = by_exp["0002/2024"]
    assert r2.actor_reservado is False
    assert r2.actor_desconocido is True
    assert r2.actor == ""

    # whitespace
    r3 = by_exp["0003/2024"]
    assert r3.actor_reservado is False
    assert r3.actor_desconocido is True

    # reservado explícito
    r4 = by_exp["0004/2024"]
    assert r4.actor_reservado is True
    assert r4.actor_desconocido is False, (
        "actor_reservado y actor_desconocido son mutuamente excluyentes"
    )

    r5 = by_exp["0005/2024"]
    assert r5.actor_reservado is True
    assert r5.actor_desconocido is False


def test_csv_real_expedientes_canonicos(listado_real):
    """Todos los registros cargados llevan expediente canónico NNNN/AAAA."""
    import re
    pat = re.compile(r"^\d{4}/\d{4}$")
    for r in listado_real:
        assert pat.match(r.expediente), (
            f"Expediente no canónico: '{r.expediente}' (fila {r.fila_origen})"
        )

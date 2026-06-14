"""Fixtures compartidas para la suite de regresión del Boletin Matcher.

Estos tests asumen que se ejecutan desde la raíz del proyecto con el venv
activado (donde están instaladas las dependencias del proyecto: pandas,
pymupdf, etc.). Ver tests/README.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Aseguramos que el paquete `core` sea importable sin instalar el proyecto.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "regresion: tests end-to-end lentos (cargan PDFs reales). "
        "Skip con `-k 'not regresion'`.",
    )


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def listado_csv_path(repo_root: Path) -> Path:
    p = repo_root / "input" / "listados" / "listado_clientes.csv"
    assert p.exists(), f"No existe el listado real esperado: {p}"
    return p


@pytest.fixture(scope="session")
def boletines_dir(repo_root: Path) -> Path:
    d = repo_root / "input" / "boletines"
    assert d.is_dir(), f"No existe el directorio de boletines: {d}"
    return d


@pytest.fixture(scope="session")
def boletines_pdfs(boletines_dir: Path) -> list[Path]:
    pdfs = sorted(boletines_dir.glob("*.pdf"))
    assert len(pdfs) >= 4, (
        f"Se esperaban al menos 4 boletines en {boletines_dir}, "
        f"encontrados: {[p.name for p in pdfs]}"
    )
    return pdfs


@pytest.fixture(scope="session")
def listado_real(listado_csv_path: Path):
    from core.listado_loader import cargar_csv
    return cargar_csv(str(listado_csv_path))

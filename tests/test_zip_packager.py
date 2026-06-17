"""Tests del empaquetador ZIP.

Cubre:
- ZIP contiene .md + imágenes
- Imagen faltante → README.txt con la lista
- Subcarpeta de imgs se aplana (imgs/sub/x.png → imgs/x.png) y se reescribe el .md
- Colisión de basename → sufijo numérico
- Sanitización de nombres (../ y caracteres inválidos)
- El módulo usa solo stdlib (heurística sobre imports)
"""
from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.zip_packager import empaquetar_reporte_zip  # noqa: E402


# 1x1 PNG transparente (válido) en bytes — para escribir imgs reales en tmp
_PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _escribir_png(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_PNG_1x1)


def _abrir_zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data), "r")


def test_zip_contiene_md_y_imagenes(tmp_path):
    img1 = tmp_path / "imgs" / "hoja_1_exp_001.png"
    img2 = tmp_path / "imgs" / "hoja_2_exp_002.png"
    _escribir_png(img1)
    _escribir_png(img2)

    md = tmp_path / "reporte.md"
    md.write_text(
        "# Reporte\n\n![hoja 1](imgs/hoja_1_exp_001.png)\n\n![hoja 2](imgs/hoja_2_exp_002.png)\n",
        encoding="utf-8",
    )

    data = empaquetar_reporte_zip(md, [img1, img2])
    with _abrir_zip(data) as zf:
        nombres = set(zf.namelist())
        assert "reporte.md" in nombres
        assert "imgs/hoja_1_exp_001.png" in nombres
        assert "imgs/hoja_2_exp_002.png" in nombres
        # README sólo si hay faltantes
        assert "README.txt" not in nombres


def test_zip_imagen_faltante_genera_readme(tmp_path):
    md = tmp_path / "reporte.md"
    md.write_text("# X\n\n![falta](imgs/no_existe.png)\n", encoding="utf-8")

    data = empaquetar_reporte_zip(md, [])  # sin imgs explícitas
    with _abrir_zip(data) as zf:
        nombres = set(zf.namelist())
        assert "README.txt" in nombres
        readme = zf.read("README.txt").decode("utf-8")
        assert "no_existe.png" in readme

        md_dentro = zf.read("reporte.md").decode("utf-8")
        # La referencia rota se reemplaza por placeholder
        assert "imagen no disponible" in md_dentro
        # Y el ![]() ya no debe estar
        assert "![falta]" not in md_dentro


def test_zip_aplana_subdir_imgs(tmp_path):
    """Imagen en imgs/subdir/foo.png → en ZIP queda imgs/foo.png y el md reescrito."""
    img = tmp_path / "imgs" / "subdir" / "foo.png"
    _escribir_png(img)

    md = tmp_path / "reporte.md"
    md.write_text("# X\n\n![foo](imgs/subdir/foo.png)\n", encoding="utf-8")

    data = empaquetar_reporte_zip(md, [img])
    with _abrir_zip(data) as zf:
        nombres = set(zf.namelist())
        assert "imgs/foo.png" in nombres
        assert "imgs/subdir/foo.png" not in nombres

        md_dentro = zf.read("reporte.md").decode("utf-8")
        assert "imgs/foo.png" in md_dentro
        assert "imgs/subdir/foo.png" not in md_dentro


def test_zip_colision_nombres(tmp_path):
    """Dos imágenes con mismo basename en subdirs distintos → sufijo numérico."""
    img1 = tmp_path / "imgs" / "a" / "foo.png"
    img2 = tmp_path / "imgs" / "b" / "foo.png"
    _escribir_png(img1)
    _escribir_png(img2)

    md = tmp_path / "reporte.md"
    md.write_text(
        "![a](imgs/a/foo.png)\n\n![b](imgs/b/foo.png)\n", encoding="utf-8"
    )

    data = empaquetar_reporte_zip(md, [img1, img2])
    with _abrir_zip(data) as zf:
        nombres = sorted(n for n in zf.namelist() if n.startswith("imgs/"))
        # Debe haber 2 imágenes, ambas bajo imgs/, con basenames distintos
        assert len(nombres) == 2
        # Una debe ser foo.png y otra foo_<n>.png
        assert "imgs/foo.png" in nombres
        assert any(re.match(r"imgs/foo_\d+\.png$", n) for n in nombres)


def test_zip_nombres_sanitizados(tmp_path):
    """Nombre con caracteres problemáticos → se sanitiza dentro del ZIP."""
    # No podemos crear un archivo con '?' en disco fácilmente; en vez de eso
    # creamos una imagen real y referenciamos con '../' en el .md para
    # verificar que el packager descarta la travesía.
    img = tmp_path / "imgs" / "real.png"
    _escribir_png(img)

    md = tmp_path / "reporte.md"
    # Referencia con ../ — debe resolverse y la entrada dentro del ZIP NO debe
    # contener .. ni rutas absolutas.
    md.write_text("![x](imgs/../imgs/real.png)\n", encoding="utf-8")

    data = empaquetar_reporte_zip(md, [img])
    with _abrir_zip(data) as zf:
        for n in zf.namelist():
            assert ".." not in n
            assert not n.startswith("/")


def test_zip_solo_stdlib():
    """Heurística: el módulo zip_packager solo importa stdlib."""
    src = (REPO_ROOT / "core" / "zip_packager.py").read_text(encoding="utf-8")
    # Tokens de import — primer término después de import/from
    permitidos = {
        "__future__", "io", "re", "zipfile", "pathlib", "os", "sys", "typing",
    }
    for linea in src.splitlines():
        s = linea.strip()
        if s.startswith("from "):
            mod = s.split()[1].split(".")[0]
            assert mod in permitidos, f"Import no stdlib detectado: {linea}"
        elif s.startswith("import "):
            mod = s.split()[1].split(".")[0]
            assert mod in permitidos, f"Import no stdlib detectado: {linea}"


def test_zip_md_inexistente_lanza(tmp_path):
    """Caso defensivo: si el .md no existe, FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        empaquetar_reporte_zip(tmp_path / "no_existe.md", [])

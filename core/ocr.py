"""OCR automático para boletines escaneados.

Detecta si un PDF tiene texto seleccionable. Si no, ejecuta ocrmypdf
con idioma español para generar una versión con texto extraíble.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import fitz


def pdf_tiene_texto(pdf_path: str, umbral_caracteres: int = 100) -> bool:
    """True si el PDF ya tiene texto seleccionable suficiente.

    Suma caracteres alfanuméricos de las primeras 3 páginas. Si supera
    el umbral, asumimos que NO necesita OCR.
    """
    doc = fitz.open(pdf_path)
    total = 0
    for i, page in enumerate(doc):
        if i >= 3:
            break
        texto = page.get_text("text")
        total += sum(1 for c in texto if c.isalnum())
    doc.close()
    return total >= umbral_caracteres


def herramientas_ocr_disponibles() -> tuple[bool, str]:
    """Devuelve (disponible, mensaje)."""
    if not shutil.which("ocrmypdf"):
        return False, (
            "OCR no disponible: falta `ocrmypdf`. "
            "Instálalo con: brew install ocrmypdf tesseract tesseract-lang"
        )
    if not shutil.which("tesseract"):
        return False, (
            "OCR no disponible: falta `tesseract`. "
            "Instálalo con: brew install tesseract tesseract-lang"
        )
    return True, "OK"


def aplicar_ocr(pdf_entrada: str, idioma: str = "spa") -> str:
    """Ejecuta ocrmypdf y devuelve la ruta del PDF con OCR aplicado.

    Si el PDF ya tiene texto, devuelve la misma ruta sin tocar el archivo.
    """
    if pdf_tiene_texto(pdf_entrada):
        return pdf_entrada

    ok, msg = herramientas_ocr_disponibles()
    if not ok:
        raise RuntimeError(msg)

    salida = Path(tempfile.gettempdir()) / (
        Path(pdf_entrada).stem + "_ocr.pdf"
    )
    cmd = [
        "ocrmypdf",
        "--language", idioma,
        "--output-type", "pdf",
        "--skip-text",          # respeta páginas que ya tengan texto
        "--optimize", "1",
        "--quiet",
        pdf_entrada,
        str(salida),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # --skip-text falla si todo el PDF ya tiene texto; reintentar sin esa flag
        cmd_retry = [c for c in cmd if c != "--skip-text"]
        cmd_retry.insert(-2, "--force-ocr")
        proc2 = subprocess.run(cmd_retry, capture_output=True, text=True)
        if proc2.returncode != 0:
            raise RuntimeError(
                f"ocrmypdf falló:\n{proc.stderr}\n--- retry ---\n{proc2.stderr}"
            )
    return str(salida)

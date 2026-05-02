"""Renderiza una página del PDF como PNG con resaltado amarillo
sobre la entrada del expediente, para verificación visual humana."""
import re
from pathlib import Path
import fitz


AMARILLO_RGB = (1, 1, 0)        # amarillo puro
AMARILLO_OPACITY = 0.45         # semi-transparente


def _candidatos_busqueda(expediente: str, actor: str, cliente: str) -> list[str]:
    """Lista de strings a intentar localizar en la página, en orden de prioridad."""
    candidatos = []
    # Expediente con varias formas
    num, ano = expediente.split("/")
    num_sin_pad = num.lstrip("0") or "0"
    candidatos.append(f"{num_sin_pad}/{ano}")
    candidatos.append(f"{num}/{ano}")
    # Primer apellido del actor (palabras de >3 chars)
    for fuente in (actor, cliente):
        if not fuente:
            continue
        for tok in fuente.split():
            tok_limpio = re.sub(r"[^A-Za-zÁÉÍÓÚáéíóúÑñ]", "", tok)
            if len(tok_limpio) >= 4:
                candidatos.append(tok_limpio)
                break
    return [c for c in candidatos if c]


def renderizar_hoja_con_resaltado(
    pdf_path: str,
    page_index_0based: int,
    expediente: str,
    actor: str,
    cliente: str,
    salida_png: Path,
    dpi: int = 150,
) -> Path | None:
    """Genera un PNG de la página con la entrada del expediente resaltada.

    Devuelve la ruta del PNG si todo bien, None si falló.
    """
    try:
        doc = fitz.open(pdf_path)
        if page_index_0based < 0 or page_index_0based >= doc.page_count:
            doc.close()
            return None
        page = doc[page_index_0based]

        # Buscar coincidencias y agregar highlights
        rects_resaltados = []
        for term in _candidatos_busqueda(expediente, actor, cliente):
            rects = page.search_for(term, quads=False)
            if rects:
                for r in rects:
                    annot = page.add_highlight_annot(r)
                    annot.set_colors(stroke=AMARILLO_RGB)
                    annot.set_opacity(AMARILLO_OPACITY)
                    annot.update()
                rects_resaltados.extend(rects)
                # Si ya encontramos el expediente (primer candidato), suficiente
                if term == _candidatos_busqueda(expediente, actor, cliente)[0]:
                    break

        # Renderizar a PNG
        salida_png.parent.mkdir(parents=True, exist_ok=True)
        pix = page.get_pixmap(dpi=dpi)
        pix.save(str(salida_png))
        doc.close()
        return salida_png if rects_resaltados else salida_png  # devolver siempre
    except Exception:
        return None

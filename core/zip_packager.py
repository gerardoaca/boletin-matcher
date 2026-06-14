"""Empaquetado del reporte (.md + imágenes referenciadas) como ZIP autocontenido.

Motivación
----------
El reporte que genera ``core/reporter.py`` referencia imágenes con rutas
relativas tipo ``imgs/<subcarpeta>/hoja_NN_exp_XXXX.png``. Cuando el usuario
descarga sólo el ``.md`` y lo abre fuera del repo (en su equipo, por email,
en otro servidor) las imágenes con resaltado amarillo se rompen porque la
ruta relativa apunta a archivos que no viajaron con el documento.

Este módulo arma un ZIP autocontenido:

  reporte.zip
  ├── <reporte>.md          (con rutas reescritas a ``imgs/<archivo>.png``)
  ├── imgs/
  │   ├── hoja_172_exp_0488-2023.png
  │   └── hoja_369_exp_1017-2025.png
  └── README.txt            (solo si hubo imágenes faltantes)

Las rutas dentro del ZIP se aplanan: cualquier estructura previa
(``imgs/<subcarpeta>/...``) se colapsa a ``imgs/<archivo>.png``. El ``.md``
se reescribe coherentemente para que cada referencia coincida con el archivo
ya colocado en ``imgs/`` del ZIP.

Diseño
------
- Sólo stdlib (``zipfile``, ``re``, ``pathlib``).
- Compresión ``ZIP_DEFLATED`` (los PNG ya están comprimidos pero el .md y
  cualquier texto auxiliar sí se benefician).
- Sanitización defensiva de nombres: nada de rutas absolutas ni ``..``.
- Tolerante a imágenes faltantes: se registran en ``README.txt`` y se
  remueven (con un placeholder textual) del ``.md`` reescrito para no dejar
  enlaces rotos.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

# Patrón markdown imagen: ![alt](path.png)
_IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+\.png)\)")


def _safe_name(name: str) -> str:
    """Devuelve un nombre de archivo seguro para usar dentro del ZIP.

    - Elimina componentes de path (``/``, ``\\``)
    - Elimina ``..`` y prefijos absolutos
    - Sustituye caracteres problemáticos por ``_``
    """
    # Tomar solo el basename
    base = Path(name).name
    # Quitar caracteres problemáticos para sistemas de archivos comunes
    base = re.sub(r"[<>:\"|?*\x00-\x1f]", "_", base)
    # Defensa contra strings vacíos o solo puntos
    if not base or set(base) <= {"."}:
        base = "archivo.png"
    return base


def _resolver_referencias(md_text: str, md_dir: Path) -> list[tuple[str, Path]]:
    """Extrae las referencias a PNGs del .md y devuelve [(ref_original, path_abs)].

    ``path_abs`` puede no existir; el caller decide qué hacer.
    """
    refs: list[tuple[str, Path]] = []
    for match in _IMG_MD_RE.finditer(md_text):
        ref = match.group(2)
        # Resolver respecto al directorio del .md
        candidato = (md_dir / ref).resolve()
        refs.append((ref, candidato))
    return refs


def empaquetar_reporte_zip(
    md_path: Path,
    imgs_referenciadas: list[Path],
    nombre_zip: str | None = None,
) -> bytes:
    """Devuelve los bytes de un ZIP autocontenido con el .md y sus imágenes.

    Parameters
    ----------
    md_path : Path
        Ruta absoluta al archivo .md del reporte.
    imgs_referenciadas : list[Path]
        Lista de paths absolutos a las imágenes que el .md referencia. Puede
        venir vacía: en ese caso el packager intenta resolverlas parseando el
        propio .md (rutas relativas a ``md_path.parent``).
    nombre_zip : str | None
        Nombre lógico del ZIP (no se usa para nada salvo retornarse implícito
        en el ``Content-Disposition`` que arme el caller). Se acepta para
        compatibilidad con la API documentada.

    Returns
    -------
    bytes
        Contenido binario del ZIP listo para ``st.download_button`` o
        cualquier transporte.
    """
    md_path = Path(md_path).resolve()
    if not md_path.exists():
        raise FileNotFoundError(f"No existe el .md: {md_path}")

    md_text = md_path.read_text(encoding="utf-8")
    md_dir = md_path.parent

    # 1. Obtener todas las referencias del .md
    referencias = _resolver_referencias(md_text, md_dir)

    # 2. Construir índice (ref_original -> path_abs) priorizando lo que
    #    pasó el caller. El caller pasa abs paths sin saber la ref textual,
    #    así que matcheamos por basename.
    pasadas_por_basename: dict[str, Path] = {}
    for p in imgs_referenciadas or []:
        p = Path(p).resolve()
        pasadas_por_basename[p.name] = p

    # 3. Decidir qué imagen entra al ZIP y bajo qué nombre aplanado.
    #    Si hay colisión de basename entre subcarpetas distintas, anteponer
    #    un sufijo numérico.
    plan: list[tuple[str, Path, str]] = []  # (ref_original, src_abs, dst_in_zip)
    faltantes: list[str] = []
    nombres_usados: set[str] = set()
    ref_a_nuevo: dict[str, str] = {}  # ref_original -> "imgs/<nombre>"

    for ref, candidato in referencias:
        base = _safe_name(Path(ref).name)
        # Resolver source: primero el que pasó el caller (por basename),
        # luego el candidato resuelto contra md_dir.
        src = pasadas_por_basename.get(Path(ref).name) or pasadas_por_basename.get(base)
        if src is None and candidato.exists():
            src = candidato

        if src is None or not Path(src).exists():
            faltantes.append(ref)
            continue

        # Resolver colisión de nombres dentro del ZIP
        dst_base = base
        contador = 1
        while dst_base in nombres_usados:
            stem = Path(base).stem
            suf = Path(base).suffix
            dst_base = f"{stem}_{contador}{suf}"
            contador += 1
        nombres_usados.add(dst_base)

        dst_in_zip = f"imgs/{dst_base}"
        plan.append((ref, Path(src), dst_in_zip))
        ref_a_nuevo[ref] = dst_in_zip

    # 4. Reescribir el .md para que cada referencia apunte al nombre nuevo,
    #    o sea reemplazada por un placeholder si la imagen está faltante.
    def _reemplazo(match: re.Match) -> str:
        alt = match.group(1)
        ref = match.group(2)
        nuevo = ref_a_nuevo.get(ref)
        if nuevo:
            return f"![{alt}]({nuevo})"
        # Imagen faltante: dejar placeholder textual sin romper render
        return f"_(imagen no disponible: {ref})_"

    md_reescrito = _IMG_MD_RE.sub(_reemplazo, md_text)

    # 5. Construir el ZIP en memoria
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # .md en el root del ZIP, con nombre saneado
        md_dst = _safe_name(md_path.name)
        zf.writestr(md_dst, md_reescrito)

        # Imágenes
        for _ref, src, dst in plan:
            try:
                zf.write(src, arcname=dst)
            except OSError as e:
                # Si el archivo desaparece entre la verificación y la escritura,
                # lo registramos como faltante y seguimos.
                faltantes.append(f"{_ref} (error de lectura: {e})")

        # README si hubo faltantes
        if faltantes:
            contenido = (
                "Imágenes faltantes al empaquetar este reporte\n"
                "=============================================\n\n"
                "Las siguientes referencias del .md no pudieron incluirse en el ZIP\n"
                "porque el archivo origen no existía o no fue accesible:\n\n"
                + "\n".join(f"  - {f}" for f in faltantes)
                + "\n\nEn el .md fueron reemplazadas por un placeholder textual\n"
                "para no dejar enlaces rotos al abrir el documento.\n"
            )
            zf.writestr("README.txt", contenido)

    return buffer.getvalue()

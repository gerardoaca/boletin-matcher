"""Generación del documento.md con coincidencias."""
import hashlib
import re
from datetime import datetime
from .matcher import Coincidencia


def _frase_busqueda(bloque_texto: str, expediente: str) -> str:
    """Devuelve un fragmento corto y único cerca del expediente para
    que el humano pueda hacer Ctrl+F en el PDF y localizarlo de inmediato.
    """
    # Normalizar para regex
    pattern = re.escape(expediente.lstrip("0"))
    # Buscar el expediente en el bloque
    m = re.search(pattern, bloque_texto)
    if not m:
        return ""
    # Tomar las 6-8 palabras que preceden al expediente: usualmente
    # es el nombre del actor + 'vs.' + parte del demandado
    pre = bloque_texto[:m.start()].rstrip(" .,")
    palabras = pre.split()
    # Buscar hacia atrás hasta encontrar un punto que termine la entrada anterior
    fin_entrada_previa = pre.rfind(". ")
    if fin_entrada_previa > 0:
        frase = pre[fin_entrada_previa + 2:].strip()
    else:
        frase = " ".join(palabras[-12:])
    # Limpiar y truncar
    frase = re.sub(r"\s+", " ", frase).strip()
    return frase[:100]


def _hash_bloque(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12]


def generar_md(
    boletin_nombre: str,
    fecha_proceso: str,
    enriquecidas: list[dict],
    revision: list[Coincidencia],
) -> str:
    lines = []
    lines.append(f"# Coincidencias Boletín — {boletin_nombre}")
    lines.append("")
    lines.append(f"**Fecha de procesamiento:** {fecha_proceso}")
    lines.append(f"**Coincidencias validadas:** {len(enriquecidas)}")
    lines.append(f"**Casos para revisión humana:** {len(revision)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if enriquecidas:
        lines.append("## Coincidencias validadas")
        lines.append("")
        for i, item in enumerate(enriquecidas, start=1):
            c: Coincidencia = item["coincidencia"]
            actor_display = c.actor_listado if c.actor_listado else "*** RESERVADO ***"
            lines.append(f"### {i}. Actor: {actor_display}")
            lines.append("")
            lines.append(f"- **Expediente:** {c.expediente}")
            pag = f"**página impresa {c.pagina_impresa}**" if c.pagina_impresa else ""
            lines.append(
                f"- **📍 Localizar en el PDF:** ve a {pag} "
                f"(o página {c.hoja} del visor)"
                if c.pagina_impresa else
                f"- **📍 Localizar en el PDF:** página {c.hoja} del visor"
            )
            frase = _frase_busqueda(c.bloque_texto, c.expediente)
            if frase:
                lines.append(f"  - Usa **Ctrl+F** y busca: `{frase}`")
            lines.append(f"- **Juzgado (boletín):** {item['juzgado_boletin'] or '(no detectado)'}")
            if c.juzgado_listado:
                lines.append(f"- **Juzgado (listado):** {c.juzgado_listado}")
            lines.append(f"- **Cliente asignado:** {c.cliente or '(sin asignar)'}")
            if item.get("tipo_acuerdo"):
                lines.append(f"- **Tipo de acuerdo:** {item['tipo_acuerdo']}")
            if item.get("entrada_literal"):
                lines.append(f"- **Entrada literal del expediente:**")
                lines.append(f"  > {item['entrada_literal']}")
            lines.append(f"- **Síntesis del asunto:** {item['sintesis']}")
            if item.get("imagen_hoja"):
                lines.append("")
                lines.append(f"**🖼️ Vista de la hoja con resaltado amarillo:**")
                lines.append("")
                lines.append(f"![Hoja {c.pagina_impresa or c.hoja} — exp {c.expediente}]({item['imagen_hoja']})")
            lines.append(f"- **Ruta de validación:** {c.ruta_validacion} — {c.motivo}")
            lines.append(f"- **Confianza IA:** {item['confianza']}")
            lines.append(f"- **Hash bloque:** `{_hash_bloque(c.bloque_texto)}`")
            lines.append(f"- **Fila listado:** {c.fila_listado}")
            lines.append("")

    if revision:
        lines.append("---")
        lines.append("")
        lines.append("## Casos para revisión humana")
        lines.append("")
        lines.append("> Estos casos NO fueron auto-validados. Requieren confirmación manual.")
        lines.append("")
        for i, c in enumerate(revision, start=1):
            actor_display = c.actor_listado if c.actor_listado else "*** RESERVADO ***"
            lines.append(f"### R{i}. Expediente: {c.expediente}")
            lines.append("")
            lines.append(f"- **Actor (listado):** {actor_display}")
            pag = f"**página impresa {c.pagina_impresa}**" if c.pagina_impresa else ""
            lines.append(
                f"- **📍 Localizar en el PDF:** ve a {pag} "
                f"(o página {c.hoja} del visor)"
                if c.pagina_impresa else
                f"- **📍 Localizar en el PDF:** página {c.hoja} del visor"
            )
            frase = _frase_busqueda(c.bloque_texto, c.expediente)
            if frase:
                lines.append(f"  - Usa **Ctrl+F** y busca: `{frase}`")
            lines.append(f"- **Cliente asignado:** {c.cliente or '(sin asignar)'}")
            lines.append(f"- **Motivo:** {c.motivo}")
            lines.append(f"- **Hash bloque:** `{_hash_bloque(c.bloque_texto)}`")
            lines.append("")
            lines.append("<details><summary>Texto literal del bloque</summary>")
            lines.append("")
            lines.append("```")
            lines.append(c.bloque_texto)
            lines.append("```")
            lines.append("</details>")
            lines.append("")

    return "\n".join(lines)

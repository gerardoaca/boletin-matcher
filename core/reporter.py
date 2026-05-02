"""Generación del documento.md con coincidencias."""
import hashlib
from datetime import datetime
from .matcher import Coincidencia


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
            lines.append(f"- **Hoja del boletín:** {c.hoja} (líneas {c.linea_inicio}–{c.linea_fin})")
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
            lines.append(f"- **Ruta de validación:** {c.ruta_validacion} — {c.motivo}")
            lines.append(f"- **Confianza IA:** {item['confianza']}")
            lines.append(f"- **Hash bloque:** `{_hash_bloque(c.bloque_texto)}`")
            lines.append(f"- **Fila listado:** {c.fila_listado}")
            lines.append("")
            lines.append("<details><summary>Texto literal del bloque</summary>")
            lines.append("")
            lines.append("```")
            lines.append(c.bloque_texto)
            lines.append("```")
            lines.append("</details>")
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
            lines.append(f"- **Hoja:** {c.hoja} (líneas {c.linea_inicio}–{c.linea_fin})")
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

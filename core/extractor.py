"""Extracción de boletines y localización de expedientes con ventana de contexto.

Estrategia robusta para los dos formatos del Boletín Judicial CDMX:
  Formato Salas:    [Actor] vs. [Demandado]. ... T. [Ap] NNN/AAAA/CCC ... Acdo.
  Formato Juzgados: Núm. Exp. NNN/AAAA. [Actor] vs. [Demandado]. ... Acdo.

En lugar de segmentar perfectamente, ubicamos cada expediente y tomamos
una ventana de líneas alrededor para validar actor y juzgado.
"""
import re
import fitz
from dataclasses import dataclass, field


@dataclass
class HojaBoletin:
    numero: int
    texto: str
    lineas: list[str]
    juzgados_pagina: list[str] = field(default_factory=list)


@dataclass
class BloqueEntrada:
    hoja: int
    linea_inicio: int
    linea_fin: int
    texto: str
    juzgado_seccion: str
    expediente_match: str = ""


EXPEDIENTE_LINEA_RE = re.compile(r"\b(\d{1,6})\s*/\s*(\d{4})\b")


# Encabezados de juzgado/sala (más permisivos)
JUZGADO_HEADER_PATTERNS = [
    re.compile(
        r"\b(PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|SEXTA|SÉPTIMA|SEPTIMA|OCTAVA|NOVENA|DÉCIMA|DECIMA|DECIMOPRIMERA|DECIMOSEGUNDA)\s+SALA\s+(CIVIL|FAMILIAR|MERCANTIL)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bJUZGAD[OA]\s+\d+\s+DE\s+LO\s+(CIVIL|FAMILIAR|MERCANTIL)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bJUZGAD[OA]\s+(PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SÉPTIMO|SEPTIMO|OCTAVO|NOVENO|DÉCIMO|DECIMO|UNDÉCIMO|UNDECIMO|DUODÉCIMO|DUODECIMO|DECIMOTERCERO|DECIMOCUARTO|DECIMOQUINTO|DECIMOSEXTO|DECIMOSÉPTIMO|DECIMOSEPTIMO|DECIMOCTAVO|DECIMONOVENO|VIGÉSIMO|VIGESIMO|VIGESIMOPRIMERO|VIGESIMOSEGUNDO|VIGESIMOTERCERO|VIGESIMOCUARTO|VIGESIMOQUINTO|VIGESIMOSEXTO|VIGESIMOSEPTIMO|VIGESIMOOCTAVO|VIGESIMONOVENO|TRIGÉSIMO|TRIGESIMO)\s+(?:DE\s+LO\s+)?(CIVIL|FAMILIAR|MERCANTIL)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d+\s+(?:DE\s+LO\s+)?(CIVIL|FAMILIAR|MERCANTIL)(?:\s+(?:DE\s+)?PROCESO\s+ORAL)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bSALA\s+\w+\s+(CIVIL|FAMILIAR|MERCANTIL)\b", re.IGNORECASE),
]


def _es_juzgado_header(linea: str) -> bool:
    s = linea.strip()
    if len(s) < 5 or len(s) > 130:
        return False
    return any(p.search(s) for p in JUZGADO_HEADER_PATTERNS)


def extraer_hojas(pdf_path: str) -> list[HojaBoletin]:
    doc = fitz.open(pdf_path)
    hojas = []
    for i, page in enumerate(doc, start=1):
        texto = page.get_text("text")
        lineas = texto.split("\n")
        juzgados_pag = [l.strip() for l in lineas if _es_juzgado_header(l)]
        hojas.append(HojaBoletin(
            numero=i, texto=texto, lineas=lineas,
            juzgados_pagina=juzgados_pag,
        ))
    doc.close()
    return hojas


def localizar_expedientes_en_hoja(
    hoja: HojaBoletin,
    ventana_atras: int = 12,
    ventana_adelante: int = 6,
) -> list[BloqueEntrada]:
    """Para cada expediente en la hoja, devuelve un bloque-ventana con contexto."""
    bloques = []
    juzgado_seccion_actual = ""

    for idx, raw in enumerate(hoja.lineas, start=1):
        linea = raw.strip()

        if _es_juzgado_header(linea):
            juzgado_seccion_actual = linea
            continue

        for m in EXPEDIENTE_LINEA_RE.finditer(linea):
            num = m.group(1).lstrip("0") or "0"
            año = m.group(2)
            exp = f"{int(num):04d}/{año}"

            ini = max(1, idx - ventana_atras)
            fin = min(len(hoja.lineas), idx + ventana_adelante)
            ventana_lineas = hoja.lineas[ini - 1: fin]
            ventana_texto = " ".join(
                l.strip() for l in ventana_lineas if l.strip()
            )

            bloques.append(BloqueEntrada(
                hoja=hoja.numero,
                linea_inicio=ini,
                linea_fin=fin,
                texto=ventana_texto,
                juzgado_seccion=juzgado_seccion_actual,
                expediente_match=exp,
            ))
    return bloques


# Compatibilidad con código antiguo
def segmentar_entradas(hoja: HojaBoletin) -> list[BloqueEntrada]:
    return localizar_expedientes_en_hoja(hoja)

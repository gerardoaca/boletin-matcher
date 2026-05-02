"""Normalización de expedientes y nombres para matching exacto."""
import re
from unidecode import unidecode

RESERVADO_TOKENS = {
    "***", "* * *", "RESERVADO", "CONFIDENCIAL", "SECRETO",
    "NOMBRE RESERVADO", "PROTEGIDO", "DATOS RESERVADOS",
    # Actores genéricos que NO aparecen literalmente en el boletín
    "SUCESION INTESTAMENTARIA", "SUCESION TESTAMENTARIA",
    "SUCESION", "INTESTADO", "TESTAMENTARIA",
}

EXPEDIENTE_PATTERNS = [
    # Forma estándar: 813/2024  ó  813 - 2024  ó  813-2024
    re.compile(r"(?<!\d)(\d{1,6})\s*[/\-]\s*(\d{4})(?!\d)"),
    # Forma año corto: 813/24
    re.compile(r"(?<!\d)(\d{1,6})\s*[/\-]\s*(\d{2})(?!\d)"),
]

# Stopwords y conectores que NO cuentan como tokens significativos del nombre
NOMBRE_STOPWORDS = {
    "DE", "DEL", "LA", "LAS", "EL", "LOS", "Y", "E", "O",
    "S", "A", "C", "V", "P", "I", "SA", "SAPI", "RL", "CV",
    "SAB", "SADE", "SADECV", "SAPIDECV", "SAPIDC",
    "SOCIEDAD", "ANONIMA", "ANÓNIMA", "CAPITAL", "VARIABLE",
    "SU", "SUS", "POR", "EN", "CON",
}


def normalizar_texto(texto: str) -> str:
    if texto is None:
        return ""
    t = unidecode(str(texto)).upper()
    t = re.sub(r"[^\w\s/\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalizar_expediente(raw: str) -> str | None:
    """Devuelve expediente canónico NÚMERO/AÑO con padding (ej. 0123/2025)."""
    if not raw:
        return None
    s = unidecode(str(raw)).upper().strip()
    for pat in EXPEDIENTE_PATTERNS:
        m = pat.search(s)
        if m:
            num = m.group(1).lstrip("0") or "0"
            año = m.group(2)
            if len(año) == 2:
                año = "20" + año if int(año) < 50 else "19" + año
            return f"{int(num):04d}/{año}"
    return None


def extraer_expedientes(texto: str) -> list[str]:
    """Encuentra todos los expedientes en un texto, en formato canónico."""
    encontrados = set()
    for pat in EXPEDIENTE_PATTERNS:
        for m in pat.finditer(texto):
            num = m.group(1).lstrip("0") or "0"
            año = m.group(2)
            if len(año) == 2:
                año = "20" + año if int(año) < 50 else "19" + año
            encontrados.add(f"{int(num):04d}/{año}")
    return sorted(encontrados)


def es_actor_reservado(nombre: str) -> bool:
    if not nombre:
        return True
    n = normalizar_texto(nombre).strip()
    if not n or n in RESERVADO_TOKENS:
        return True
    if re.fullmatch(r"[\*\s]+", n):
        return True
    return False


def normalizar_nombre(nombre: str) -> str:
    return normalizar_texto(nombre)


def normalizar_juzgado(juzgado: str) -> str:
    return normalizar_texto(juzgado)


def tokens_significativos(nombre: str) -> set[str]:
    """Devuelve el conjunto de tokens significativos de un nombre.

    Filtra stopwords, conectores y tokens cortos (<=2). Sirve para
    matching de nombres independiente del orden (listado: 'LEONOR AMELIA
    VILLALOBOS BEDOLLA' vs boletín: 'Villalobos Bedolla Leonor Amelia').
    """
    if not nombre:
        return set()
    n = normalizar_texto(nombre)
    return {
        t for t in n.split()
        if len(t) > 2 and t not in NOMBRE_STOPWORDS and not t.isdigit()
    }


def todos_tokens_en_texto(nombre: str, texto_norm: str) -> bool:
    """True si TODOS los tokens significativos del nombre están en texto_norm."""
    toks = tokens_significativos(nombre)
    if not toks:
        return False
    return all(t in texto_norm for t in toks)


def dividir_partes(nombre: str) -> list[str]:
    """Divide un campo de partes procesales múltiples en candidatos.

    Ej: "ALFREDO HIDALGO TAPIA y MIGUEL GUADARRAMA VÁZQUEZ"
       → ["ALFREDO HIDALGO TAPIA", "MIGUEL GUADARRAMA VÁZQUEZ"]
    Ej: "A, B y C" → ["A", "B", "C"]
    """
    if not nombre:
        return []
    n = normalizar_texto(nombre)
    # separadores: " Y ", coma; mantener tokens compuestos legítimos
    partes = re.split(r"\s+Y\s+|,\s*", n)
    return [p.strip() for p in partes if p.strip()]


def alguna_parte_en_texto(nombre: str, texto_norm: str) -> bool:
    """True si AL MENOS UNA parte del campo (separado por Y/coma)
    tiene todos sus tokens significativos en texto_norm."""
    partes = dividir_partes(nombre)
    if not partes:
        return False
    return any(todos_tokens_en_texto(p, texto_norm) for p in partes)

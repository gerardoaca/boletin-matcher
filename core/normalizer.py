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


# --------------------------------------------------------------------------- #
# Parser ordinal compositivo en español (sin tope numérico).
# Permite que la app reconozca el número del juzgado SIN tabla acotada:
#   DECIMO PRIMERO = UNDECIMO = 11
#   VIGESIMO TERCERO = 23
#   CENTESIMO VIGESIMO TERCERO = 123
#   DUCENTESIMO QUINCUAGESIMO OCTAVO = 258
#   NONINGENTESIMO NONAGESIMO NOVENO = 999
# Política irreductible: si el parser no resuelve con certeza, devuelve None
# y el matcher degrada a REVISION (nunca valida bajo ambigüedad).
# --------------------------------------------------------------------------- #

_ORD_UNI = {
    "PRIMERO": 1, "PRIMER": 1, "SEGUNDO": 2, "TERCERO": 3, "TERCER": 3,
    "CUARTO": 4, "QUINTO": 5, "SEXTO": 6, "SEPTIMO": 7, "OCTAVO": 8,
    "NOVENO": 9,
}
_ORD_DEC = {
    "DECIMO": 10, "VIGESIMO": 20, "TRIGESIMO": 30, "CUADRAGESIMO": 40,
    "QUINCUAGESIMO": 50, "SEXAGESIMO": 60, "SEPTUAGESIMO": 70,
    "OCTOGESIMO": 80, "NONAGESIMO": 90,
}
_ORD_CEN = {
    "CENTESIMO": 100, "DUCENTESIMO": 200, "TRICENTESIMO": 300,
    "CUADRINGENTESIMO": 400, "QUINGENTESIMO": 500, "SEXCENTESIMO": 600,
    "SEPTINGENTESIMO": 700, "OCTINGENTESIMO": 800, "NONINGENTESIMO": 900,
}
# Formas irregulares 11–12 (sintéticas con DECIMO también se aceptan)
_ORD_IRR = {"UNDECIMO": 11, "DUODECIMO": 12}

# Reparaciones OCR canónicas dentro de un token candidato a ordinal.
_OCR_FIX = str.maketrans({"5": "S", "0": "O", "1": "I"})


def _flex_ordinal(token: str) -> str:
    """Normaliza un token ordinal: quita género/número final, mayúsculas, OCR comunes."""
    if not token:
        return token
    t = token
    # Quitar sufijos de género/número: PRIMERA→PRIMER + O, etc. Mantener variantes
    # más comunes mapeadas vía _ORD_UNI/_ORD_DEC. Aquí solo arreglamos -A/-AS/-OS.
    if t.endswith("AS") or t.endswith("OS"):
        t = t[:-2] + "O"
    elif t.endswith("A"):
        t = t[:-1] + "O"
    return t


def _lookup_ordinal(token: str) -> tuple[int | None, str]:
    """Resuelve un token ordinal. Devuelve (valor, motivo_correccion).
    motivo_correccion ∈ {"", "ocr", "flex"} para auditoría.
    """
    if not token:
        return None, ""
    tflex = _flex_ordinal(token)
    for d in (_ORD_IRR, _ORD_CEN, _ORD_DEC, _ORD_UNI):
        if tflex in d:
            return d[tflex], "" if tflex == token else "flex"
    # Reparación OCR conservadora (5→S, 0→O, 1→I) — sólo si produce match único
    cand = tflex.translate(_OCR_FIX)
    if cand != tflex:
        for d in (_ORD_IRR, _ORD_CEN, _ORD_DEC, _ORD_UNI):
            if cand in d:
                return d[cand], "ocr"
    return None, ""


def _parse_ordinal_es(texto_norm: str) -> int | None:
    """Parser greedy compositivo. Suma centenas + decenas + unidades.
    Acepta cualquier composición válida (sin tope). Devuelve None si:
      - no encuentra ningún morfema ordinal,
      - un mismo orden se repite (e.g. dos centenas) → ambigüedad.
    Cualquier basura intermedia (palabras no-ordinales) se ignora; lo que
    no se ignora es la presencia de morfemas inválidos en posiciones
    contradictorias.
    """
    if not texto_norm:
        return None
    tokens = texto_norm.split()
    seen_cen = seen_dec = seen_uni = seen_irr = False
    total = 0
    matched_any = False
    for tok in tokens:
        val, _why = _lookup_ordinal(tok)
        if val is None:
            continue
        # Clasificar y validar no-repetición de orden
        if val >= 100:
            if seen_cen:
                return None  # dos centenas → ambigüedad
            seen_cen = True
            total += val
        elif val in _ORD_IRR.values():
            if seen_irr or seen_dec or seen_uni:
                return None
            seen_irr = True
            total += val
        elif val % 10 == 0 and val >= 10:
            if seen_dec or seen_irr:
                return None
            seen_dec = True
            total += val
        else:  # unidad 1..9
            if seen_uni or seen_irr:
                return None
            seen_uni = True
            total += val
        matched_any = True
    return total if matched_any else None


def _numero_juzgado(texto_norm: str) -> int | None:
    """Extrae el número del juzgado. Estrategia:
      1) Dígito arábigo directo en el texto ("JUZGADO 23 CIVIL").
      2) Ordinal escrito vía parser compositivo (sin tope).
    Si AMBOS están presentes y no coinciden → None (ambigüedad → REVISIÓN).
    """
    if not texto_norm:
        return None
    n_digito: int | None = None
    # Sólo aceptamos dígitos que sean un TOKEN propio (no embebidos en
    # palabras corruptas por OCR, p.ej. "VIGE5IMO" → no debe leerse "5").
    m = re.search(r"(?:^|\s)(\d{1,4})(?:\s|$)", texto_norm)
    if m:
        cand = int(m.group(1))
        if 1 <= cand <= 9999:
            n_digito = cand
    n_ordinal = _parse_ordinal_es(texto_norm)
    # Cross-check: si los dos existen y disienten, no inventes — REVISIÓN.
    if n_digito is not None and n_ordinal is not None:
        if n_digito != n_ordinal:
            return None
        return n_digito
    return n_digito if n_digito is not None else n_ordinal


_MATERIAS = {"CIVIL", "FAMILIAR", "PENAL", "MERCANTIL",
             "ARRENDAMIENTO", "INMOBILIARIO", "PAZ", "CONTROL",
             "ENJUICIAMIENTO", "EJECUCION", "ADOLESCENTES", "TUTELA"}
# Modalidad procesal: si ambos lados la declaran, DEBE coincidir exactamente
_MODALIDADES = {"ORAL", "ESCRITO", "TRADICIONAL"}


def _tokens_de(texto_norm: str, universo: set[str]) -> set[str]:
    return {t for t in texto_norm.split() if t in universo}


def normalizar_juzgado(juzgado: str) -> str:
    return normalizar_texto(juzgado)


def juzgados_equivalentes(juzgado_listado_norm: str, header_norm: str) -> bool:
    """True si juzgado del listado y header del boletín apuntan al mismo órgano.

    Compara por número (dígito ↔ ordinal escrito) y materia. Si el listado
    no tiene número detectable, cae al match de substring permisivo previo.
    """
    if not juzgado_listado_norm or not header_norm:
        return False
    n_listado = _numero_juzgado(juzgado_listado_norm)
    n_header = _numero_juzgado(header_norm)
    if n_listado is not None and n_header is not None:
        if n_listado != n_header:
            return False
        mat_l = _tokens_de(juzgado_listado_norm, _MATERIAS)
        mat_h = _tokens_de(header_norm, _MATERIAS)
        # Si ambos traen materia, debe intersectar
        if mat_l and mat_h and not (mat_l & mat_h):
            return False
        mod_l = _tokens_de(juzgado_listado_norm, _MODALIDADES)
        mod_h = _tokens_de(header_norm, _MODALIDADES)
        # Si ambos traen modalidad procesal (Oral/Escrito), debe ser idéntica:
        # el juzgado 23 ORAL y el juzgado 23 ESCRITO son órganos distintos.
        if mod_l and mod_h and mod_l != mod_h:
            return False
        return True
    # Fallback: substring (comportamiento previo)
    return juzgado_listado_norm in header_norm


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


def tokens_juntos_en_texto(nombre: str, texto_norm: str, max_gap: int = 120) -> bool:
    """True si todos los tokens significativos del nombre aparecen dentro de
    una misma ventana de `max_gap` caracteres en texto_norm.

    Sirve para evitar falsos homónimos: "VELAZQUEZ", "CARRANZA" y "ARMANDO"
    pueden aparecer en una página entera como nombres de pleitos distintos
    sin pertenecer a la misma persona. Si los tres tokens no caben juntos en
    ~un renglón/párrafo, no son la misma parte procesal.
    """
    toks = tokens_significativos(nombre)
    if not toks:
        return False
    if not all(t in texto_norm for t in toks):
        return False
    # Posiciones de la primera aparición de cada token
    posiciones = []
    for t in toks:
        idxs = []
        start = 0
        while True:
            pos = texto_norm.find(t, start)
            if pos == -1:
                break
            idxs.append(pos)
            start = pos + 1
        posiciones.append(sorted(idxs))
    # Búsqueda greedy: elegir un índice por token tal que max-min <= max_gap.
    # Heurística simple: iterar combinaciones limitando por la primera lista.
    for p0 in posiciones[0]:
        elegidos = [p0]
        ok = True
        for lista in posiciones[1:]:
            # buscar el más cercano a p0
            mejor = min(lista, key=lambda x: abs(x - p0))
            elegidos.append(mejor)
            if max(elegidos) - min(elegidos) > max_gap:
                ok = False
                break
        if ok and max(elegidos) - min(elegidos) <= max_gap:
            return True
    return False


def alguna_parte_junta_en_texto(nombre: str, texto_norm: str, max_gap: int = 120) -> bool:
    """Como alguna_parte_en_texto pero exigiendo proximidad entre tokens.
    Útil para el filtro anti-homónimo sobre una HOJA completa: evita
    considerar "presente" a un nombre cuyos tokens están dispersos en
    pleitos distintos.
    """
    partes = dividir_partes(nombre)
    if not partes:
        return False
    return any(tokens_juntos_en_texto(p, texto_norm, max_gap) for p in partes)

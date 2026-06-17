"""Carga de listados de clientes desde Excel, CSV o PDF.

Incluye validación con mensajes humanos pensada para abogados no-técnicos.
La API histórica `cargar_listado(path) -> list[RegistroCliente]` se mantiene
intacta; para detalles ricos (warnings, columnas detectadas/faltantes) usar
`cargar_listado_detallado(path) -> ResultadoCargaListado`.
"""
import re
import logging
import datetime as _dt
from pathlib import Path
from dataclasses import dataclass, field

import pandas as pd
import fitz

from .normalizer import (
    normalizar_expediente,
    normalizar_nombre,
    normalizar_juzgado,
    es_actor_reservado,
)


@dataclass
class RegistroCliente:
    expediente: str           # canónico
    actor: str                # normalizado, vacío si reservado o desconocido
    actor_reservado: bool     # True si declarado explícitamente reservado/genérico
    juzgado: str              # normalizado
    cliente: str              # nombre del cliente al que se asigna
    fila_origen: int          # para auditoría
    raw: dict                 # registro original sin tocar
    actor_desconocido: bool = False  # True si la celda actor está vacía (no declarado)


@dataclass
class ResultadoCargaListado:
    registros: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    columnas_detectadas: dict = field(default_factory=dict)
    columnas_faltantes: list = field(default_factory=list)


COLUMNAS_ESPERADAS = {
    "expediente": ["expediente", "numero de expediente", "no expediente",
                   "juicio", "numero de juicio", "no juicio", "exp"],
    "actor": ["actor", "actores", "demandante", "promovente", "parte actora",
              "nombre actor"],
    "juzgado": ["juzgado", "tribunal", "organo", "autoridad",
                "juzgado/tribunal"],
    "cliente": ["cliente", "asignado", "asignado a", "responsable",
                "abogado", "asunto cliente"],
}


_EXPEDIENTE_ANIO_RE = re.compile(r"(\d{4})(?!.*\d{4})")
_ACTOR_RAREZA_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]")


def _detectar_columna(columnas_df, candidatos):
    cols_norm = {c: c.lower().strip() for c in columnas_df}
    for col, norm in cols_norm.items():
        if norm in candidatos:
            return col
    for col, norm in cols_norm.items():
        for cand in candidatos:
            if cand in norm:
                return col
    return None


def _extraer_anio(exp_canonico: str):
    m = _EXPEDIENTE_ANIO_RE.search(exp_canonico)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _construir_resultado(df: pd.DataFrame) -> ResultadoCargaListado:
    res = ResultadoCargaListado()
    cols = list(df.columns)

    col_exp = _detectar_columna(cols, COLUMNAS_ESPERADAS["expediente"])
    col_actor = _detectar_columna(cols, COLUMNAS_ESPERADAS["actor"])
    col_juz = _detectar_columna(cols, COLUMNAS_ESPERADAS["juzgado"])
    col_cli = _detectar_columna(cols, COLUMNAS_ESPERADAS["cliente"])

    if col_exp is None:
        nombres_aceptados = ", ".join(COLUMNAS_ESPERADAS["expediente"])
        raise ValueError(
            "No encontré la columna de **expediente** en el listado.\n"
            f"Columnas detectadas en tu archivo: {cols}\n"
            f"Columnas esperadas (cualquiera de estas, mayúsculas/minúsculas "
            f"da igual): {nombres_aceptados}.\n"
            "Sugerencia: renombra una columna a 'Expediente' y vuelve a "
            "cargar el archivo."
        )

    detectadas = {"expediente": col_exp}
    if col_actor:
        detectadas["actor"] = col_actor
    if col_juz:
        detectadas["juzgado"] = col_juz
    if col_cli:
        detectadas["cliente"] = col_cli
    res.columnas_detectadas = detectadas

    for clave in ("actor", "juzgado", "cliente"):
        if clave not in detectadas:
            res.columnas_faltantes.append(clave)
            res.warnings.append({
                "fila": None,
                "campo": clave,
                "valor": None,
                "motivo": (
                    f"Columna '{clave}' no detectada — la app funcionará "
                    "pero con menor precisión. Nombres aceptados: "
                    + ", ".join(COLUMNAS_ESPERADAS[clave])
                ),
            })

    anio_actual = _dt.date.today().year
    vistos: dict = {}  # clave -> fila origen

    for idx, row in df.iterrows():
        fila_origen = int(idx) + 2  # header + 1-indexado
        exp_raw = row.get(col_exp, "")
        exp_raw_str = "" if exp_raw is None else str(exp_raw).strip()
        exp = normalizar_expediente(exp_raw_str)

        if not exp:
            # Solo registra warning si la celda venía con algo (no fila vacía).
            if exp_raw_str:
                motivo = "expediente inválido — falta año o formato no reconocido"
                if exp_raw_str.isdigit() and len(exp_raw_str) <= 4:
                    motivo = (
                        f"expediente '{exp_raw_str}' incompleto: falta el año "
                        "(formato esperado: NNN/AAAA)"
                    )
                res.warnings.append({
                    "fila": fila_origen,
                    "campo": "expediente",
                    "valor": exp_raw_str,
                    "motivo": motivo,
                })
            continue

        anio = _extraer_anio(exp)
        if anio is not None:
            if anio > anio_actual + 1:
                res.warnings.append({
                    "fila": fila_origen,
                    "campo": "expediente",
                    "valor": exp,
                    "motivo": f"año {anio} parece futuro",
                })
            elif anio < 1980:
                res.warnings.append({
                    "fila": fila_origen,
                    "campo": "expediente",
                    "valor": exp,
                    "motivo": f"año {anio} parece muy antiguo",
                })

        actor_raw = str(row.get(col_actor, "")) if col_actor else ""
        reservado = es_actor_reservado(actor_raw)
        desconocido = (not reservado) and (not actor_raw.strip())

        if actor_raw.strip() and not reservado and not _ACTOR_RAREZA_RE.search(actor_raw):
            res.warnings.append({
                "fila": fila_origen,
                "campo": "actor",
                "valor": actor_raw,
                "motivo": "celda actor con caracteres extraños (solo símbolos)",
            })

        juzgado = normalizar_juzgado(str(row.get(col_juz, ""))) if col_juz else ""
        cliente = str(row.get(col_cli, "")) if col_cli else ""

        actor_norm = "" if (reservado or desconocido) else normalizar_nombre(actor_raw)

        clave_dup = (exp, cliente.strip().lower(), actor_norm.lower())
        if clave_dup in vistos:
            res.warnings.append({
                "fila": fila_origen,
                "campo": "duplicado",
                "valor": exp,
                "motivo": f"fila duplicada de fila {vistos[clave_dup]}",
            })
        else:
            vistos[clave_dup] = fila_origen

        res.registros.append(RegistroCliente(
            expediente=exp,
            actor=actor_norm,
            actor_reservado=reservado,
            juzgado=juzgado,
            cliente=cliente,
            fila_origen=fila_origen,
            raw=row.to_dict(),
            actor_desconocido=desconocido,
        ))

    return res


def _df_a_registros(df: pd.DataFrame) -> list:
    """Compat shim: usa el constructor rico y devuelve solo la lista."""
    return _construir_resultado(df).registros


def _escribir_log(path_origen: str, res: ResultadoCargaListado) -> None:
    """Escribe un log por carga. NO falla si no puede escribir."""
    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "listado_validation.log"
        ts = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        logger = logging.getLogger("listado_validation")
        logger.setLevel(logging.INFO)
        # Limpia handlers previos para no duplicar y para escribir (no append-acumulado).
        for h in list(logger.handlers):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        # mode='w' => un archivo por carga (sobrescribe el anterior).
        fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(message)s"))
        logger.addHandler(fh)
        logger.info("carga timestamp=%s origen=%s", ts, path_origen)
        logger.info("columnas_detectadas=%s", res.columnas_detectadas)
        logger.info("columnas_faltantes=%s", res.columnas_faltantes)
        logger.info("registros=%d warnings=%d", len(res.registros), len(res.warnings))
        for w in res.warnings[:200]:  # corta a 200 para no llenar
            logger.info("warning %s", w)
        fh.close()
        logger.removeHandler(fh)
    except Exception:
        # silencioso por diseño
        return


def cargar_excel(path: str) -> list:
    df = pd.read_excel(path, dtype=str).fillna("")
    return _df_a_registros(df)


def cargar_csv(path: str) -> list:
    df = pd.read_csv(path, dtype=str).fillna("")
    return _df_a_registros(df)


def cargar_pdf(path: str) -> list:
    """Para PDF de listado: extrae texto y busca filas tabulares heurísticamente.
    El usuario puede necesitar revisar manualmente el resultado.
    """
    doc = fitz.open(path)
    filas = []
    for page in doc:
        for line in page.get_text("text").split("\n"):
            line = line.strip()
            if not line:
                continue
            exp = normalizar_expediente(line)
            if exp:
                filas.append({"expediente": exp, "linea_completa": line})
    doc.close()
    if not filas:
        raise ValueError(
            "No detecté expedientes en el PDF de listado. "
            "Recomiendo convertirlo a Excel/CSV para garantizar precisión."
        )
    df = pd.DataFrame(filas)
    df["actor"] = ""
    df["juzgado"] = ""
    df["cliente"] = ""
    return _df_a_registros(df)


def _cargar_df(path: str) -> pd.DataFrame:
    p = path.lower()
    if p.endswith(".xlsx") or p.endswith(".xls"):
        return pd.read_excel(path, dtype=str).fillna("")
    if p.endswith(".csv"):
        return pd.read_csv(path, dtype=str).fillna("")
    if p.endswith(".pdf"):
        doc = fitz.open(path)
        filas = []
        for page in doc:
            for line in page.get_text("text").split("\n"):
                line = line.strip()
                if not line:
                    continue
                exp = normalizar_expediente(line)
                if exp:
                    filas.append({"expediente": exp, "linea_completa": line})
        doc.close()
        if not filas:
            raise ValueError(
                "No detecté expedientes en el PDF de listado. "
                "Recomiendo convertirlo a Excel/CSV para garantizar precisión."
            )
        df = pd.DataFrame(filas)
        df["actor"] = ""
        df["juzgado"] = ""
        df["cliente"] = ""
        return df
    raise ValueError(f"Formato no soportado: {path}")


def cargar_listado(path: str) -> list:
    """API histórica: devuelve list[RegistroCliente]."""
    df = _cargar_df(path)
    res = _construir_resultado(df)
    _escribir_log(path, res)
    return res.registros


def cargar_listado_detallado(path: str) -> ResultadoCargaListado:
    """API nueva: devuelve ResultadoCargaListado con warnings y metadatos."""
    df = _cargar_df(path)
    res = _construir_resultado(df)
    _escribir_log(path, res)
    return res

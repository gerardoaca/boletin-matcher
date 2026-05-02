"""Carga de listados de clientes desde Excel, CSV o PDF."""
import pandas as pd
import fitz
from dataclasses import dataclass
from .normalizer import (
    normalizar_expediente,
    normalizar_nombre,
    normalizar_juzgado,
    es_actor_reservado,
)


@dataclass
class RegistroCliente:
    expediente: str           # canónico
    actor: str                # normalizado, vacío si reservado
    actor_reservado: bool
    juzgado: str              # normalizado
    cliente: str              # nombre del cliente al que se asigna
    fila_origen: int          # para auditoría
    raw: dict                 # registro original sin tocar


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


def _detectar_columna(columnas_df: list[str], candidatos: list[str]) -> str | None:
    cols_norm = {c: c.lower().strip() for c in columnas_df}
    for col, norm in cols_norm.items():
        if norm in candidatos:
            return col
    for col, norm in cols_norm.items():
        for cand in candidatos:
            if cand in norm:
                return col
    return None


def _df_a_registros(df: pd.DataFrame) -> list[RegistroCliente]:
    cols = list(df.columns)
    col_exp = _detectar_columna(cols, COLUMNAS_ESPERADAS["expediente"])
    col_actor = _detectar_columna(cols, COLUMNAS_ESPERADAS["actor"])
    col_juz = _detectar_columna(cols, COLUMNAS_ESPERADAS["juzgado"])
    col_cli = _detectar_columna(cols, COLUMNAS_ESPERADAS["cliente"])

    if col_exp is None:
        raise ValueError(
            f"No encontré la columna de expediente. Columnas disponibles: {cols}"
        )

    registros = []
    for idx, row in df.iterrows():
        exp_raw = row.get(col_exp, "")
        exp = normalizar_expediente(str(exp_raw))
        if not exp:
            continue
        actor_raw = str(row.get(col_actor, "")) if col_actor else ""
        reservado = es_actor_reservado(actor_raw)
        juzgado = normalizar_juzgado(str(row.get(col_juz, ""))) if col_juz else ""
        cliente = str(row.get(col_cli, "")) if col_cli else ""
        registros.append(RegistroCliente(
            expediente=exp,
            actor="" if reservado else normalizar_nombre(actor_raw),
            actor_reservado=reservado,
            juzgado=juzgado,
            cliente=cliente,
            fila_origen=int(idx) + 2,  # +2 por header y 1-indexado
            raw=row.to_dict(),
        ))
    return registros


def cargar_excel(path: str) -> list[RegistroCliente]:
    df = pd.read_excel(path, dtype=str).fillna("")
    return _df_a_registros(df)


def cargar_csv(path: str) -> list[RegistroCliente]:
    df = pd.read_csv(path, dtype=str).fillna("")
    return _df_a_registros(df)


def cargar_pdf(path: str) -> list[RegistroCliente]:
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


def cargar_listado(path: str) -> list[RegistroCliente]:
    p = path.lower()
    if p.endswith(".xlsx") or p.endswith(".xls"):
        return cargar_excel(path)
    if p.endswith(".csv"):
        return cargar_csv(path)
    if p.endswith(".pdf"):
        return cargar_pdf(path)
    raise ValueError(f"Formato no soportado: {path}")

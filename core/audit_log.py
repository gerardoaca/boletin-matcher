"""Audit log persistente para Boletin Matcher.

Cada run de la app anexa una línea JSON a logs/audit.jsonl con metadatos
trazables (timestamp UTC, hashes de entrada, conteos, costo, git SHA).

Diseñado fail-safe: el audit log JAMÁS debe romper el flujo principal.
"""
from pathlib import Path
import json
import hashlib
from datetime import datetime, timezone
import subprocess

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_FILE = LOG_DIR / "audit.jsonl"


def _git_sha() -> str:
    """Devuelve el SHA corto del HEAD; '' si no es git o git no está."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent.parent,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode().strip()
    except Exception:
        return ""


def _hash_archivo(path: Path) -> str:
    """SHA1 corto del archivo, '' si falla."""
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    except Exception:
        return ""


def registrar_run(
    boletin_nombre: str,
    boletin_path: Path | None,
    listado_path: Path | None,
    validadas: int,
    revision: int,
    errores_ia: int = 0,
    costo_usd: float | None = None,
    duracion_s: float | None = None,
    metadata: dict | None = None,
) -> None:
    """Anexa una línea JSON al log de auditoría. No falla nunca."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        registro = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "boletin": boletin_nombre,
            "boletin_sha1": _hash_archivo(boletin_path) if boletin_path else "",
            "listado_sha1": _hash_archivo(listado_path) if listado_path else "",
            "validadas": validadas,
            "revision": revision,
            "errores_ia": errores_ia,
            "costo_usd": round(costo_usd, 5) if costo_usd is not None else None,
            "duracion_s": round(duracion_s, 2) if duracion_s is not None else None,
            "git_sha": _git_sha(),
            "metadata": metadata or {},
        }
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except Exception:
        # Log de auditoría JAMÁS debe romper el flujo principal de la app
        pass


def leer_runs(n: int = 50) -> list[dict]:
    """Devuelve los últimos N runs del log; [] si no hay log."""
    if not LOG_FILE.exists():
        return []
    try:
        with LOG_FILE.open(encoding="utf-8") as f:
            lines = f.readlines()
        runs = []
        for ln in lines[-n:]:
            try:
                runs.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return runs
    except Exception:
        return []

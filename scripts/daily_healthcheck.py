"""Health check diario de Boletin Matcher.

Ejecuta una serie de verificaciones (URL pública, API key, tests,
smoke-test end-to-end) y genera un reporte markdown.

Uso:
    python3 scripts/daily_healthcheck.py [--verbose] [--no-ia]

Exit codes:
    0 = todo OK (o solo fallas no-críticas)
    1 = problemas críticos detectados
    2 = error fatal (script no pudo correr)

Output:
    - stdout: 1 línea por check + verdict final
    - logs/healthcheck/YYYY-MM-DD.md : reporte detallado
"""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

# ── Configuración ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
URL_BASE = "https://gerardoaca-boletin-matcher-app-jf7g3l.streamlit.app"
URL_HEALTH = f"{URL_BASE}/_stcore/health"
LISTADO_CSV = ROOT / "input" / "listados" / "listado_clientes.csv"
BOLETINES_DIR = ROOT / "input" / "boletines"
LOGS_DIR = ROOT / "logs" / "healthcheck"
OUTPUT_DIR = ROOT / "output"
MIN_FREE_MB = 500
SMOKE_VALIDADAS_ESPERADAS = 6
PYTEST_TIMEOUT_S = 15 * 60

CRITICAL_CHECKS = {1, 3, 5, 6}  # si fallan → exit 1


# ── Modelo ───────────────────────────────────────────────────────────────────
@dataclass
class CheckResult:
    n: int
    name: str
    ok: bool
    detail: str = ""
    duracion_s: float = 0.0
    error_tb: str = ""
    skipped: bool = False

    @property
    def emoji(self) -> str:
        if self.skipped:
            return "⏭️"
        return "✅" if self.ok else "❌"


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)
    metricas: dict = field(default_factory=dict)
    started: _dt.datetime = field(default_factory=lambda: _dt.datetime.now())


# ── Helpers ──────────────────────────────────────────────────────────────────
def _load_env_file(env_path: Path) -> None:
    """Carga manual de .env (sin requerir python-dotenv)."""
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


def _print_check(r: CheckResult, total: int) -> None:
    name = r.name
    pad = name.ljust(38, ".")
    print(f"[{r.n}/{total}] {pad} {r.emoji}  {r.detail}".rstrip(), flush=True)


def _run_check(n: int, name: str, fn) -> CheckResult:
    t0 = time.time()
    try:
        ok, detail = fn()
        return CheckResult(n=n, name=name, ok=ok, detail=detail, duracion_s=time.time() - t0)
    except Exception as e:
        tb = traceback.format_exc()
        return CheckResult(
            n=n, name=name, ok=False,
            detail=f"excepción: {type(e).__name__}: {e}",
            duracion_s=time.time() - t0, error_tb=tb,
        )


# ── Checks ───────────────────────────────────────────────────────────────────
def check_url_publica() -> tuple[bool, str]:
    t0 = time.time()
    # urllib sigue redirects 301/302/307 pero NO 303 — usamos un handler permisivo.
    class _Redirector(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return urllib.request.Request(
                newurl, headers={"User-Agent": "boletin-healthcheck/1.0"}
            )

    opener = urllib.request.build_opener(_Redirector())
    try:
        req = urllib.request.Request(URL_HEALTH, headers={"User-Agent": "boletin-healthcheck/1.0"})
        with opener.open(req, timeout=30) as resp:
            code = resp.status
            elapsed = time.time() - t0
            if 200 <= code < 400:
                return True, f"HTTP {code} en {elapsed:.1f}s"
            return False, f"HTTP {code} en {elapsed:.1f}s"
    except urllib.error.HTTPError as e:
        # 2xx/3xx llegan acá solo si redirect loop; los tratamos como vivo si <400
        if 200 <= e.code < 400:
            return True, f"HTTP {e.code} (redirect) en {time.time()-t0:.1f}s"
        return False, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_login_html() -> tuple[bool, str]:
    class _Redirector(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return urllib.request.Request(
                newurl, headers={"User-Agent": "boletin-healthcheck/1.0"}
            )
    opener = urllib.request.build_opener(_Redirector())
    try:
        req = urllib.request.Request(URL_BASE, headers={"User-Agent": "boletin-healthcheck/1.0"})
        try:
            resp = opener.open(req, timeout=30)
            body = resp.read(20_000).decode("utf-8", errors="ignore").lower()
        except urllib.error.HTTPError as e:
            # 3xx loop con cuerpo: leemos lo que mandó el server
            if 200 <= e.code < 400 and hasattr(e, "read"):
                body = e.read(20_000).decode("utf-8", errors="ignore").lower()
            else:
                raise
        if any(m in body for m in ("acceso restringido", "streamlit", "<title")):
            marker = "acceso restringido" if "acceso restringido" in body else "streamlit/html"
            return True, f"'{marker}' presente"
        return False, "no se reconocieron marcadores de la app"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_api_key() -> tuple[bool, str]:
    _load_env_file(ROOT / ".env")
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return False, "ANTHROPIC_API_KEY no configurada"
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError:
        return False, "anthropic SDK no instalado"
    t0 = time.time()
    try:
        client = Anthropic(api_key=key)
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=5,
            messages=[{"role": "user", "content": "ping"}],
        )
        elapsed = time.time() - t0
        # respuesta minimal
        text = ""
        for b in getattr(resp, "content", []) or []:
            if getattr(b, "type", "") == "text":
                text = (getattr(b, "text", "") or "").strip()
                break
        return True, f"respuesta '{text[:20]}' en {elapsed:.1f}s"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_sintaxis_python() -> tuple[bool, str]:
    skip_dirs = {".venv", "venv", "__pycache__", ".git", "node_modules", "output", ".pytest_cache"}
    errores: list[str] = []
    total = 0
    for path in ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in path.relative_to(ROOT).parts):
            continue
        total += 1
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errores.append(f"{path.relative_to(ROOT)}:{e.lineno}: {e.msg}")
        except Exception as e:
            errores.append(f"{path.relative_to(ROOT)}: {type(e).__name__}: {e}")
    if errores:
        return False, f"{len(errores)} archivo(s) con error: " + "; ".join(errores[:3])
    return True, f"{total} archivos OK"


def check_pytest() -> tuple[bool, str]:
    python_exe = sys.executable
    try:
        proc = subprocess.run(
            [python_exe, "-m", "pytest", "tests/", "-q", "--tb=line", "--no-header"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout {PYTEST_TIMEOUT_S}s"
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # Parse última línea tipo "53 passed in 0.34s" o "1 failed, 52 passed"
    summary = ""
    for line in reversed(out.strip().splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            summary = line.strip()
            break
    if proc.returncode == 0:
        return True, summary or "OK"
    # Captura nombres de tests rotos
    fallidos = [
        l.strip() for l in out.splitlines()
        if l.strip().startswith("FAILED ") or l.strip().startswith("ERROR ")
    ]
    detalle = summary or f"exit {proc.returncode}"
    if fallidos:
        detalle += " | " + "; ".join(fallidos[:3])
    return False, detalle


def check_smoke_end_to_end() -> tuple[bool, str]:
    if not LISTADO_CSV.exists():
        return False, f"listado no encontrado: {LISTADO_CSV.name}"
    boletines = sorted(BOLETINES_DIR.glob("*.pdf"))
    if not boletines:
        return False, "no hay PDFs de boletín en input/boletines/"

    sys.path.insert(0, str(ROOT))
    try:
        from core.listado_loader import cargar_listado
        from core.matcher import buscar_coincidencias
        from core.extractor import extraer_hojas
    except Exception as e:
        return False, f"import falló: {type(e).__name__}: {e}"

    try:
        listado = cargar_listado(str(LISTADO_CSV))
    except Exception as e:
        return False, f"cargar_listado falló: {type(e).__name__}: {e}"

    total_validadas = 0
    errores_pdf: list[str] = []
    for pdf in boletines:
        try:
            hojas = extraer_hojas(str(pdf))
            validadas, _revision = buscar_coincidencias(hojas, listado)
            total_validadas += len(validadas)
        except Exception as e:
            errores_pdf.append(f"{pdf.name}: {type(e).__name__}")

    detalle = f"{total_validadas}/{SMOKE_VALIDADAS_ESPERADAS} validadas ({len(boletines)} PDFs)"
    if errores_pdf:
        detalle += " | errores: " + "; ".join(errores_pdf[:2])
    ok = total_validadas == SMOKE_VALIDADAS_ESPERADAS and not errores_pdf
    return ok, detalle


def check_ocr_disponible() -> tuple[bool, str]:
    ocr = shutil.which("ocrmypdf")
    tess = shutil.which("tesseract")
    if not ocr:
        return False, "ocrmypdf no encontrado en PATH"
    if not tess:
        return False, "tesseract no encontrado en PATH"
    # versión de ocrmypdf (best effort)
    ver = ""
    try:
        out = subprocess.run([ocr, "--version"], capture_output=True, text=True, timeout=10)
        ver = (out.stdout or "").strip().splitlines()[0] if out.stdout else ""
    except Exception:
        pass
    return True, f"ocrmypdf {ver or 'OK'}, tesseract OK"


def check_disk_space() -> tuple[bool, str]:
    target = OUTPUT_DIR if OUTPUT_DIR.exists() else ROOT
    try:
        usage = shutil.disk_usage(str(target))
        free_mb = usage.free / (1024 * 1024)
        free_gb = free_mb / 1024
        if free_mb < MIN_FREE_MB:
            return False, f"solo {free_mb:.0f} MB libres (mínimo {MIN_FREE_MB} MB)"
        return True, f"{free_gb:.1f} GB libres"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_git_limpio() -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return False, f"git error: {e}"
    if proc.returncode != 0:
        return False, f"git status exit {proc.returncode}"
    cambios = [l for l in (proc.stdout or "").splitlines() if l.strip()]
    if cambios:
        return False, f"{len(cambios)} archivo(s) con cambios"
    return True, "sin cambios"


def check_head_vs_remoto() -> tuple[bool, str]:
    try:
        subprocess.run(
            ["git", "fetch", "--quiet"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )
        proc = subprocess.run(
            ["git", "status", "-sb"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return False, f"git error: {e}"
    line = (proc.stdout or "").splitlines()[:1]
    txt = line[0] if line else ""
    if "behind" in txt:
        return False, f"atrasado vs remoto: {txt}"
    if "ahead" in txt:
        return True, f"adelantado vs remoto: {txt}"  # no es problema crítico
    return True, "sincronizado"


# ── Métricas (best effort) ───────────────────────────────────────────────────
def recoger_metricas() -> dict:
    m = {"runs_30d": None, "ultima_validada": None, "costo_30d_usd": None}
    audit = ROOT / "logs" / "audit.jsonl"
    if not audit.exists():
        return m
    try:
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
        runs = 0
        costo = 0.0
        ultima = None
        for line in audit.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            ts_raw = entry.get("ts_utc")
            try:
                ts = _dt.datetime.fromisoformat(ts_raw.replace("Z", "+00:00")) if ts_raw else None
            except Exception:
                ts = None
            if ts and ts >= cutoff:
                runs += 1
                c = entry.get("costo_usd")
                if isinstance(c, (int, float)):
                    costo += float(c)
            if entry.get("validadas", 0) and ts:
                if ultima is None or ts > ultima:
                    ultima = ts
        m["runs_30d"] = runs
        m["costo_30d_usd"] = round(costo, 4)
        m["ultima_validada"] = ultima.isoformat() if ultima else None
    except Exception:
        pass
    return m


# ── Reporte ──────────────────────────────────────────────────────────────────
def escribir_reporte(report: Report, verdict: str, exit_code: int) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fecha = report.started.strftime("%Y-%m-%d")
    hora = report.started.strftime("%H:%M %Z").strip() or report.started.strftime("%H:%M")
    out = LOGS_DIR / f"{fecha}.md"

    lines: list[str] = []
    lines.append(f"# Health check Boletin Matcher — {fecha}")
    lines.append("")
    lines.append(f"**Hora**: {hora}")
    lines.append(f"**Verdict**: {verdict}")
    lines.append(f"**Exit code**: {exit_code}")
    lines.append("")
    lines.append("| # | Check | Estado | Detalle | Duración |")
    lines.append("|---|---|---|---|---|")
    for r in report.results:
        detalle = r.detail.replace("|", "\\|")
        lines.append(f"| {r.n} | {r.name} | {r.emoji} | {detalle} | {r.duracion_s:.2f}s |")
    lines.append("")

    fallas = [r for r in report.results if not r.ok and not r.skipped]
    lines.append("## Detalles de fallas")
    lines.append("")
    if not fallas:
        lines.append("_(vacío — todo OK)_")
    else:
        for r in fallas:
            critico = " (CRÍTICO)" if r.n in CRITICAL_CHECKS else ""
            lines.append(f"### [{r.n}] {r.name}{critico}")
            lines.append("")
            lines.append(f"- Detalle: {r.detail}")
            if r.error_tb:
                lines.append("```")
                lines.append(r.error_tb.strip())
                lines.append("```")
            lines.append("")
    lines.append("## Métricas")
    lines.append("")
    m = report.metricas
    runs = m.get("runs_30d")
    ult = m.get("ultima_validada")
    costo = m.get("costo_30d_usd")
    lines.append(f"- Runs de la app en últimos 30 días: {runs if runs is not None else 'n/d (sin audit log)'}")
    lines.append(f"- Última coincidencia validada: {ult if ult else 'n/d'}")
    lines.append(f"- Costo IA acumulado últimos 30 días: {f'${costo:.4f} USD' if costo is not None else 'n/d'}")
    lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Health check diario de Boletin Matcher")
    parser.add_argument("--verbose", action="store_true", help="logging extendido a stdout")
    parser.add_argument("--no-ia", action="store_true", help="omite el check 3 (llamada a Claude)")
    args = parser.parse_args()

    report = Report()
    total = 10

    checks = [
        (1, "URL pública responde", check_url_publica),
        (2, "App carga login", check_login_html),
        (3, "ANTHROPIC_API_KEY válida", check_api_key),
        (4, "Sintaxis Python", check_sintaxis_python),
        (5, "pytest tests/", check_pytest),
        (6, "Smoke test end-to-end", check_smoke_end_to_end),
        (7, "OCR disponible", check_ocr_disponible),
        (8, "Espacio disco", check_disk_space),
        (9, "Git limpio", check_git_limpio),
        (10, "HEAD vs remoto", check_head_vs_remoto),
    ]

    for n, name, fn in checks:
        if n == 3 and args.no_ia:
            r = CheckResult(n=n, name=name, ok=True, detail="omitido (--no-ia)", skipped=True)
        else:
            r = _run_check(n, name, fn)
        report.results.append(r)
        _print_check(r, total)
        if args.verbose and r.error_tb:
            print(r.error_tb, file=sys.stderr)

    report.metricas = recoger_metricas()

    fallidos_criticos = [
        r for r in report.results
        if not r.ok and not r.skipped and r.n in CRITICAL_CHECKS
    ]
    fallidos_no_crit = [
        r for r in report.results
        if not r.ok and not r.skipped and r.n not in CRITICAL_CHECKS
    ]

    if fallidos_criticos:
        verdict = f"❌ {len(fallidos_criticos)} CRÍTICO(S), {len(fallidos_no_crit)} warning(s)"
        exit_code = 1
    elif fallidos_no_crit:
        verdict = f"⚠️ {len(fallidos_no_crit)} warning(s) no-crítico(s)"
        exit_code = 0
    else:
        verdict = "✅ TODO OK"
        exit_code = 0

    try:
        out_path = escribir_reporte(report, verdict, exit_code)
        print(f"VERDICT: {verdict}")
        print(f"Reporte: {out_path}")
    except Exception as e:
        print(f"VERDICT: {verdict}")
        print(f"WARN: no se pudo escribir reporte: {e}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(2)

"""Bridge to the `etal` binary: validation, tree materialization,
subprocess invocation, and diagnostic parsing.

The compiler is the source of truth for ETAL semantics; this module
only transports bytes and translates its
`etal: error: file:line:col: msg` stderr into structured diagnostics.

Provisioning: ETAL_BIN (dev checkout) wins; otherwise $COMPILER_DIR/etal
(release fetch target); otherwise `ensure_compiler()` fetches the pinned
release when ETAL_SOURCE=release:<tag> (see scripts/fetch-compiler.sh).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings

# Frontend-facing ids -> etal --target rows. Only rows served today;
# everything else the compiler knows is reported as coming-soon.
TARGET_ROWS: dict[str, str] = {
    "linux": "linux-x86_64",
    "web": "web",
}
COMING_SOON_ROWS = ("macos-arm64", "macos-x86_64", "linux-aarch64", "windows-x86_64")

ALLOWED_EXTENSIONS = (".ux", ".chr", ".icn", ".wav")

_ERROR_RE = re.compile(r"^etal: error: (.*?):(\d+):(\d+):\s*(.*)$")


@dataclass
class ValidationError:
    key: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass
class CompileResult:
    ok: bool
    artifacts: dict[str, bytes] = field(default_factory=dict)
    diagnostics: list[dict[str, object]] = field(default_factory=list)
    stderr: str = ""


def validate_files(files: dict[str, str], entry: str) -> ValidationError | None:
    if not files:
        return ValidationError("error.empty_files")
    if len(files) > settings.max_files:
        return ValidationError("error.too_many_files", {"n": len(files), "max": settings.max_files})
    total = 0
    for path, content in files.items():
        if (
            not path
            or path.startswith("/")
            or ".." in Path(path).parts
            or len(path) > settings.max_path_chars
        ):
            return ValidationError("error.bad_path", {"path": path, "max": settings.max_path_chars})
        if Path(path).suffix.lower() not in ALLOWED_EXTENSIONS:
            return ValidationError(
                "error.bad_extension", {"path": path, "allowed": ", ".join(ALLOWED_EXTENSIONS)}
            )
        size = len(content.encode("utf-8"))
        if size > settings.max_file_bytes:
            return ValidationError("error.file_too_large", {"path": path, "max": settings.max_file_bytes})
        total += size
    if total > settings.max_total_bytes:
        return ValidationError("error.total_too_large", {"max": settings.max_total_bytes})
    if entry not in files:
        return ValidationError("error.bad_entry", {"entry": entry})
    return None


def files_sha(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode())
        h.update(b"\x00")
        h.update(files[path].encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


BACKEND_DIR = Path(__file__).resolve().parent.parent
COMPILER_DIR = Path(os.environ.get("COMPILER_DIR", "/opt/etal"))
FETCH_SCRIPT = BACKEND_DIR / "scripts" / "fetch-compiler.sh"


def _usable(path: str) -> bool:
    p = Path(path)
    return p.is_file() and os.access(p, os.X_OK)


def etal_bin() -> str:
    candidates = [settings.etal_bin, str(Path(COMPILER_DIR) / "etal")]
    found = shutil.which(settings.etal_bin)
    if found:
        candidates.insert(0, found)
    for candidate in candidates:
        if candidate and _usable(candidate):
            return candidate
    raise FileNotFoundError(settings.etal_bin)


def ensure_compiler() -> str:
    """Return a working etal path, fetching the pinned release when the
    checkout is absent and ETAL_SOURCE=release:<tag>. Raises
    FileNotFoundError with guidance otherwise."""
    try:
        return etal_bin()
    except FileNotFoundError:
        pass
    source = os.environ.get("ETAL_SOURCE", "local")
    if source.startswith("release:") and FETCH_SCRIPT.is_file():
        proc = subprocess.run(
            [str(FETCH_SCRIPT), source],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode == 0:
            return etal_bin()
        raise FileNotFoundError(f"compiler fetch failed: {proc.stderr.strip()}")
    raise FileNotFoundError(
        f"no usable compiler (ETAL_BIN={settings.etal_bin}, "
        f"{COMPILER_DIR}/etal missing, ETAL_SOURCE={source})"
    )


def query_rows() -> dict[str, dict[str, object]]:
    """Parse `etal --list-targets` into {row: {kind, vendored}}.

    Returns {} when the binary is unavailable; callers degrade to
    static knowledge instead of failing.
    """
    try:
        proc = subprocess.run(
            [etal_bin(), "--list-targets"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    rows: dict[str, dict[str, object]] = {}
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] == "native":
            continue
        rows[parts[0]] = {"kind": parts[1], "vendored": parts[2] == "yes"}
    return rows


def parse_diagnostics(stderr: str) -> list[dict[str, object]]:
    diags: list[dict[str, object]] = []
    for line in stderr.splitlines():
        m = _ERROR_RE.match(line.strip())
        if m:
            diags.append(
                {
                    "file": m.group(1) or None,
                    "line": int(m.group(2)),
                    "col": int(m.group(3)),
                    "msg": m.group(4),
                }
            )
    return diags


def _artifact_name(target: str, mode: str) -> str:
    if mode == "tal":
        return "game.tal"
    if mode == "rom":
        return "game.rom"
    if target == "web":
        return "game.html"
    if TARGET_ROWS.get(target, "").startswith("windows"):
        return "game.zip"
    return "game"


def run_compile(target: str, mode: str, entry: str, files: dict[str, str]) -> CompileResult:
    """Materialize the tree, invoke etal, collect outputs.

    Raises FileNotFoundError (no compiler) or TimeoutExpired.
    """
    binary = etal_bin()
    row = TARGET_ROWS[target]
    out_name = _artifact_name(target, mode)
    with tempfile.TemporaryDirectory(prefix="uxnweb-") as tmp:
        tmpdir = Path(tmp)
        for path, content in files.items():
            dest = tmpdir / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        cmd = [binary]
        if mode == "tal":
            cmd.append("-t")
        elif mode == "rom":
            cmd.append("-r")
        else:
            cmd += ["--target", row]
        cmd += [entry, "-o", out_name]
        proc = subprocess.run(
            cmd,
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=settings.compile_timeout,
        )
        if proc.returncode != 0:
            return CompileResult(
                ok=False,
                diagnostics=parse_diagnostics(proc.stderr),
                stderr=proc.stderr,
            )
        data = (tmpdir / out_name).read_bytes()
        if mode == "tal":
            key = "tal"
        elif mode == "rom":
            key = "rom"
        else:
            key = "html" if target == "web" else "bundle"
        return CompileResult(ok=True, artifacts={key: data})

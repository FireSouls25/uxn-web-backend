"""Bridge to the `etal` binary: validation, tree materialization,
subprocess invocation, and diagnostic parsing.

The compiler is the source of truth for ETAL semantics; this module
only transports bytes and translates its
`etal: error: file:line:col: msg` stderr into structured diagnostics.
"""
from __future__ import annotations

import hashlib
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


def etal_bin() -> str:
    found = shutil.which(settings.etal_bin) or (
        settings.etal_bin if Path(settings.etal_bin).exists() else None
    )
    if not found:
        raise FileNotFoundError(settings.etal_bin)
    return found


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

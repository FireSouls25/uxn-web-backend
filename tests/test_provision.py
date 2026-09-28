"""Compiler provisioning: local resolution, $COMPILER_DIR fallback,
fetch-script validation. (The real release download is proven in ops,
not unit-tested — no network in CI.)"""
from __future__ import annotations

import os
import stat
import subprocess

import pytest  # noqa: E402

from app import compiler  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ETAL_SOURCE", raising=False)
    yield


def _stub_etal(path: str) -> None:
    with open(path, "w") as f:
        f.write("#!/bin/sh\necho stub-etal\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_etal_bin_prefers_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etal_bin", "/nonexistent-etal")
    monkeypatch.setenv("COMPILER_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        compiler.etal_bin()


def test_etal_bin_falls_back_to_compiler_dir(tmp_path, monkeypatch):
    stub = tmp_path / "etal"
    _stub_etal(str(stub))
    monkeypatch.setattr(settings, "etal_bin", "/nonexistent-etal")
    monkeypatch.setenv("COMPILER_DIR", str(tmp_path))
    # COMPILER_DIR is read at import; patch the module constant.
    monkeypatch.setattr(compiler, "COMPILER_DIR", tmp_path)
    assert compiler.etal_bin() == str(stub)


def test_ensure_reports_guidance(monkeypatch):
    monkeypatch.setattr(settings, "etal_bin", "/nonexistent-etal")
    monkeypatch.setattr(compiler, "COMPILER_DIR", "/nonexistent-dir")
    with pytest.raises(FileNotFoundError, match="ETAL_SOURCE"):
        compiler.ensure_compiler()


def test_fetch_script_rejects_unpinned_and_unknown(tmp_path):
    script = compiler.FETCH_SCRIPT
    assert script.is_file()
    bad = subprocess.run([str(script), "bogus"], capture_output=True, text=True)
    assert bad.returncode != 0
    empty = subprocess.run(
        [str(script), "release:v9.9.9"], capture_output=True, text=True,
        env={**os.environ, "COMPILER_DIR": str(tmp_path)},
    )
    assert empty.returncode != 0
    assert "no pinned hash" in empty.stderr

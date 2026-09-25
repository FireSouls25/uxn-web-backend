"""Compile service: receive a file tree, run etal, return artifacts."""
from __future__ import annotations

import base64
import subprocess
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy.orm import Session

from . import compiler
from .compiler import COMING_SOON_ROWS, TARGET_ROWS
from .config import settings
from .db import Base, engine, get_db
from .i18n import resolve_lang, t
from .models import CompileJob
from .schemas import (
    CompileRequest,
    CompileResponse,
    Diagnostic,
    JobResponse,
    TargetInfo,
    TargetsResponse,
    VersionResponse,
)

BACKEND_VERSION = "0.1.0"

app = FastAPI(title="uxn-webpage compile service", version=BACKEND_VERSION)
Base.metadata.create_all(bind=engine)


def _encode(artifacts: dict[str, bytes]) -> dict[str, str]:
    """`tal` travels as text; binary artifacts as base64 (`rom_b64`, …)."""
    out: dict[str, str] = {}
    for key, data in artifacts.items():
        if key == "tal":
            out[key] = data.decode("utf-8")
        else:
            out[f"{key}_b64"] = base64.b64encode(data).decode()
    return out


def _store(job: CompileJob, artifacts: dict[str, bytes]) -> None:
    if "tal" in artifacts:
        job.tal_text = artifacts["tal"].decode("utf-8")
    if "rom" in artifacts:
        job.rom_bin = artifacts["rom"]
    if "html" in artifacts:
        job.html_bin = artifacts["html"]
    if "bundle" in artifacts:
        job.bundle_bin = artifacts["bundle"]


def _job_artifacts(job: CompileJob) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if job.tal_text is not None:
        out["tal"] = job.tal_text.encode("utf-8")
    if job.rom_bin is not None:
        out["rom"] = bytes(job.rom_bin)
    if job.html_bin is not None:
        out["html"] = bytes(job.html_bin)
    if job.bundle_bin is not None:
        out["bundle"] = bytes(job.bundle_bin)
    return out


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/version", response_model=VersionResponse)
def version() -> VersionResponse:
    return VersionResponse(backend=BACKEND_VERSION, etal=settings.etal_version)


@app.get("/targets", response_model=TargetsResponse)
def targets() -> TargetsResponse:
    rows = compiler.query_rows()
    supported = [
        TargetInfo(
            id="linux",
            etal_target="linux-x86_64",
            kind="sh+tar.gz",
            vendored=rows.get("linux-x86_64", {}).get("vendored"),
        ),
        TargetInfo(
            id="web",
            etal_target="web",
            kind="uxn5 html",
            vendored=rows.get("web", {}).get("vendored"),
        ),
    ]
    coming_soon = [
        TargetInfo(
            id=row,
            etal_target=row,
            kind=str(rows.get(row, {}).get("kind", "native")),
            vendored=rows.get(row, {}).get("vendored"),
        )
        for row in COMING_SOON_ROWS
    ]
    return TargetsResponse(supported=supported, coming_soon=coming_soon)


@app.post("/compile", response_model=CompileResponse)
def compile_tree(
    body: CompileRequest,
    db: Session = Depends(get_db),
    accept_language: str | None = Header(default=None),
) -> CompileResponse:
    lang = resolve_lang(body.lang, accept_language)
    job_id = str(uuid.uuid4())

    if body.etal_version and body.etal_version != settings.etal_version:
        raise HTTPException(
            status_code=409,
            detail=t(
                "error.version_mismatch",
                lang,
                want=body.etal_version,
                have=settings.etal_version,
            ),
        )
    if body.target in TARGET_ROWS:
        pass
    elif body.target in COMING_SOON_ROWS or body.target in ("macos", "windows", "native"):
        raise HTTPException(
            status_code=400,
            detail=t("error.coming_soon", lang, target=body.target),
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=t(
                "error.unknown_target",
                lang,
                target=body.target,
                supported=", ".join(sorted(TARGET_ROWS)),
            ),
        )

    verr = compiler.validate_files(body.files, body.entry)
    if verr:
        raise HTTPException(status_code=400, detail=t(verr.key, lang, **verr.params))

    try:
        result = compiler.run_compile(body.target, body.mode, body.entry, body.files)
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=t("error.compiler_missing", lang, detail=settings.etal_bin),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=t("error.timeout", lang, seconds=settings.compile_timeout),
        )

    job = CompileJob(
        id=job_id,
        target=body.target,
        mode=body.mode,
        lang=lang,
        status="ok" if result.ok else "error",
        message=t("ok.compiled", lang) if result.ok else t("error.compile_failed", lang),
        diagnostics=result.diagnostics,
        files_sha=compiler.files_sha(body.files),
    )
    if result.ok:
        _store(job, result.artifacts)
    db.add(job)
    db.commit()

    return CompileResponse(
        status="ok" if result.ok else "error",
        job_id=job_id,
        message=job.message,
        diagnostics=[Diagnostic(**d) for d in result.diagnostics],
        artifacts=_encode(result.artifacts),
    )


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    accept_language: str | None = Header(default=None),
) -> JobResponse:
    lang = resolve_lang(None, accept_language)
    job = db.get(CompileJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=t("error.job_not_found", lang, job_id=job_id),
        )
    return JobResponse(
        status=job.status,  # type: ignore[arg-type]
        job_id=job.id,
        message=job.message,
        diagnostics=[Diagnostic(**d) for d in job.diagnostics],
        artifacts=_encode(_job_artifacts(job)),
        target=job.target,
        mode=job.mode,
    )

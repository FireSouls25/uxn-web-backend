"""Compile service: receive a file tree, run etal, return artifacts."""
from __future__ import annotations

import base64
import os
import re
import subprocess
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import auth, compiler
from . import limits
from .auth import TokenError
from .compiler import COMING_SOON_ROWS, TARGET_ROWS
from .config import settings
from .db import Base, engine, get_db
from .i18n import resolve_lang, t
from .limits import RateLimited, ServerBusy
from .models import CompileJob, RefreshToken, User
from .schemas import (
    CompileRequest,
    CompileResponse,
    Diagnostic,
    HealthResponse,
    JobResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TargetInfo,
    TargetsResponse,
    TokenPair,
    UserProfile,
    VersionResponse,
)

BACKEND_VERSION = "0.1.0"

compiler_state: dict[str, str] = {"status": "unknown", "path": ""}


async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    try:
        compiler_state["path"] = compiler.ensure_compiler()
        compiler_state["status"] = "ok"
    except FileNotFoundError as e:
        compiler_state["status"] = f"missing: {e}"
    yield


app = FastAPI(
    title="uxn-webpage compile service",
    version=BACKEND_VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
Base.metadata.create_all(bind=engine)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def get_api_keys() -> list[str]:
    # Read live so tests can inject keys without reimporting.
    return [k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()]


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _auth_attempt(request: Request, lang: str) -> None:
    """Strict per-IP budget for auth endpoints (brute-force brake)."""
    key = f"auth:{request.client.host if request.client else 'unknown'}"
    try:
        limits.check_rate_limit(key, limit=settings.auth_rate_per_minute)
    except RateLimited as e:
        raise HTTPException(
            status_code=429,
            detail=t("error.rate_limited", lang, seconds=e.retry_after),
            headers={"Retry-After": str(e.retry_after)},
        )


def _issue_pair(db: Session, user: User) -> TokenPair:
    access, expires_in = auth.create_access_token(user.id)
    refresh, _jti, expires_at = auth.create_refresh_token(user.id)
    db.add(
        RefreshToken(
            id=str(uuid.uuid4()),
            user_id=user.id,
            fingerprint=auth.refresh_fingerprint(refresh),
            expires_at=expires_at,
        )
    )
    db.commit()
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in)


async def authorize(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    accept_language: str | None = Header(default=None),
) -> User | None:
    """User bearer token preferred; service API key accepted; open dev
    mode when neither is configured. Returns the user, or None for
    service/open callers."""
    lang = resolve_lang(None, accept_language)
    if authorization and authorization.lower().startswith("bearer "):
        try:
            payload = auth.decode_token(authorization[7:].strip(), "access")
        except TokenError:
            raise HTTPException(status_code=401, detail=t("error.invalid_token", lang))
        user = db.get(User, payload["sub"])
        if user is None:
            raise HTTPException(status_code=401, detail=t("error.invalid_token", lang))
        return user
    keys = get_api_keys()
    if x_api_key is not None and x_api_key in keys:
        return None
    if not keys:
        return None
    raise HTTPException(
        status_code=401,
        detail=t("error.not_authenticated", lang),
    )


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


def _job_response(job: CompileJob, cached: bool = False) -> JobResponse:
    return JobResponse(
        status=job.status,  # type: ignore[arg-type]
        job_id=job.id,
        message=job.message,
        diagnostics=[Diagnostic(**d) for d in job.diagnostics],
        cached=cached,
        artifacts=_encode(_job_artifacts(job)),
        target=job.target,
        mode=job.mode,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    if compiler_state["status"] == "unknown":
        try:
            compiler_state["path"] = compiler.ensure_compiler()
            compiler_state["status"] = "ok"
        except FileNotFoundError as e:
            compiler_state["status"] = f"missing: {e}"
    return HealthResponse(status="ok", compiler=compiler_state["status"])


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


@app.post("/auth/register", response_model=TokenPair, status_code=201)
def register(
    body: RegisterRequest, request: Request, db: Session = Depends(get_db)
) -> TokenPair:
    lang = resolve_lang(body.lang, request.headers.get("accept-language"))
    _auth_attempt(request, lang)
    name, email = body.name.strip(), body.email.strip().lower()
    if not (2 <= len(name) <= 64):
        raise HTTPException(status_code=400, detail=t("error.bad_name", lang))
    if not EMAIL_RE.match(email) or len(email) > 320:
        raise HTTPException(status_code=400, detail=t("error.invalid_email", lang))
    if not (8 <= len(body.password) <= 72):
        raise HTTPException(status_code=400, detail=t("error.weak_password", lang))
    if db.query(User).filter_by(email=email).first() is not None:
        raise HTTPException(status_code=409, detail=t("error.email_taken", lang))
    user = User(
        id=str(uuid.uuid4()), email=email, name=name,
        password_hash=auth.hash_password(body.password),
    )
    db.add(user)
    db.commit()
    return _issue_pair(db, user)


@app.post("/auth/login", response_model=TokenPair)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    lang = resolve_lang(body.lang, request.headers.get("accept-language"))
    _auth_attempt(request, lang)
    user = db.query(User).filter_by(email=body.email.strip().lower()).first()
    if user is None or not auth.verify_password(body.password, user.password_hash):
        # Same message either way: no account enumeration.
        raise HTTPException(status_code=401, detail=t("error.invalid_credentials", lang))
    return _issue_pair(db, user)


@app.post("/auth/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    lang = resolve_lang(None, request.headers.get("accept-language"))
    _auth_attempt(request, lang)
    try:
        payload = auth.decode_token(body.refresh_token, "refresh")
    except TokenError:
        raise HTTPException(status_code=401, detail=t("error.invalid_token", lang))
    row = db.query(RefreshToken).filter_by(
        fingerprint=auth.refresh_fingerprint(body.refresh_token)
    ).first()
    if row is None or row.user_id != payload["sub"]:
        raise HTTPException(status_code=401, detail=t("error.invalid_token", lang))
    user = db.get(User, row.user_id)
    db.delete(row)  # rotation: the presented token dies here
    if user is None:
        db.commit()
        raise HTTPException(status_code=401, detail=t("error.invalid_token", lang))
    return _issue_pair(db, user)


@app.post("/auth/logout")
def logout(body: RefreshRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    row = db.query(RefreshToken).filter_by(
        fingerprint=auth.refresh_fingerprint(body.refresh_token)
    ).first()
    if row is not None:
        db.delete(row)
        db.commit()
    return {"status": "ok"}


@app.get("/auth/me", response_model=UserProfile)
def me(user: User | None = Depends(authorize)) -> UserProfile:
    if user is None:
        raise HTTPException(status_code=401, detail=t("error.not_authenticated", "en"))
    return UserProfile(id=user.id, name=user.name, email=user.email)


@app.post("/compile", response_model=CompileResponse)
def compile_tree(
    body: CompileRequest,
    request: Request,
    db: Session = Depends(get_db),
    _caller: User | None = Depends(authorize),
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

    rate_key = _caller.id if _caller else (request.client.host if request.client else "unknown")
    try:
        limits.check_rate_limit(rate_key)
    except RateLimited as e:
        raise HTTPException(
            status_code=429,
            detail=t("error.rate_limited", lang, seconds=e.retry_after),
            headers={"Retry-After": str(e.retry_after)},
        )

    sha = compiler.files_sha(body.files)
    hit = (
        db.query(CompileJob)
        .filter_by(
            status="ok",
            files_sha=sha,
            target=body.target,
            mode=body.mode,
            etal_version=settings.etal_version,
        )
        .order_by(CompileJob.created_at.desc())
        .first()
    )
    if hit is not None:
        return _job_response(hit, cached=True)

    try:
        with limits.compile_slot():
            result = compiler.run_compile(body.target, body.mode, body.entry, body.files)
    except ServerBusy:
        raise HTTPException(status_code=503, detail=t("error.server_busy", lang))
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
        files_sha=sha,
        etal_version=settings.etal_version,
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
        cached=False,
        artifacts=_encode(result.artifacts),
    )


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    _caller: User | None = Depends(authorize),
    accept_language: str | None = Header(default=None),
) -> JobResponse:
    lang = resolve_lang(None, accept_language)
    job = db.get(CompileJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=t("error.job_not_found", lang, job_id=job_id),
        )
    return _job_response(job)

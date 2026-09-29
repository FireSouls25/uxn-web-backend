"""Compile service: receive a file tree, run etal, return artifacts."""
from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import auth, compiler
from . import limits
from . import llm
from . import llm_registry
from . import llm_relay
from .auth import TokenError
from .compiler import COMING_SOON_ROWS, TARGET_ROWS
from .config import settings
from .db import Base, engine, get_db
from .i18n import resolve_lang, t
from .limits import RateLimited, ServerBusy
from .models import CompileJob, RefreshToken, User
from .schemas import (
    Candidate,
    CompileRequest,
    CompileResponse,
    Diagnostic,
    HealthResponse,
    JobResponse,
    LoginRequest,
    ProviderConfig,
    ProviderStatus,
    RefreshRequest,
    RegisterRequest,
    RelayRequest,
    RelayResponse,
    RouteRequest,
    RouteResponse,
    TargetInfo,
    TargetsResponse,
    TokenPair,
    TurnRequest,
    TurnResponse,
    UsageRequest,
    UserProfile,
    VersionResponse,
)

BACKEND_VERSION = "0.1.0"
log = logging.getLogger("uxnweb")

compiler_state: dict[str, str] = {"status": "unknown", "path": ""}


def configure_logging() -> None:
    """Uvicorn configures its own loggers and leaves the root logger
    bare, so without this every `log.info` in the app — the routing
    decision above all — is dropped before it reaches anyone. Touch
    the root only when nothing else has claimed it (a real handler,
    pytest's caplog), and never uvicorn's own."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    # httpx narrates every upstream call at INFO, which doubles the
    # relay's own lines. Its warnings and errors still come through.
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    configure_logging()
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


async def lang_of(request: Request) -> str:
    return resolve_lang(None, request.headers.get("accept-language"))


async def authorize_optional(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    accept_language: str | None = Header(default=None),
) -> User | None:
    """Guests included: a missing token means anonymous, not rejected.
    A *present* token must still be valid — a stale session should not
    silently downgrade to guest. Anonymous callers are rate limited by
    IP instead, and an operator can lock guests out entirely."""
    if not (authorization and authorization.lower().startswith("bearer ")):
        return None
    lang = resolve_lang(None, accept_language)
    try:
        payload = auth.decode_token(authorization[7:].strip(), "access")
    except TokenError:
        raise HTTPException(status_code=401, detail=t("error.invalid_token", lang))
    user = db.get(User, payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail=t("error.invalid_token", lang))
    return user


def require_service_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    accept_language: str | None = Header(default=None),
) -> None:
    """Operator-only routes: a configured service key is mandatory.
    Unlike user auth, there is no open dev mode here — an unset
    API_KEYS is a 503 telling the operator to configure it."""
    lang = resolve_lang(None, accept_language)
    keys = get_api_keys()
    if not keys:
        raise HTTPException(status_code=503, detail=t("error.service_locked", lang))
    if x_api_key is None or x_api_key not in keys:
        raise HTTPException(status_code=401, detail=t("error.unauthorized", lang))


@app.get("/agent/providers", response_model=list[ProviderStatus])
def agent_providers(
    db: Session = Depends(get_db), _caller: User | None = Depends(authorize)
) -> list[ProviderStatus]:
    """Public catalog: names, models, tiers — never key material."""
    out = []
    for provider in llm_registry.providers():
        state = llm.get_state(db, provider["id"])
        enabled = state.enabled_override if state.enabled_override is not None else provider.get("enabled_default", True)
        out.append(
            ProviderStatus(
                id=provider["id"],
                enabled=bool(enabled),
                key_present=llm.key_present(provider),
                key_env=provider.get("key_env"),
                daily_token_budget=state.daily_token_budget,
                spent_today=llm.spent_today(db, provider["id"]),
                fails=state.fails or 0,
                cooldown_until=state.cooldown_until.isoformat() if state.cooldown_until else None,
                models=[m["id"] for m in llm_registry.models_of(provider)]
                or (["<any>"] if provider.get("dynamic") else []),
            )
        )
    return out


def _route_response(chosen: dict | None, ranked: dict) -> RouteResponse:
    return RouteResponse(
        chosen=Candidate(provider=chosen["provider"], model=chosen["model"], reason=chosen["reason"])
        if chosen
        else None,
        candidates=[Candidate(provider=c["provider"], model=c["model"], reason=c["reason"]) for c in ranked["candidates"]],
        excluded=[Candidate(provider=e["provider"], model=e.get("model"), reason=e["reason"]) for e in ranked["excluded"]],
    )


@app.post("/agent/route", response_model=RouteResponse)
def agent_route(
    body: RouteRequest, db: Session = Depends(get_db), _svc: None = Depends(require_service_key)
) -> RouteResponse:
    """The dry run of the exact decision `/agent/turn` makes: same
    `llm.plan`, so the preview and the turn cannot disagree. An
    optional pin is validated, never blind-trusted."""
    plan = llm.plan(db, body.capability)
    if not (body.provider or body.model):
        return _route_response(plan["chosen"], plan)
    provider = llm_registry.get_provider(body.provider or "")
    if provider is None:
        raise HTTPException(status_code=400, detail=f"unknown provider {body.provider}")
    models = [m for m in llm_registry.models_of(provider) if not body.model or m["id"] == body.model]
    if provider.get("dynamic") and body.model:
        models = [{"id": body.model, "tools": True, "free": False, "tier": 1, "quality": 2}]
    if not models:
        raise HTTPException(status_code=400, detail=f"unknown model {body.model}")
    pinned = [
        c
        for c in plan["candidates"]
        if c["provider"] == provider["id"] and (not body.model or c["model"] == body.model)
    ]
    if not pinned:
        reasons = [e["reason"] for e in plan["excluded"] if e["provider"] == provider["id"]]
        raise HTTPException(status_code=400, detail=f"pinned route ineligible: {'; '.join(reasons) or 'filtered out'}")
    chosen = dict(pinned[0])
    chosen["reason"] = "pinned: " + chosen["reason"]
    return _route_response(chosen, plan)


@app.post("/agent/usage")
def agent_usage(
    body: UsageRequest, db: Session = Depends(get_db), _svc: None = Depends(require_service_key)
) -> dict[str, str]:
    if llm_registry.get_provider(body.provider) is None:
        raise HTTPException(status_code=400, detail=f"unknown provider {body.provider}")
    llm.record_usage(db, body.provider, body.model, body.in_tokens, body.out_tokens)
    llm.report_result(db, body.provider, body.ok)
    return {"status": "ok"}


@app.post("/agent/turn", response_model=TurnResponse)
def agent_turn(
    body: TurnRequest,
    request: Request,
    db: Session = Depends(get_db),
    caller: User | None = Depends(authorize_optional),
) -> TurnResponse:
    """The browser's only LLM door. It names no provider: routing,
    keys, budgets and the breaker all stay here, and the caller learns
    nothing beyond whether a model answered."""
    if caller is None and not settings.agent_allow_guests:
        raise HTTPException(status_code=401, detail=t("error.not_authenticated", lang_of(request)))
    identity = caller.id if caller else (request.client.host if request.client else "unknown")
    try:
        limits.check_rate_limit(f"turn:{identity}", settings.agent_turns_per_minute)
    except limits.RateLimited as e:
        raise HTTPException(
            status_code=429,
            detail="too many agent turns, slow down",
            headers={"Retry-After": str(e.retry_after)},
        )
    try:
        result = llm_relay.turn(
            db,
            [m.model_dump(exclude_none=True) for m in body.messages],
            [t.model_dump() for t in body.tools],
        )
    except llm_relay.RelayError as e:
        log.warning("agent turn failed for %s: %s", identity, e)
        raise HTTPException(status_code=502 if e.provider_down else 503, detail=str(e))
    return TurnResponse(**result)


@app.post("/agent/llm", response_model=RelayResponse)
def agent_llm(
    body: RelayRequest, db: Session = Depends(get_db), _caller: User | None = Depends(authorize)
) -> RelayResponse:
    """One LLM turn through the server-held key. The caller must be an
    eligible route — rechecked here so direct calls can't bypass
    budgets, kill switches, or cooldowns."""
    ranked = llm.rank_models(db, "tools")
    eligible = {(c["provider"], c["model"]) for c in ranked["candidates"]}
    provider = llm_registry.get_provider(body.provider)
    allowed = (body.provider, body.model) in eligible or (
        provider is not None
        and provider.get("dynamic")
        and any(c["provider"] == body.provider for c in ranked["candidates"])
    )
    if not allowed:
        reasons = [e["reason"] for e in ranked["excluded"] if e["provider"] == body.provider]
        raise HTTPException(
            status_code=400,
            detail=f"route ineligible: {'; '.join(reasons) or 'filtered out'}",
        )
    try:
        result = llm_relay.call(
            db,
            body.provider,
            body.model,
            [m.model_dump(exclude_none=True) for m in body.messages],
            [t.model_dump() for t in body.tools],
        )
    except llm_relay.RelayError as e:
        raise HTTPException(status_code=502 if e.provider_down else 400, detail=str(e))
    return RelayResponse(**result)


@app.get("/admin/llm/status", response_model=list[ProviderStatus])
def admin_llm_status(
    db: Session = Depends(get_db), _svc: None = Depends(require_service_key)
) -> list[ProviderStatus]:
    return agent_providers(db, None)


@app.post("/admin/llm/provider")
def admin_llm_provider(
    body: ProviderConfig, db: Session = Depends(get_db), _svc: None = Depends(require_service_key)
) -> dict[str, str]:
    if llm_registry.get_provider(body.provider) is None:
        raise HTTPException(status_code=400, detail=f"unknown provider {body.provider}")
    state = llm.get_state(db, body.provider)
    if body.enabled is not None:
        state.enabled_override = body.enabled
    if body.daily_token_budget is not None:
        if body.daily_token_budget < 0:
            raise HTTPException(status_code=400, detail="budget must be >= 0")
        state.daily_token_budget = body.daily_token_budget
    db.commit()
    return {"status": "ok"}


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

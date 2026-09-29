"""Request/response models for the compile API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Target = Literal["linux", "web"]
Mode = Literal["bundle", "tal", "rom"]
Lang = Literal["en", "es"]


class CompileRequest(BaseModel):
    target: str = Field(description="'linux' or 'web'; anything else is rejected or coming-soon")
    mode: Mode = "bundle"
    entry: str = "main.ux"
    files: dict[str, str] = Field(description="Relative path -> file content")
    etal_version: str | None = None
    lang: Lang = "en"


class Diagnostic(BaseModel):
    file: str | None = None
    line: int | None = None
    col: int | None = None
    msg: str


class CompileResponse(BaseModel):
    status: Literal["ok", "error"]
    job_id: str
    message: str
    diagnostics: list[Diagnostic] = []
    cached: bool = False
    artifacts: dict[str, str] = Field(
        default_factory=dict,
        description="tal (text) and/or rom_b64, html_b64, bundle_b64",
    )


class TargetInfo(BaseModel):
    id: str
    etal_target: str
    kind: str
    vendored: bool | None = None


class TargetsResponse(BaseModel):
    supported: list[TargetInfo]
    coming_soon: list[TargetInfo]


class VersionResponse(BaseModel):
    backend: str
    etal: str


class HealthResponse(BaseModel):
    status: str
    compiler: str


class JobResponse(CompileResponse):
    target: str
    mode: str


class RouteRequest(BaseModel):
    capability: str = "tools"
    provider: str | None = None
    model: str | None = None


class Candidate(BaseModel):
    provider: str
    model: str | None = None
    reason: str


class RouteResponse(BaseModel):
    chosen: Candidate | None
    candidates: list[Candidate]
    excluded: list[Candidate]


class UsageRequest(BaseModel):
    provider: str
    model: str
    in_tokens: int = 0
    out_tokens: int = 0
    ok: bool = True


class RelayMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[dict] = []
    tool_call_id: str | None = None


class RelayTool(BaseModel):
    name: str
    description: str = ""
    parameters: dict = {}


class RelayRequest(BaseModel):
    provider: str
    model: str
    messages: list[RelayMessage]
    tools: list[RelayTool] = []


class RelayToolCall(BaseModel):
    id: str
    name: str
    arguments: str


class RelayResponse(BaseModel):
    content: str | None = None
    tool_calls: list[RelayToolCall] = []
    usage: dict = {}
    model: str


class TurnRequest(BaseModel):
    """No provider, no model, no key: the client cannot pick. Extra
    fields are refused so a crafted body cannot smuggle a route in."""

    model_config = ConfigDict(extra="forbid")

    messages: list[RelayMessage]
    tools: list[RelayTool] = []


class TurnResponse(BaseModel):
    content: str | None = None
    tool_calls: list[RelayToolCall] = []
    usage: dict = {}


class ProviderStatus(BaseModel):
    id: str
    enabled: bool
    key_present: bool
    key_env: str | None = None
    daily_token_budget: int | None = None
    spent_today: int = 0
    fails: int = 0
    cooldown_until: str | None = None
    models: list[str] = []


class ProviderConfig(BaseModel):
    provider: str
    enabled: bool | None = None
    daily_token_budget: int | None = None


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str
    lang: Lang = "en"


class LoginRequest(BaseModel):
    email: str
    password: str
    lang: Lang = "en"


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserProfile(BaseModel):
    id: str
    name: str
    email: str

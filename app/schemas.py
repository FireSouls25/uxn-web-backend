"""Request/response models for the compile API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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


class JobResponse(CompileResponse):
    target: str
    mode: str

"""Persisted compile jobs: request metadata, diagnostics, outputs.

Outputs live here as bytes (ROMs are ~1KB, web pages a few hundred
KB — comfortably inside Postgres). If artifacts ever grow, move them
to object storage and keep only a reference.
"""
from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class CompileJob(Base):
    __tablename__ = "compile_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    target: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))
    lang: Mapped[str] = mapped_column(String(8), default="en")
    status: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text, default="")
    diagnostics: Mapped[list] = mapped_column(JSON, default=list)
    files_sha: Mapped[str] = mapped_column(String(64), default="")
    tal_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rom_bin: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    html_bin: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    bundle_bin: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

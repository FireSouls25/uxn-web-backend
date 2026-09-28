"""Settings read from the environment (see .env.example)."""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Local-dev fallback: compiler checkout next to uxn-webpage/.
DEV_ETAL = BACKEND_DIR.parent.parent / "uxn-dsl" / "build" / "linux-x86" / "etal"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


class Settings:
    etal_bin: str = _env("ETAL_BIN", str(DEV_ETAL) if DEV_ETAL.exists() else "etal")
    etal_version: str = _env("ETAL_VERSION", "0.1.1")
    database_url: str = _env(
        "DATABASE_URL",
        "postgresql+psycopg://uxn:uxn@localhost:5432/uxnweb",
    )
    compile_timeout: int = int(_env("COMPILE_TIMEOUT", "60"))
    max_files: int = int(_env("MAX_FILES", "64"))
    max_file_bytes: int = int(_env("MAX_FILE_BYTES", str(256 * 1024)))
    max_total_bytes: int = int(_env("MAX_TOTAL_BYTES", str(2 * 1024 * 1024)))
    max_path_chars: int = int(_env("MAX_PATH_CHARS", "64"))
    rate_limit_per_minute: int = int(_env("RATE_LIMIT_PER_MINUTE", "30"))
    auth_rate_per_minute: int = int(_env("AUTH_RATE_PER_MINUTE", "10"))
    max_concurrent_compiles: int = int(_env("MAX_CONCURRENT_COMPILES", "4"))
    jwt_secret: str = _env("JWT_SECRET", "dev-only-secret-change-me")
    access_minutes: int = int(_env("ACCESS_MINUTES", "30"))
    refresh_days: int = int(_env("REFRESH_DAYS", "30"))
    cors_origins: tuple[str, ...] = tuple(
        o.strip() for o in _env("CORS_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",") if o.strip()
    )


settings = Settings()

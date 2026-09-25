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


settings = Settings()

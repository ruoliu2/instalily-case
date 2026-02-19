from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_data_mode() -> str:
    mode = os.getenv("DATA_MODE", "mock").strip().lower()
    return mode if mode in {"mock", "supabase"} else "mock"


@dataclass(frozen=True)
class Settings:
    app_name: str = "instalily-agent-backend"
    sample_dir: Path = Path(__file__).resolve().parents[2] / "sample"

    # Controls where runtime retrieval/compatibility tools read data from.
    # Allowed values: "mock" | "supabase"
    data_mode: str = _env_data_mode()
    supabase_db_url: Optional[str] = os.getenv("SUPABASE_DB_URL")

    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "ollama")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-oss:20b")
    use_llm: bool = _env_bool("USE_LLM", True)


settings = Settings()

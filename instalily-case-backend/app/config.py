from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _env_data_mode() -> str:
    mode = os.getenv("DATA_MODE", "supabase").strip().lower()
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
    backend_reload: bool = os.getenv("BACKEND_RELOAD", "true").lower() == "true"
    mcp_browser_enabled: bool = os.getenv("MCP_BROWSER_ENABLED", "false").lower() == "true"
    mcp_browser_command: str = os.getenv("MCP_BROWSER_COMMAND", "npx")
    mcp_browser_args_raw: str = os.getenv(
        "MCP_BROWSER_ARGS", "-y @playwright/mcp@latest --headless"
    )

    @property
    def mcp_browser_args(self) -> list[str]:
        return shlex.split(self.mcp_browser_args_raw)


settings = Settings()

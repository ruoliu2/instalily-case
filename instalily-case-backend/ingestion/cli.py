from __future__ import annotations

import json

from .config import IngestionConfig, require_db_url
from .service import run_ingestion


def main() -> None:
    cfg = IngestionConfig()
    require_db_url(cfg)
    stats = run_ingestion(cfg)
    print(json.dumps(stats.__dict__, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class IngestionConfig:
    supabase_db_url: str = os.getenv("SUPABASE_DB_URL", "")
    crawl_concurrency: int = int(os.getenv("CRAWL_CONCURRENCY", "12"))
    max_runtime_hours: float = float(os.getenv("MAX_RUNTIME_HOURS", "48"))
    max_pages: int = int(os.getenv("MAX_PAGES", "250000"))
    requeue_seeds_on_start: bool = os.getenv("REQUEUE_SEEDS_ON_START", "false").lower() == "true"
    save_markdown: bool = os.getenv("SAVE_MARKDOWN", "false").lower() == "true"

    seed_urls: List[str] = field(
        default_factory=lambda: [
            "https://www.partselect.com/",
            "https://www.partselect.com/Appliance-Parts.htm",
            "https://www.partselect.com/Dishwasher-Parts.htm",
            "https://www.partselect.com/Refrigerator-Parts.htm",
            "https://www.partselect.com/Washer-Parts.htm",
            "https://www.partselect.com/Dryer-Parts.htm",
            "https://www.partselect.com/Range-Parts.htm",
            "https://www.partselect.com/Microwave-Parts.htm",
            "https://www.partselect.com/Oven-Parts.htm",
            "https://www.partselect.com/Freezer-Parts.htm",
            "https://www.partselect.com/Ice-Machine-Parts.htm",
            "https://www.partselect.com/Trash-Compactor-Parts.htm",
            "https://www.partselect.com/Cooktop-Parts.htm",
            "https://www.partselect.com/Brands/",
            "https://www.partselect.com/Whirlpool-Parts.htm",
            "https://www.partselect.com/GE-Parts.htm",
            "https://www.partselect.com/Frigidaire-Parts.htm",
            "https://www.partselect.com/Kenmore-Parts.htm",
            "https://www.partselect.com/LG-Parts.htm",
            "https://www.partselect.com/Maytag-Parts.htm",
            "https://www.partselect.com/KitchenAid-Parts.htm",
            "https://www.partselect.com/Whirlpool-Dishwasher-Parts.htm",
            "https://www.partselect.com/Whirlpool-Refrigerator-Parts.htm",
            "https://www.partselect.com/Whirlpool-Washer-Parts.htm",
            "https://www.partselect.com/Whirlpool-Dryer-Parts.htm",
            "https://www.partselect.com/Whirlpool-Range-Parts.htm",
            "https://www.partselect.com/GE-Dishwasher-Parts.htm",
            "https://www.partselect.com/GE-Refrigerator-Parts.htm",
            "https://www.partselect.com/GE-Washer-Parts.htm",
            "https://www.partselect.com/GE-Dryer-Parts.htm",
            "https://www.partselect.com/GE-Range-Parts.htm",
            "https://www.partselect.com/Frigidaire-Dishwasher-Parts.htm",
            "https://www.partselect.com/Frigidaire-Refrigerator-Parts.htm",
            "https://www.partselect.com/Frigidaire-Washer-Parts.htm",
            "https://www.partselect.com/Frigidaire-Dryer-Parts.htm",
            "https://www.partselect.com/Frigidaire-Range-Parts.htm",
        ]
    )


def require_db_url(cfg: IngestionConfig) -> None:
    if not cfg.supabase_db_url:
        raise RuntimeError("Missing SUPABASE_DB_URL")

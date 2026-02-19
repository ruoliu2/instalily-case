from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

PS_RE = re.compile(r"\b(PS\d{6,})\b", re.IGNORECASE)
MODEL_RE = re.compile(r"/Models/([A-Za-z0-9\-]+)/?")
PRICE_RE = re.compile(r"\$(\d+(?:\.\d{2})?)")
LINK_RE = re.compile(r"\[[^\]]+\]\((https://www\.partselect\.com[^\s)]+)\)")
H2_RE = re.compile(r"^##\s+(.+)$", re.M)
PART_LINK_RE = re.compile(r'\((https://www\.partselect\.com/(PS\d+[^)\s"]+))')


@dataclass
class ParsedPart:
    partselect_number: str
    part_url: str
    name: Optional[str] = None
    manufacturer_part_number: Optional[str] = None
    price_value: Optional[float] = None


@dataclass
class ParsedModel:
    model_number: str
    brand: Optional[str] = None
    appliance_type: str = "unknown"
    parts: List[ParsedPart] = field(default_factory=list)
    symptoms: List[str] = field(default_factory=list)
    media: List[tuple[str, str, str]] = field(default_factory=list)  # (type,title,url)
    qa: List[tuple[str, str]] = field(default_factory=list)


@dataclass
class ParsedPage:
    page_kind: str
    model: Optional[ParsedModel] = None
    part: Optional[ParsedPart] = None
    discovered_urls: List[str] = field(default_factory=list)


def normalize_url(url: str) -> str:
    return canonicalize_url(url)


def canonicalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = "https"
    host = parsed.netloc.lower()
    if host == "partselect.com":
        host = "www.partselect.com"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return f"{scheme}://{host}{path}"


def classify_page(url: str) -> str:
    if "/Models/" in url:
        return "model"
    if re.search(r"/PS\d+", url):
        return "part"
    if "/Repair/" in url:
        return "repair"
    return "other"


def _extract_brand_and_type(title: str) -> tuple[Optional[str], str]:
    lower = title.lower()
    appliance = "unknown"
    if "dishwasher" in lower:
        appliance = "dishwasher"
    elif "refrigerator" in lower or "fridge" in lower:
        appliance = "refrigerator"

    # common format: "Whirlpool Dishwasher WDT... - ..."
    brand = None
    tokens = title.split()
    if tokens:
        brand = tokens[0]
    return brand, appliance


def _extract_model_section(md: str, model_number: str) -> str:
    key = f"Parts for the {model_number}"
    start = md.find(key)
    if start == -1:
        return md
    end_candidates = [
        md.find("Questions And Answers", start),
        md.find("Common Symptoms", start),
        md.find("Videos related", start),
        md.find("Installation Instructions", start),
    ]
    ends = [e for e in end_candidates if e != -1]
    end = min(ends) if ends else min(len(md), start + 20000)
    return md[start:end]


def parse_model_page(url: str, markdown: str, title: str) -> Optional[ParsedModel]:
    m = MODEL_RE.search(url)
    if not m:
        return None
    model_number = m.group(1).upper()
    brand, appliance = _extract_brand_and_type(title)

    section = _extract_model_section(markdown, model_number)
    parts: List[ParsedPart] = []

    for link, slug in PART_LINK_RE.findall(section):
        ps_match = re.match(r"PS\d+", slug, re.IGNORECASE)
        if not ps_match:
            continue
        ps = ps_match.group(0).upper()
        manufacturer_part = None
        # slug form: PS3406971-Whirlpool-W10195416-Lower...
        bits = slug.split("-")
        if len(bits) >= 3:
            manufacturer_part = bits[2]

        parts.append(
            ParsedPart(
                partselect_number=ps,
                part_url=normalize_url(link),
                manufacturer_part_number=manufacturer_part,
            )
        )

    # dedupe parts by PS
    dedup = {}
    for p in parts:
        dedup[p.partselect_number] = p
    parts = list(dedup.values())

    symptoms = []
    for h in H2_RE.findall(markdown):
        if h.lower().startswith("common symptoms"):
            continue
    for s in re.findall(r"/Symptoms/([^)/]+)", markdown):
        symptom = s.replace("-", " ").replace("%E2%80%99", "'")
        if symptom and symptom not in symptoms:
            symptoms.append(symptom)

    media: List[tuple[str, str, str]] = []
    for l in LINK_RE.findall(markdown):
        if "/Videos/?VideoID=" in l:
            media.append(("video", "model video", normalize_url(l)))
        elif "/Instructions/" in l:
            media.append(("instruction", "model instruction", normalize_url(l)))

    # very lightweight QA extraction: "Q:" / "A:" pairs when present
    qa = []
    q_blocks = re.findall(r"\bQ:\s*(.+?)\n\s*A:\s*(.+?)(?=\n\s*Q:|\Z)", markdown, flags=re.S)
    for q, a in q_blocks[:50]:
        q = q.strip()
        a = a.strip()
        if q and a:
            qa.append((q[:2000], a[:4000]))

    return ParsedModel(
        model_number=model_number,
        brand=brand,
        appliance_type=appliance,
        parts=parts,
        symptoms=symptoms[:100],
        media=media[:200],
        qa=qa,
    )


def parse_part_page(url: str, markdown: str) -> Optional[ParsedPart]:
    m = re.search(r"/(PS\d+)-", url, flags=re.I)
    if not m:
        return None
    ps = m.group(1).upper()

    slug = url.split("/")[-1]
    slug = slug.split("?")[0]
    bits = slug.split("-")
    manufacturer_part = bits[2] if len(bits) >= 3 else None

    price = None
    pm = PRICE_RE.search(markdown)
    if pm:
        try:
            price = float(pm.group(1))
        except Exception:
            price = None

    name = None
    hm = re.search(r"^#\s+(.+)$", markdown, flags=re.M)
    if hm:
        name = hm.group(1).strip()

    return ParsedPart(
        partselect_number=ps,
        part_url=normalize_url(url),
        name=name,
        manufacturer_part_number=manufacturer_part,
        price_value=price,
    )


def parse_page(url: str, markdown: str, title: str) -> ParsedPage:
    page_kind = classify_page(url)
    discovered = [normalize_url(u) for u in LINK_RE.findall(markdown)]

    if page_kind == "model":
        model = parse_model_page(url, markdown, title)
        return ParsedPage(page_kind=page_kind, model=model, discovered_urls=discovered)

    if page_kind == "part":
        part = parse_part_page(url, markdown)
        return ParsedPage(page_kind=page_kind, part=part, discovered_urls=discovered)

    return ParsedPage(page_kind=page_kind, discovered_urls=discovered)


def is_core_url(url: str) -> bool:
    if not url.startswith("https://www.partselect.com/"):
        return False
    path = url.replace("https://www.partselect.com", "")
    allowed = (
        "/Models/",
        "/PS",
        "/Dishwasher-Parts",
        "/Refrigerator-Parts",
        "/Washer-Parts",
        "/Dryer-Parts",
        "/Range-Parts",
        "/Microwave-Parts",
        "/Oven-Parts",
        "/Freezer-Parts",
        "/Ice-Machine-Parts",
        "/Trash-Compactor-Parts",
        "/Cooktop-Parts",
        "/Appliance-Parts",
        "/Repair/",
        "/Brands",
        "-Dishwasher-Parts",
        "-Refrigerator-Parts",
        "-Washer-Parts",
        "-Dryer-Parts",
        "-Range-Parts",
        "-Microwave-Parts",
        "-Oven-Parts",
        "-Freezer-Parts",
        "-Ice-Machine-Parts",
        "-Trash-Compactor-Parts",
        "-Cooktop-Parts",
    )
    return any(path.startswith(a) or a in path for a in allowed)

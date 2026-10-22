from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class Area:
    lat: float
    lon: float
    radius_km: float
    label: str                  # what the user picked, e.g. "Koramangala, Bengaluru, Karnataka"
    place: str                  # short locality name, e.g. "Koramangala"
    city: str                   # e.g. "Bengaluru"
    localities: list[str] = field(default_factory=list)  # names to query on locality-based sites


@dataclass
class Listing:
    source: str
    source_id: str
    deal: str                   # "rent" or "sale"
    title: str
    url: str
    price: float | None = None
    lat: float | None = None
    lon: float | None = None
    locality: str = ""
    building: str = ""              # society / project name, when the site gives one
    bedrooms: float | None = None   # 1 RK counts as 0.5
    area_sqft: float | None = None
    image: str = ""
    posted_at: str | None = None    # ISO-8601, as reported by the site
    details: dict = field(default_factory=dict)
    distance_km: float | None = None
    feed: str = ""                  # which search produced it; a feed's first successful read is the baseline

    def to_dict(self) -> dict:
        return asdict(self)


def num(value) -> float | None:
    """Number from 35000, "35,000", "1305 sqft" or "38000"; None for blanks and zeros."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) or None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    return (float(m.group().replace(",", "")) or None) if m else None


_INR_UNITS = {"cr": 1e7, "crore": 1e7, "crores": 1e7, "l": 1e5, "lac": 1e5, "lacs": 1e5, "lakh": 1e5, "lakhs": 1e5}


def inr_from_text(text) -> float | None:
    """₹ amount written with a unit, e.g. "priced at ₹1.25 Cr" or "85 lakhs"."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(crores?|cr|lakhs?|lacs?|l)\b", str(text or ""), re.I)
    return float(m.group(1)) * _INR_UNITS[m.group(2).lower()] if m else None


def bedrooms_from(text) -> float | None:
    if not text:
        return None
    text = str(text)
    m = re.search(r"(\d+(?:\.5)?)\s*-?\s*BHK", text, re.I) or re.search(r"BHK\s*(\d+)", text, re.I)
    if m:
        return float(m.group(1))
    if re.search(r"\d?\s*RK\b", text, re.I):
        return 0.5
    return num(text) if re.fullmatch(r"\s*\d+(\.5)?\s*", text) else None


def ms_to_iso(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not False and v not in (None, "", [], {})}

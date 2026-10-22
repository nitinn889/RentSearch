"""Does a listing belong to a watched building? Name matching that tolerates the ways sites spell societies."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .geo import haversine_km
from .models import Listing

# Words that say what kind of place it is rather than which one; they are ignored when matching.
GENERIC = {
    "the", "apartment", "apartments", "apts", "apt", "flat", "flats", "residency", "residences", "residence",
    "society", "chs", "chsl", "enclave", "complex", "tower", "towers", "homes", "home", "phase", "block", "wing",
    "project", "building", "bldg", "co", "op", "housing",
}


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", text)


def name_tokens(name: str) -> list[str]:
    words = tokens(name)
    return [w for w in words if w not in GENERIC] or words


def name_matches(name: str, listing: Listing) -> bool:
    want = name_tokens(name)
    if not want:
        return False
    hay = tokens(f"{listing.building} {listing.title}")
    if set(want) <= set(hay):
        return True
    joined = "".join(want)  # "Spring Leaf" vs "Springleaf", "Lake Side" vs "Lakeside"
    if len(joined) >= 5 and joined in "".join(hay):
        return True
    if listing.building:
        have = " ".join(name_tokens(listing.building))
        return SequenceMatcher(None, " ".join(want), have).ratio() >= 0.88
    return False


def in_building(building: dict, listing: Listing) -> bool:
    """Name matches, the deal is one the watch wants, and (when the site gives coordinates) it is nearby."""
    if building["deal"] != "both" and listing.deal != building["deal"]:
        return False
    if not name_matches(building["name"], listing):
        return False
    if listing.lat and listing.lon:
        return haversine_km(building["lat"], building["lon"], listing.lat, listing.lon) <= building["radius_km"] * 1.1 + 0.2
    return True

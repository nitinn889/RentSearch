"""Listings from schema.org JSON-LD, which many property sites embed for search engines."""
from __future__ import annotations

from .models import Listing, bedrooms_from, clean_text, inr_from_text, num

ACTIONS = {"rentaction": "rent", "buyaction": "sale", "sellaction": "sale"}
RESIDENCES = {"apartment", "house", "singlefamilyresidence", "residence", "accommodation",
              "apartmentcomplex", "room", "suite", "realestatelisting"}


def _types(item: dict) -> set[str]:
    t = item.get("@type")
    return {str(x).lower() for x in (t if isinstance(t, list) else [t]) if x}


def _first_image(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or ""
    return value if isinstance(value, str) else ""


def _price(item: dict) -> float | None:
    for holder in (item, item.get("priceSpecification"), item.get("offers")):
        if isinstance(holder, list):
            holder = holder[0] if holder else None
        if isinstance(holder, dict) and num(holder.get("price")):
            return num(holder.get("price"))
    return None


def listings_from_json_ld(items: list[dict], deal: str, source: str) -> list[Listing]:
    found: dict[str, Listing] = {}
    for item in items:
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        types = _types(item)
        action = next((ACTIONS[t] for t in types if t in ACTIONS), None)
        if not action and not types & RESIDENCES:
            continue
        listing = found.setdefault(url, Listing(source=source, source_id=url, deal=action or deal, title="", url=url))
        obj = item.get("object") if isinstance(item.get("object"), dict) else {}
        listing.title = listing.title or clean_text(item.get("name") or obj.get("name"))
        listing.price = listing.price or _price(item)
        if not listing.price and (action or deal) == "sale":  # sale pages often state the price only in prose
            listing.price = inr_from_text(item.get("description"))
        listing.image = listing.image or _first_image(item.get("image"))
        if action:
            listing.deal = action
            listing.posted_at = listing.posted_at or item.get("startTime")
        geo = item.get("geo") if isinstance(item.get("geo"), dict) else {}
        listing.lat = listing.lat or num(geo.get("latitude"))
        listing.lon = listing.lon or num(geo.get("longitude"))
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        address = item.get("address") or location.get("address")
        address = address if isinstance(address, dict) else {}
        listing.locality = listing.locality or clean_text(address.get("streetAddress") or address.get("addressLocality"))
        building = clean_text(address.get("name"))
        if building and building.lower() != listing.locality.lower():
            listing.building = listing.building or building
        listing.bedrooms = listing.bedrooms or num(item.get("numberOfRooms")) or bedrooms_from(listing.title)
        size = item.get("floorSize")
        listing.area_sqft = listing.area_sqft or num(size.get("value") if isinstance(size, dict) else size)
    return [l for l in found.values() if l.title]

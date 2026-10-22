"""NoBroker: owner listings; its search pages take a point and a radius directly."""
from __future__ import annotations

import base64
import json
import re
from urllib.parse import quote, urljoin

from ..extract import find_dicts, json_values_after, next_flight
from ..geo import haversine_km
from ..models import Listing, bedrooms_from, clean_text, compact, ms_to_iso, num
from .base import LayoutChanged, Source

CITIES = {  # NoBroker's city slug -> approximate centre
    "bangalore": (12.9716, 77.5946),
    "mumbai": (19.0760, 72.8777),
    "pune": (18.5204, 73.8567),
    "chennai": (13.0827, 80.2707),
    "hyderabad": (17.3850, 78.4867),
    "delhi": (28.6139, 77.2090),
    "gurgaon": (28.4595, 77.0266),
    "noida": (28.5355, 77.3910),
    "ghaziabad": (28.6692, 77.4538),
    "faridabad": (28.4089, 77.3178),
}
MAX_CITY_KM = 60
MAX_RADIUS_KM = 10


def _society(name) -> str:
    """NoBroker fills `society` with placeholders like "Independent House" when there is none."""
    name = clean_text(name)
    return "" if re.match(r"(?i)(independent|standalone|individual|stand alone)\b", name) else name


class NoBroker(Source):
    key = "nobroker"
    name = "NoBroker"
    site = "https://www.nobroker.in"

    def _city(self, area) -> str | None:
        city = min(CITIES, key=lambda c: haversine_km(area.lat, area.lon, *CITIES[c]))
        return city if haversine_km(area.lat, area.lon, *CITIES[city]) <= MAX_CITY_KM else None

    def unavailable_reason(self, area, settings):
        if not self._city(area):
            return "NoBroker only covers Bangalore, Mumbai, Pune, Chennai, Hyderabad and Delhi NCR"
        return None

    def fetch(self, area, deal, client, settings, log):
        city = self._city(area)
        place = area.place or city
        search = base64.b64encode(json.dumps(
            [{"lat": area.lat, "lon": area.lon, "placeId": "", "placeName": place}]).encode()).decode()
        params = {"searchParam": search, "radius": f"{min(area.radius_km, MAX_RADIUS_KM):.1f}",
                  "city": city, "locality": place}
        if deal == "rent":
            params["sharedAccomodation"] = "0"
        page = "rent" if deal == "rent" else "sale"
        html = client.get(f"{self.site}/property/{page}/{city}/{quote(place)}", params=params).text

        # Rent pages are Next.js (listings in the streamed payload); sale pages keep a legacy appState.
        blobs = list(json_values_after(next_flight(html), r'"initialProperties":'))
        blobs += list(json_values_after(html, r"(?:window\.)?nb\.appState\s*=\s*"))
        if not blobs:
            raise LayoutChanged("NoBroker's page format changed; listings could not be read")
        raw = {}
        for p in find_dicts(blobs, lambda d: "propertyTitle" in d and "id" in d):
            raw.setdefault(str(p["id"]), p)
        return [self._listing(p, deal) for p in raw.values()]

    def _listing(self, p: dict, deal: str) -> Listing:
        return Listing(
            source=self.key,
            source_id=str(p["id"]),
            deal=deal,
            title=clean_text(p.get("propertyTitle")),
            url=urljoin(self.site, p.get("detailUrl") or f"/property/{p['id']}/detail"),
            price=num(p.get("rent") if deal == "rent" else p.get("price")),
            lat=num(p.get("latitude")),
            lon=num(p.get("longitude")),
            locality=clean_text(p.get("locality")),
            building=_society(p.get("society")),
            bedrooms=bedrooms_from(p.get("typeDesc") or p.get("propertyTitle")),
            area_sqft=num(p.get("propertySize")),
            image=p.get("thumbnailImage") or "",
            posted_at=ms_to_iso(p.get("activationDate") or p.get("creationDate")),
            details=compact({
                "Furnishing": p.get("furnishingDesc"),
                "Deposit": p.get("formattedDeposit") and f"₹{p['formattedDeposit']}",
                "Floor": p.get("floor") is not None and p.get("totalFloor") and f"{p['floor']} of {p['totalFloor']}",
                "Available from": (ms_to_iso(p.get("availableFrom")) or "")[:10],
            }),
        )

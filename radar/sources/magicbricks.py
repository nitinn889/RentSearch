"""MagicBricks: public locality pages (its filter-style search URLs are off-limits in robots.txt)."""
from __future__ import annotations

import re

from ..extract import json_values_after
from ..http import Blocked, FetchError
from ..models import Listing, bedrooms_from, clean_text, compact, num
from .base import Source, city_key, read_area_pages, slug

CITY_SLUGS = {  # geocoder spelling -> MagicBricks URL spelling
    "bengaluru": "bangalore",
    "bengaluru urban": "bangalore",
    "gurugram": "gurgaon",
    "delhi": "new-delhi",
    "new delhi": "new-delhi",
    "mumbai suburban": "mumbai",
}
NOT_A_HOME = re.compile(r"(?i)commercial|office|shop|showroom|warehouse|plot|\bland\b|hostel|\bpg\b|godown")
MAX_LOCALITIES = 4


class MagicBricks(Source):
    key = "magicbricks"
    name = "MagicBricks"
    site = "https://www.magicbricks.com"
    locality_based = True

    def unavailable_reason(self, area, settings):
        return None if area.city else "the location's city is unknown"

    def fetch(self, area, deal, client, settings, log):
        city = CITY_SLUGS.get(city_key(area.city), slug(area.city))
        kind, suffix = ("rent", "pppfr") if deal == "rent" else ("sale", "pppfs")
        found: dict[str, Listing] = {}

        def read(url: str) -> bool:
            try:
                html = client.get(url).text
            except Blocked:
                raise
            except FetchError as e:
                log(f"{url.rsplit('/', 1)[-1]}: {e}")
                return False
            state = next(json_values_after(html, r"window\.SERVER_PRELOADED_STATE_\s*=\s*"), None)
            if not isinstance(state, dict):
                log(f"{url.rsplit('/', 1)[-1]}: page format not recognised")
                return False
            for raw in state.get("searchResult") or []:
                if raw.get("id") and not NOT_A_HOME.search(str(raw.get("propTypeD", ""))):
                    found.setdefault(str(raw["id"]), self._listing(raw, deal))
            return True

        localities = [loc for loc in area.localities[:MAX_LOCALITIES] or [area.place] if loc]
        pages = [f"{self.site}/property-for-{kind}-in-{slug(loc)}-{city}-{suffix}" for loc in localities]
        read_area_pages(self.name, pages, f"{self.site}/property-for-{kind}-in-{city}-{suffix}", read)
        return list(found.values())

    def _listing(self, p: dict, deal: str) -> Listing:
        return Listing(
            source=self.key,
            source_id=str(p["id"]),
            deal=deal,
            title=clean_text(p.get("propertyTitle") or p.get("auto_desc")),
            url=f"{self.site}/propertyDetails/{p['url']}" if p.get("url") else self.site,
            price=num(p.get("price")),
            lat=num(p.get("pmtLat")),
            lon=num(p.get("pmtLong")),
            locality=clean_text(p.get("lmtDName") or p.get("locSeoName")),
            building=clean_text(p.get("prjname")),
            bedrooms=num(p.get("bedroomD")) or bedrooms_from(p.get("propertyTitle")),
            area_sqft=num(p.get("caSqFt") or p.get("coveredArea")),
            image=p.get("wapImgUrl1") or p.get("wapImgUrl") or "",
            posted_at=p.get("postDateT"),
            details=compact({
                "Type": p.get("propTypeD"),
                "Furnishing": p.get("furnishedD"),
                "Posted by": p.get("userType"),
                "Floor": p.get("floorD"),
                "Tenants": p.get("tenantsPreference"),
            }),
        )

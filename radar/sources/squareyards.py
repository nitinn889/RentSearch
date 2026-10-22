"""SquareYards: locality pages that list homes as schema.org JSON-LD with coordinates."""
from __future__ import annotations

from ..extract import json_ld
from ..http import Blocked, FetchError
from ..schema_org import listings_from_json_ld
from .base import Source, city_key, read_area_pages, slug

CITY_SLUGS = {
    "bengaluru": "bangalore",
    "bengaluru urban": "bangalore",
    "gurugram": "gurgaon",
    "new delhi": "delhi",
    "mumbai suburban": "mumbai",
}
MAX_LOCALITIES = 4


class SquareYards(Source):
    key = "squareyards"
    name = "SquareYards"
    site = "https://www.squareyards.com"
    locality_based = True

    def unavailable_reason(self, area, settings):
        return None if area.city else "the location's city is unknown"

    def fetch(self, area, deal, client, settings, log):
        city = CITY_SLUGS.get(city_key(area.city), slug(area.city))
        prefix = f"{self.site}/rent/property-for-rent-in" if deal == "rent" else f"{self.site}/sale/property-for-sale-in"
        found = {}

        def read(url: str) -> bool:
            try:
                html = client.get(url).text
            except Blocked:
                raise
            except FetchError as e:
                log(f"{url.rsplit('/', 1)[-1]}: {e}")
                return False
            for listing in listings_from_json_ld(json_ld(html), deal, self.key):
                if listing.deal == deal:
                    found.setdefault(listing.source_id, listing)
            return True

        localities = [loc for loc in area.localities[:MAX_LOCALITIES] or [area.place] if loc]
        read_area_pages(self.name, [f"{prefix}-{slug(loc)}-{city}" for loc in localities], f"{prefix}-{city}", read)
        return list(found.values())

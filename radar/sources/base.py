from __future__ import annotations

from typing import Callable

from ..http import FetchError, PoliteClient
from ..models import Area, Listing

Log = Callable[[str], None]


class LayoutChanged(FetchError):
    """The page loaded but no longer has the data this parser expects."""


class Source:
    key = ""
    name = ""
    site = ""
    # Locality-search sites may return listings without coordinates; those are kept only
    # when their locality text matches the searched localities.
    locality_based = False

    def unavailable_reason(self, area: Area, settings: dict) -> str | None:
        return None

    def fetch(self, area: Area, deal: str, client: PoliteClient, settings: dict, log: Log) -> list[Listing]:
        raise NotImplementedError


def read_area_pages(site_name: str, locality_urls: list[str], city_url: str, read: Callable[[str], bool]) -> None:
    """Read each locality page; if the site knew none of them, read the city-wide page instead.

    `read` returns False when a page could not be loaded (e.g. the site has no page for that locality).
    """
    if not sum(read(url) for url in locality_urls) and not read(city_url):
        raise LayoutChanged(f"{site_name} has no readable pages for this area")


def city_key(city: str) -> str:
    return city.lower().replace("-", " ").strip()


def slug(text: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    return "-".join(part for part in out.split("-") if part)

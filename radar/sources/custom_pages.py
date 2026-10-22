"""Any results page you paste in: a saved search on another site, a society notice board, etc.

Listings come from the page's schema.org data when it has some; otherwise every
listing-looking link on the page counts as a listing, so new links mean new listings.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from ..extract import json_ld
from ..http import FetchError
from ..models import Listing, clean_text
from ..schema_org import listings_from_json_ld
from .base import Source

_LISTINGISH = re.compile(r"(?i)\d{4,}|bhk|\brk\b|flat|apartment|rent|sale|property|house|villa|listing|detail")


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text)))
            self._href = None


def _link_listings(html: str, page_url: str, deal: str) -> list[Listing]:
    parser = _Links()
    parser.feed(html)
    host = urlsplit(page_url).netloc
    found: dict[str, Listing] = {}
    for href, text in parser.links:
        url = urljoin(page_url, href).split("#", 1)[0]
        parts = urlsplit(url)
        text = clean_text(text)
        if (parts.scheme not in ("http", "https") or parts.netloc != host or url.rstrip("/") == page_url.rstrip("/")
                or len(text) < 12 or not _LISTINGISH.search(f"{parts.path} {text}")):
            continue
        found.setdefault(url, Listing(source=host, source_id=url, deal=deal, title=text[:160], url=url))
    return list(found.values())


class CustomPages(Source):
    key = "custom"
    name = "Your pages"

    def unavailable_reason(self, area, settings):
        return None if settings.get("custom_pages") else "no pages added (add any results page under Sources)"

    def fetch(self, area, deal, client, settings, log):
        pages = [p for p in settings.get("custom_pages", []) if p.get("deal") == deal]
        listings, problems = [], []
        for page in pages:
            try:
                r = client.get(page["url"])
            except FetchError as e:  # one bad page must not hide the others
                problems.append(e)
                log(f"{page['url']}: {e}")
                continue
            host = urlsplit(r.url).netloc
            found = listings_from_json_ld(json_ld(r.text), deal, host) or _link_listings(r.text, r.url, deal)
            for listing in found:
                listing.source = host
                listing.feed = f"page:{page['url']}"
            listings += found
        if pages and len(problems) == len(pages):
            raise problems[0]
        return listings

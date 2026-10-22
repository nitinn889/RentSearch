import json

from radar.alerts import inr, price_text
from radar.extract import json_ld
from radar.http import Robots
from radar.models import Listing, bedrooms_from, inr_from_text, num
from radar.schema_org import listings_from_json_ld
from radar.sources.custom_pages import _link_listings
from radar.sources.magicbricks import MagicBricks
from radar.sources.nobroker import NoBroker
from radar.models import Area

AREA = Area(lat=12.9352, lon=77.6245, radius_km=2, label="Koramangala", place="Koramangala", city="Bengaluru",
            localities=["Koramangala"])


class FakeClient:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get(self, url, params=None, **_):
        self.urls.append((url, params))
        page = self.pages[len(self.urls) - 1] if isinstance(self.pages, list) else self.pages

        class R:
            text, url = page, "https://example.test/page"
        return R()


def test_robots_rules():
    rb = Robots("User-agent: ClaudeBot\nDisallow: /\n\nUser-agent: *\nDisallow: /*proptype=\n"
                "Disallow: /property/\nAllow: /property/ok$\n")
    assert not rb.allows("/x/residential?proptype=1&a=2")
    assert rb.allows("/property-for-rent-in-x-pppfr")
    assert not rb.allows("/property/123")
    assert rb.allows("/property/ok") and not rb.allows("/property/ok/2")
    assert not Robots("User-agent: rentradar\nDisallow: /\n").allows("/a")
    assert Robots("").allows("/anything")


def test_number_helpers():
    assert num("1,305 sqft") == 1305 and num(0) is None and num("") is None
    assert bedrooms_from("3 BHK Flat") == 3 and bedrooms_from("1 RK House") == 0.5
    assert bedrooms_from("BHK2") == 2 and bedrooms_from("3.5 BHK Villa") == 3.5 and bedrooms_from("Studio") is None
    assert inr_from_text("priced at ₹1.25 Cr including") == 12500000 and inr_from_text("85 lakhs") == 8500000
    assert inr_from_text("available at 80,000") is None


def test_price_formatting():
    rent = lambda p: Listing(source="s", source_id="1", deal="rent", title="t", url="u", price=p)  # noqa: E731
    sale = lambda p: Listing(source="s", source_id="1", deal="sale", title="t", url="u", price=p)  # noqa: E731
    assert inr(1234567) == "12,34,567" and inr(999) == "999"
    assert price_text(rent(35000)) == "₹35,000/mo"
    assert price_text(sale(52000000)) == "₹5.2 Cr" and price_text(sale(10000000)) == "₹1 Cr"
    assert price_text(sale(8500000)) == "₹85 L"
    assert price_text(rent(None)) == "Price on request"


def test_schema_org_merges_residence_and_action():
    html = """
    <script type="application/ld+json">{"@type":"Apartment","name":"2 BHK Flat for Rent in Yelahanka",
      "url":"https://sy.test/rental-1","geo":{"latitude":13.13,"longitude":77.57},"numberOfRooms":"2",
      "floorSize":"1305","address":{"name":"Shriram Suhaana","streetAddress":"Yelahanka"}}</script>
    <script type="application/ld+json">{"@type":"RentAction","price":"38000","startTime":"2026-10-21T18:30:00",
      "url":"https://sy.test/rental-1","object":{"name":"2 BHK Flat for Rent in Yelahanka"}}</script>
    <script type="application/ld+json">{"@type":"Apartment","name":"3 BHK for Sale","url":"https://sy.test/resale-2",
      "description":"Spacious, priced at 1.5 Cr"}</script>
    <script type="application/ld+json">{ broken json </script>"""
    by_url = {l.url: l for l in listings_from_json_ld(json_ld(html), "rent", "squareyards")}
    rent = by_url["https://sy.test/rental-1"]
    assert (rent.deal, rent.price, rent.lat, rent.bedrooms, rent.area_sqft) == ("rent", 38000, 13.13, 2, 1305)
    assert rent.building == "Shriram Suhaana" and rent.locality == "Yelahanka" and rent.posted_at
    sale = listings_from_json_ld(json_ld(html), "sale", "squareyards")
    assert {l.url: l.price for l in sale}["https://sy.test/resale-2"] == 1.5e7


def _flight(payload: dict) -> str:
    chunk = "55:" + json.dumps(payload) + "\n"
    return f'<script>self.__next_f.push([1,{json.dumps(chunk)}])</script>'


def test_nobroker_rent_and_sale_pages():
    prop = {"id": "abc", "propertyTitle": "2 BHK Flat In Dnr Spring Leaf for Rent", "rent": 35000, "latitude": 12.93,
            "longitude": 77.63, "locality": "Koramangala", "society": "DNR Spring Leaf", "typeDesc": "2 BHK",
            "detailUrl": "/property/abc/detail", "activationDate": 1792675208000, "propertySize": 1100}
    rent_html = _flight({"searchUrl": "x", "initialProperties": [prop]})
    [l] = NoBroker().fetch(AREA, "rent", FakeClient(rent_html), {}, print)
    assert (l.source_id, l.price, l.bedrooms, l.building) == ("abc", 35000, 2, "DNR Spring Leaf")
    assert l.url == "https://www.nobroker.in/property/abc/detail" and l.posted_at.startswith("2026")

    house = {**prop, "id": "def", "price": 9_000_000, "society": "Independent House"}
    sale_html = "<script>nb.appState = " + json.dumps({"listPage": {"listPageProperties": [house, house]}}) + ";</script>"
    [s] = NoBroker().fetch(AREA, "sale", FakeClient(sale_html), {}, print)
    assert (s.source_id, s.price, s.building) == ("def", 9_000_000, "")


def test_magicbricks_reads_state_and_skips_commercial():
    state = {"searchResult": [
        {"id": 1, "propertyTitle": "1BHK Multistorey Apartment for Rent in Mantri Classic", "price": 15000,
         "pmtLat": 12.93, "pmtLong": 77.63, "prjname": "Mantri Classic", "propTypeD": "Apartment",
         "url": "1-BHK-x&id=1", "postDateT": "2026-10-22T09:07:06.000Z", "bedroomD": "1"},
        {"id": 2, "propertyTitle": "Office space", "price": 99000, "propTypeD": "Commercial Office Space"},
    ]}
    html = "<script>window.SERVER_PRELOADED_STATE_ = " + json.dumps(state) + "</script>"
    client = FakeClient(html)
    [l] = MagicBricks().fetch(AREA, "rent", client, {}, print)
    assert (l.building, l.price, l.bedrooms) == ("Mantri Classic", 15000, 1)
    assert l.url == "https://www.magicbricks.com/propertyDetails/1-BHK-x&id=1"
    assert client.urls[0][0] == "https://www.magicbricks.com/property-for-rent-in-koramangala-bangalore-pppfr"


def test_custom_page_links():
    html = """<a href="/flat/12345">2 BHK flat for rent in HSR Layout</a>
              <a href="/about">About us</a><a href="https://other.test/x/99999">Elsewhere 2 BHK flat</a>
              <a href="/flat/777#top">1 RK house near the lake, rent</a>"""
    urls = {l.url for l in _link_listings(html, "https://site.test/search", "rent")}
    assert urls == {"https://site.test/flat/12345", "https://site.test/flat/777"}

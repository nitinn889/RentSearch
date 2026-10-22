import copy

import pytest

from radar import alerts, scanner, store
from radar.models import Listing
from radar.sources.base import Source

KORAMANGALA = {"lat": 12.9352, "lon": 77.6245, "radius_km": 2, "label": "Koramangala", "place": "Koramangala",
               "city": "Bengaluru", "localities": ["Koramangala"], "localities_for": "12.9352,77.6245,2"}


class FakeSite(Source):
    key, name = "fake", "Fake"

    def __init__(self):
        self.listings = {"rent": [], "sale": []}
        self.calls = []

    def fetch(self, area, deal, client, settings, log):
        self.calls.append((round(area.lat, 3), deal))
        return [copy.copy(l) for l in self.listings[deal]]


def flat(i, lat=12.936, lon=77.625, building="", deal="rent", price=30000):
    return Listing(source="fake", source_id=str(i), deal=deal, title=f"2 BHK in {building or 'Koramangala'}", url=f"u{i}",
                   lat=lat, lon=lon, building=building, price=price)


@pytest.fixture
def env(database, monkeypatch):
    site = FakeSite()
    sent = []
    monkeypatch.setattr(scanner, "SOURCES", [site])
    monkeypatch.setattr(scanner.geo, "nearby_localities", lambda lat, lon, r, place: [place])
    monkeypatch.setattr(scanner.alerts, "dispatch", lambda alert, settings: sent.append(alert) or {})
    uid = store.create_user("a@b.in", "password1")

    def run():
        scan = scanner.Scan(uid, "manual")
        scanner._scan(scan)
        return scan.result["summary"]
    return site, sent, uid, run


def test_area_first_scan_is_a_baseline_then_new_listings_alert(env):
    site, sent, uid, run = env
    store.save_settings(uid, {"area": KORAMANGALA})
    site.listings["rent"] = [flat(1), flat(2), flat(3, lat=13.2)]       # #3 is 30 km away
    first = run()
    assert first["in_area"] == 2 and first["new"] == 0 and first["baseline"] == ["fake:rent"] and not sent
    site.listings["rent"].append(flat(4))
    second = run()
    assert second["new"] == 1 and [l.source_id for l in sent[0].listings] == ["4"]
    assert "new home" in sent[0].title and "for rent" in sent[0].title
    assert run()["new"] == 0 and len(sent) == 1


def test_budget_filter_hides_but_still_remembers(env):
    site, sent, uid, run = env
    store.save_settings(uid, {"area": KORAMANGALA, "max_rent": 25000})
    site.listings["rent"] = [flat(1)]
    run()
    site.listings["rent"] += [flat(2, price=50000), flat(3, price=20000)]
    summary = run()
    assert summary["filtered_out"] == 2 and [l.source_id for l in sent[0].listings] == ["3"]


def test_building_watch_alerts_current_flats_then_only_new_ones(env):
    site, sent, uid, run = env
    store.add_building(uid, {"name": "Prestige Lakeside Habitat", "lat": 12.95, "lon": 77.75, "city": "Bengaluru",
                             "place": "Varthur", "label": "Varthur"})
    site.listings["rent"] = [flat(1, 12.951, 77.751, "Prestige Lakeside Habitat"), flat(2, 12.951, 77.751, "Other Towers")]
    first = run()
    assert first["new"] == 1 and "available now in Prestige Lakeside Habitat" in sent[0].title
    [b] = store.list_buildings(uid)
    assert b["status"]["available"] == 1 and b["status"]["checked_at"]
    assert run()["new"] == 0
    site.listings["rent"].append(flat(3, 12.952, 77.752, "Prestige Lakeside Habitat Phase 2"))
    run()
    assert "1 new flat in Prestige Lakeside Habitat" in sent[-1].title and sent[-1].listings[0].source_id == "3"


def test_building_inside_area_reuses_the_area_read(env):
    site, sent, uid, run = env
    store.save_settings(uid, {"area": KORAMANGALA})
    store.add_building(uid, {"name": "Mantri Classic", "lat": 12.936, "lon": 77.626, "radius_km": 0.5})
    site.listings["rent"] = [flat(1, building="Mantri Classic")]
    summary = run()
    assert site.calls == [(12.935, "rent")]                       # one read, not two
    # The area's first read is a baseline, but the building watch still reports what is there now.
    assert summary["new"] == 1 and sent[0].title.startswith("🏢")
    item = store.last_results(uid)["listings"][0]
    assert set(item["tags"]) == {"area", f"bldg:{store.list_buildings(uid)[0]['id']}"} and item["unread"]


def test_scan_needs_something_to_watch(env):
    _, _, uid, _ = env
    with pytest.raises(ValueError):
        scanner.start(uid)


def test_alert_titles():
    l = flat(1)
    assert scanner._title([("🏢 1 new flat in A", [l])]) == "🏢 1 new flat in A"
    assert scanner._title([("🏢 x", [l]), ("🏢 y", [l])]) == "🏢 Flats available in 2 of your buildings"
    assert scanner._title([("🏢 x", [l]), ("2 new homes near K", [l, l])]) == "3 new listings: 2 near you, 1 in your buildings"
    assert alerts.Alert("t", [("h", [l, l])]).listings == [l, l]

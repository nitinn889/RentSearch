"""A scan: read every site around each watched place, work out what's new, send alerts.

A user watches an *area* (homes inside a circle, with budget and bedroom filters) and any number
of *buildings* (any flat whose society name matches). Sites are read once per region; a building
that lies inside the area circle reuses the area's results.
"""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from . import alerts, config, geo, store
from .buildings import in_building
from .http import FetchError, shared as http_client
from .models import Area, Listing
from .sources import ALL as SOURCES
from .sources.base import Source

log = logging.getLogger("radar.scan")

Key = tuple[str, str]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def place_key(p: dict) -> str:
    return f"{p['lat']:.4f},{p['lon']:.4f},{p['radius_km']:g}"


def _key(l: Listing) -> Key:
    return l.source, l.source_id


def _deals(deal: str) -> list[str]:
    return ["rent", "sale"] if deal == "both" else [deal]


@dataclass
class Region:
    key: str            # "area" or "bldg:<id>"
    label: str
    area: Area
    deals: list[str]
    sources: list[Source]


class Scan:
    def __init__(self, uid: str, trigger: str):
        self.id = uuid.uuid4().hex[:12]
        self.uid = uid
        self.trigger = trigger          # manual | schedule
        self.started = now_iso()
        self.state = "running"          # running | done | failed
        self.stage = "Starting"
        self.sources: dict[str, dict] = {
            s.key: {"name": s.name, "status": "queued", "message": "", "found": 0, "shown": 0,
                    "notes": [], "problems": [], "read_ok": False}
            for s in SOURCES}
        self.result: dict | None = None
        self.error = ""

    def note(self, source: str, message: str) -> None:
        notes = self.sources[source]["notes"]
        if len(notes) < 30:
            notes.append(message)

    def to_dict(self) -> dict:
        return {"id": self.id, "trigger": self.trigger, "started": self.started, "state": self.state,
                "stage": self.stage, "sources": self.sources, "result": self.result, "error": self.error}


_lock = threading.Lock()
_scans: dict[str, Scan] = {}
_active: dict[str, Scan] = {}
_slots = threading.BoundedSemaphore(2)  # scans reading sites at the same time, across all users


def running(uid: str) -> Scan | None:
    scan = _active.get(uid)
    return scan if scan and scan.state == "running" else None


def get(scan_id: str, uid: str) -> Scan | None:
    scan = _scans.get(scan_id)
    return scan if scan and scan.uid == uid else None


def start(uid: str, trigger: str = "manual") -> Scan:
    """Start a scan for `uid` in the background, or return the one already running."""
    settings = store.load_settings(uid)
    if not settings.get("area") and not store.list_buildings(uid):
        raise ValueError("Pick an area or add a building to watch first")
    with _lock:
        if running(uid):
            return _active[uid]
        if trigger == "manual" and config.MANUAL_COOLDOWN_S:
            last = store.last_finished(uid)
            wait = config.MANUAL_COOLDOWN_S - (datetime.now(timezone.utc).timestamp() - _ts(last))
            if last and wait > 0:
                raise ValueError(f"Just checked. You can refresh again in {int(wait) + 1} s")
        scan = Scan(uid, trigger)
        _scans[scan.id] = scan
        _active[uid] = scan
        for old in list(_scans)[:-100]:
            del _scans[old]
    threading.Thread(target=_run, args=(scan,), name=f"scan-{scan.id}", daemon=True).start()
    return scan


def _run(scan: Scan) -> None:
    try:
        if not _slots.acquire(blocking=False):
            scan.stage = "Waiting for other scans to finish"
            _slots.acquire()
        try:
            _scan(scan)
        finally:
            _slots.release()
        scan.state, scan.stage = "done", "Done"
    except Exception as e:
        log.exception("scan %s failed", scan.id)
        scan.state, scan.error, scan.stage = "failed", str(e) or e.__class__.__name__, "Failed"


# ---------------------------------------------------------------- reading sites

def _prepare(place: dict) -> tuple[dict, Area]:
    """Fill in a stored place's city and nearby locality names (needed by locality-based sites)."""
    p = dict(place)
    if not p.get("city"):
        info = geo.describe(p["lat"], p["lon"])
        p["city"] = info["city"]
        p["place"] = p.get("place") or info["place"]
    key = place_key(p)
    if p.get("localities_for") != key:
        p["localities"] = geo.nearby_localities(p["lat"], p["lon"], p["radius_km"], p.get("place", ""))
        p["localities_for"] = key
    return p, Area(lat=p["lat"], lon=p["lon"], radius_km=p["radius_km"], label=p.get("label", ""),
                   place=p.get("place", ""), city=p.get("city", ""), localities=p.get("localities", []))


def _run_source(scan: Scan, source: Source, region: Region, settings: dict) -> tuple[list[Listing], set[str]]:
    status = scan.sources[source.key]
    reason = source.unavailable_reason(region.area, settings)
    if reason:
        if not status["read_ok"] and not status["problems"]:
            status.update(status="skipped", message=reason)
        return [], set()
    status["status"] = "running"
    listings: list[Listing] = []
    feeds: set[str] = set()
    where = "" if region.key == "area" else f" near {region.label}"
    for deal in region.deals:
        status["message"] = f"Reading {deal} listings{where}"
        try:
            found = source.fetch(region.area, deal, http_client, settings, lambda m: scan.note(source.key, m))
        except FetchError as e:
            status["problems"].append({"deal": deal, "where": region.label, "status": e.status, "message": str(e)})
            continue
        except Exception as e:  # a parser bug in one source must not sink the whole scan
            log.exception("%s failed", source.key)
            status["problems"].append({"deal": deal, "where": region.label, "status": "error",
                                       "message": f"unexpected {e.__class__.__name__}: {e}"})
            continue
        default_feed = f"{source.key}:{deal}"
        for listing in found:
            listing.feed = listing.feed or default_feed
        feeds |= {default_feed} | {l.feed for l in found}
        listings += found
    status["found"] += len(listings)
    status["read_ok"] = status["read_ok"] or bool(feeds)
    problems = status["problems"]
    status["status"] = "ok" if status["read_ok"] or not problems else problems[0]["status"]
    status["message"] = "; ".join(
        f"{p['deal']}{'' if p['where'] == 'your area' else ' near ' + p['where']}: {p['message']}" for p in problems)
    return listings, feeds


def _in_area(listings: list[Listing], area: Area) -> list[Listing]:
    locality_sources = {s.key for s in SOURCES if s.locality_based}
    names = [n.lower() for n in [*area.localities, area.place] if n]
    kept = []
    for l in listings:
        if l.lat and l.lon:
            inside = geo.haversine_km(area.lat, area.lon, l.lat, l.lon) <= area.radius_km * 1.05 + 0.05
        elif l.feed.startswith("page:"):
            inside = True  # a page the user chose for this area
        else:
            loc = l.locality.lower()
            inside = l.source in locality_sources and bool(loc) and any(n in loc or loc in n for n in names)
        if inside:
            kept.append(l)
    return kept


def matches_filters(l: Listing, settings: dict) -> bool:
    budget = settings["max_rent"] if l.deal == "rent" else settings["max_price"]
    if budget and l.price and l.price > budget:
        return False
    if settings["min_bedrooms"] and l.bedrooms and l.bedrooms < settings["min_bedrooms"]:
        return False
    return True


def _distance(l: Listing, lat: float, lon: float) -> float | None:
    return round(geo.haversine_km(lat, lon, l.lat, l.lon), 2) if l.lat and l.lon else None


# ---------------------------------------------------------------- one scan

def _scan(scan: Scan) -> None:
    uid = scan.uid
    settings = store.load_settings(uid)
    buildings = store.list_buildings(uid)
    enabled = [s for s in SOURCES if settings["sources"].get(s.key, True)]
    for s in SOURCES:
        if s not in enabled:
            scan.sources[s.key].update(status="off", message="turned off in settings")

    scan.stage = "Working out which localities to search"
    regions: list[Region] = []
    area = None
    area_deals = _deals(settings["deal"])
    if settings["area"]:
        place, area = _prepare(settings["area"])
        if place != settings["area"]:
            store.save_settings(uid, {"area": place})
        regions.append(Region("area", "your area", area, area_deals, enabled))
    region_of: dict[str, str] = {}
    for b in buildings:
        place, b_area = _prepare(b)
        if place != b:
            b.update(place)
            store.save_building(uid, b)
        b_deals = _deals(b["deal"])
        covered = area and set(b_deals) <= set(area_deals) and \
            geo.haversine_km(area.lat, area.lon, b["lat"], b["lon"]) + b["radius_km"] <= area.radius_km
        if covered:
            region_of[b["id"]] = "area"
            continue
        region_of[b["id"]] = f"bldg:{b['id']}"
        regions.append(Region(region_of[b["id"]], b["name"], b_area, b_deals,
                              [s for s in enabled if s.key != "custom"]))  # your pages belong to the area

    pool: dict[Key, Listing] = {}
    feeds_read: dict[str, set[str]] = {}
    for i, region in enumerate(regions, 1):
        scan.stage = "Reading property sites" + (f" ({region.label}, {i} of {len(regions)})" if len(regions) > 1 else "")
        with ThreadPoolExecutor(max_workers=max(1, len(region.sources))) as ex:
            results = list(ex.map(lambda s: _run_source(scan, s, region, settings), region.sources))
        feeds_read[region.key] = set().union(*(feeds for _, feeds in results)) if results else set()
        for listings, _ in results:
            for l in listings:
                pool.setdefault(_key(l), l)

    scan.stage = "Comparing with earlier scans"
    listings = list(pool.values())
    finished = now_iso()
    tags: dict[Key, list[str]] = {}

    in_area, area_matches = [], []
    if area:
        in_area = [l for l in _in_area(listings, area) if l.deal in area_deals]
        area_matches = [l for l in in_area if matches_filters(l, settings)]
        for l in in_area:
            l.distance_km = _distance(l, area.lat, area.lon)
        for l in area_matches:
            tags.setdefault(_key(l), []).append("area")
    by_building: dict[str, list[Listing]] = {}
    for b in buildings:
        by_building[b["id"]] = matched = [l for l in listings if in_building(b, l)]
        for l in matched:
            if "area" not in tags.get(_key(l), []):
                l.distance_km = _distance(l, b["lat"], b["lon"])
            tags.setdefault(_key(l), []).append(f"bldg:{b['id']}")

    tracked = {_key(l): l for l in in_area + [l for m in by_building.values() for l in m]}
    fresh = store.record(uid, list(tracked.values()), finished)

    # The first successful read of a site for an area is its baseline: no alert for what is already there.
    groups: list[tuple[str, list[Listing]]] = []
    alerted: set[Key] = set()
    building_summary = {}
    for b in buildings:
        matched = by_building[b["id"]]
        if not feeds_read.get(region_of[b["id"]]):
            b["status"] = {**b.get("status", {}), "error": "Couldn't read any site for this building this time"}
            store.save_building(uid, b)
            continue
        first = not b.get("status", {}).get("checked_at")
        hits = matched if first else [l for l in matched if _key(l) in fresh]
        if hits:
            n = len(hits)
            heading = (f"🏢 {n} flat{'s' if n != 1 else ''} available now in {b['name']}" if first
                       else f"🏢 {n} new flat{'s' if n != 1 else ''} in {b['name']}")
            groups.append((heading, hits))
            alerted |= {_key(l) for l in hits}
        b["status"] = {"checked_at": finished, "available": len(matched), "error": "",
                       "last_alert_at": finished if hits else b.get("status", {}).get("last_alert_at")}
        store.save_building(uid, b)
        building_summary[b["id"]] = {"name": b["name"], "available": len(matched), "alerted": len(hits), "first": first}

    baseline: set[str] = set()
    if area:
        watch = f"area:{place_key(settings['area'])}"
        baseline = feeds_read["area"] - store.baselined_feeds(uid, watch)
        store.add_baselines(uid, watch, baseline, finished)
        new_area = [l for l in area_matches if _key(l) in fresh and l.feed not in baseline and _key(l) not in alerted]
        if new_area:
            n = len(new_area)
            what = {"rent": " for rent", "sale": " for sale"}.get(settings["deal"], "")
            groups.append((f"{n} new home{'s' if n != 1 else ''}{what} near {area.place or area.label}", new_area))
            alerted |= {_key(l) for l in new_area}

    alert_info = None
    if groups:
        scan.stage = "Sending alerts"
        alert = alerts.Alert(_title(groups), groups)
        channels = alerts.dispatch(alert, settings)
        store.mark_unread(uid, alerted)
        alert_info = {"title": alert.title, "channels": channels,
                      "groups": [{"heading": h, "keys": [list(_key(l)) for l in g]} for h, g in groups]}
        store.log_alert(uid, {"sent_at": finished, "title": alert.title, "channels": channels,
                              "groups": [{"heading": h, "listings": [{"title": l.title, "url": l.url,
                                                                      "price": alerts.price_text(l)} for l in g[:10]]}
                                         for h, g in groups]})

    shown = {**{_key(l): l for l in area_matches}, **{_key(l): l for m in by_building.values() for l in m}}
    seen_at = store.first_seen(uid, set(shown))
    items = [{**l.to_dict(), "tags": tags.get(k, []), "first_seen": seen_at.get(k, finished)} for k, l in shown.items()]
    items.sort(key=lambda d: -_ts(d["posted_at"] or d["first_seen"]))
    for item in items:
        owner = "custom" if item["feed"].startswith("page:") else item["source"]
        scan.sources[owner]["shown"] += 1

    summary = {
        "finished": finished,
        "trigger": scan.trigger,
        "area": None if not area else {"lat": area.lat, "lon": area.lon, "radius_km": area.radius_km,
                                       "label": area.label, "place": area.place, "city": area.city,
                                       "localities": area.localities},
        "deals": area_deals,
        "found": len(listings),
        "in_area": len(in_area),
        "filtered_out": len(in_area) - len(area_matches),
        "new": len(alerted),
        "baseline": sorted(baseline),
        "buildings": building_summary,
        "alert": alert_info,
        "sources": scan.sources,
    }
    store.save_scan(uid, scan.id, scan.started, finished, summary, items)
    scan.result = store.last_results(uid)


def _title(groups: list[tuple[str, list[Listing]]]) -> str:
    """One line for the whole alert; the area group, when present, is always last."""
    building_groups = [g for h, g in groups if h.startswith("🏢")]
    if len(groups) == 1:
        return groups[0][0]
    if len(building_groups) == len(groups):
        return f"🏢 Flats available in {len(groups)} of your buildings"
    near_you = len(groups[-1][1])
    in_buildings = sum(map(len, building_groups))
    return f"{near_you + in_buildings} new listings: {near_you} near you, {in_buildings} in your buildings"

"""Geocoding via OpenStreetMap Nominatim, plus distance helpers."""
from __future__ import annotations

import math
import threading

from .http import APP_UA, FetchError, PoliteClient

NOMINATIM = "https://nominatim.openstreetmap.org"
_HEADERS = {"User-Agent": APP_UA}
_client = PoliteClient(min_interval=1.1, timeout=20)  # Nominatim's usage policy: at most 1 request/second
_reverse_cache: dict[tuple, dict] = {}
_cache_lock = threading.Lock()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def offset(lat: float, lon: float, km: float, bearing_deg: float) -> tuple[float, float]:
    """The point `km` away from (lat, lon) along a compass bearing."""
    d, b = km / 6371.0, math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def _city(a: dict) -> str:
    return (a.get("city") or a.get("town") or a.get("municipality") or a.get("state_district")
            or a.get("county") or "")


def _locality(a: dict) -> str:
    return (a.get("suburb") or a.get("neighbourhood") or a.get("quarter") or a.get("village")
            or a.get("city_district") or "")


def _place(x: dict) -> dict:
    a = x.get("address") or {}
    return {
        "label": x.get("display_name") or "",
        "lat": float(x["lat"]),
        "lon": float(x["lon"]),
        "place": x.get("name") or _locality(a) or _city(a),
        "locality": _locality(a),
        "city": _city(a),
        "country_code": a.get("country_code", ""),
    }


def search(query: str, limit: int = 6) -> list[dict]:
    r = _client.get(f"{NOMINATIM}/search", headers=_HEADERS, respect_robots=False,
                    params={"q": query, "format": "jsonv2", "addressdetails": 1, "limit": limit})
    return [_place(x) for x in r.json()]


def reverse(lat: float, lon: float, zoom: int = 16) -> dict:
    key = (round(lat, 4), round(lon, 4), zoom)
    with _cache_lock:
        if key in _reverse_cache:
            return _reverse_cache[key]
    r = _client.get(f"{NOMINATIM}/reverse", headers=_HEADERS, respect_robots=False,
                    params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": zoom, "addressdetails": 1})
    data = r.json()
    with _cache_lock:
        _reverse_cache[key] = data
    return data


def describe(lat: float, lon: float) -> dict:
    """Label / place / city for a point the user clicked on the map."""
    data = reverse(lat, lon)
    if "address" not in data:
        return {"label": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon, "place": "", "city": "", "country_code": ""}
    a = data["address"]
    place = _locality(a) or _city(a)
    return {"label": ", ".join(p for p in (place, _city(a), a.get("state")) if p), "lat": lat, "lon": lon,
            "place": place, "city": _city(a), "country_code": a.get("country_code", "")}


def nearby_localities(lat: float, lon: float, radius_km: float, primary: str, limit: int = 5) -> list[str]:
    """Locality names that cover the search circle, for sites that can only search by locality."""
    names = [primary] if primary else []
    points = [(lat, lon)]
    if radius_km > 1:
        points += [offset(lat, lon, radius_km * 0.6, bearing) for bearing in range(0, 360, 60)]
    for plat, plon in points:
        if len(names) >= limit:
            break
        try:
            a = reverse(plat, plon).get("address") or {}
        except FetchError:
            continue
        for name in (a.get("suburb"), a.get("neighbourhood")):
            if name and name.lower() not in {n.lower() for n in names}:
                names.append(name)
                break
    return names[:limit]

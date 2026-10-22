"""Everything persisted: users, their settings and building watches, listing history, scans, alerts."""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from . import config
from .db import Database
from .models import Listing

SECRET_FIELDS = ("telegram_token", "smtp_password")
MASK = "••••••••"   # what the page sees instead of a saved secret

DEFAULT_SETTINGS = {
    "area": None,           # {lat, lon, radius_km, label, place, city, localities, localities_for}
    "deal": "rent",         # rent | sale | both
    "max_rent": None,
    "max_price": None,
    "min_bedrooms": None,
    "sources": {"nobroker": True, "magicbricks": True, "squareyards": True, "custom": True},
    "custom_pages": [],     # [{url, deal}]
    "schedule_min": 0,      # automatic checks by the server; 0 = only when you press Refresh
    "alerts": {
        "sound": True,
        "browser": True,
        "windows_toast": True,
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "",
        "telegram_token": "",
        "telegram_chat_id": "",
        "email_to": "",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_user": "",
        "smtp_password": "",
    },
}

_db: Database | None = None


def db() -> Database:
    global _db
    if _db is None:
        _db = Database(config.DATABASE_URL, config.DATA_DIR / "rent-radar.sqlite3")
    return _db


def use(database: Database) -> None:
    """Point the store at another database (tests)."""
    global _db
    _db = database


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------- users

def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def create_user(email: str, password: str) -> str:
    email = normalise_email(email)
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 200:
        raise ValueError("Enter a valid email address")
    if len(password or "") < 8:
        raise ValueError("Use a password of at least 8 characters")
    uid = _new_id()
    with db().tx() as conn:
        if conn.one("SELECT 1 FROM users WHERE email = ?", (email,)):
            raise ValueError("An account with this email already exists; sign in instead")
        conn.run("INSERT INTO users VALUES (?, ?, ?, ?)", (uid, email, generate_password_hash(password), now_iso()))
    return uid


def check_login(email: str, password: str) -> str | None:
    with db().tx() as conn:
        row = conn.one("SELECT id, pw_hash FROM users WHERE email = ?", (normalise_email(email),))
    return row[0] if row and check_password_hash(row[1], password or "") else None


def get_user(uid: str) -> dict | None:
    with db().tx() as conn:
        row = conn.one("SELECT id, email, created FROM users WHERE id = ?", (uid,))
    return {"id": row[0], "email": row[1], "created": row[2]} if row else None


def ensure_local_user() -> None:
    with db().tx() as conn:
        conn.run("INSERT INTO users VALUES (?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
                 (config.LOCAL_USER, "local", "-", now_iso()))


# ---------------------------------------------------------------- settings

def load_settings(uid: str) -> dict:
    with db().tx() as conn:
        row = conn.one("SELECT data FROM settings WHERE user_id = ?", (uid,))
    return _merge(copy.deepcopy(DEFAULT_SETTINGS), json.loads(row[0]) if row else {})


def save_settings(uid: str, patch: dict) -> dict:
    clean = _validate(patch)
    with db().tx() as conn:
        row = conn.one("SELECT data FROM settings WHERE user_id = ?", (uid,))
        merged = _merge(_merge(copy.deepcopy(DEFAULT_SETTINGS), json.loads(row[0]) if row else {}), clean)
        conn.run("INSERT INTO settings VALUES (?, ?) ON CONFLICT (user_id) DO UPDATE SET data = excluded.data",
                 (uid, json.dumps(merged, ensure_ascii=False)))
    return merged


def public_settings(settings: dict) -> dict:
    """Settings as sent to the page: saved secrets are masked, never echoed back."""
    out = copy.deepcopy(settings)
    for key in SECRET_FIELDS:
        if out["alerts"].get(key):
            out["alerts"][key] = MASK
    return out


def _merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if key not in base:
            continue
        if isinstance(base[key], dict) and isinstance(value, dict) and key != "area":
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _number(value, lo: float, hi: float, name: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
    return value


def _place(a: dict, radius_lo: float, radius_hi: float) -> dict:
    if not isinstance(a, dict):
        raise ValueError("location must be an object")
    out = {
        "lat": _number(a.get("lat"), -90, 90, "latitude"),
        "lon": _number(a.get("lon"), -180, 180, "longitude"),
        "radius_km": _number(a.get("radius_km"), radius_lo, radius_hi, "radius"),
        "label": str(a.get("label") or "")[:200],
        "place": str(a.get("place") or "")[:100],
        "city": str(a.get("city") or "")[:100],
        "localities": [str(x)[:100] for x in a.get("localities") or []][:10],
        "localities_for": str(a.get("localities_for") or ""),
    }
    if out["lat"] is None or out["lon"] is None or out["radius_km"] is None:
        raise ValueError("a location needs lat, lon and radius_km")
    return out


def _validate(patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise ValueError("settings must be an object")
    out: dict = {}
    if "area" in patch:
        out["area"] = None if patch["area"] is None else _place(patch["area"], 0.2, 50)
    if "deal" in patch:
        if patch["deal"] not in ("rent", "sale", "both"):
            raise ValueError("deal must be rent, sale or both")
        out["deal"] = patch["deal"]
    for key, hi in (("max_rent", 1e8), ("max_price", 1e11), ("min_bedrooms", 20)):
        if key in patch:
            out[key] = _number(patch[key], 0, hi, key)
    if "sources" in patch:
        out["sources"] = {str(k): bool(v) for k, v in dict(patch["sources"]).items()}
    if "custom_pages" in patch:
        pages = []
        for p in patch["custom_pages"] or []:
            url = str(p.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"not a web address: {url!r}")
            pages.append({"url": url, "deal": "sale" if p.get("deal") == "sale" else "rent"})
        out["custom_pages"] = pages[:10]
    if "schedule_min" in patch:
        minutes = int(_number(patch["schedule_min"], 0, 1440, "check interval") or 0)
        out["schedule_min"] = max(minutes, config.MIN_SCHEDULE_MIN) if minutes else 0
    if "alerts" in patch:
        alerts = {}
        for key, default in DEFAULT_SETTINGS["alerts"].items():
            if key not in patch["alerts"]:
                continue
            value = patch["alerts"][key]
            if key in SECRET_FIELDS and value == MASK:
                continue  # the page sent the mask back unchanged: keep the saved secret
            if isinstance(default, bool):
                alerts[key] = bool(value)
            elif isinstance(default, int):
                alerts[key] = int(_number(value, 1, 65535, key) or default)
            else:
                alerts[key] = str(value or "").strip()[:300]
        out["alerts"] = alerts
    return out


# ---------------------------------------------------------------- building watches

def list_buildings(uid: str) -> list[dict]:
    with db().tx() as conn:
        rows = conn.all("SELECT data FROM buildings WHERE user_id = ? ORDER BY created", (uid,))
    return [json.loads(r[0]) for r in rows]


def add_building(uid: str, data: dict) -> dict:
    name = str(data.get("name") or "").strip()[:120]
    if len(name) < 3:
        raise ValueError("Enter the building or society name")
    place = _place({**data, "radius_km": data.get("radius_km") or 1}, 0.2, 5)
    deal = data.get("deal") if data.get("deal") in ("rent", "sale", "both") else "rent"
    building = {"id": _new_id(), "name": name, "deal": deal, **place, "status": {}}
    with db().tx() as conn:
        (count,) = conn.one("SELECT COUNT(*) FROM buildings WHERE user_id = ?", (uid,))
        if count >= config.MAX_BUILDINGS:
            raise ValueError(f"You can watch up to {config.MAX_BUILDINGS} buildings")
        conn.run("INSERT INTO buildings VALUES (?, ?, ?, ?)",
                 (building["id"], uid, json.dumps(building, ensure_ascii=False), now_iso()))
    return building


def save_building(uid: str, building: dict) -> None:
    with db().tx() as conn:
        conn.run("UPDATE buildings SET data = ? WHERE id = ? AND user_id = ?",
                 (json.dumps(building, ensure_ascii=False), building["id"], uid))


def delete_building(uid: str, building_id: str) -> bool:
    with db().tx() as conn:
        conn.run("DELETE FROM baselines WHERE user_id = ? AND watch = ?", (uid, f"bldg:{building_id}"))
        return conn.run("DELETE FROM buildings WHERE id = ? AND user_id = ?", (building_id, uid)) > 0


# ---------------------------------------------------------------- listing history

def record(uid: str, listings: list[Listing], now: str) -> set[tuple[str, str]]:
    """Save listings and mark them seen by `uid`; return the keys this user had never seen before."""
    unique = {(l.source, l.source_id): l for l in listings}
    with db().tx() as conn:
        conn.many("INSERT INTO listings VALUES (?, ?, ?, ?) ON CONFLICT (source, source_id) "
                  "DO UPDATE SET data = excluded.data, updated = excluded.updated",
                  [(s, i, json.dumps(l.to_dict(), ensure_ascii=False), now) for (s, i), l in unique.items()])
        known = {(r[0], r[1]) for r in conn.all("SELECT source, source_id FROM seen WHERE user_id = ?", (uid,))}
        fresh = set(unique) - known
        conn.many("UPDATE seen SET last_seen = ? WHERE user_id = ? AND source = ? AND source_id = ?",
                  [(now, uid, s, i) for s, i in unique.keys() & known])
        conn.many("INSERT INTO seen VALUES (?, ?, ?, ?, ?, 0)", [(uid, s, i, now, now) for s, i in fresh])
    return fresh


def first_seen(uid: str, keys: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    with db().tx() as conn:
        rows = conn.all("SELECT source, source_id, first_seen FROM seen WHERE user_id = ?", (uid,))
    return {(s, i): t for s, i, t in rows if (s, i) in keys}


def mark_unread(uid: str, keys: set[tuple[str, str]]) -> None:
    with db().tx() as conn:
        conn.many("UPDATE seen SET unread = 1 WHERE user_id = ? AND source = ? AND source_id = ?",
                  [(uid, s, i) for s, i in keys])


def mark_all_read(uid: str) -> int:
    with db().tx() as conn:
        return conn.run("UPDATE seen SET unread = 0 WHERE user_id = ? AND unread = 1", (uid,))


def baselined_feeds(uid: str, watch: str) -> set[str]:
    with db().tx() as conn:
        return {r[0] for r in conn.all("SELECT feed FROM baselines WHERE user_id = ? AND watch = ?", (uid, watch))}


def add_baselines(uid: str, watch: str, feeds: set[str], now: str) -> None:
    with db().tx() as conn:
        conn.many("INSERT INTO baselines VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                  [(uid, watch, f, now) for f in feeds])


# ---------------------------------------------------------------- scans and alerts

def save_scan(uid: str, scan_id: str, started: str, finished: str, summary: dict, items: list[dict]) -> None:
    with db().tx() as conn:
        conn.run("INSERT INTO scans VALUES (?, ?, ?, ?, ?, ?)",
                 (scan_id, uid, started, finished, json.dumps(summary, ensure_ascii=False),
                  json.dumps(items, ensure_ascii=False)))
        keep = [r[0] for r in conn.all("SELECT id FROM scans WHERE user_id = ? ORDER BY finished DESC LIMIT 5", (uid,))]
        conn.many("DELETE FROM scans WHERE id = ?",
                  [(r[0],) for r in conn.all("SELECT id FROM scans WHERE user_id = ?", (uid,)) if r[0] not in keep])


def last_results(uid: str) -> dict | None:
    """The latest scan's summary and listings, with each listing's current read/unread state."""
    with db().tx() as conn:
        row = conn.one("SELECT id, summary, items FROM scans WHERE user_id = ? ORDER BY finished DESC LIMIT 1", (uid,))
        if not row:
            return None
        unread = {(r[0], r[1]) for r in conn.all("SELECT source, source_id FROM seen WHERE user_id = ? AND unread = 1",
                                                  (uid,))}
    items = json.loads(row[2])
    for item in items:
        item["unread"] = (item["source"], item["source_id"]) in unread
    return {"scan_id": row[0], "summary": json.loads(row[1]), "listings": items}


def last_finished(uid: str) -> str | None:
    with db().tx() as conn:
        row = conn.one("SELECT MAX(finished) FROM scans WHERE user_id = ?", (uid,))
    return row[0] if row else None


def scheduled_users() -> list[tuple[str, int]]:
    """(user id, interval in minutes) for everyone who turned on automatic checks."""
    with db().tx() as conn:
        rows = conn.all("SELECT user_id, data FROM settings")
    out = []
    for uid, data in rows:
        minutes = int(json.loads(data).get("schedule_min") or 0)
        if minutes:
            out.append((uid, minutes))
    return out


def log_alert(uid: str, entry: dict) -> None:
    with db().tx() as conn:
        conn.run("INSERT INTO alert_log VALUES (?, ?, ?, ?)",
                 (_new_id(), uid, entry["sent_at"], json.dumps(entry, ensure_ascii=False)))


def recent_alerts(uid: str, limit: int = 20) -> list[dict]:
    with db().tx() as conn:
        rows = conn.all("SELECT data FROM alert_log WHERE user_id = ? ORDER BY sent_at DESC LIMIT ?", (uid, limit))
    return [json.loads(r[0]) for r in rows]

"""The web app: pages, JSON API, sign-in (hosted mode only)."""
from __future__ import annotations

import hmac
import logging
import time
from collections import defaultdict, deque
from datetime import timedelta

from flask import Flask, g, jsonify, redirect, request, send_from_directory, session

from . import alerts, config, geo, scanner, scheduler, store
from .http import FetchError
from .sources import ALL as SOURCES

STATIC = config.ROOT / "static"
OPEN_PATHS = {"/login", "/healthz", "/manifest.webmanifest", "/api/auth/info", "/api/auth/login",
              "/api/auth/signup", "/api/cron"}
_LOOPBACK = {"127.0.0.1", "localhost"}
_attempts: dict[str, deque] = defaultdict(deque)


def _too_many_attempts(ip: str, limit: int = 10, window_s: int = 600) -> bool:
    q, now = _attempts[ip], time.monotonic()
    while q and now - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _err(message: str, status: int = 400):
    return jsonify({"error": message}), status


def create_app() -> Flask:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app = Flask(__name__, static_folder=None)
    app.secret_key = config.secret_key()
    app.json.sort_keys = False  # keep sources in their configured order
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.PUBLIC_URL.startswith("https://"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=256 * 1024,
    )
    store.db()
    if not config.SERVER:
        store.ensure_local_user()
    if config.SCHEDULER:
        scheduler.start_background()

    @app.before_request
    def guard():
        # On a PC the app must only answer to itself, not to pages that point a hostname at 127.0.0.1.
        if not config.SERVER and config.HOST in ("127.0.0.1", "localhost") and request.host.split(":")[0] not in _LOOPBACK:
            return _err("forbidden host", 403)
        if request.path in OPEN_PATHS or request.path.startswith("/static/"):
            return None
        uid = config.LOCAL_USER if not config.SERVER else session.get("uid")
        if uid and config.SERVER and not store.get_user(uid):
            session.clear()
            uid = None
        if not uid:
            return _err("Please sign in", 401) if request.path.startswith("/api/") else redirect("/login")
        g.uid = uid
        # Changes must come as JSON: a cross-site form can't send that without the browser asking first.
        if request.method != "GET" and not request.is_json:
            return _err("send JSON", 415)
        return None

    # ------------------------------------------------------------ pages

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/login")
    def login_page():
        if not config.SERVER or session.get("uid"):
            return redirect("/")
        return send_from_directory(STATIC, "login.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(STATIC, name)

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(STATIC, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "database": store.db().kind})

    # ------------------------------------------------------------ accounts

    @app.get("/api/auth/info")
    def auth_info():
        return jsonify({"server": config.SERVER, "invite_code": bool(config.SIGNUP_CODE)})

    @app.post("/api/auth/signup")
    def signup():
        body = request.get_json(silent=True) or {}
        if _too_many_attempts(request.remote_addr or "?"):
            return _err("Too many attempts. Try again in a few minutes.", 429)
        if config.SIGNUP_CODE and not hmac.compare_digest(str(body.get("code") or ""), config.SIGNUP_CODE):
            return _err("That invite code isn't right", 403)
        try:
            uid = store.create_user(body.get("email", ""), body.get("password", ""))
        except ValueError as e:
            return _err(str(e))
        session.clear()
        session.permanent = True
        session["uid"] = uid
        return jsonify({"ok": True})

    @app.post("/api/auth/login")
    def login():
        body = request.get_json(silent=True) or {}
        if _too_many_attempts(request.remote_addr or "?"):
            return _err("Too many attempts. Try again in a few minutes.", 429)
        uid = store.check_login(body.get("email", ""), body.get("password", ""))
        if not uid:
            return _err("Wrong email or password", 401)
        session.clear()
        session.permanent = True
        session["uid"] = uid
        return jsonify({"ok": True})

    @app.post("/api/auth/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    # ------------------------------------------------------------ app state

    def app_info() -> dict:
        user = store.get_user(g.uid)
        return {
            "server": config.SERVER,
            "email": user["email"] if config.SERVER and user else None,
            "windows_toast": config.WINDOWS_TOAST,
            "persistent": config.persistent_storage(),
            "min_schedule": config.MIN_SCHEDULE_MIN,
            "max_buildings": config.MAX_BUILDINGS,
        }

    def results_bundle() -> dict:
        scan = scanner.running(g.uid)
        return {
            "results": store.last_results(g.uid),
            "buildings": store.list_buildings(g.uid),
            "alert_log": store.recent_alerts(g.uid),
            "running_scan": scan.id if scan else None,
        }

    @app.get("/api/state")
    def state():
        return jsonify({
            "app": app_info(),
            "settings": store.public_settings(store.load_settings(g.uid)),
            "sources": [{"key": s.key, "name": s.name, "site": s.site} for s in SOURCES],
            **results_bundle(),
        })

    @app.get("/api/updates")
    def updates():
        """Polled by the page: the newest results, so scheduled checks show up without a reload."""
        return jsonify(results_bundle())

    @app.post("/api/settings")
    def save_settings():
        try:
            return jsonify(store.public_settings(store.save_settings(g.uid, request.get_json())))
        except (ValueError, TypeError, AttributeError) as e:
            return _err(str(e))

    @app.get("/api/geocode")
    def geocode():
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify([])
        try:
            return jsonify(geo.search(q))
        except FetchError as e:
            return _err(f"Location search failed: {e}", 502)

    @app.get("/api/reverse")
    def reverse():
        try:
            return jsonify(geo.describe(float(request.args["lat"]), float(request.args["lon"])))
        except (KeyError, ValueError):
            return _err("lat and lon are required")
        except FetchError as e:
            return _err(f"Location lookup failed: {e}", 502)

    # ------------------------------------------------------------ scans, buildings, alerts

    @app.post("/api/scan")
    def start_scan():
        try:
            return jsonify(scanner.start(g.uid).to_dict())
        except ValueError as e:
            return _err(str(e), 429 if "refresh again" in str(e) else 400)

    @app.get("/api/scan/<scan_id>")
    def scan_status(scan_id):
        scan = scanner.get(scan_id, g.uid)
        return jsonify(scan.to_dict()) if scan else _err("unknown scan", 404)

    @app.post("/api/read")
    def mark_read():
        return jsonify({"marked": store.mark_all_read(g.uid)})

    @app.post("/api/buildings")
    def add_building():
        try:
            return jsonify(store.add_building(g.uid, request.get_json()))
        except (ValueError, TypeError, AttributeError) as e:
            return _err(str(e))

    @app.delete("/api/buildings/<building_id>")
    def delete_building(building_id):
        return jsonify({"deleted": store.delete_building(g.uid, building_id)})

    @app.post("/api/alerts/test")
    def test_alert():
        channel = (request.get_json() or {}).get("channel")
        if channel not in alerts.CHANNELS:
            return _err("unknown channel")
        try:
            alerts.test(channel, store.load_settings(g.uid))
        except Exception as e:
            return _err(str(e), 502)
        return jsonify({"ok": True})

    @app.route("/api/cron", methods=["GET", "POST"])
    def cron():
        """Starts every due automatic check; for an outside timer on hosts that sleep when idle."""
        if not config.CRON_SECRET:
            return _err("set CRON_SECRET on the server to use this", 404)
        given = request.headers.get("X-Cron-Secret") or request.args.get("key") or ""
        if not hmac.compare_digest(given, config.CRON_SECRET):
            return _err("forbidden", 403)
        return jsonify({"started": len(scheduler.tick())})

    return app

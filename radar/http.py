"""Polite HTTP client shared by every source.

Every request honours the site's robots.txt, waits between hits to the same host,
and turns bot-wall responses into a clear "blocked" status instead of trying to
get around them.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
APP_UA = "RentRadar/1.0 (personal apartment alert tool)"
ROBOTS_AGENT = "rentradar"

# Small pages carrying these phrases are bot walls, not listing pages.
_BLOCK_MARKERS = re.compile(
    r"(?i)access denied|security alert|captcha|are you a robot|unusual traffic|"
    r"request blocked|we'll be back soon|attention required")


class FetchError(Exception):
    status = "error"


class Blocked(FetchError):
    status = "blocked"


class Disallowed(FetchError):
    status = "disallowed"


class Robots:
    """robots.txt matcher with `*` / `$` wildcards; the longest matching rule wins."""

    def __init__(self, text: str, agent: str = ROBOTS_AGENT):
        groups: list[tuple[set[str], list[tuple[bool, str]]]] = []
        agents: set[str] = set()
        rules: list[tuple[bool, str]] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            field, value = (part.strip() for part in line.split(":", 1))
            field = field.lower()
            if field == "user-agent":
                if in_rules:
                    groups.append((agents, rules))
                    agents, rules, in_rules = set(), [], False
                agents.add(value.lower())
            elif field in ("allow", "disallow"):
                in_rules = True
                if value:
                    rules.append((field == "allow", value))
        if agents:
            groups.append((agents, rules))
        specific = [r for names, r in groups if any(n != "*" and n in agent for n in names)]
        generic = [r for names, r in groups if "*" in names]
        self.rules = [rule for group in (specific or generic) for rule in group]

    def allows(self, path: str) -> bool:
        best, allowed = -1, True
        for allow, pattern in self.rules:
            if _robots_match(pattern, path) and (len(pattern) > best or (len(pattern) == best and allow)):
                best, allowed = len(pattern), allow
        return allowed


def _robots_match(pattern: str, path: str) -> bool:
    regex = re.escape(pattern).replace(r"\*", ".*")
    if regex.endswith(r"\$"):
        regex = regex[:-2] + "$"
    return re.match(regex, path) is not None


@dataclass
class Page:
    url: str          # final URL after redirects
    status_code: int
    text: str

    def json(self):
        return json.loads(self.text)


ROBOTS_TTL = 12 * 3600


class PoliteClient:
    """One per process, shared by every scan and every user, so the per-site delay holds globally."""

    def __init__(self, min_interval: float = 1.5, timeout: float = 30, cache_ttl: float = 300):
        self.min_interval = min_interval
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._local = threading.local()  # requests.Session isn't thread-safe; one per thread
        self._robots: dict[str, tuple[float, Robots]] = {}
        self._cache: dict[str, tuple[float, Page]] = {}
        self._next_slot: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers.update({
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
            })
            self._local.session = s
        return self._local.session

    def _robots_for(self, origin: str) -> Robots:
        with self._lock:
            cached = self._robots.get(origin)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            r = self.session.get(origin + "/robots.txt", timeout=self.timeout)
            text = r.text if r.status_code < 400 else ""
        except requests.RequestException:
            text = ""
        robots = Robots(text)
        with self._lock:
            self._robots[origin] = (time.monotonic() + ROBOTS_TTL, robots)
        return robots

    def _throttle(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot.get(host, 0.0))
            self._next_slot[host] = slot + self.min_interval
        if slot > now:
            time.sleep(slot - now)

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None,
            respect_robots: bool = True, cache: bool = True) -> Page:
        url = requests.Request("GET", url, params=params).prepare().url
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(url) if cache else None
            if len(self._cache) > 500:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        if hit and hit[0] > now:
            return hit[1]
        parts = urlsplit(url)
        host = parts.netloc
        if respect_robots:
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            if not self._robots_for(f"{parts.scheme}://{host}").allows(path):
                raise Disallowed(f"{host}'s robots.txt does not allow automated access to this page")
        self._throttle(host)
        try:
            r = self.session.get(url, headers=headers, timeout=self.timeout)
        except requests.Timeout:
            raise FetchError(f"{host} did not respond within {self.timeout:.0f}s") from None
        except requests.RequestException as e:
            raise FetchError(f"could not reach {host} ({e.__class__.__name__})") from None
        marker = _BLOCK_MARKERS.search(r.text) if len(r.content) < 20_000 else None
        if marker:
            raise Blocked(f"{host} served a \"{marker.group()}\" page instead of listings")
        if r.status_code in (401, 403, 406, 429):
            raise Blocked(f"{host} refused automated access (HTTP {r.status_code})")
        if r.status_code >= 400:
            raise FetchError(f"{host} returned HTTP {r.status_code}")
        page = Page(r.url, r.status_code, r.text)
        if cache:
            with self._lock:
                self._cache[url] = (time.monotonic() + self.cache_ttl, page)
        return page


shared = PoliteClient()


"""Automatic checks: the server scans each user's watches on their chosen interval.

Runs in a background thread while the app is up. Free hosts that sleep when idle can call
/api/cron from an outside timer (see .github/workflows/scheduled-checks.yml) to wake it.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from . import scanner, store

log = logging.getLogger("radar.scheduler")
_started = False
_start_lock = threading.Lock()
_last_attempt: dict[str, float] = {}  # so a scan that keeps failing is not retried every minute
TICK_S = 60


def due_users(now: float | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc).timestamp()
    due = []
    for uid, minutes in store.scheduled_users():
        if scanner.running(uid):
            continue
        last = max(scanner._ts(store.last_finished(uid)), _last_attempt.get(uid, 0.0))
        if now - last >= minutes * 60 - 30:
            due.append(uid)
    return due


def tick() -> list[str]:
    """Start scans for every user whose automatic check is due; returns their ids."""
    started = []
    for uid in due_users():
        _last_attempt[uid] = datetime.now(timezone.utc).timestamp()
        try:
            scanner.start(uid, trigger="schedule")
            started.append(uid)
        except ValueError:
            pass  # nothing to watch yet
        except Exception:
            log.exception("could not start a scheduled scan for %s", uid)
    return started


def _loop() -> None:
    while True:
        time.sleep(TICK_S)
        try:
            tick()
        except Exception:
            log.exception("scheduler tick failed")


def start_background() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="scheduler", daemon=True).start()

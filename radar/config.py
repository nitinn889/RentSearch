"""Runtime configuration, read from environment variables.

Local (the default): one user, no sign-in, listens on 127.0.0.1, data in ./data.
Server (RENT_RADAR_MODE=server, set by the Dockerfile and render.yaml): accounts and sign-in.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RENT_RADAR_DATA") or ROOT / "data")

SERVER = os.environ.get("RENT_RADAR_MODE", "local").strip().lower() == "server"
HOST = os.environ.get("HOST") or ("0.0.0.0" if SERVER else "127.0.0.1")
PORT = int(os.environ.get("PORT") or 8750)
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
              or f"http://127.0.0.1:{PORT}").rstrip("/")

# Empty: SQLite file in DATA_DIR. "postgres://…": that database (e.g. a free Neon project).
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SIGNUP_CODE = os.environ.get("SIGNUP_CODE", "").strip()     # if set, needed to create an account
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()     # lets an outside timer trigger due checks
SCHEDULER = os.environ.get("RENT_RADAR_SCHEDULER", "1") != "0"

LOCAL_USER = "local"
# Shortest automatic-check interval; a hosted instance serves many users and sites must not be hammered.
MIN_SCHEDULE_MIN = 15 if SERVER else 5
# Shortest gap between two manual Refreshes by one user on a hosted instance.
MANUAL_COOLDOWN_S = 60 if SERVER else 0
MAX_BUILDINGS = 10
WINDOWS_TOAST = sys.platform == "win32" and not SERVER


def persistent_storage() -> bool:
    """False when data lives on a disk the host wipes on restart (hosted without a database)."""
    return bool(DATABASE_URL) or not SERVER or os.environ.get("RENT_RADAR_PERSISTENT_DISK") == "1"


def secret_key() -> str:
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    path = DATA_DIR / "secret_key"
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        key = secrets.token_hex(32)
        path.write_text(key)
        return key

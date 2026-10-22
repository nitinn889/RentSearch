import os
import sys
import tempfile
from pathlib import Path

# Configure before anything imports radar.config.
os.environ.setdefault("RENT_RADAR_SCHEDULER", "0")
os.environ.setdefault("RENT_RADAR_DATA", tempfile.mkdtemp(prefix="rent-radar-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from radar import store  # noqa: E402
from radar.db import Database  # noqa: E402

TABLES = ["alert_log", "scans", "buildings", "baselines", "seen", "listings", "settings", "users"]


@pytest.fixture
def database(tmp_path):
    """A clean database per test: Postgres if TEST_DATABASE_URL is set (CI), else a temp SQLite file."""
    url = os.environ.get("TEST_DATABASE_URL", "")
    db = Database(url, tmp_path / "test.sqlite3")
    if db.postgres:
        with db.tx() as conn:
            for table in TABLES:
                conn.run(f"DELETE FROM {table}")
    store.use(db)
    yield db
    db.close()

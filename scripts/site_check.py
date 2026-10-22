"""Live check: can each site be read from this machine? Prints a table (and a GitHub job summary).

    python scripts/site_check.py            # Koramangala, Bangalore, 2 km

Useful before hosting: some sites treat cloud servers differently from home connections.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import geo  # noqa: E402
from radar.http import FetchError, shared  # noqa: E402
from radar.models import Area  # noqa: E402
from radar.sources import ALL  # noqa: E402

LAT, LON, RADIUS = 12.9352, 77.6245, 2.0


def main() -> int:
    localities = geo.nearby_localities(LAT, LON, RADIUS, "Koramangala")
    area = Area(lat=LAT, lon=LON, radius_km=RADIUS, label="Koramangala, Bengaluru", place="Koramangala",
                city="Bengaluru", localities=localities)
    rows, working = [], 0
    for source in ALL:
        if source.key == "custom":
            continue
        for deal in ("rent", "sale"):
            t = time.time()
            try:
                found = source.fetch(area, deal, shared, {}, lambda m: None)
                near = sum(1 for l in found if l.lat and geo.haversine_km(LAT, LON, l.lat, l.lon) <= RADIUS)
                result, working = f"ok: {len(found)} listings, {near} within {RADIUS:g} km", working + 1
            except FetchError as e:
                result = f"{e.status}: {e}"
            rows.append((source.name, deal, result, f"{time.time() - t:.1f}s"))
    table = "| Site | Deal | Result | Time |\n|---|---|---|---|\n" + "\n".join(f"| {' | '.join(r)} |" for r in rows)
    print(table)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### Site check from this runner\n\nLocalities: {', '.join(localities)}\n\n{table}\n")
    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main())

"""Rent Radar.

On your PC: `python app.py` (or double-click start.bat); the page opens in your browser.
Hosted: `gunicorn app:app` with RENT_RADAR_MODE=server (see render.yaml / Dockerfile).
"""
from __future__ import annotations

import sys
import threading
import webbrowser

from radar import config
from radar.web import create_app

app = create_app()


def main() -> None:
    # Windows consoles default to a legacy code page; never crash on a ₹ in a log line.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    import logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    url = f"http://127.0.0.1:{config.PORT}" if not config.SERVER else config.PUBLIC_URL
    print(f"\n  Rent Radar is running at {url}\n  Keep this window open; close it to stop the app.\n")
    if not config.SERVER and "--no-browser" not in sys.argv:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host=config.HOST, port=config.PORT, threaded=True)


if __name__ == "__main__":
    main()

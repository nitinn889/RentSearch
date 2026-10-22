# Rent Radar

Get alerted the moment a flat comes up **for rent or sale**: anywhere inside the area you choose,
or in the **exact apartment building** you want to live in.

Rent Radar checks NoBroker, MagicBricks, SquareYards and any results pages you add, remembers
everything it has already seen, and tells you only about what's new: by phone push, Telegram,
email, Windows or browser notification.

[![CI](https://github.com/nitinn889/RentSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/nitinn889/RentSearch/actions/workflows/ci.yml)
[![Site check](https://github.com/nitinn889/RentSearch/actions/workflows/site-check.yml/badge.svg)](https://github.com/nitinn889/RentSearch/actions/workflows/site-check.yml)

## What it does

- **Area watch**: pick a point (search, GPS or click the map) and a radius from 0.5 to 20 km.
  Choose rent, sale or both, with an optional budget and minimum BHK.
- **Building watch**: name a society, e.g. *Prestige Lakeside Habitat*, and get alerted whenever
  any flat in it is listed. The first check tells you what's available right now. Watch up to 10 buildings,
  inside or outside your area.
- **Refresh** checks every site on demand, with live progress per site. **Auto-check** repeats it
  every 5 minutes to 6 hours.
- **No alert floods.** The first check of an area is saved as the starting point; after that only
  listings that weren't there before count as new.
- **Alerts**: in-page banner and chime, browser notification, Windows notification, free phone push
  ([ntfy](https://ntfy.sh)), Telegram, email. Each channel has a Test button, and there's an alert history.
- Map with your circle, building pins and every listing; filters by watch; NEW badges until you mark them seen.
- Works on a phone and can be installed as an app (Add to Home Screen).

## Run it on your PC (Windows)

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/) (tick *Add to PATH*).
2. Download this repository (Code → Download ZIP) and unzip it.
3. Double-click **`start.bat`**. It installs what it needs and opens <http://127.0.0.1:8750>.

On your PC there's no sign-in. Keep the black window open: auto-checks run only while it's running.

## Host it online for free (Render + Neon)

Hosted, it keeps checking while your PC is off and works from your phone. Each person gets their
own account.

**1. Create a free database (keeps your data when the server restarts).**
Sign up at [neon.tech](https://neon.tech) and create a project. Pick the region
*AWS Asia Pacific (Singapore)*. Copy the connection string; it looks like
`postgresql://user:password@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`.

**2. Deploy the app.**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/nitinn889/RentSearch)

Sign in to Render with GitHub. The blueprint asks for two values:
- `DATABASE_URL`: paste the Neon connection string.
- `SIGNUP_CODE`: an invite code people must enter to create an account. Leave it empty to let
  anyone with the link sign up.

Click **Apply** and wait for the first build (about 3 minutes). Open your
`https://rent-radar-….onrender.com` address and create your account.

**3. Keep automatic checks running.** Render's free plan sleeps after 15 minutes without
visitors. To have it wake up and check every 30 minutes:
1. In Render, open the service → **Environment** and copy the value of `CRON_SECRET`.
2. In this GitHub repository: **Settings → Secrets and variables → Actions → New repository secret**.
   Add `RENT_RADAR_URL` (your onrender.com address) and `CRON_SECRET`.
3. In the app, set **Auto-check** to every 30 minutes and set up **phone push** under *How to alert you*.

Free-plan notes: the first visit after it sleeps takes about a minute. GitHub pauses scheduled
workflows in repositories with no commits for 60 days; re-enable it from the Actions tab.

**Other hosts.** The `Dockerfile` runs anywhere that runs containers (Railway, Fly.io, Koyeb, a VPS).
Set the environment variables below.

| Variable | Needed | What it does |
|---|---|---|
| `RENT_RADAR_MODE` | set to `server` (the Dockerfile does) | turns on accounts and sign-in |
| `SECRET_KEY` | yes | any long random string; signs sign-in cookies |
| `DATABASE_URL` | strongly advised | Postgres connection string; without it data is in a local SQLite file |
| `SIGNUP_CODE` | optional | invite code required to create an account |
| `CRON_SECRET` | optional | lets an outside timer call `POST /api/cron` with header `X-Cron-Secret` |
| `PUBLIC_URL` | optional | the app's address, used in alert links (Render fills it in by itself) |
| `PORT` | set by the host | port to listen on |

## Sites

| Site | Rent | Sale | How it searches |
|---|---|---|---|
| NoBroker | ✓ | ✓ | your exact point and radius (up to 10 km). Bangalore, Mumbai, Pune, Chennai, Hyderabad, Delhi NCR |
| MagicBricks | ✓ | ✗ | locality pages. Its sale pages answer automated requests with a "down for maintenance" page |
| SquareYards | ✓ | ✓ | locality pages with coordinates |
| Your pages | ✓ | ✓ | any results URL you paste; new links on it count as new listings |

Not included because they refuse automated access: Housing.com, 99acres, OLX. Facebook needs a login.

**Rules the scanner follows:** it reads each site's `robots.txt` and skips pages that the file
disallows, waits 1.5 s between requests to the same site (shared by all users of a hosted app), and
reports blocks instead of trying to get around them. It does not solve CAPTCHAs or disguise traffic.
Use it for your own home search; the listings belong to the sites that publish them.

Sites change their pages from time to time, and some treat cloud servers differently from home
connections. The [Site check](../../actions/workflows/site-check.yml) workflow reads every site from
a cloud machine weekly and after every scraper change, so you can see what works when hosted.

## How building matching works

A listing belongs to a watched building when its society/project name (or title) contains every
distinctive word of the name you typed. Generic words like *apartments*, *residency* and *phase*
are ignored, spacing differences ("Springleaf" vs "Spring Leaf") and small typos are tolerated.
The listing must also be within the building's circle (1 km, or 2 km when only the area was found).
Listings without coordinates are matched on the name alone.

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest -q
TEST_DATABASE_URL=postgresql://… python -m pytest -q
python scripts/site_check.py
```

```
app.py                  entry point (python app.py on a PC, gunicorn app:app when hosted)
radar/web.py            pages, JSON API, sign-in
radar/scanner.py        a check: reads sites per region, area + building watches, new-vs-seen, alerts
radar/buildings.py      society-name matching
radar/scheduler.py      automatic checks + /api/cron
radar/store.py, db.py   users, settings, history: SQLite or Postgres
radar/http.py           polite client: robots.txt, per-site delay, block detection, short page cache
radar/sources/*.py      one file per site; add a site by subclassing Source and listing it in __init__.py
radar/alerts.py         Windows toast, ntfy, Telegram, email
static/                 the web page
tests/                  pytest suite (runs on SQLite and Postgres in CI)
```

# HaulLog — Django Backend

> **Django API backend for the HaulLog ELD Trip Planner.**  
> Handles TomTom API proxying (geocoding + routing), API key security, and the full FMCSA-compliant Hours of Service (HOS) trip calculation engine.

---

## Table of Contents

- [Project Overview](#project-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [API Endpoints](#api-endpoints)
- [HOS Calculation Engine — Deep Dive](#hos-calculation-engine--deep-dive)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [CORS Setup](#cors-setup)
- [Getting Started (Local)](#getting-started-local)
- [Deployment](#deployment)

---

## Project Overview

This is the Django backend for HaulLog. It serves four API endpoints consumed exclusively by the React frontend. Its core responsibilities are:

1. **API Key Security** — The TomTom API key never touches the frontend bundle. It's stored as an environment variable and served on demand via a dedicated endpoint.
2. **Geocoding Proxy** — Translates city name strings into `lat/lng` coordinates by proxying the TomTom Search API.
3. **Route Proxy** — Fetches real road geometry (full polyline + per-leg distances) from TomTom Routing API for truck-mode routing.
4. **HOS Engine** — A pure Python simulation that takes trip distances and driver cycle state, and produces a FMCSA-compliant timeline of driving, rest, fuel, and break events — one per day.

There are **no models and no database** used in the current feature set. `db.sqlite3` exists (Django default) but is unused — all computation is stateless and request-scoped.

---

## Tech Stack

| Layer            | Technology                          |
|------------------|-------------------------------------|
| Framework        | Django 5.2.4                        |
| Language         | Python 3.13                         |
| CORS             | django-cors-headers 4.7.0           |
| Env management   | python-dotenv                       |
| HTTP client      | `requests` (stdlib-style, sync)     |
| Server (prod)    | Gunicorn                            |
| External API     | TomTom Maps API (Search + Routing)  |
| Deployment       | Render                              |

---

## Project Structure

```
ELD_project/           ← Django project (config layer)
├── settings.py        ← All config: installed apps, CORS, env vars, DB
├── urls.py            ← Root URL conf — mounts admin + ELDapp URLs
├── wsgi.py            ← WSGI entry point (Gunicorn uses this)
└── asgi.py            ← ASGI entry point (unused currently)

ELDapp/                ← Django app (business logic layer)
├── views.py           ← All 4 API view functions
├── urls.py            ← URL patterns for the 4 endpoints
├── models.py          ← Empty (no DB models used)
├── admin.py           ← Empty
└── migrations/        ← Empty (no migrations needed)

requirements.txt       ← Python dependencies
env.env                ← Local env file (not committed — holds secrets)
```

---

## API Endpoints

All endpoints return `application/json`. No authentication is required (public API consumed by same-origin frontend in production).

---

### `GET /api/tomtom-key/`

Returns the TomTom API key to the frontend so it can initialize the TomTom Web SDK map.

**Response:**
```json
{ "key": "YOUR_TOMTOM_API_KEY" }
```

**Why this exists:** Exposing the API key in the React bundle would make it publicly visible in source. By serving it from Django (where it lives in an env var), the key is never shipped in frontend code.

---

### `GET /api/geocode/?q=<city>`

Geocodes a city name string to coordinates by proxying TomTom's Search API (`/search/2/geocode/`).

**Query params:**
| Param | Required | Description |
|-------|----------|-------------|
| `q`   | Yes      | City name string (e.g. `Chicago, IL`) |

**Success response:**
```json
{
  "lat": 41.8781,
  "lng": -87.6298,
  "name": "Chicago, Illinois"
}
```

**Error responses:**
```json
{ "error": "Query too short" }          // 400 — q < 2 chars
{ "error": "Location not found" }       // 404 — TomTom returned no results
{ "error": "Backend Error: ..." }       // 500 — network/exception
```

**Logic:** Hits `https://api.tomtom.com/search/2/geocode/{q}.json` with `limit=1` and `countrySet=US`. Extracts `position.lat` and `position.lon` from the first result.

---

### `GET /api/route/?origin=<lat,lng>&pickup=<lat,lng>&dropoff=<lat,lng>`

Fetches a real truck road route through all three coordinates using TomTom's Routing API (`/routing/1/calculateRoute/`).

**Query params:**
| Param     | Required | Description |
|-----------|----------|-------------|
| `origin`  | Yes      | `lat,lng` of current location |
| `pickup`  | Yes      | `lat,lng` of pickup city |
| `dropoff` | Yes      | `lat,lng` of dropoff city |

**Success response:**
```json
{
  "points": [{ "lat": 41.87, "lng": -87.62 }, ...],
  "distanceMeters": 1996000,
  "distanceMiles": 1240.3,
  "travelTimeSeconds": 81600,
  "legs": [
    { "distanceMiles": 299.1, "travelTimeSeconds": 19800 },
    { "distanceMiles": 941.2, "travelTimeSeconds": 61800 }
  ]
}
```

**Logic:**
- Constructs a 3-point TomTom route string `origin:pickup:dropoff`
- Uses `travelMode: truck` and `vehicleCommercial: true` for truck-specific road restrictions
- Collects all `points` from both legs into a flat polyline array (used by the frontend map to draw the route)
- Returns per-leg distances separately so the HOS engine can simulate each leg independently

---

### `GET /api/calculate-trip/`

The core HOS engine. Takes trip geometry and driver cycle state, runs a full FMCSA simulation, and returns a complete day-by-day timeline.

**Query params:**
| Param             | Required | Default | Description |
|-------------------|----------|---------|-------------|
| `leg1Miles`       | Yes      | `0`     | Distance from current location to pickup (miles) |
| `leg2Miles`       | Yes      | `0`     | Distance from pickup to dropoff (miles) |
| `cycleUsed`       | Yes      | `0`     | Hours already used in driver's current cycle |
| `hosRule`         | No       | `"70"`  | `"70"` (70hr/8-day) or `"60"` (60hr/7-day) |
| `currentLocation` | No       | `"Origin"` | Label for origin in timeline |
| `pickupLocation`  | No       | `"Pickup"` | Label for pickup in timeline |
| `dropoffLocation` | No       | `"Dropoff"` | Label for dropoff in timeline |

**Success response:**
```json
{
  "totalMiles": 1240.3,
  "daysNeeded": 2,
  "fuelStops": 1,
  "hoursAvail": 58.0,
  "totalDriveHrs": 22.5,
  "timeline": [
    {
      "name": "Chicago, IL",
      "type": "origin",
      "meta": "Day 1 · 06:00 AM",
      "day": 1,
      "start": null,
      "end": null
    },
    {
      "name": "Drive to St. Louis, MO",
      "type": "driving",
      "meta": "Day 1 · 6:00 AM → 11:27 AM · 299.1 mi",
      "day": 1,
      "start": 6.0,
      "end": 11.45
    },
    ...
  ],
  "hosViolation": false,
  "hosViolationMsg": null,
  "maxCycle": 70,
  "cycleUsed": 12.0
}
```

**Timeline event types:**

| `type`    | What it represents                          |
|-----------|---------------------------------------------|
| `origin`  | Trip start or day resume marker             |
| `driving` | A driving segment (with miles + time range) |
| `fuel`    | 0.3hr fuel stop (every 1,000 miles)         |
| `break30` | 30-minute mandatory break (after 8hr continuous driving) |
| `rest`    | 10hr off-duty reset (end of driving day)    |
| `pickup`  | Arrival at pickup location (1hr stop)       |
| `dropoff` | Arrival at dropoff location (1hr stop)      |

---

## HOS Calculation Engine — Deep Dive

The engine lives entirely in `calculate_trip()` in `views.py`. It's a **forward-simulation loop** — it walks through time from 06:00 AM on Day 1 and makes decisions at each step based on FMCSA rules.

### FMCSA Constants

```python
SPEED_MPH    = 55      # Average driving speed
MAX_DRIVE_DAY = 11     # Max driving hours per day (11-hour rule)
BREAK_AFTER   = 8      # Mandatory 30-min break after this many continuous hours
FUEL_EVERY    = 1000   # Insert fuel stop every 1,000 miles
MAX_CYCLE     = 70     # Hours in cycle (70 for 8-day, 60 for 7-day)
```

### State Variables

| Variable           | Tracks |
|--------------------|--------|
| `clock`            | Current time in hours from midnight (starts at 6.0) |
| `day_num`          | Current day number (1-indexed) |
| `drive_today`      | Hours driven today (resets after 10hr rest) |
| `continuous_drive` | Hours driven without a break (resets after 30-min break or any stop) |
| `fuel_mile_accum`  | Miles since last fuel stop |
| `total_drive_used` | Total driving hours for the entire trip |

### Simulation Loop

The engine processes two legs (`leg1: origin → pickup`, `leg2: pickup → dropoff`) sequentially. For each leg, it loops while miles remain:

```
WHILE miles_left > 0:
    1. Check if fuel stop needed (fuel_mile_accum >= 1000)
       → Insert 0.3hr fuel event, reset fuel_mile_accum

    2. Check if 30-min break needed (continuous_drive >= 8)
       → Insert 0.5hr break event, reset continuous_drive

    3. Check if daily limit hit (drive_today >= 11)
       → Insert 10hr rest event, increment day_num
       → Reset clock to 6.0, reset drive_today and continuous_drive
       → Continue (re-check fuel/break on next iteration)

    4. Calculate how much to drive now:
       can_drive = min(hours_to_break_limit, hours_to_daily_limit)
       drive_now = min(can_drive, hours_remaining_for_leg)

    5. Insert driving event, advance clock, update all state variables
```

After completing each leg, an arrival event is added (`pickup` or `dropoff`, 1hr stop). If arrival at pickup happens after 16:00 (4 PM), a 10hr rest is forced before starting the next leg — simulating a realistic decision a driver would make rather than driving through the night.

### HOS Violation Detection

After the simulation completes, the engine checks if `cycle_used + total_drive_used > MAX_CYCLE`. If yes, `hosViolation: true` is returned along with a human-readable message telling the driver how many hours they're short and suggesting a 34-hour restart.

---

## Configuration & Environment Variables

The project reads secrets from `env.env` (loaded via `python-dotenv`).

Create `env.env` in the project root:

```env
TOMTOM_KEY=your_tomtom_api_key_here
DJANGO_SECRET=your_django_secret_key_here
```

| Variable        | Used in             | Description                        |
|-----------------|---------------------|------------------------------------|
| `TOMTOM_KEY`    | `settings.py` → `TOMTOM_API_KEY` | TomTom API key for geocoding and routing |
| `DJANGO_SECRET` | `settings.py` → `SECRET_KEY`     | Django's cryptographic secret key  |

> ⚠️ **Never commit `env.env` to version control.** Add it to `.gitignore`.

---

## CORS Setup

`django-cors-headers` is configured in `settings.py` to allow requests from the React dev server:

```python
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",   # Vite default
    "http://localhost:5174",   # Vite alternate port
]
```

For production (e.g. when the frontend is deployed on Vercel/Netlify), add the deployed frontend URL here:

```python
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "https://your-frontend.vercel.app",
]
```

`CorsMiddleware` is placed **first** in `MIDDLEWARE` — this is required by the library.

---

## Getting Started (Local)

```bash
# 1. Clone and enter the project
cd ELD_project_root/

# 2. Create and activate virtual environment
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create env.env with your secrets
echo "TOMTOM_KEY=your_key_here" >> env.env
echo "DJANGO_SECRET=your_secret_here" >> env.env

# 5. Run migrations (creates db.sqlite3, required by Django even if unused)
python manage.py migrate

# 6. Start development server
python manage.py runserver
```

The backend will be available at `http://localhost:8000`.

Make sure `CORS_ALLOWED_ORIGINS` includes `http://localhost:5173` (already set by default) so the React frontend can communicate.

---

## Deployment

The backend is deployed on **Render** as a web service using Gunicorn.

**Start command used by Render:**
```bash
gunicorn ELD_project.wsgi:application
```

**Required environment variables on Render:**
- `TOMTOM_KEY`
- `DJANGO_SECRET`
- `PYTHON_VERSION` (e.g. `3.13.0`)

**Production checklist before deploying:**
- Set `DEBUG = False` in `settings.py`
- Add your Render domain to `ALLOWED_HOSTS`:
  ```python
  ALLOWED_HOSTS = ["haul-log-1.onrender.com"]
  ```
- Add the deployed frontend URL to `CORS_ALLOWED_ORIGINS`

---

*HaulLog Backend — Django 5.2 · FMCSA 49 CFR Part 395 · Deployed on Render · 2026*

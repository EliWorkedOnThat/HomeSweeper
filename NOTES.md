# HomeSweeper project notes

_Last updated: 2026-11-03_

## Purpose
HomeSweeper is an early-stage collection of real-estate listing scrapers. Each target site has its own folder for its site-specific scraper code (Playwright browser scraper + direct HTTP request scraper). Shared coordination, configuration, database and output logic lives in `Scraper/`.

The end goal is an app that **non-technical staff** use at work. That's why the plan below prefers simple, reliable choices that need no extra installs.

## Project layout
- `MerrJep_al/` — MerrJep.al scrapers (folder was renamed from `MerrJep.al` so it can be imported as a Python package).
  - `PlaywrightScraper1.py` — the working scraper. Exposes `create_browser_instance()`, which returns a list of listing dicts.
  - `Request1.py` — HTTPX `fetch()` with browser-like headers. Not used by the engine yet.
- `GazetaCelesi/` — placeholder; `PlaywrightScraper.py` and `Request.py` are empty files.
- `Scraper/Engine.py` — orchestrator. Runs the MerrJep scraper and passes the results to `save_listings()`.
- `Scraper/DB.py` — the only file that talks to the database (SQLite). See [Database](#database-scraperdbpy).
- `Scraper/Output.py` — writes listings to `Output/listings.xlsx` (openpyxl).
- `Scraper/config.py` — global settings shared by every scraper.
- `Data/` — holds the SQLite database file (`Homesweeper.db`). Created automatically.
- `Test/` — local dev utilities (`HTML-Analyzer.py`, `Test.py`). Ignored by Git and no longer tracked.
- `Output/` — generated Excel output. Ignored by Git.
- `Analyze.md` — scratch notes and HTML samples (currently MerrJep phone-button markup).

## Current state

### MerrJep.al scraper (working, v1)
- Builds page URLs as `BASE_URL`, then `BASE_URL?Page=N`, using a small URL queue. It stops when a page has no listing cards.
- On each results page it collects unique `data-product-id` values (UPIDs), opens each listing, scrapes it, then goes back.
- On each listing page it pulls:
  - JSON-LD `Product` data: title, price, currency, description and images. If a listing has no JSON-LD, it is skipped.
  - HTML tag attributes (`.tags-area`), stored as a dict.
  - Description text, with the JSON-LD description as a fallback.
  - Images, with the gallery links as a fallback.
  - Seller name.
  - Phone number, read from two places: the contact button (`button .main-contact bdi`) and the `tel:` contact link. `combine_phones()` keeps one number if both match by digits, joins them with `; ` if they differ, and returns `None` if neither exists.
  - Published date and time.
- It checks that the UPID in the JSON-LD matches the card it clicked on.
- It runs with `headless=False`, waits a fixed 5 s after each page load, and rests `Rest_Time` seconds after each listing.

### Config (`Scraper/config.py`)
- `Demo_Environment = True` limits each run to `DEMO_LISTING_LIMIT` (5) listings, which is set in the MerrJep scraper. Set it to `False` to scrape every page.
- `Rest_Time = 3` is the number of seconds to rest after each listing before opening the next one, so requests aren't fired instantly and flagged.
- `Rotation_Rescraping_Time = 5` is meant to be the minutes between rescrapes. It is **defined but not used yet**.
- `Database_Name = "Homesweeper.db"` is the name of the SQLite file inside `Data/`.

### Database (`Scraper/DB.py`)
We chose **SQLite**. It is built into Python, so there is no server to install or keep running. The whole database is one file, so backing up means copying `Data/Homesweeper.db`. It is reliable when the PC crashes or loses power.

What exists so far:
- `connect_to_db()` builds the path from the project root plus `Data/` plus `Database_Name`, so the location is the same whichever folder you run from. It creates `Data/` if it is missing.
- `create_tables(connection)` creates the `listings` table if it doesn't exist. It is safe to call on every run.

The `listings` table:

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Internal row number; future tables point to this |
| `site`, `upid` | TEXT, required | **Unique together**, so the same listing can never be saved twice |
| `url`, `title`, `description` | TEXT | |
| `price` | REAL | Can be empty ("price on request") |
| `currency` | TEXT | |
| `phone` | TEXT | One number, or two joined with `; ` |
| `seller` | TEXT | |
| `published_date`, `published_time` | TEXT | As shown on the site (Albanian month abbreviations, e.g. `tet 03 2026`; still to be converted to `2026-10-03`) |
| `listing_type` | TEXT | Shitet / Jepet me qera… |
| `size_m2` | REAL | Number only (`"1.200 m2"` → `1200`) so it can be sorted and filtered |
| `rooms` | INTEGER | Empty for land and commercial listings |
| `condition` | TEXT | I ri / I përdorur… |
| `city`, `address` | TEXT | Seller-entered, so not always accurate |
| `posted_by` | TEXT | Kompani / Privat |
| `images` | TEXT | JSON list of image URLs |
| `attributes` | TEXT | JSON dict of listing tags (size, rooms, city…) |
| `first_seen`, `last_seen` | TEXT | Filled in automatically. Stored in **UTC**, not Albanian time. |

**The filter columns** (`listing_type` to `posted_by`) use standard English names that every site shares. Each scraper maps its own labels to them. MerrJep does this with `ATTRIBUTE_COLUMNS` and `parse_number()` in `PlaywrightScraper1.py`. `attributes` still keeps every tag as a backup.

`save_listing(connection, listing, site)` inserts new listings and updates existing ones in place, keeping `id` and `first_seen` and refreshing `last_seen`. `Engine.py` connects to the database, runs the scraper, saves every listing, then exports Excel.

**Changing the table later:** `CREATE TABLE IF NOT EXISTS` never changes an existing table. During development, delete `Data/Homesweeper.db` and let it rebuild. Once there is real data, use an `ALTER TABLE` step instead.

### Output (`Scraper/Output.py`)
- `save_listings(listings, site)` appends rows to the `Listings` sheet in `Output/listings.xlsx`. It creates the file and headers if they are missing.
- Each attribute key gets its own column, and new keys add new columns.
- It skips rows whose `(site, upid)` is already in the sheet.
- If the file is open in Excel, it prints a warning instead of crashing.

## The plan (work in this order)

### Phase 1 — Connect the database (next up)
1. Add `Data/` to `.gitignore`, the same as `Output/`, so listing data is never committed.
2. Remove the unused `import sqlite3` from `MerrJep_al/PlaywrightScraper1.py`. Only `DB.py` may import `sqlite3`.
3. ~~Turn on WAL mode in `connect_to_db()`~~ **Done.** The UI can read listings while a scrape is writing.
4. Add these to `DB.py`:
   - `get_known_upids(site)` returns a set of every saved UPID for that site.
   - `save_listing(listing, site)` inserts a new listing. If it already exists, it updates `last_seen` and the fields that can change (price, phone, description). Use `INSERT ... ON CONFLICT(site, upid) DO UPDATE`. Store `images` and `attributes` with `json.dumps`.
   - `get_listings(site=None)` returns the stored listings for the export, the UI and, later, the agents.
5. In `Engine.py`, call `connect_to_db()` and `create_tables()` once at startup. Then save each scraped listing with `save_listing()`.
6. In the MerrJep scraper, load `get_known_upids("merrjep")` before scraping and skip those UPIDs **before** clicking into the listing. That saves a page load and a `Rest_Time` wait per listing.
7. Change `Output.py` to build the Excel file from `get_listings()`. The database is the main store and Excel is a report. If Excel is open, only the export fails and the scraped data is already safe.

Rules for this phase:
- **All SQL lives in `DB.py`.** Scrapers and the engine call its functions. If we ever move to PostgreSQL, only `DB.py` changes.
- Every write goes inside `with connection:` so a crash never leaves half-saved rows.

### Phase 2 — Local web UI
Staff won't use a terminal. The app will run as a **local website**: a Python server on the user's own PC, opened in their normal browser. That avoids device-specific installs and native app problems.

```
Browser (localhost:8000)  ──HTTP──▶  Python web server (FastAPI)
  HTML / CSS / JS                      ├── GET  /api/listings      → DB.get_listings()
  table, filters, buttons              ├── POST /api/scrape/start  → Engine.main() in a background thread
                                       ├── GET  /api/scrape/status → running / done / error + counts
                                       └── GET  /api/export        → download listings.xlsx
```

- **Server:** `Scraper/Server.py` using **FastAPI**. It is a thin layer that exposes engine and DB functions as URLs, with no scraping logic of its own. FastAPI's `/docs` page lets you test each endpoint during development.
- **Frontend:** a `UI/` folder with `index.html`, `style.css` and `app.js`, written in **plain HTML/CSS/JS** and served by FastAPI. No React for now, because it would need Node.js and a build step on every PC. We can switch later without touching the Python side.

Must-haves:
1. **Scrapes run in a background thread.** A scrape takes minutes, so "Start scrape" returns immediately and the page polls `/api/scrape/status` every few seconds to show progress. Playwright's sync API also can't run inside FastAPI's async code, which is another reason it needs its own thread.
2. **Only one scrape at a time.** Ignore or reject a second start while one is running.
3. **Bind to `127.0.0.1` only** so other devices on the network can't reach it. Open it up on purpose later if needed.
4. **Double-click to start.** Add a `Start HomeSweeper.bat` that launches the server and opens the browser, so nobody needs a terminal.
5. Before this phase, fix the module-level globals in the MerrJep scraper (see Known gotchas below). The server will call the scraper many times in one process.

Later: if several staff need the same data at once, run this same app on one office PC or server and have everyone open its address. That is also when we'd consider moving from SQLite to PostgreSQL.

### Phase 3 — More sites and polish
1. Start the GazetaCelesi scraper using the same contract: a function that returns a list of listing dicts in the same shape, which the engine saves with `site="gazetacelesi"`.
2. Use `Rotation_Rescraping_Time` to rescrape on a timer, either through the engine or a "schedule" option in the UI.
3. Add a `price_history` table so price changes are kept instead of overwritten.
4. Move per-site settings such as the demo limit, headless mode and wait times into `config.py` where it makes sense.
5. Replace the fixed `wait_for_timeout(5000)` with element-based waits.
6. Use `Request1.py` (HTTPX) where JS rendering isn't needed. It is faster than Playwright.
7. Add proper logging to the engine.
8. Later: agent tables and features, and a `status` column on listings (called, interested, sold…).

## Repository housekeeping
- `.gitignore` excludes test folders and files, Python caches and `Output/`. `Data/` still needs adding (Phase 1, step 1).
- `Test/Test.py` has been removed from the index, so it is no longer tracked.

## For Alex's Claude


## To run the Engine use the following command:
Run from the project root (needed for the `Scraper.` / `MerrJep_al.` imports):

    py -3.14 -m Scraper.Engine

Dependencies used: `playwright` (plus `playwright install chromium`), `openpyxl`, `rich`, `httpx`, `requests`, `beautifulsoup4`. Phase 2 adds `fastapi` and `uvicorn`.

## Errors and Potential Errors / Fixes
At the moment we test and scrape each site one at a time to check errors and exported data. Later we must use asynchronous processes and unique Playwright drivers so each site is scraped safely and quickly in its own instance.

Known gotchas:
- If `listings.xlsx` is open in Excel, saving fails. Close it and rerun. Phase 1, step 7 removes this problem for the scraped data.
- The MerrJep scraper keeps its state (`URL_QUEUE`, `PROCESSED_UPIDS`, `SCRAPED_LISTINGS`, `PAGE_COUNT`) in module-level globals. Calling `create_browser_instance()` twice in one process continues from the old state instead of starting fresh. **This must be fixed before the rescrape loop or the web UI.**
- Listings without JSON-LD `Product` data are skipped and logged as failures.
- `create_tables()` won't add new columns to an existing database (see the Database section).

# CLAUDE.md

Guidance for Claude Code sessions working on HomeSweeper.

## What this project is
HomeSweeper scrapes Albanian real-estate listing sites, stores the listings in SQLite and exports them to Excel. The end goal is a **local web app (FastAPI + plain HTML/CSS/JS) for non-technical office staff**, so keep the setup simple: no extra servers, no Node/build steps, no installs on staff PCs beyond Python.

**`NOTES.md` is the source of truth for the plan, the DB schema and the known gotchas.** Read it before any non-trivial change, and update it (including the `_Last updated_` date) when you change behaviour or finish a plan step.

## Run
The project must run on **Windows, Linux and macOS**. Use `pathlib` paths and no OS-specific launchers (no `.bat`/`.sh` for now). We're still testing, so everything is started from a console.

Run from the project root. The imports are package-style (`Scraper.`, `MerrJep_al.`, `Tools.`), so running a file directly breaks them. Use `python3` on Linux/macOS and `py -3.14` on Windows:

    python3 -m Scraper.Engine               # one scrape in the terminal
    python3 -m Scraper.Server               # web UI at http://127.0.0.1:8000, Ctrl+C to stop
    python3 -m Tools.FetchHTML <url>        # save a page's HTML to Tools/HTML/ (--fast = plain HTTP, --show = visible browser)

Deps: `playwright` (+ `playwright install chromium`), `openpyxl`, `rich`, `httpx`, `requests`, `beautifulsoup4`, `fastapi`, `uvicorn`. There is no requirements file and no test suite yet.

## Layout
- `Scraper/Engine.py` is the orchestrator. `main()` connects to the DB, creates the tables, runs the MerrJep scraper, calls `save_listing()` for each listing, exports to Excel, and returns `{found, saved, failed}` (or `None` if the DB can't be opened).
- `Scraper/DB.py` is the **only** file that touches SQLite: `connect_to_db()` (WAL on), `create_tables()`, `save_listing(connection, listing, site)` (an upsert on `(site, upid)`), `get_listings(connection, site=None)` (dicts with `images`/`attributes` decoded from JSON).
- `Scraper/Output.py` has two functions. `save_listings(listings, site)` appends to `Output/listings.xlsx` and is used by the engine. `build_workbook(listings)` returns fresh `.xlsx` bytes from DB rows and is used by the UI's export download.
- `Scraper/Server.py` is the FastAPI server, kept as a thin layer with no scraping logic. Its endpoints are `GET /api/listings`, `POST /api/scrape/start` (runs `Engine.main()` in a background thread, returns 409 if one is already running), `GET /api/scrape/status`, and `GET /api/export`. It serves `UI/` at `/` and binds to `127.0.0.1:8000` only.
- `UI/` (`index.html`, `style.css`, `app.js`) is plain HTML/CSS/JS with no build step. It loads every listing once, then filters and sorts in the browser, and polls the status every 3 s during a scrape. It builds DOM nodes with `textContent`, never `innerHTML`, because the data is scraped.
- `Tools/` holds dev utilities. `FetchHTML.py` saves a URL's HTML to `Tools/HTML/` (git-ignored) so a site's markup can be studied before writing its scraper. It uses Playwright by default and HTTPX with `--fast`.
- `Scraper/config.py` holds the global settings: `Demo_Environment` (True means 5 listings per run), `Rest_Time` (s between listings), `Rotation_Rescraping_Time` (unused so far), `Database_Name`.
- `MerrJep_al/PlaywrightScraper1.py` is the working scraper. `create_browser_instance()` returns a list of listing dicts. `ATTRIBUTE_COLUMNS` maps Albanian tag labels to the standard DB columns, with `parse_number()` and `combine_phones()` as helpers.
- `MerrJep_al/Request1.py` is an HTTPX `fetch()` that nothing uses yet.
- `GazetaCelesi/` holds empty placeholder files for the next site.
- `Data/Homesweeper.db` and `Output/` are generated and git-ignored.
- `Analyze.md` is a scratchpad for HTML samples and URLs.

## Rules / conventions
- **All SQL lives in `DB.py`.** Scrapers and the engine call its functions, so a later PostgreSQL switch only touches that file. Every write goes inside `with connection:`.
- **Scraper contract:** each site exposes a function that returns a list of dicts with the same keys (`upid, url, title, description, price, currency, phone, seller, published_date, published_time, listing_type, size_m2, rooms, condition, city, address, posted_by, images (list), attributes (dict)`). The engine saves them with a site key (`"merrjep"`, later `"gazetacelesi"`). Each site maps its own labels to the shared English filter columns and keeps the raw tags in `attributes`.
- `CREATE TABLE IF NOT EXISTS` never alters an existing table. In dev, delete `Data/Homesweeper.db` after a schema change. Once real data exists, use `ALTER TABLE`.
- Keep the existing code style: `#Comment` headers above each function, `rich` coloured prints for logging, `Path(__file__)`-relative paths.
- Work on the `dev` branch. `main` is the PR target. Commit only when asked.

## Current status (2026-11-03)
Phase 1 (connect the DB) is partly done. `NOTES.md` lags a little here: `Data/` is already in `.gitignore`, and the unused `sqlite3` import is already gone from the scraper.
- Done in Phase 1: WAL, the `save_listing` upsert, `get_listings`, and the engine saving to the DB before the Excel export.
- Still open in Phase 1: `get_known_upids(site)`, then having the MerrJep scraper skip known UPIDs *before* clicking into a listing.
- Phase 2 (the local web UI) has a first version working: the server and `UI/`, started from the console. A double-click launcher is deferred until testing is done, and it must be cross-platform. Possible next steps: show live progress during a scrape (the status currently only says running/done), a listing detail view, and the config toggles in the UI.
- The automatic scraping mode (a rescrape timer using `Rotation_Rescraping_Time`) is **assigned to Alex**. The spec is in `NOTES.md` under "For Alex's Claude". Anything that starts a scrape must go through `SCRAPE_LOCK` (see "How the scrape lock works" in `NOTES.md`).
- Phase 3 (GazetaCelesi, price history) is next after that. Details are in `NOTES.md`.

## Gotchas
- The MerrJep scraper keeps its state in module globals. `create_browser_instance()` now calls `reset_state()` first, so repeated runs in one process start fresh, but two runs must never overlap (the server's lock enforces this).
- Scrape state lives in server memory, so restarting the server resets the status pill. The listings themselves are safe in the DB.
- Playwright runs with `headless=False` and fixed `wait_for_timeout(5000)` waits. Listings with no JSON-LD `Product` are skipped.
- Saving the Excel file fails if `listings.xlsx` is open in Excel (it warns and doesn't crash).
- `published_date` is raw Albanian text (e.g. `tet 03 2026`) and isn't normalised yet. `first_seen`/`last_seen` are UTC.

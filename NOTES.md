# HomeSweeper project notes

_Last updated: 2026-11-04_

## Purpose
HomeSweeper is an early-stage collection of real-estate listing scrapers. Each target site has its own folder for its site-specific scraper code (Playwright browser scraper + direct HTTP request scraper). Shared coordination, configuration, database and output logic lives in `Scraper/`.

The end goal is an app that **non-technical staff** use at work. That's why the plan below prefers simple, reliable choices that need no extra installs.

## Project layout
- `MerrJep_al/` — MerrJep.al scrapers (folder was renamed from `MerrJep.al` so it can be imported as a Python package).
  - `PlaywrightScraper1.py` — the working scraper. Exposes `create_browser_instance()`, which returns a list of listing dicts.
  - `Request1.py` — HTTPX `fetch()` with browser-like headers. Not used by the engine yet.
- `GazetaCelesi/` — Gazeta Celesi scrapers.
  - `PlaywrightScraper.py` — the working scraper. Exposes `scrape_gazetacelesi(skip_agencies=False)`, which returns a list of listing dicts. See [Gazeta Celesi scraper](#gazetacelesial-scraper-working-v1).
  - `Request.py` — empty placeholder.
- `Scraper/Engine.py` — orchestrator. Runs every site in `SCRAPERS` one after another (MerrJep, then Gazeta Celesi) and saves each site's results with its own site key. A crash in one site is logged and the next site still runs. Returns `{found, saved, failed, sites: {site: {...}}}`.
- `Scraper/DB.py` — the only file that talks to the database (SQLite). See [Database](#database-scraperdbpy).
- `Scraper/Output.py` — writes listings to `Output/listings.xlsx` (openpyxl).
- `Scraper/config.py` — global settings shared by every scraper.
- `Data/` — holds the SQLite database file (`Homesweeper.db`). Created automatically.
- `Scraper/Server.py` + `UI/` — the local web UI (FastAPI + plain HTML/CSS/JS). See [Phase 2](#phase-2--local-web-ui).
- `Tools/` — dev utilities. `FetchHTML.py` saves a URL's HTML to `Tools/HTML/` (ignored by Git). It replaces the old `Test/` folder, which has been deleted.
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
- It runs headless (no window) when `Headless = True` in `config.py`, which is the default. In that mode `launch_browser()` uses `channel="chromium"` and a user agent without "HeadlessChrome", so sites see the same fingerprint as the visible browser. It waits a fixed 5 s after each page load and rests `Rest_Time` seconds after each listing.

### GazetaCelesi.al scraper (working, v1)
- Gazeta Celesi is a Next.js site. Each results page (`https://www.gazetacelesi.al/shtepi/njoftime?page=N`, 20 per page, newest first) embeds its listings as JSON in `<script id="__NEXT_DATA__">` under `props.pageProps.getAdsList.data` (`data` = the ads, `meta` = `currentPage`/`totalPages`).
- That JSON is **identical** to what a listing's own page has (checked 2026-11-04: same keys, images, description and properties), so the scraper **never clicks into a listing**. One page load gives 20 complete listings, and there's no `go_back()`.
- It stops when a page has no ads or when `totalPages` is reached. There's no fixed 5 s wait: it waits for the `__NEXT_DATA__` script, which is in the server HTML.
- **To look human**, the data comes from the JSON, but the browser still acts like a person reading the list. For each listing, `scan_card()` scrolls to its card (`a[class*="ad-list-card_containerH"][href$="-<upid>.html"]`) with real mouse-wheel steps of uneven size, then pauses a random `SCAN_TIME` (1–2.5 s). Agency cards are scanned too, even when skipped. Between pages it rests `Rest_Time` plus up to `PAGE_REST_JITTER` (2 s) of random extra time. A demo run of 5 listings takes about 16 s.
- `premiumAds` (the 10 promoted ads repeated on every page) are ignored on purpose; they are not in the real results.
- Agencies are `userType == "business"`, so "Jo Listime Agjensie" filters from the JSON with no DOM selector.
- Field mapping: `properties` (label/value list) becomes `attributes` (lists joined with `, `, bools as `Po`/`Jo`), plus `Zona`, `Kategoria` and `Nuk pranon agjenci` (the seller's `rejectAgencyContact` flag). `Siperfaqe` → `size_m2`, `Dhome gjumi` → `rooms` (fallback: the category slug `2-1` → 2), `Gjendja` → `condition`. Phone = `contact.mobilePhone` + `contact.phone` merged by `combine_phones()` (matched on the last 9 digits), or `None` if `hidePhoneNumber`.
- Values are rewritten to MerrJep's wording so the UI dropdowns don't show duplicates: `NE SHITJE` → `Shitet`, `ME QERA` → `Jepet me qera`, `business` → `Kompani`, `EURO`/`LEKE` → `EUR`/`ALL`, and `Tirane` → `Tiranë` (the `CITY_NAMES` map). `consumer` becomes `Person privat`; check this against MerrJep's private-seller wording once one shows up.
- Price `"1"` is the site's "no price" placeholder and is saved as `None`. Descriptions are HTML (`<br>`) and are turned into plain text.
- `published_date`/`published_time` come from `publishedAt` (UTC), converted to Tirana time by hand (`to_tirana_time()`, EU summer-time rule) and saved as text. The date uses the site's own display format (`04/11/2026`, as in "Publikuar më 04/11/2026"). The site shows no time, but we keep it (`13:51`). Like MerrJep's raw `tet 03 2026`, it is text and doesn't affect ordering, because the UI's "newest" sort uses `first_seen`.
- Condition wording differs by site (`E re` here, `I ri` on MerrJep) and is not merged yet.
- Plain HTTP also returns `__NEXT_DATA__` (tested with `FetchHTML --fast`), so `Request.py` with HTTPX could replace Playwright for this site later. **Not yet:** a bare HTTP client is easy for the site to spot (no JS, no scrolling, no browser fingerprint), so for now we stay with Playwright.

### Turn-based scraping (both sites)
A run is **one turn**, not a full crawl. Each turn, every site in `Engine.SCRAPERS` scrapes up to `Listings_Per_Turn` (50 in `config.py`, 5 in demo) **new** listings, which the Engine saves before starting the next site. "Start scrape" runs one turn; auto mode runs one turn per interval. A turn ends when the site has given 50 new listings or it runs out of pages, so it can't run forever.
- `DB.get_known_upids(connection, site)` is passed to each scraper as `known_upids`. Known listings are skipped **before** clicking (MerrJep) or mapping (Gazeta). Each page logs "N new, M already seen".
- Every turn starts at **page 1**, so fresh posts are always picked up first (both sites list newest first). When a page has nothing new, the turn has caught up and jumps to `RESUME_PAGE`, the deepest page any turn has reached, to keep filling in older listings. Each turn digs a little deeper. Once everything is caught up, a turn is only a few page loads.
- Kept in memory between turns (module globals that `reset_state()` doesn't clear): `RESUME_PAGE`, `AGENCY_UPIDS` (agency cards skipped by "Jo Listime Agjensie", so they don't look new every turn) and `FAILED_UPIDS` (listings that errored, not retried until restart, so one broken listing can't stop a page from counting as caught up).
- **Restart caveat:** after a restart `RESUME_PAGE` is back to 1, so the first turn walks through saved pages (no clicks, but MerrJep still waits 5 s per page) until it finds 50 new listings. Fix: save `RESUME_PAGE` per site in `settings.json` (Phase 4, step 4).
- Known listings are no longer re-scraped, so `last_seen` now means "last scraped", not "still online", and price changes on known listings aren't picked up. A `price_history` re-check pass is a Phase 3 topic.
- Listings deleted from the site shift the pages up, so a few listings near `RESUME_PAGE` can be missed by the backfill. New posts shift pages down, which only causes harmless re-checks of known listings.
- Tested 2026-11-04: three Gazeta turns of 30 gave 90 unique listings (page 1 → jump to 2 → 3 → 5). Two Engine runs for both sites gave 10 + 10 per site, with no duplicates.

### Config (`Scraper/config.py`)
- `Demo_Environment = True` limits each run to `DEMO_LISTING_LIMIT` (5) listings, which is set in the MerrJep scraper. Set it to `False` to scrape every page.
- `Rest_Time = 3` is the number of seconds to rest after each listing before opening the next one, so requests aren't fired instantly and flagged.
- `Headless`: `True` runs the scraper browser with no window, and `False` shows it so you can watch a scrape.
- The rescrape interval is **no longer in config**. The old `Rotation_Rescraping_Time` constant was removed, and staff now pick the interval in the UI (see [Automatic scraping](#automatic-scraping)).
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
  table, filters, buttons              ├── POST /api/auto/start   → play: auto_loop() runs Engine.main() turn after turn in a background thread
                                       ├── GET  /api/scrape/status → running / done / error + counts + auto mode
                                       ├── POST /api/auto/start    → auto mode on / change interval {interval_minutes}
                                       ├── POST /api/auto/stop     → auto mode off
                                       ├── POST /api/preferences   → scrape preferences {skip_agencies}
                                       └── GET  /api/export        → download listings.xlsx
```

- **Server:** `Scraper/Server.py` using **FastAPI**. It is a thin layer that exposes engine and DB functions as URLs, with no scraping logic of its own. FastAPI's `/docs` page lets you test each endpoint during development.
- **Frontend:** a `UI/` folder with `index.html`, `style.css` and `app.js`, written in **plain HTML/CSS/JS** and served by FastAPI. No React for now, because it would need Node.js and a build step on every PC. We can switch later without touching the Python side.

Must-haves:
1. **Scrapes run in a background thread.** A scrape takes minutes, so "Start scrape" returns immediately and the page polls `/api/scrape/status` every few seconds to show progress. Playwright's sync API also can't run inside FastAPI's async code, which is another reason it needs its own thread.
2. **Only one scrape at a time.** Ignore or reject a second start while one is running. **Done** with `SCRAPE_LOCK`; see [How the scrape lock works](#how-the-scrape-lock-works).
3. **Bind to `127.0.0.1` only** so other devices on the network can't reach it. Open it up on purpose later if needed.
4. **Double-click to start (later).** The project must run on Windows, Linux and macOS, so there is no `.bat` for now. While testing, start the server from a console with `python3 -m Scraper.Server` (Windows: `py -3.14 -m Scraper.Server`). Add a cross-platform launcher once testing is done.
5. ~~Fix the module-level globals in the MerrJep scraper~~ **Done.** `create_browser_instance()` calls `reset_state()` first, so every run starts fresh.

Later: if several staff need the same data at once, run this same app on one office PC or server and have everyone open its address. That is also when we'd consider moving from SQLite to PostgreSQL.

#### How the scrape lock works
`Scraper/Server.py` has `SCRAPE_LOCK = threading.Lock()`. It is **not** there because of Python's GIL. You would need it even on a Python with no GIL.

- **What the GIL does:** it only stops two threads from running Python bytecode at the exact same instant, which protects the interpreter's own internals. It doesn't stop two threads from both running the same scrape function. They'd just take turns, especially since Playwright spends most of its time waiting on the network and releases the GIL while it waits. (Python 3.14 also has an optional build without the GIL, but the standard `py -3.14` still has it, and it doesn't matter here either way.)
- **What `SCRAPE_LOCK` does:** it's a rule for the app, "only one scrape at a time." Without it, clicking Start scrape twice, or opening two browser tabs, would start two scrape threads at once, and that causes three problems:
  1. **The scraper's globals would clash.** `URL_QUEUE`, `PROCESSED_UPIDS`, `SCRAPED_LISTINGS` and `PAGE_COUNT` are module-level, so two runs would share them. Each run calls `reset_state()` at the start and would wipe the other's progress mid-scrape. Pages would be skipped or scraped twice, and the results would mix together.
  2. **Traffic to the site.** Two browsers hitting MerrJep at once doubles the request rate and undoes the `Rest_Time` pacing, which raises the chance of being blocked.
  3. **SQLite writes.** SQLite allows only one writer at a time. Two scrapes saving together would mostly just wait for each other, but it's needless contention.
- **How it's used:** `SCRAPE_LOCK.acquire(blocking=False)` doesn't wait for the lock. If a turn is already running, it fails immediately and `auto_loop()` checks again after `BUSY_RETRY_SECONDS`. The lock is then held for the whole scrape and released in the `finally` block of `run_scrape()`, so it's freed even if the scrape crashes.
- **Rule for new code:** anything that starts a scrape (a button, a timer, a new site) must go through this same lock. Never call `Engine.main()` from a second place without it. In practice, call `start_scrape()`: `auto_loop()` is its only caller (play just turns the loop on).

#### Automatic scraping
**Done, now as continuous play/pause (2026-11-04).** The separate "Mblidh njoftimet" (one run) and "Nis automatikun" buttons were merged into **one button: ▶ Nis mbledhjen / ⏸ Pauzë**. Play runs [turns](#turn-based-scraping-both-sites) one after another, forever, until paused. Each turn writes its new listings to the DB, and the page reloads them after every turn. The dropdown next to it is now the **break between turns** (`Pushim 1 minutë` … `Pushim 1 orë`, default 5 min). The blue label shows "Raundi tjetër pas 4:32 · 120 të reja" (the countdown plus the new listings since play was pressed). Its tooltip shows the turn count.
- **Pause works mid-turn.** `POST /api/auto/stop` sets `PAUSE_EVENT`. `Engine.main(should_stop=PAUSE_EVENT.is_set)` passes it to each scraper as `should_stop`, which is checked between listings and pages. The turn ends early (tested: 1.6 s), **what it found so far is saved**, and the remaining sites are skipped for that turn (`result.paused = True`). While it winds down, the status shows "Duke ndaluar… ruhen të gjeturat".
- Pressing play while a paused turn is still winding down clears `PAUSE_EVENT`, so that turn just carries on. If it had already ended early, the next turn starts straight away instead of waiting for the break.
- `AUTO["turns"]` and `AUTO["new_listings"]` count the session since play (reset on each play after a pause) and are sent in `/api/scrape/status` under `auto`.
- The one-turn endpoint `POST /api/scrape/start` was **removed** (the UI no longer used it). For a single turn from a console, run `python3 -m Scraper.Engine`.
- **No duplicates:** each turn skips known UPIDs (`get_known_upids`), and `UNIQUE(site, upid)` plus the upsert make a repeat save impossible anyway. Tested: 0 duplicate `(site, upid)` and 0 duplicate URLs after several turns. The same property posted on both sites, or re-posted under a new ID, is still stored twice; detecting that needs fuzzy matching (phone + size + price), which isn't built.

How it works (`Scraper/Server.py`):
- **The presets live in `AUTO_INTERVALS`.** The server rejects any other value with a 400. If you change the presets, change the `<option>`s in `UI/index.html` too.
- **`auto_loop()` runs in a background thread** started with the server, so it needs no cron or Task Scheduler and works the same on every OS. It sleeps on the `AUTO_WAKE` event until the next run is due, and changes like auto on/off or a finished scrape wake it early.
- **Every turn goes through `start_scrape()`**, which holds `SCRAPE_LOCK`. If the lock is somehow still held when a turn is due, it checks again every `BUSY_RETRY_SECONDS`.
- **The break is counted from when a turn finishes.** `run_scrape()` sets `next_run = finished + interval`, so runs never overlap.
- **Behaviour when switching:**
  - Turning auto on scrapes straight away.
  - Changing the interval while auto is on reschedules from the last run's finish time.
  - Pausing cancels the next run and ends a running turn early (see above). Its listings so far are saved.
- **`STATE_LOCK`** (an RLock) guards `SCRAPE_STATUS` and `AUTO`, because the request threads, the scrape thread and the auto thread all touch them.
- **Auto mode lives in server memory**, so restarting the server turns it off.
- **The page polls `/api/scrape/status`** every 3 s while auto mode is on, and reloads the listings whenever `finished_at` changes.

#### Scrape preferences ("Jo Listime Agjensie")
The top bar has a preference dropdown: **Të gjitha listimet** (everything, the default) or **Jo Listime Agjensie** (no agency listings).
- The UI sends it to `POST /api/preferences` (`{skip_agencies: bool}`). The server keeps it in `PREFERENCES` (guarded by `STATE_LOCK`) and reports it in `/api/scrape/status` under `preferences`.
- `start_scrape()` takes a snapshot of `PREFERENCES` and passes it to `run_scrape()` → `Engine.main(skip_agencies=...)` → `create_browser_instance(skip_agencies=...)`. Changing it mid-scrape only affects the next run. A terminal run (`python3 -m Scraper.Engine`) keeps everything.
- MerrJep marks agency listings on the **results card** with `<span class="label label-default isstore">Agjent për shitje të pronës</span>` (`AGENCY_SELECTOR` in the scraper). The check happens before clicking, so a skipped listing costs no page load and no `Rest_Time` wait. Skipped UPIDs are not saved.
- Most MerrJep listings are agency ones (52 of 55 on page 1 on 2026-11-04), so with this on, a demo run walks through several pages to find 5 listings.
- It only filters new scrapes. Agency listings already in the DB stay there.
- Like auto mode, it lives in server memory, so restarting the server resets it to "everything".

### Phase 3 — More sites and polish
1. ~~Start the GazetaCelesi scraper~~ **Done (v1)**; see [Gazeta Celesi scraper](#gazetacelesial-scraper-working-v1). The engine runs both sites one after another. A full run is about 673 pages, so it needs known-UPID skipping (stop after a page of only known listings, since results are newest first) before auto mode is used with `Demo_Environment = False`.
2. ~~Automatic scraping mode~~ **Done**; see [Automatic scraping](#automatic-scraping). Follow-ups are listed in [For Alex's Claude](#for-alexs-claude).
3. Add a `price_history` table so price changes are kept instead of overwritten.
4. Move per-site settings such as the demo limit and wait times into `config.py` where it makes sense.
5. Replace the fixed `wait_for_timeout(5000)` with element-based waits.
6. Use `Request1.py` (HTTPX) where JS rendering isn't needed. It is faster than Playwright.
7. Add proper logging to the engine.
8. Later: agent tables and features, and a `status` column on listings (called, interested, sold…).

### Phase 4 — Ship it as a self-updating desktop app
The architecture and the reasons for it are in **"Product architecture" in `CLAUDE.md`** (agreed 2026-11-04). In short: PyInstaller onedir builds for Windows (an Inno Setup per-user install) and macOS (a signed, notarized `.app` in a `.dmg`), the UI in the normal browser with a tray icon, per-agent SQLite in the user data folder, and a signed `latest.json` on a public releases repo for auto-updates. Work in this order:
1. `requirements.txt` with pinned versions.
2. `Scraper/Version.py` (`__version__`), and show the version in the UI.
3. `Scraper/Paths.py` (`platformdirs`, frozen vs dev), and move every path in `DB.py`, `Output.py` and `Server.py` to it.
4. `settings.json` for agent settings (the auto interval, "Jo Listime Agjensie"), loaded at startup.
5. DB migrations with `PRAGMA user_version` instead of "delete the DB".
6. Logging to files (windowed builds have no console, and `sys.stdout` is `None`).
7. The `HomeSweeper.py` launcher: single instance, free port, open the browser, tray icon.
8. First-run Chromium download into user data, with a progress page.
9. A PyInstaller spec, built and tested by hand on Windows first, then macOS.
10. The Windows installer (Inno Setup) and the macOS `.dmg`.
11. Code signing is deferred: the owner installs each copy by hand (see "Code signing" in `CLAUDE.md`). Keep the macOS ad-hoc signature.
12. `Updater.py` and the helper: signed manifest, sha256 check, no downgrade, never during a scrape, rollback.
13. A GitHub Actions release pipeline publishing to the public `HomeSweeper-releases` repo.

## Repository housekeeping
- `.gitignore` excludes test folders and files, Python caches and `Output/`. `Data/` still needs adding (Phase 1, step 1).
- The old `Test/` folder has been deleted. Dev utilities now live in `Tools/`.

## For Alex's Claude

### Automatic scraping mode: built (this was Alex's task, now done)
The base version is in, following the spec that used to be here: an on/off button plus an interval dropdown in the UI, a timer thread inside the server, every run going through `SCRAPE_LOCK`, the wait counted from the end of each run, and a countdown in the status. See [Automatic scraping](#automatic-scraping) for how it works.

Still open, if you want to pick these up:
- ~~**Most important:** Phase 1, step 6, `get_known_upids(site)`~~ **Done**, together with turn-based scraping (see [Turn-based scraping](#turn-based-scraping-both-sites)). Each auto round is now one turn of up to 50 new listings per site.
- Make sure `Headless = True` in `config.py` before staff use auto mode, so a Chrome window doesn't pop up every round.
- Remember auto mode across server restarts (it currently lives in memory and turns off on restart).
- Only run during working hours, with the hours set in `config.py`.
- Add a little random jitter to the interval, so the requests don't arrive on an exact clock.
- ~~When there are more sites (GazetaCelesi), go through them one after another inside the same lock.~~ Done: `Engine.main()` loops over `SCRAPERS`, all inside the one `start_scrape()` lock.


## To run the Engine use the following command:
Run from the project root (needed for the `Scraper.` / `MerrJep_al.` imports):

    python3 -m Scraper.Engine        (Windows: py -3.14 -m Scraper.Engine)
    python3 -m Scraper.Server        (web UI at http://127.0.0.1:8000)
    python3 -m Tools.FetchHTML <url>

Dependencies used: `playwright` (plus `playwright install chromium`), `openpyxl`, `rich`, `httpx`, `requests`, `beautifulsoup4`. Phase 2 adds `fastapi` and `uvicorn`.

## Errors and Potential Errors / Fixes
At the moment we test and scrape each site one at a time to check errors and exported data. Later we must use asynchronous processes and unique Playwright drivers so each site is scraped safely and quickly in its own instance.

Known gotchas:
- If `listings.xlsx` is open in Excel, saving fails. Close it and rerun. Phase 1, step 7 removes this problem for the scraped data.
- The MerrJep scraper keeps its state (`URL_QUEUE`, `PROCESSED_UPIDS`, `SCRAPED_LISTINGS`, `PAGE_COUNT`) in module-level globals. Calling `create_browser_instance()` twice in one process continues from the old state instead of starting fresh. **This must be fixed before the rescrape loop or the web UI.**
- Listings without JSON-LD `Product` data are skipped and logged as failures.
- The server sends `Cache-Control: no-cache` on every response (`no_stale_ui` middleware in `Server.py`). Without it, browsers kept an old `style.css`/`app.js` after an update, so new UI elements looked unstyled.
- `create_tables()` won't add new columns to an existing database (see the Database section).

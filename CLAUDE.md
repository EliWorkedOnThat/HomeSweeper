# CLAUDE.md

Guidance for Claude Code sessions working on HomeSweeper.

## What this project is
HomeSweeper scrapes Albanian real-estate listing sites, stores the listings in SQLite and exports them to Excel. It is being built into a **product sold to real estate agents who are not technical**: a double-click desktop app (a local FastAPI server plus a plain HTML/CSS/JS UI in their normal browser) that updates itself whenever a new version is released. Keep it simple for them: no Python, no console, no extra servers, no Node/build steps. How the finished product is built and shipped is in [Product architecture](#product-architecture-target-for-the-finished-app). Follow it for any packaging, path, settings or update work.

**`NOTES.md` is the source of truth for the plan, the DB schema and the known gotchas.** Read it before any non-trivial change, and update it (including the `_Last updated_` date) when you change behaviour or finish a plan step.

## Run
The code must run on **Windows, Linux and macOS**. Linux is a dev platform only; shipped builds are Windows and macOS (see below). Use `pathlib` paths and no OS-specific launchers (no `.bat`/`.sh`). During development everything is started from a console.

Run from the project root. The imports are package-style (`Scraper.`, `MerrJep_al.`, `Tools.`), so running a file directly breaks them. Use `python3` on Linux/macOS and `py -3.14` on Windows:

    python3 -m Scraper.Engine               # one scrape in the terminal
    python3 -m Scraper.Server               # web UI at http://127.0.0.1:8000, Ctrl+C to stop
    python3 -m Tools.FetchHTML <url>        # save a page's HTML to Tools/HTML/ (--fast = plain HTTP, --show = visible browser)

Deps: `playwright` (+ `playwright install chromium`), `openpyxl`, `rich`, `httpx`, `requests`, `beautifulsoup4`, `fastapi`, `uvicorn`. There is no requirements file and no test suite yet.

## Layout
- `Scraper/Engine.py` is the orchestrator. `main()` connects to the DB, creates the tables, runs the MerrJep scraper, calls `save_listing()` for each listing, exports to Excel, and returns `{found, saved, failed}` (or `None` if the DB can't be opened).
- `Scraper/DB.py` is the **only** file that touches SQLite: `connect_to_db()` (WAL on), `create_tables()`, `save_listing(connection, listing, site)` (an upsert on `(site, upid)`), `get_listings(connection, site=None)` (dicts with `images`/`attributes` decoded from JSON).
- `Scraper/Output.py` has two functions. `save_listings(listings, site)` appends to `Output/listings.xlsx` and is used by the engine. `build_workbook(listings)` returns fresh `.xlsx` bytes from DB rows and is used by the UI's export download.
- `Scraper/Server.py` is the FastAPI server, kept as a thin layer with no scraping logic. Its endpoints are `GET /api/listings`, `GET /api/scrape/status` (the running/last turn, plus continuous mode: next turn, turn count and new listings this session), `POST /api/auto/start` (play: `{interval_minutes}` from `AUTO_INTERVALS`, the break between turns) and `POST /api/auto/stop` (pause: sets `PAUSE_EVENT`, so a running turn stops early and saves what it has), `POST /api/preferences` (`{skip_agencies}`, the "Jo Listime Agjensie" dropdown, snapshotted when each scrape starts and passed to `Engine.main(skip_agencies=...)`), and `GET /api/export`. It serves `UI/` at `/` and binds to `127.0.0.1:8000` only. Every turn starts through `start_scrape()`, which holds `SCRAPE_LOCK`, and `auto_loop()` (a background thread that wakes on `AUTO_WAKE`) is its only caller. Never start a scrape any other way, and `STATE_LOCK` guards the shared status dicts.
- `UI/` (`index.html`, `style.css`, `app.js`) is plain HTML/CSS/JS with no build step. It loads every listing once, then filters and sorts in the browser, and polls the status every 3 s during a scrape. Listings are shown as a flexbox grid of cards that display every scraped field. Every flex child has `min-width: 0`, so nothing overflows its box. Text is clamped, and `markOverflow()` makes any cut-off box clickable; it opens the full content in a shared `<dialog>` preview. The same dialog is used for the photo gallery and the "All details" view. It builds DOM nodes with `textContent`, never `innerHTML`, because the data is scraped.
- `Tools/` holds dev utilities. `FetchHTML.py` saves a URL's HTML to `Tools/HTML/` (git-ignored) so a site's markup can be studied before writing its scraper. It uses Playwright by default and HTTPX with `--fast`.
- `Scraper/config.py` holds the global settings: `Demo_Environment` (True means 5 listings per run), `Headless` (True means no browser window), `Rest_Time` (s between listings), `Database_Name`. The auto-scrape interval is **not** in config; staff pick it in the UI.
- `MerrJep_al/PlaywrightScraper1.py` is the working scraper. `create_browser_instance(skip_agencies=False)` returns a list of listing dicts. With `skip_agencies=True` it skips any results card containing `span.isstore` ("Agjent për shitje të pronës") before clicking into it. `ATTRIBUTE_COLUMNS` maps Albanian tag labels to the standard DB columns, with `parse_number()` and `combine_phones()` as helpers.
- `MerrJep_al/Request1.py` is an HTTPX `fetch()` that nothing uses yet.
- `GazetaCelesi/PlaywrightScraper.py` is the second scraper. `scrape_gazetacelesi(skip_agencies=False)` reads each results page's embedded Next.js JSON (`script#__NEXT_DATA__`) and never opens individual listings; details are in `NOTES.md`. `Request.py` there is still empty.
- `Engine.SCRAPERS` lists `(site key, scrape function)` pairs. To add a site, write its scraper and add one line there.
- `Data/Homesweeper.db` and `Output/` are generated and git-ignored.
- `Analyze.md` is a scratchpad for HTML samples and URLs.

## Rules / conventions
- **All SQL lives in `DB.py`.** Scrapers and the engine call its functions, so a later PostgreSQL switch only touches that file. Every write goes inside `with connection:`.
- **Scraper contract:** each site exposes a function that returns a list of dicts with the same keys (`upid, url, title, description, price, currency, phone, seller, published_date, published_time, listing_type, size_m2, rooms, condition, city, address, posted_by, images (list), attributes (dict)`). The engine saves them with a site key (`"merrjep"`, later `"gazetacelesi"`). Each site maps its own labels to the shared English filter columns and keeps the raw tags in `attributes`.
- `CREATE TABLE IF NOT EXISTS` never alters an existing table. In dev, delete `Data/Homesweeper.db` after a schema change. Once real data exists, use `ALTER TABLE`.
- **All UI text is Albanian.** The product is sold to Albanian staff, so every label, button, placeholder, tooltip, `aria-label`, alert and message in `UI/` and every user-facing filename (e.g. the Excel export) is in Albanian. Code, comments, API field names and console logs stay in English. Numbers use Albanian style (`185.000`, `72,5`) and times are shown in Tirana time (`03.11.2026, 16:01`) via `formatNumber()`/`localTime()` in `app.js`, which are formatted by hand because browsers don't reliably ship `sq-AL` locale data.
- Keep the existing code style: `#Comment` headers above each function, `rich` coloured prints for logging, `Path(__file__)`-relative paths.
- Work on the `dev` branch. `main` is the PR target. Commit only when asked.

## Product architecture (target for the finished app)
Agreed on 2026-11-04. Nothing below is built yet; the step-by-step plan is Phase 4 in `NOTES.md`. If a change would contradict this section, discuss it first and update this section rather than drifting from it.

### Decisions
| Topic | Decision | Why |
|---|---|---|
| Users | Non-technical real estate agents, **almost all on Windows**. macOS must work too. | Windows is the main target and gets tested first. macOS is a supported platform, not an afterthought. |
| Data | **Each agent has their own separate data** in a local SQLite file. There is no shared or central DB. | One file per PC and no server to run. Never add a feature that needs a central database. |
| UI | Opens in the agent's **normal browser**, with a **system tray / menu-bar icon** (`pystray`) to reopen or quit. | No pywebview: browsers are on every PC and behave the same everywhere. |
| Licensing | **Sold without licence keys.** No activation, no telemetry. | Nothing phones home except the update check. |
| Source code | Stays in the **private** repo. | Builds are published from a separate **public releases repo** (see Updates). |
| Platforms shipped | Windows (x64) and macOS (Apple Silicon and Intel, as one universal build or two builds). | Linux builds are not shipped, but the code stays cross-platform. |

### Packaging
- **PyInstaller, `--onedir`, windowed (no console).** `--onefile` is not used: it starts slowly because it unpacks on every launch, and it triggers more antivirus false positives. PyInstaller can't cross-compile, so each OS is built on its own CI runner (see the release flow).
- **Windows:** an **Inno Setup** installer that installs **per user** to `%LOCALAPPDATA%\Programs\HomeSweeper`, with no admin rights needed. Per-user is required so the updater can replace the files without a UAC prompt. The installer adds a Start menu shortcut, an optional desktop shortcut and an uninstaller.
- **macOS:** a `HomeSweeper.app` inside a `.dmg` (drag to Applications).
- **Chromium is not bundled.** On first launch the app runs Playwright's driver to download Chromium into the user data folder (`PLAYWRIGHT_BROWSERS_PATH` = `<data>/browsers`). While it downloads, the browser shows an Albanian "Duke përgatitur…" page. When an update bumps the Playwright version, the matching Chromium is downloaded the same way. We keep Playwright's own Chromium rather than the user's Edge or Chrome, so `launch_browser()`'s headless setup behaves the same on every PC.
- In the shipped app, `Demo_Environment = False` and `Headless = True`, always.

### Where files live
The app folder is **replaced on every update**, so nothing the agent creates may be stored there.
- All paths come from **one module, `Scraper/Paths.py`** (to be created). Never build paths ad hoc elsewhere.
  - **App files** (`UI/` and other bundled resources): the project root in dev, `sys._MEIPASS` when frozen (`getattr(sys, "frozen", False)`).
  - **User data**: `platformdirs.user_data_dir("HomeSweeper")`, which is `%LOCALAPPDATA%\HomeSweeper` on Windows and `~/Library/Application Support/HomeSweeper` on macOS. In dev it stays the project's `Data/` and `Output/`, so nothing changes while testing.
  - Inside user data: `Homesweeper.db`, `exports/`, `settings.json`, `logs/`, `browsers/`, and `update/` (downloads in progress, plus the previous version for rollback).
- The current `Path(__file__)`-relative paths in `DB.py`, `Output.py` and `Server.py` move to `Paths.py`. That is the only allowed exception to the "`Path(__file__)`-relative paths" convention below.
- **Settings the agent changes** (the auto interval, "Jo Listime Agjensie", and later working hours) are saved to `settings.json` and survive restarts and updates. `config.py` stays for developer constants only.
- **DB schema changes must migrate**; never delete the file, because agents' DBs hold real data. Store a schema version in `PRAGMA user_version` and run ordered `ALTER TABLE` steps in `DB.py` from `create_tables()`. Back up the DB file to `update/` before migrating.

### Launcher
- The entry point `HomeSweeper.py` (to be created at the project root) is what PyInstaller freezes. It does four things:
  1. Sets up rotating logs in `<data>/logs/`. In a windowed PyInstaller build `sys.stdout` and `sys.stderr` are `None`, so `rich` printing would crash. Either redirect them to the log, or route all logging through a small wrapper that uses `rich` in dev and the log file when frozen.
  2. Enforces **one instance**: a lock file in user data holds the port. If the app is already running, a second launch just opens the browser to it and exits.
  3. Picks a **free port on `127.0.0.1`** (never `0.0.0.0`), starts uvicorn in a thread, and opens the default browser with `webbrowser.open`.
  4. Shows the tray icon with Albanian items: **Hap HomeSweeper** (open), **Kontrollo për përditësime** (check for updates) and **Mbyll** (quit; it stops a running scrape cleanly first).
- `python -m Scraper.Server` keeps working for development.

### Updates
**Hosting:** a separate **public GitHub repo, `HomeSweeper-releases`**, holds only GitHub Releases: the installers, the update archives and the manifest, with no source. CI in the private repo publishes there using a fine-grained token stored as a CI secret. The app downloads from the fixed URL `https://github.com/<owner>/HomeSweeper-releases/releases/latest/download/latest.json`, so there is no server to run and no token inside the app. If GitHub ever becomes a problem, any static host (for example Cloudflare R2) works the same way.

**Manifest** (`latest.json`, plus a detached signature in `latest.json.sig`):
```json
{
  "version": "1.3.0",
  "released": "2026-12-01",
  "min_version": "1.0.0",
  "notes_sq": "Çfarë ka të re…",
  "windows": {"url": "…/HomeSweeper-1.3.0-windows.zip", "sha256": "…", "size": 0},
  "macos":   {"url": "…/HomeSweeper-1.3.0-macos.zip",   "sha256": "…", "size": 0}
}
```

**Rules for the updater** (`Scraper/Updater.py` plus a tiny separate helper exe):
- Check at startup, then every 6 h, and whenever the tray item is clicked. Compare versions with `packaging.version.Version`, never as strings.
- **Verify the Ed25519 signature of `latest.json`** with the public key compiled into the app, then verify the **sha256 of the download**. If either check fails, discard the download and log it; never install it. The private key lives only in CI secrets and offline with the owner, never in the repo. Without these checks, the updater is a way to push malware to every agent.
- **Never downgrade.** Ignore a manifest whose version is older than the running one.
- The UI shows a banner such as "Version 1.3.0 is available" with a **Përditëso tani** (update now) button. The update installs on that click, or on quit. **Never update while a scrape is running**: take `SCRAPE_LOCK` (or wait for it) first.
- A running app can't overwrite itself (Windows locks the exe). So the app downloads and unpacks into `update/`, starts the **helper**, and quits. The helper waits for the app to exit, moves the current install to `update/previous/`, moves the new one in, and relaunches. If the new version fails to start (no "started OK" marker within about 60 s), the helper **restores `update/previous/`**.
- Updates replace the whole app folder, which is simple and reliable. No delta patches for now.
- `min_version` is an escape hatch for breaking changes to the updater itself: installs older than it are told to download the installer again instead of auto-updating.

### Code signing (deferred, decided 2026-11-04)
Not doing it for now. **The owner installs the app on every agent's PC personally**, so the first-install warnings are clicked through by them and agents never see them. That only works because of these rules:
- **Windows:** SmartScreen ("Windows protected your PC") only triggers on files carrying the browser's Mark-of-the-Web. On the first install the owner chooses "More info → Run anyway". Updates are downloaded by our own updater with `httpx`, which doesn't add that mark, so they install silently. **Never hand agents an installer link to download in their browser**; that brings the warning back.
- **macOS:** on the first install the owner right-clicks → Open (or runs `xattr -dr com.apple.quarantine /Applications/HomeSweeper.app`). Apple Silicon still requires an **ad-hoc signature** (PyInstaller does this by default; keep it, and re-sign ad-hoc with `codesign -s -` if the bundle is modified after the build). The updater must not set `com.apple.quarantine` on the files it writes.
- If the product ever gets sold to people the owner doesn't install for, signing becomes required: Azure Trusted Signing (or an OV certificate) on Windows, and Apple Developer ID plus notarization on macOS ($99/year). Updates must be signed the same way.

### Versioning and release flow
- **There is one version source: `Scraper/Version.py`**, with `__version__ = "X.Y.Z"` (semver). The UI footer, the updater and the build all read it.
- To release: bump `__version__`, commit, merge to `main`, and push the tag `vX.Y.Z`.
- **GitHub Actions** in the private repo runs on the tag, as a matrix of `windows-latest` and `macos-latest`. It installs pinned deps from `requirements.txt`, runs PyInstaller, signs the build (and notarizes it on macOS), builds the installer or dmg and the update zip, computes the sha256s, writes and signs `latest.json`, and uploads everything to a new release in `HomeSweeper-releases`.
- Pin every dependency to an exact version in `requirements.txt` so builds are reproducible. Upgrade Playwright deliberately, because it changes the Chromium download.
- Scraper fixes (sites change their markup) ship through this same flow, so a fix for a broken scraper reaches every agent within hours.

## Current status (2026-11-04)
Phase 1 (connect the DB) is partly done. `NOTES.md` lags a little here: `Data/` is already in `.gitignore`, and the unused `sqlite3` import is already gone from the scraper.
- Done in Phase 1: WAL, the `save_listing` upsert, `get_listings`, and the engine saving to the DB before the Excel export.
- Phase 1 is finished: `get_known_upids(site)` is in, and both scrapers skip known UPIDs before opening them. A run is now **one turn** of up to `Listings_Per_Turn` (50) new listings per site (newest first, then it continues older listings from `RESUME_PAGE`). See "Turn-based scraping" in `NOTES.md`.
- Phase 2 (the local web UI) has a first version working: the server and `UI/`, started from the console. A double-click launcher is deferred until testing is done, and it must be cross-platform. Possible next steps: show live progress during a scrape (the status currently only says running/done), a listing detail view, and the config toggles in the UI.
- The **"Jo Listime Agjensie" preference** is done: the top-bar dropdown calls `POST /api/preferences`, and the scraper skips agency cards. It lives in server memory for now and moves to `settings.json` in Phase 4.
- **Phase 4 (packaging and auto-update)** follows the [Product architecture](#product-architecture-target-for-the-finished-app) section above. None of it is built yet.
- Scraping is **continuous with play/pause**. One **▶ Nis mbledhjen / ⏸ Pauzë** button runs turns forever, with a break from the dropdown (1/5/10/15/30/60 min) in between. Pause sets `PAUSE_EVENT`, which reaches the scrapers as `should_stop`, so a running turn ends within seconds and still saves what it found. See "Automatic scraping" in `NOTES.md`. Remaining follow-ups are under "For Alex's Claude".
- Phase 3 (GazetaCelesi, price history) is next after that. Details are in `NOTES.md`.

## Gotchas
- The MerrJep scraper keeps its state in module globals. `create_browser_instance()` now calls `reset_state()` first, so repeated runs in one process start fresh, but two runs must never overlap (the server's lock enforces this).
- Scrape state lives in server memory, so restarting the server resets the status pill. The listings themselves are safe in the DB.
- Playwright headless mode is controlled by `Headless` in `config.py`. It is currently `False` in the repo, so a window shows for debugging; the shipped app always uses `True`. When it is `True`, `launch_browser()` in the MerrJep scraper uses `channel="chromium"` (Chrome's newer headless mode, which has real plugins and the real GPU) and swaps `HeadlessChrome` for `Chrome` in the user agent, so headless looks like the visible browser to sites. New scrapers should copy this helper. Page loads still use fixed `wait_for_timeout(5000)` waits. Listings with no JSON-LD `Product` are skipped.
- The server sends `Cache-Control: no-cache` on every response (the `no_stale_ui` middleware). Without it, browsers keep an old `style.css`/`app.js` after a change and new UI elements look unstyled. Keep it; it matters even more once updates ship.
- Saving the Excel file fails if `listings.xlsx` is open in Excel (it warns and doesn't crash).
- `published_date` is raw Albanian text (e.g. `tet 03 2026`) and isn't normalised yet. `first_seen`/`last_seen` are UTC.

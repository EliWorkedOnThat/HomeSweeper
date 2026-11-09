# HomeSweeper project notes

_Last updated: 2026-11-09_

## Purpose
HomeSweeper is an early-stage collection of real-estate listing scrapers. Each target site has its own folder for its site-specific scraper code (Playwright browser scraper + direct HTTP request scraper). Shared coordination, configuration, database and output logic lives in `Scraper/`.

The end goal is an app that **non-technical staff** use at work. That's why the plan below prefers simple, reliable choices that need no extra installs.

## Project layout
- `MerrJep_al/` — MerrJep.al scrapers (folder was renamed from `MerrJep.al` so it can be imported as a Python package).
  - `PlaywrightScraper1.py` — the working scraper. Exposes `create_browser_instance(skip_agencies=False, known_upids=(), should_stop=None)`, which runs one turn and returns a list of listing dicts.
  - `Request1.py` — HTTPX `fetch()` with browser-like headers. Not used by the engine yet.
- `GazetaCelesi/` — Gazeta Celesi scrapers.
  - `PlaywrightScraper.py` — the working scraper. Exposes `scrape_gazetacelesi(skip_agencies=False, known_upids=(), should_stop=None)`, which runs one turn and returns a list of listing dicts. See [Gazeta Celesi scraper](#gazetacelesial-scraper-working-v1).
  - `Request.py` — empty placeholder.
- `Duashpi_al/` — Duashpi.al scrapers (underscore, not hyphen, so it imports as a Python package).
  - `PlaywrightScraper2.py` — the working scraper. Exposes `scrape_duashpi_al(skip_agencies=False, known_upids=(), should_stop=None)`, which runs one turn and returns a list of listing dicts. See [Duashpi scraper](#duashpial-scraper-working-v1).
  - `Request2.py` — empty placeholder.
- `Scraper/Engine.py` — orchestrator. Runs every site in `SCRAPERS` one after another (MerrJep, then Gazeta Celesi, then Duashpi) and saves each site's results with its own site key. A crash in one site is logged and the next site still runs. Returns `{found, saved, failed, sites: {site: {...}}}`.
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
- Page URLs come from `page_url(n)`: `BASE_URL` for page 1, then `BASE_URL?Page=N`. It runs in [turns](#turn-based-scraping-all-sites) and stops when a page has no listing cards.
- On each results page it collects unique `data-product-id` values (UPIDs), drops the ones already seen, then opens each new listing, scrapes it and goes back.
- On each listing page it pulls:
  - JSON-LD `Product` data: title, price, currency, description and images. If a listing has no JSON-LD, it is skipped.
  - HTML tag attributes (`.tags-area`), stored as a dict.
  - Description text, with the JSON-LD description as a fallback.
  - Images, with the gallery links as a fallback.
  - Seller name.
  - Phone number, read from two places: the contact button (`button .main-contact bdi`) and the `tel:` contact link. `combine_phones()` keeps one number if both match by digits, joins them with `; ` if they differ, and returns `None` if neither exists.
  - Published date and time.
- It checks that the UPID in the JSON-LD matches the card it clicked on.
- It runs headless (no window) when `Headless = True` in `config.py`. The repo currently has `False` for debugging. In headless mode `launch_browser()` uses `channel="chromium"` and a user agent without "HeadlessChrome", so sites see the same fingerprint as the visible browser. It waits a fixed 5 s after each page load and rests `Rest_Time` seconds after each listing.

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

### Duashpi.al scraper (working, v1)
- Works like MerrJep: results cards are short, so each **new** listing is opened (scroll to the card with `scan_card()`, click, read, `go_back()`), resting `Rest_Time` + up to 2 s between listings and between pages. Results: `https://duashpi.al/kerko-prona?sort=new&page=N`; the last page is the highest `a[data-page]` (4651 on 2026-11-04).
- **Cookie popup:** a "Cookies dhe privatësia" banner with a full-page `#backdrop` blocks every click. `accept_cookies()` clicks `#acceptAllButton` ("Prano dhe Mbyll") once per turn, after a short pause. If clicks start timing out with "intercepts pointer events", check this first.
- **Premium cards are ignored.** A page has ~30 normal cards (newest first) and ~78 premium cards mixed in between, not in date order and repeated across pages (`href` ends in `?pt=N`). `LISTING_SELECTOR` (`a.property-item-list-v2:not(:has(.premium-badge))`) keeps only the normal ones. Premium listings also show up as normal cards, so nothing is lost.
- UPID = the hex id in the url (`/prona/<upid>/<slug>.html`), checked against the opened page's url.
- **Cards move while we work.** New posts arrive every minute, so during the ~5 min it takes to open a page's 30 listings, the last cards get pushed onto the next page. Before each click the scraper checks the card is still there; if not, it is left unmarked (not failed) and gets picked up on the next page. Without this check every moved card waited out a 30 s click timeout and was wrongly added to `FAILED_UPIDS`. Clicks use `CLICK_TIMEOUT_MS` (10 s).
- Listing page: JSON-LD `Product` gives the description (title comes from the `h1`, which has no emoji). The seller is a second JSON-LD block, `LocalBusiness` (category "Real Estate Agency") for agency accounts, `RealEstateAgent` for everyone else; its `name` → `seller`, its `telephone` + the "Telefono …" button → `phone` via `combine_phones()`.
- **Price comes from the page, not the JSON-LD**: the JSON-LD converts LEK to EUR (40,000 LEK → 440 EUR). `parse_price()` reads `500 € / në muaj` / `12,000,000 LEK` → `EUR`/`ALL`; "Cmimi sipas kerkeses." → `None`.
- Date: the relative badge ("8 orë më parë") has the exact time in its `title` (`04/11/2026 12:34`), saved as `published_date`/`published_time` in Gazeta's format. The badge before it is the type: `Me qera` → `Jepet me qera`, `Për shitje` → `Shitet`.
- Facts under the title (`75 m²`, `2 Dhoma gjumi`, `1 Tualet`, `Kati 5`, `DSH-…`) go to `attributes` under fixed names (`FACT_LABELS`, the label changes with the count), plus `Detaje të tjera` (the tag list), `Kategoria` (breadcrumb) and `Nr. liçensës`. The view count is dropped. `Sipërfaqe` → `size_m2`, `Dhoma gjumi` → `rooms`. There's no condition field (`condition = None`).
- Location `Rruga 5 Maji, Tirane` → `city = Tiranë` (`CITY_NAMES`), `address = Rruga 5 Maji`.
- **Agencies** = `LocalBusiness` seller or a licensed-agent box (`a[href*="/profili/"]` with "Agjent i liçensuar") → `posted_by = Kompani`; everyone else gets `None` (an agency posting from a personal account looks private). Neither marker is on the card, so with "Jo Listime Agjensie" on, each agency listing costs one page load before it's skipped (then it's in `AGENCY_UPIDS`). Page 1 is mostly agencies, so that mode is slow on this site.
- **Tested 2026-11-04** (scraper on its own, no DB, `Headless = False`):
  - Demo turns of 2 and 5 listings worked.
  - One full non-demo turn of 50 took **6 minutes** (~7 s per listing): 27 from page 1, then on to page 2 for the other 23.
  - 0 failed, 0 duplicates. 3 cards pushed off page 1 by new posts were skipped there and picked up on page 2.
  - Every listing had title, description, phone, seller, date and time, type, m², rooms, city, address and photos (3–18 each).
  - Price was missing on only 1 of 50: "Çmimi: Për informacion, kontaktoni!", which is correct. Currencies were 30 `ALL` and 19 `EUR`.
  - `posted_by` was 36 `Kompani` and 14 empty.
- **Not tested yet:** "Jo Listime Agjensie" on Duashpi, a full Engine turn with all three sites saving to the DB, and headless mode on this site.

### Turn-based scraping (all sites)
A run is **one turn**, not a full crawl. Each turn, every site in `Engine.SCRAPERS` scrapes up to `Listings_Per_Turn` (50 in `config.py`, 5 in demo) **new** listings, which the Engine saves before starting the next site. Play in the UI repeats turns, with a break in between, until paused (see [Automatic scraping](#automatic-scraping)). `python3 -m Scraper.Engine` runs a single turn. A turn ends when the site has given 50 new listings, runs out of pages, or the user pauses, so a single turn can't run forever.
- `DB.get_known_upids(connection, site)` is passed to each scraper as `known_upids`. Known listings are skipped **before** clicking (MerrJep) or mapping (Gazeta). Each page logs "N new, M already seen".
- Every turn starts at **page 1**, so fresh posts are always picked up first (every site lists newest first). When a page has nothing new, the turn has caught up and jumps to `RESUME_PAGE`, the deepest page any turn has reached, to keep filling in older listings. Each turn digs a little deeper. Once everything is caught up, a turn is only a few page loads.
- Kept in memory between turns (module globals that `reset_state()` doesn't clear): `RESUME_PAGE`, `AGENCY_UPIDS` (agency cards skipped by "Jo Listime Agjensie", so they don't look new every turn) and `FAILED_UPIDS` (listings that errored, not retried until restart, so one broken listing can't stop a page from counting as caught up).
- **Restart caveat:** after a restart `RESUME_PAGE` is back to 1, so the first turn walks through saved pages (no clicks, but MerrJep still waits 5 s per page) until it finds 50 new listings. Fix: save `RESUME_PAGE` per site in `settings.json` (Phase 4, step 4).
- Known listings are no longer re-scraped, so `last_seen` now means "last scraped", not "still online", and price changes on known listings aren't picked up. A `price_history` re-check pass is a Phase 3 topic.
- Listings deleted from the site shift the pages up, so a few listings near `RESUME_PAGE` can be missed by the backfill. New posts shift pages down, which only causes harmless re-checks of known listings.
- Tested 2026-11-04: three Gazeta turns of 30 gave 90 unique listings (page 1 → jump to 2 → 3 → 5). Two Engine runs for both sites gave 10 + 10 per site, with no duplicates.

### Config (`Scraper/config.py`)
- `Demo_Environment = True` limits each turn to `DEMO_LISTING_LIMIT` (5) new listings per site, which is set in each scraper. Set it to `False` for real turns of `Listings_Per_Turn`.
- `Listings_Per_Turn = 50` is how many new listings each site scrapes per turn when demo is off.
- `Rest_Time = 3` is the number of seconds to rest after each listing (MerrJep) or between pages (Gazeta, plus up to 2 s of random extra), so requests aren't fired instantly and flagged.
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

### UI filters (`UI/app.js`)
All filtering runs in the browser on the listings the server hands over. `enrich()` derives one shared value per filter from the columns **and the raw `attributes` tags**, because each site words things differently and several fields only exist as tags. Nothing is written back to the DB, so these work on listings that are already saved.
- **Multi-select dropdowns** (`FACETS`): Lloji, Prona, Qyteti, Gjendja, Mobilimi, Karakteristikat, Postuar nga, Faqja. Ticking several options in one dropdown means *any of them*, except Karakteristikat, where the listing must have *all* ticked features. Listings without the field appear as "Pa të dhëna", and each option shows how many listings it would give with the other filters applied.
- **Range filters** (`RANGES`): Çmimi, Sipërfaqja, Çmimi për m², Dhoma gjumi, Banjo, Kati. Each one shows **fixed-range buttons** (`presets`, e.g. `300–500 €`, `2`, `Përdhe`); clicking the active button again clears it. The price buttons follow the Lloji filter: rent ranges for "Jepet me qera", sale ranges for "Shitet", both groups otherwise. **Diapazon i lirë** unfolds a two-thumb slider plus typed min/max in Albanian number style. A hand-picked range that matches no button keeps the slider open, and turning the slider off clears that range. Price, m² and €/m² use a log scale because rents and sale prices are orders of magnitude apart. A thumb left at the end means "no limit". By default a range filter hides listings that don't have the field. The "Shfaq edhe njoftimet që nuk e kanë…" checkbox keeps them.
- **Prices** are compared in EUR, with ALL converted at the fixed `EUR_TO_ALL = 100`. The price slider can show € or Lekë. A price of 0 is treated as "me marrëveshje" (no price). Sorting by price uses the EUR value, so EUR and ALL listings sort together.
- **Derived fields:** `_type` (from the Gazeta/Duashpi `Kategoria`, otherwise keywords in the title, which is the only source for MerrJep); `_condition` (merges `I ri`/`E re`, `Si i ri`, `Në gjendje të mirë`, `E rinovuar`, `Në ndërtim`, with Duashpi's `I rinovuar` read from its tags); `_furnishing`; `_features` (Ashensor, Parkim / Garazh, Ballkon / Tarracë); `_floor`; `_baths`; `_priceEur`; `_perM2`.
- **Coverage** (first full turn, 150 listings): m² 138, bedrooms 130, bathrooms 69, floor 64 (121 once the floor is also read from the text, see Pyet AI). MerrJep almost never gives floor or bathrooms. The "N nga M" on each slider shows the live count.
- The panel folds behind a **Filtrat** dropdown button on every screen size, with **Pastro filtrat** right beside it. Active filters show as removable chips under the panel.
- **"Shfaq edhe njoftimet pa të dhënën"** is on by default and clearing the filters puts it back on. Only the dashboard: the AI's `include_missing` stays off unless the question asks for it.
- **Two-thumb sliders:** the thumbs may pass each other (the lower one is the minimum, they swap back on release), so two thumbs on the same spot can always be dragged.
- **Blocked sellers:** "Blloko shitësin" on a card or in Detajet stores a row in `blocked_sellers`. `Server.without_blocked()` drops those sellers from the dashboard, the AI search and the Excel export; the listings stay stored and return on "Zhblloko". A seller is recognised by phone, and by name only for companies or when there is no phone (`Derive.block_rule_of`), because private sellers are often just "Frida" or "Klient".
- **Daily start ("Nis me orar"):** at the chosen time on the PC's clock the server turns continuous mode on, as if play was pressed, and it runs until paused. Saved in `Data/settings.json` (survives a restart). If the app is closed at that time nothing runs; if the PC was asleep it starts on waking.
- **Cards and pages:** the matching listings are split into pages (12 / 24 / 48 per page, with page buttons under the strip). One page sits in a **horizontal strip** of cards that are all the same size (`--card-w` × `--card-h` on `.cards-wrap`: 340 × 720 px on desktop, screen width minus 72 px × 740 px on phones). Inside a card, `.facts` takes whatever height is left and fades out at the bottom, so the boxes are ordered by importance; everything is still in **Detajet**. Changing a filter or the sort goes back to page 1, but new listings arriving after a scrape keep you on the page you're on (`render()` vs `draw()` in `app.js`).

### Server safety and speed (2026-11-06)
- **Only our own page can use the API.** `TrustedHostMiddleware` accepts only the Host `127.0.0.1`/`localhost` (stops DNS rebinding: a website pointing its domain at 127.0.0.1). `same_origin_only` rejects any POST/DELETE whose `Origin` isn't our own address with a 403. Browsers send `Origin` on every `fetch` POST, so the UI needs nothing special; scripts and tests must send `Origin: http://127.0.0.1:8000`.
- Every response has a Content-Security-Policy (`script-src 'self'`, no inline or eval scripts) plus `nosniff` and `no-referrer`. Never add inline `<script>`/`onclick`/`style=""` to the UI. Playwright tests that use string predicates (`wait_for_function("…")`) need `bypass_csp=True`.
- `/api/listings` and the AI endpoints share an in-memory copy of the derived listings (`cached_listings()`), rebuilt only when `DB.listings_version()` (count, max id, max last_seen) changes. Responses are gzipped.
- `DB.get_listings` raises `DBError` (shown as 503) instead of returning `[]`. Tables are created once in the lifespan, not per request.
- **Console encoding:** every entry point calls `Scraper.Console.utf8_output()` first; titles are `rich.markup.escape`d before printing. `Engine.run_site` guards each save, the auto loop catches its own errors, and quitting pauses a running turn and waits up to 30 s.
- **Demo through a tunnel:** set `HOMESWEEPER_PUBLIC_HOST=<public name>` before starting the server, or the Host check answers 400. It makes the listings (with phones), scraping controls and the paid AI reachable by anyone with the link, so use it for short demos only and never in the shipped app.
- The full review with the roadmap is in `REVIEW.md`.

### Pyet AI (`Scraper/AI.py`, `Scraper/Search.py`, `Scraper/Derive.py`)
Agents type a question in Albanian (or English) on the **Pyet AI** tab. The results show below the question, and every question is saved in the **Historia** sidebar beside them (on top, folded, on phones). Each search has its own address, `#/ai/<id>`, so the back button and a reload keep it; the old `#/historia` link redirects to `#/ai`.
- **How it works:** `AI.question_to_filters()` sends the question to Claude, plus the list of cities in the DB so it spells them the way the listings do. Claude only fills in a `Search.Filters` object, as structured JSON output. **It never writes SQL and never sees the listings**: only the question, the city names and up to 80 area names taken from listing addresses leave the PC (plus anything the agent types, e.g. a client's phone). `Search.search()` then runs the filters over the listings in Python.
- **Filters it can fill:** sale/rent, property type, places (matched against city *and* address, so "Golem" works), excluded places ("jo në Kombinat"), condition, furnishing, features, posted by, site, price (EUR or ALL), €/m², m², bedrooms, bathrooms, floor, posted within N days ("sot", "këtë javë"), has photos, has price, sort, limit. The seller's name and phone (digits, with and without 355) are part of the searchable text, so "what has 069… posted" works. Anything else goes into **`keyword_groups`**: free-text search in the title, description, address and tags. Every group must match, and any one word inside a group is enough. Words match from the start of a word, accents and case ignored. `exclude_keywords` drops listings.
- **Requirements vs wishes:** the prompt makes Claude split a question in two. Clear requirements ("kati 4", "deri 600 €", "jo agjenci") become hard filters. Vague, taste-based wishes ("diçka që duket bukur", "i ndritshëm", "zonë e qetë", "luksoz") become **`preferences`**: concrete signals with a weight 1–3 and a short Albanian reason. Example: "looks good" means condition new/renovated, words like modern/luks/design, and 5+ photos. Preferences never filter anything out; with `sort: best_match` the listings meeting more of them come first, and each result card shows the reasons it met in green ("✓ E rinovuar ose e re"). Preference kinds: `keywords`, `condition`, `feature`, `furnishing`, `many_photos`, `near_center`.
- **Around a number:** "rreth 120 mijë", "rreth 100 m²" set `price_target` / `size_target` plus a ±30% range, ranked closest first.
- **City centre:** Duashpi tags carry the distance ("2.4km nga qendra"), which `Derive.py` reads as `center_km` (shown on cards). "Në qendër" sets `center_max_km` (about 1.5) and `center_words` (qendër plus the central streets and areas). Listings with a distance are judged by it; the others must mention one of the words. As a wish it's a `near_center` preference instead. The "km nga qendra" tag is stripped from the searchable text, otherwise every Duashpi listing would match "qendra". Claude also gets the most common area names from the listings' addresses (`Search.known_areas`), so it uses names that actually occur.
- **Floor from the text:** most MerrJep and half of Gazeta listings have no floor field but say it in the text ("kati 4", "në katin e dytë", "kati i 9-te", "kati II"). `Derive.floor_in_text()` reads it when the field is missing (`derived.floor_from_text` marks those). Floor coverage went from 64 to 121 of 150.
- **Posting date:** `Derive.posted_date()` turns each site's `published_date` (Gazeta `04/11/2026`, MerrJep `nën 04 2026`) into an ISO date under `derived.posted`, falling back to `first_seen`. **Duashpi's `published_date` is the last bump, not the post date**, so for Duashpi the date comes from the UPID, a MongoDB ObjectId whose first 8 hex digits are the creation time. It drives `posted_within_days` and the "newest" sort.
- **Results page:** the question, what the AI understood, anything it couldn't express, and numbers for the results (count per site, typical rent and sale price as medians with the range, €/m², typical size). Chips are split into **Kërkesa** (filter) and **Dëshira** (only rank). Removing a chip or changing the sort re-runs the search through `POST /api/ai/search` **without calling the AI**, so it's free; "Rikthe filtrat e AI" restores the AI's filters. With best-match sorting the top 3 cards carry a #1–#3 badge. Before the first question the page lists what can be asked, with an example per kind.
- **Near misses:** when a search finds fewer than 6 listings, `Search.near_misses()` also returns up to 12 that fail exactly **one** requirement (never sale/rent or the property type), shown under "Pothuajse përputhen" with the missed requirement in red ("✗ Kati: 3"). Wrong-place misses are listed last. `Search.failures()` produces these notes and is also the normal match check.
- **Places:** a city ("Tiranë", also "Tirana", "Durrësi", "Vlora" via `Search.CITY_ALIASES`) is compared with the city column; an area or street must start a word in the address or title. `exclude_places` uses the same test.
- **Search text:** title, description, address, city, seller, every phone (digits, with and without 355) and the tags, except tags whose value says no ("Ashensor: Jo"). Keywords of 3+ letters match from the start of a word, shorter ones ("AC") only as whole words, and never right after "pa"/"nuk ka" ("pa ashensor").
- **Bedrooms and seller type:** `derived.bedrooms` comes from "N+M" in the category or title before the rooms column (MerrJep sometimes counts the living room). `derived.posted_by` treats Duashpi's empty value as "Person privat" (its scraper only marks agencies). Rents under 30 € or over 20.000 € count as no price (`Derive.RENT_EUR_RANGE`).
- **Wishes as numbers:** a `range` preference (`field` floor/price/size/…, `min`/`max`) ranks "mundësisht kat i lartë" or "e lirë dhe e bukur" without filtering.
- **Near misses never relax** sale/rent, the property type, or anything the agent excluded (places, words, agencies, sites).
- **Schema limit:** the API allows at most 16 "value or null" fields per schema, so Claude returns the number limits as one `ranges` list and `AI.expand_ranges()` turns it into the flat `Filters` fields. The API also rejects a schema whose compiled grammar is too large (400, "compiled grammar is too large"): nullable fields inside `preferences` caused it, so those use `none` and -1 for "not set". After changing the schema, run one real question (a rejected schema costs nothing).
- **Safety:** at most one question at a time, and a daily cap of `AI_DAILY_LIMIT_USD` (2 $) in `Server.py`.
- **Shared rules:** `Derive.py` works out property type, merged condition, furnishing, features, floor, bathrooms, price in EUR and €/m² from the columns and raw tags. `/api/listings` sends them under `derived`, and the dashboard filters use them too, so the AI and the dashboard always agree.
- **History:** table `ai_searches` (question, filters JSON, what was understood, result count, model, tokens, cost in USD). New table, so `create_tables()` adds it to an existing DB.
- **Endpoints:** `GET /api/ai/status`, `POST /api/ai/ask {question}`, `POST /api/ai/search {filters}` (both return `{ids, reasons, near}`), `GET /api/ai/history`, `GET /api/ai/history/{id}`, `DELETE /api/ai/history/{id}`.
- **API key:** the Claude API is billed separately from a Claude Pro/Max subscription (pay-as-you-go credits at console.anthropic.com → API Keys). The key is read from the `ANTHROPIC_API_KEY` environment variable, or from `Data/anthropic_api_key.txt` (git-ignored, read on every question so no restart is needed). **Never commit it.** Without a key the tab shows a setup note and questions return an Albanian error.
- **Model and cost:** `claude-opus-5-5` at `effort: "low"`, with `fallbacks: "default"` (if the model declines a question, the API retries it on Anthropic's recommended fallback model). The system prompt is cached. Expect about 2–5 US cents per question (questions are usually minutes apart, so the cached prompt is often re-written at 1.25×); each question's cost is shown on the results and in the history. Change `MODEL`/`EFFORT` (and the `PRICE_*` constants) in `AI.py`.
- **Testing:** `py -3.14 -m Tools.AIEval` runs 39 agent-style questions (vague wishes, the centre, "around", floors, dates, exclusions, phone numbers, extras like heating, market questions and things the data can't know) against the real API and checks the filters (costs a few tens of cents; `Tools.AIEval 5` runs only the first 5). Re-run it after changing the prompt, the schema or the model.
- **Before shipping to agents:** the key must not be built into the app (anyone could extract it). Decide between each agent using their own key, or a small relay that holds the owner's key (it conflicts with "nothing phones home"; see `CLAUDE.md` → Product architecture).
- **Not built yet:** Claude reading the descriptions of the top matches to judge vague wishes ("good light", "quiet street"). It would cost roughly 5–10× more per question and send listing text (including phone numbers) to the API, so it should be a separate, opt-in button.

### Output (`Scraper/Output.py`)
- `save_listings(listings, site)` appends rows to the `Listings` sheet in `Output/listings.xlsx`. It creates the file and headers if they are missing.
- Each attribute key gets its own column, and new keys add new columns.
- It skips rows whose `(site, upid)` is already in the sheet.
- If the file is open in Excel, it prints a warning instead of crashing.
- Every cell goes through `clean_cell()`: control characters such as `` are removed (openpyxl raises `IllegalCharacterError` on them, which used to break the whole file) and text is cut to Excel's 32,767-character limit. Fixed 2026-11-04: before this, one such character in a scraped listing failed the turn after the first site and broke the UI's Excel download for good.
- Rows are added with `append_row()`, which turns any cell openpyxl made a formula (text starting with `=`) back into plain text. Sellers write their own titles and descriptions, so `=HYPERLINK(...)` would otherwise become a working link in the agent's Excel. The text is kept exactly as written (no `'` prefix, which would show up in phone numbers like `+355…`). We never write real formulas. Fixed 2026-11-04.
- The Engine wraps the per-turn Excel save in `try`/`except`, so an Excel problem (bad character, renamed sheet, damaged file) is logged and never stops a turn. The data is already in the DB by then.

## To do next (prioritized, 2026-11-04)
The phases below hold the long-term plan. This is the short list of what to pick up next, most important first.

**Before agents use it for real**
1. **Set `Headless = True` and `Demo_Environment = False`** in `config.py` and do a long real run, for an hour or more, with play on and a 1–5 min break. Watch for blocks or captchas, slow turns, and listings that fail. The longest run so far is one full Duashpi turn (50 listings, 6 min, no problems); MerrJep and Gazeta have only had demo turns. A full turn of all three sites will take roughly 15 min, because MerrJep and Duashpi open every listing.
   - Also test Duashpi with "Jo Listime Agjensie" on (expect it to be slow), and run a full Engine turn so all three sites save to the DB.
2. **Persist the runtime state** to `settings.json`: play on/off, the break, "Jo Listime Agjensie", and each site's `RESUME_PAGE`. Today a restart pauses scraping and loses the backfill position (Phase 4, step 4).
3. **Live progress in the UI.** Show "site X, page N, M new so far" during a turn. The status currently only says running/done.
4. **Working hours.** Only scrape between set hours, so the PC isn't scraping at night.

**Data quality**

5. **Normalise `published_date`** to ISO (`2026-10-03`) for every site. MerrJep uses Albanian month abbreviations, while Gazeta and Duashpi use `04/11/2026`.
6. **Merge the condition wording in the scrapers too.** The UI already merges it for display and filtering (see UI filters), but the DB and the Excel export still hold each site's own wording. Do it across sites (`I ri` on MerrJep, `E re` on Gazeta, …), and check `Person privat` against MerrJep's real private-seller wording once one shows up. Duashpi has no condition field; its tags (`I rinovuar`, `E mobiluar`…) are in `attributes["Detaje të tjera"]` and could be mapped later.
7. **`price_history` plus a re-check pass.** Saved listings are never re-scraped, so price changes and removed listings go unnoticed. Gazeta is cheap to re-check, because its results JSON already has the price.
8. **Cross-site duplicates.** The same property posted on more than one site, or re-posted under a new ID, is stored twice. A fuzzy match on phone + size + price could flag them.

**Code health**

9. **Excel per turn:** `Output.save_listings()` appends to `Output/listings.xlsx` every turn, which grows forever. Decide whether to keep it, or only use the UI's export (Phase 1, step 7).
10. **MerrJep's fixed 5 s page wait** → wait for an element instead.
11. **A `requirements.txt`** with pinned versions (Phase 4, step 1), so the next person installs the same versions.
12. **Logging to a file** instead of only `rich` prints (needed for Phase 4 anyway).

**Product (Phase 4)**

13. **Packaging and auto-update**, in the order in [Phase 4](#phase-4--ship-it-as-a-self-updating-desktop-app) and `CLAUDE.md` → "Product architecture".

**Next site**

14. **Further sites follow the same contract** (see `CLAUDE.md` → Scraper contract): copy the turn helpers from either scraper and add one line to `Engine.SCRAPERS`.

## The plan (work in this order)

### Phase 1 — Connect the database (done, except step 7)
1. ~~Add `Data/` to `.gitignore`~~ **Done.**
2. ~~Remove the unused `import sqlite3` from the MerrJep scraper~~ **Done.** Only `DB.py` may import `sqlite3`.
3. ~~Turn on WAL mode in `connect_to_db()`~~ **Done.** The UI can read listings while a scrape is writing.
4. ~~Add these to `DB.py`~~ **Done**:
   - `get_known_upids(site)` returns a set of every saved UPID for that site.
   - `save_listing(listing, site)` inserts a new listing. If it already exists, it updates `last_seen` and the fields that can change (price, phone, description). Use `INSERT ... ON CONFLICT(site, upid) DO UPDATE`. Store `images` and `attributes` with `json.dumps`.
   - `get_listings(site=None)` returns the stored listings for the export, the UI and, later, the agents.
5. ~~In `Engine.py`, call `connect_to_db()` and `create_tables()` once at startup, then save each listing with `save_listing()`~~ **Done.**
6. ~~Skip known UPIDs before clicking~~ **Done** for every site (the Engine passes `get_known_upids(site)` to each scraper).
7. **Still open:** change `Output.py` to build the Excel file from `get_listings()`. (The UI's export download already does; the engine's per-turn append to `Output/listings.xlsx` doesn't.) The database is the main store and Excel is a report. If Excel is open, only the export fails and the scraped data is already safe.

Rules for this phase:
- **All SQL lives in `DB.py`.** Scrapers and the engine call its functions. If we ever move to PostgreSQL, only `DB.py` changes.
- Every write goes inside `with connection:` so a crash never leaves half-saved rows.

### Phase 2 — Local web UI
Staff won't use a terminal. The app will run as a **local website**: a Python server on the user's own PC, opened in their normal browser. That avoids device-specific installs and native app problems.

```
Browser (localhost:8000)  ──HTTP──▶  Python web server (FastAPI)
  HTML / CSS / JS                      ├── GET  /api/listings      → DB.get_listings()
  cards, filters, buttons              ├── GET  /api/scrape/status → running / done / error + counts + continuous mode
                                       ├── POST /api/auto/start    → ▶ play: auto_loop() runs Engine.main() turn after turn {interval_minutes = break}
                                       ├── POST /api/auto/stop     → ⏸ pause: the running turn ends early and saves
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
  1. **The scraper's globals would clash.** `PROCESSED_UPIDS`, `SCRAPED_LISTINGS` and `RESUME_PAGE` are module-level, so two runs would share them. Each run calls `reset_state()` at the start and would wipe the other's progress mid-scrape. Pages would be skipped or scraped twice, and the results would mix together.
  2. **Traffic to the site.** Two browsers hitting MerrJep at once doubles the request rate and undoes the `Rest_Time` pacing, which raises the chance of being blocked.
  3. **SQLite writes.** SQLite allows only one writer at a time. Two scrapes saving together would mostly just wait for each other, but it's needless contention.
- **How it's used:** `SCRAPE_LOCK.acquire(blocking=False)` doesn't wait for the lock. If a turn is already running, it fails immediately and `auto_loop()` checks again after `BUSY_RETRY_SECONDS`. The lock is then held for the whole scrape and released in the `finally` block of `run_scrape()`, so it's freed even if the scrape crashes.
- **Rule for new code:** anything that starts a scrape (a button, a timer, a new site) must go through this same lock. Never call `Engine.main()` from a second place without it. In practice, call `start_scrape()`: `auto_loop()` is its only caller (play just turns the loop on).

#### Automatic scraping
**Done, now as continuous play/pause (2026-11-04).** The separate "Mblidh njoftimet" (one run) and "Nis automatikun" buttons were merged into **one button: ▶ Nis mbledhjen / ⏸ Pauzë**. Play runs [turns](#turn-based-scraping-all-sites) one after another, forever, until paused. Each turn writes its new listings to the DB, and the page reloads them after every turn. The dropdown next to it is now the **break between turns** (`Pushim 1 minutë` … `Pushim 1 orë`, default 5 min). The blue label shows "Raundi tjetër pas 4:32 · 120 të reja" (the countdown plus the new listings since play was pressed). Its tooltip shows the turn count.
- **Pause works mid-turn.** `POST /api/auto/stop` sets `PAUSE_EVENT`. `Engine.main(should_stop=PAUSE_EVENT.is_set)` passes it to each scraper as `should_stop`, which is checked between listings and pages. The turn ends early (tested: 1.6 s), **what it found so far is saved**, and the remaining sites are skipped for that turn (`result.paused = True`). While it winds down, the status shows "Duke ndaluar… ruhen të gjeturat".
- Pressing play while a paused turn is still winding down clears `PAUSE_EVENT`, so that turn just carries on. If it had already ended early, the next turn starts straight away instead of waiting for the break.
- `AUTO["turns"]` and `AUTO["new_listings"]` count the session since play (reset on each play after a pause) and are sent in `/api/scrape/status` under `auto`.
- The one-turn endpoint `POST /api/scrape/start` was **removed** (the UI no longer used it). For a single turn from a console, run `python3 -m Scraper.Engine`.
- **No duplicates:** each turn skips known UPIDs (`get_known_upids`), and `UNIQUE(site, upid)` plus the upsert make a repeat save impossible anyway. Tested: 0 duplicate `(site, upid)` and 0 duplicate URLs after several turns. The same property posted on more than one site, or re-posted under a new ID, is still stored twice; detecting that needs fuzzy matching (phone + size + price), which isn't built.

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
1. ~~Start the GazetaCelesi scraper~~ **Done (v1)**; see [Gazeta Celesi scraper](#gazetacelesial-scraper-working-v1). The engine runs every site one after another, in turns.
2. ~~Add Duashpi.al~~ **Done (v1, 2026-11-04)**; see [Duashpi scraper](#duashpial-scraper-working-v1).
3. ~~Automatic scraping mode~~ **Done**; see [Automatic scraping](#automatic-scraping). Follow-ups are listed in [For Alex's Claude](#for-alexs-claude).
4. Add a `price_history` table so price changes are kept instead of overwritten.
5. Move per-site settings such as the demo limit and wait times into `config.py` where it makes sense.
6. Replace the fixed `wait_for_timeout(5000)` with element-based waits.
7. Use `Request1.py` (HTTPX) where JS rendering isn't needed. It is faster than Playwright. **On hold:** a bare HTTP client is easier for sites to spot, so we stay with Playwright for now.
8. Add proper logging to the engine.
9. Later: agent tables and features, and a `status` column on listings (called, interested, sold…).

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
- `.gitignore` excludes test folders and files, Python caches, `Output/`, `Data/` and `Tools/HTML/`.
- The old `Test/` folder has been deleted. Dev utilities now live in `Tools/`.

## For Alex's Claude

### Automatic scraping: built (this was Alex's task, now done)
It was built first as an on/off timer, then reworked on 2026-11-04 into **continuous play/pause with turns**. See [Automatic scraping](#automatic-scraping) and [Turn-based scraping](#turn-based-scraping-all-sites). Known-UPID skipping and running every site inside one lock are done too. **If Alex built his own version** of auto mode, keep the play/pause version and port anything his version does better into it (see "After pulling Alex's commits" below).

The remaining follow-ups are in [To do next](#to-do-next-prioritized-2026-11-04). Good ones to pick up:
- Item 2, persisting the state to `settings.json`.
- Item 3, live progress.
- Item 4, working hours.
- A little random jitter on the break between turns, so turns don't start on an exact clock. Gazeta already jitters between pages.

## Running the project
Run everything from the **project root**, because the imports are package-style (`Scraper.`, `MerrJep_al.`, `GazetaCelesi.`). Use `py -3.14` on Windows and `python3` on macOS/Linux. Python 3.10 or newer is required (developed on 3.14).

### First-time setup (fresh clone or new PC)
    py -3.14 -m pip install playwright openpyxl rich httpx requests beautifulsoup4 fastapi uvicorn anthropic
    py -3.14 -m playwright install chromium

There's no `requirements.txt` yet (to-do item 11), so this list is the source of truth. If someone adds a new import, add it here too.

### Start it
    py -3.14 -m Scraper.Server                    # the app: open http://127.0.0.1:8000, Ctrl+C to stop
    py -3.14 -m Scraper.Engine                    # one turn of every site in the terminal, no UI
    py -3.14 -m GazetaCelesi.PlaywrightScraper    # one Gazeta turn only, prints the listings, doesn't touch the DB
    py -3.14 -m MerrJep_al.PlaywrightScraper1     # one MerrJep turn only, doesn't touch the DB
    py -3.14 -m Tools.FetchHTML <url>             # save a page's HTML to Tools/HTML/ to study a site's markup

In the UI, choose the break between turns, then press **▶ Nis mbledhjen**. Press **⏸ Pauzë** to stop. New listings appear after every turn, and the blue label counts them.

Check `Scraper/config.py` before a run:
- `Demo_Environment = True` gives quick turns of 5 new listings per site. `False` gives real turns of `Listings_Per_Turn` (50).
- `Headless = False` shows the browser window so you can watch it work. `True` hides it.

### After pulling Alex's commits
1. **Commit or stash your own work before pulling.** The 2026-11-04 work (Gazeta scraper, turns, play/pause) touched `Engine.py`, `Server.py`, `DB.py`, `config.py`, both scrapers and all three `UI/` files. Work on `dev`; `main` is the PR target.
2. **Pull and resolve conflicts.** The likely spots are `Scraper/Server.py`, `UI/app.js` and `UI/index.html` (if Alex built auto mode his own way) and `MerrJep_al/PlaywrightScraper1.py`. Keep the contract the Engine relies on: each scraper takes `(skip_agencies, known_upids, should_stop)`, and every scrape starts through `start_scrape()`.
3. **Re-run the first-time setup** if Alex added a dependency (check his new `import` lines).
4. **The database:** if Alex changed the `listings` table, `CREATE TABLE IF NOT EXISTS` won't apply it. In dev, delete `Data/Homesweeper.db` and let it rebuild (the data is only test data). If nobody changed the table, keep the file.
5. **Smoke test:** with `Demo_Environment = True`, run `py -3.14 -m Scraper.Engine` twice.
   - Run 1 should save 5 per site.
   - Run 2 should log "N new, 5 already seen" and save 5 *different* listings per site.
   - Then start the server, press play, wait for one turn, press pause, and check that it stops within a few seconds.
6. **Update `NOTES.md`** (and the `_Last updated_` date) and `CLAUDE.md` for whatever Alex's commit changed.

## Errors and Potential Errors / Fixes
At the moment we test and scrape each site one at a time to check errors and exported data. Later we must use asynchronous processes and unique Playwright drivers so each site is scraped safely and quickly in its own instance.

Known gotchas:
- If `listings.xlsx` is open in Excel, saving fails. Close it and rerun. Phase 1, step 7 removes this problem for the scraped data.
- Every scraper keeps its state in module-level globals. `reset_state()` clears the per-turn part (`PROCESSED_UPIDS`, `SCRAPED_LISTINGS`) at the start of every turn. `RESUME_PAGE`, `AGENCY_UPIDS` and `FAILED_UPIDS` carry over on purpose (see [Turn-based scraping](#turn-based-scraping-all-sites)).
- MerrJep listings without JSON-LD `Product` data fail, are logged, and go into `FAILED_UPIDS`, so they aren't reopened until a restart.
- The server sends `Cache-Control: no-cache` on every response (`no_stale_ui` middleware in `Server.py`). Without it, browsers kept an old `style.css`/`app.js` after an update, so new UI elements looked unstyled.
- `create_tables()` won't add new columns to an existing database (see the Database section).

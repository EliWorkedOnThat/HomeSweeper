# HomeSweeper team review

_2026-11-06, branch `dev-1`._ Five reviewers went through the whole app in parallel:
- architecture and backend
- Pyet AI and search
- frontend, UX and design
- security, QA and DevOps
- scrapers and data pipeline

The first four could have their findings fixed straight away. The scraper reviewer was told **not to change any Playwright code** and only made suggestions.

The section below lists what was fixed in this pass. Everything else is a ranked roadmap, with the reason for each item and a rough size: S is under a day, M is a few days, L is a week or more.

The product constraints shape every recommendation (see `CLAUDE.md` → Product architecture):
- one desktop app per agent
- a local SQLite file
- no central server
- no telemetry

Where an idea only makes sense if HomeSweeper ever becomes a hosted service, it says so.

---

## Fixed in this pass

### Security
- **DNS rebinding blocked.** Any website the agent visited could point its domain at 127.0.0.1 and read every listing, including phone numbers, spend Claude credits and start or stop scraping. `TrustedHostMiddleware` now accepts only `127.0.0.1` and `localhost`.
- **Cross-site requests blocked.** Every POST and DELETE must carry our own `Origin` (`same_origin_only` in `Server.py`). Tested: a foreign Host gets 400, a foreign or missing Origin gets 403, and our own page works.
- **Security headers.** Every response now carries a Content-Security-Policy (no inline or eval scripts, no framing), `nosniff` and `no-referrer`. The API docs are off in the frozen app.
- **AI spending cap and lock.** Questions stop at $2 per day (`AI_DAILY_LIMIT_USD`), and only one question runs at a time. Saved searches still reopen for free.
- **Scraped links are checked.** Only `http(s)` URLs become links or images (`safeUrl()` in `app.js`).
- **`.gitignore`.** It no longer ignores every test file, which would have kept a test suite out of git. It now ignores `.env*`, `*.pem`, `*.key`, `secrets/`, `build/` and `dist/`.

### Reliability (data loss)
- **The emoji/cp1252 crash.** `Scraper/Console.utf8_output()` switches stdout to UTF-8 in the Server and Engine entry points.
- **Saving titles.** Saved titles are escaped for `rich`: a title containing `[/b]` crashed the save.
- **One bad listing no longer ends the turn.** `Engine.run_site` now guards each save, so one bad listing can no longer drop the rest of the turn.
- **The auto-scrape thread** survives errors. Before, any exception stopped auto mode for good while the UI still showed it as on.
- **Clean shutdown.** Ctrl+C or quit pauses the running turn and waits up to 30 s for it to save.
- **DB read errors** raise `DBError` and show as a 503 with an Albanian message. A broken DB no longer looks like "no listings".
- **SQLite settings.** `busy_timeout=5000` and `synchronous=NORMAL`. Tables are created once at startup, not on every request, and the per-request log spam is gone.

### Performance
- **Listings cache.** The derived listings stay in memory and are rebuilt only when the DB changes (`listings_version`). The dashboard and every AI search share the same copy.
- **Gzip** on API responses: the listings JSON is about 8–10× smaller.

### Pyet AI and search correctness
Each fix below was first shown to be a real bug with a script on the real data.
- **Places.**
  - "Tirana", "Durrësi" and "Vlora" now find their city. Before, "Tirana" found 5 listings instead of 108, and "Durrësi" 2 instead of 38.
  - A city is compared with the city column, so a Mullet listing on "autostrada Tiranë" is no longer in Tirana.
  - Areas must start a word, so "Ali" no longer matches "spitalit".
- **"Jo agjenci".** Duashpi leaves private sellers empty, which now counts as "Person privat": 58 results instead of 34. MerrJep still says "Kompani" on all 50 listings; see roadmap S4.
- **Near misses.** They never show something the agent excluded (a place, a word, agencies, a site).
- **"No" tags.** Tags such as "Ashensor: Jo" and "Ngrohje: Nuk ka" are no longer searchable as if the listing had the thing. Keywords right after "pa" or "nuk ka" don't match either ("pa ashensor").
- **Short keywords.** "AC" and "TV" match as whole words. Before, they were silently dropped and the search returned all 150 listings.
- **Phones.** Both numbers of a two-phone listing are searchable, and numbers written with spaces in descriptions ("069 721 1116") are found.
- **Bedrooms** come from "2+1" in the category or title first. MerrJep's rooms column sometimes counts the living room. The dashboard uses the same rule.
- **Impossible rents.** Rents under 30 € or over 20.000 € (a mislabelled sale, a missing zero) count as "no price", so "the cheapest" no longer starts with a 3,50 € rent.
- **New "range" wish.** It covers "mundësisht kat i lartë", "e lirë dhe e bukur" and "for a family" without throwing listings away.
- **Prompt fixes:**
  - exact city spellings
  - €/m² only for sales
  - no contradictory "family" rule
  - "jo kati i parë" means floor ≥ 1
  - the centre words no longer include "qendror", which also matched "ngrohje qendrore"
- **Corrected instead of rejected.** A `limit` of 0 or a negative day count from the model is now corrected. Before, the answer was rejected after it had already been paid for.
- **API client retries once,** so a dead network fails in about 3 min instead of about 6.
- **Known area names** sent to the AI are de-duplicated ("Golem" and "golem" count once). City names and HTML entities are removed.
- **Duashpi posting date.** It now comes from the listing ID (a MongoDB ObjectId holds the creation time), not the "bumped" date. 21 old listings were counting as "posted this week".

### UI
- **Dashboard search.**
  - Accents are ignored: "tirane" finds 110 listings, as "tiranë" does. Before, it found 17.
  - Phone numbers are found in either form.
  - The search box is read once per keystroke, not once per listing.
- **Cards fit on a 1366×768 laptop** (`--card-h` clamps to the window height).
- **Contrast fixes:**
  - dark-mode primary buttons: from 3.2:1 to over 6:1
  - "Telefono" button: from 3.8:1 to 5.5:1
  - muted text: 5.6:1
- **"Telefono" on a computer** copies the number ("069 721 1116") and shows a toast. `tel:` does nothing useful on Windows. Phones still dial.
- **Card text:**
  - Prices show as "650 €" and "40.000 Lekë".
  - €/m² is hidden for rents, which used to show "0 €/m²".
  - The footer shows "Duashpi · Postuar 04.11.2026" instead of a hash ID.
  - Details say "Përdhe" instead of "Kati 0" and use proper site names.
- **Errors and buttons.** A loading error shows with a "Provo përsëri" button, where before the page stayed silently empty. "Kërko me AI" is disabled when no API key is set.
- **Accessibility:**
  - Previews open with Enter or Space on every card.
  - The dialog has a name.
  - Status pills and the count are `aria-live`.
  - There is a visible focus ring.
  - Touch targets are 44 px on touch screens.

---

## Roadmap

### Phase 1: stop losing data and lock in what works (about 1–2 weeks)
| # | Item | Area | Size | Why |
|---|---|---|---|---|
| 1 | **Commit and merge `dev-1`.** About 1,500 lines of uncommitted work, including the new files `AI.py`, `Search.py` and `Derive.py`. | All | S | Work that isn't committed can be lost. |
| 2 | **Test suite (pytest).** See [Testing](#testing). Start with Derive, Search, DB and the API (security checks included). | QA | M | There are no tests today. The search bugs above would have been caught by a table of cases. |
| 3 | **`requirements.txt` with exact pins,** plus `requirements-dev.txt` (pytest, ruff, pip-audit). Build it from a clean venv with `pip-compile --generate-hashes`. | DevOps | S | Reproducible PyInstaller builds. Playwright is upgraded on purpose, never by accident. |
| 4 | **CI** (GitHub Actions): ruff, pip-audit, pytest on Windows, macOS and Ubuntu with Python 3.12. Run Windows **without** `PYTHONUTF8`, to catch the cp1252 problem coming back. | DevOps | S | |
| 5 | **Mask personal data in logs.** Phones and the full listing JSON are printed by the scrapers; the AI question is printed too. Use the last 3 digits only, or DEBUG level. | Security | S | Today it's harmless console output, but it becomes stored personal data once Phase 4 writes log files. |
| 6 | **DB migrations** using `PRAGMA user_version`, a `MIGRATIONS` list and a backup through `connection.backup()`. A plain file copy misses WAL pages. | Backend | M | Required before any schema change reaches agents. |
| S1–S4 | Scraper fixes, see [Scrapers](#scrapers-suggestions-only-playwright-was-not-touched). | Scrapers | S each | A crash mid-turn currently throws away the whole site's turn. |

### Phase 2: a clean data pipeline (about 2–3 weeks)
| # | Item | Size | Why |
|---|---|---|---|
| 7 | **Save normalised values when writing,** not only when reading: ISO `published_at`, Duashpi `created_at`, `price_eur`, `price = NULL` instead of 0, merged condition, `phones` as a list, `bedrooms`, `quality_flags`. Keep `Derive.py` as the single place for the rules and store a `derive_version` so stored values can be recomputed. | M | Read-time derivation costs about 0.1 ms per listing, about 10 s at 100k. It also enables indexes and SQL filters. |
| 8 | **Price history and listing status:** a `listing_events(listing_id, field, old, new, seen_at)` table, plus `status` (active/removed), `last_checked` and `removed_at`. Results pages already show known listings each turn, so this is close to free. | M | Agents want "price dropped" and "still online". Today the upsert silently overwrites the price. |
| 9 | **Move scraper state into the DB:** `site_state` (`RESUME_PAGE`), `scrape_failures(site, upid, reason, attempts)`, and a `scrape_runs` table (per-site start/end, counts, error). User settings go in `settings.json`, written atomically. | M | Today a restart forgets everything and the first turn walks known pages again. |
| 10 | **Slim list API:** `/api/listings` sends card fields only (no description, no full attributes); `/api/listings/{id}` sends the rest; `?since=` returns only new rows after a turn. | M | The JSON measured 24 MB for 10k listings, with first paint after about 1.9 s. |
| 11 | **A `ScrapeController` class** instead of module globals (lock, events, AUTO, PREFERENCES), with an injectable engine and clock. | M | Makes play/pause testable, and gives persistence and working hours a natural home. |
| 12 | **Logging module** (`Scraper/Log.py`): stdlib `logging`, RichHandler in dev, a rotating UTF-8 file when frozen, `sys.excepthook`/`threading.excepthook`, and a "Ruaj raportin e gabimit" button that zips masked logs. | M | Support without telemetry. |
| 13 | **Store the AI's cache read/write tokens separately** and show them in AIEval. | S | To see whether prompt caching pays off. With questions minutes apart, most calls are cache *writes* at 1.25×. A realistic cost is 2–5¢ per question. |

### Phase 3: product polish and scale
| # | Item | Size | Why |
|---|---|---|---|
| 14 | **Dashboard: a grid instead of the horizontal strip, with a compact card** (about 420 px: photo, price, title, place, specs, Telefono/Detajet). Description, seller and tags move into Detajet. Add a **list/table view** toggle for comparing listings. | L | The strip hides 9 of 12 cards. More than half of each 720 px card is low-value faded boxes. The AI page already uses a grid. |
| 15 | **Move the scrape controls** (interval, play, agency preference) out of the header into a "Mbledhja" popover on the status pill. | S | They're operator controls; agents need search, tabs and export. |
| 16 | **Export the filtered set** ("Eksporto këto 37"), not the whole DB, plus checkboxes to compare or export selected listings. | M | |
| 17 | **Filters in the URL** (`#/?type=…&price=300-500`), so searches can be bookmarked and shared, the way `#/ai/<id>` already works. | M | |
| 18 | **Phone filter panel as a bottom sheet** with a sticky "Shfaq 37 njoftime" button. Stats in one horizontal row on phones. | M | Today the open panel pushes cards about 1,300 px down. |
| 19 | **Light/dark toggle** (`data-theme` stored in localStorage). | S | |
| 20 | **Split `app.js`** (about 1,900 lines) into native ES modules (`format`, `dom`, `api`, `store`, `filters/*`, `cards`, `dialog`, `dashboard`, `scrape`, `ai/*`, `router`), still with no build step. | M | |
| 21 | **Facet counts in one pass,** and a Web Worker above about 20k listings. Server-side filtering through the existing `Search.search()` above about 50k. | M | `render()` takes about 50 ms at 10k listings, half of it facet counts. |
| 22 | **AI schema additions:** build year (the data has it), date ranges ("javën e kaluar"), OR across features, `exclude_sites`, an "oldest" sort, daily vs monthly rent. Also look for features in description text ("me ashensor" is mentioned by 38 listings that lack the tag). | M | Gaps from the 40-question coverage check. |
| 23 | **Geocoding:** normalise addresses, geocode them into a local cache (Nominatim at 1 req/s with a cache, or an OSM gazetteer of Tirana areas), and store lat/lon plus precision. | M | Real "near X" and "within 1 km" searches. Duashpi's km-from-centre value works as a sanity check. |
| 24 | **Cross-site duplicates:** cluster listings on phone + city + type, then size, price, rooms and title similarity. Show the clusters; don't delete. Also pull the phone numbers that 34 of 50 MerrJep titles carry. | M | The same flat posted on three sites shows three times. |
| 25 | **"AI reads descriptions"** as an opt-in button. Strip phone numbers before sending, and say so in Albanian. | M | Judges vague wishes better. Costs 5–10× more per question. |

### Scrapers (suggestions only, Playwright was not touched)
| # | Item | Size |
|---|---|---|
| S1 | **Done 2026-11-09** (see "Scraper error handling" in `NOTES.md`; saving each listing as soon as it's scraped is still open). **Return what was collected when a turn crashes.** A `page.goto` timeout or a failing `go_back` today escapes the scraper and the Engine saves **0**, up to about 6 min of work lost. Better still, save each listing as soon as it's scraped (an `on_listing` callback). | S |
| S2 | **Done 2026-11-09** (a page that didn't load never moves `RESUME_PAGE`; one retry after 10 s). **A slow page isn't the end of the site.** A timeout waiting for cards currently clamps `RESUME_PAGE` as if it were the last page, which destroys the backfill position. Confirm with the pagination count, or retry once. | S |
| S3 | **Partly done 2026-11-09** (temporary failures are retried next turn, the rest go into `FAILED_UPIDS`; storing them in the DB is still open). **Separate the two kinds of failure in `FAILED_UPIDS`.** Parse errors are permanent; network blips get a retry next turn with backoff. Store them in the DB. | S |
| S4 | **MerrJep `posted_by` is "Kompani" on all 50 listings.** Check the mapping, since "jo agjenci" currently removes all of MerrJep. Also, MerrJep's `combine_phones` compares all digits where the other scrapers compare the last 9, so `+35569…` and `069…` are kept as two numbers. | S |
| S5 | **Drop the fixed `wait_for_timeout(5000)` on MerrJep and block images, fonts and analytics** with `context.route`. This is the biggest speed win, and it's lighter on the sites. | S |
| S6 | **A shared `BaseScraper` with a declarative `SiteConfig`.** `launch_browser`, the turn helpers, `clean_description`, `CITY_NAMES` and `parse_number` are copied three times. Make the parsers pure functions over HTML/JSON and test them against saved fixtures. | M |
| S7 | **Run the three sites in parallel.** They are different hosts, so each site's load is unchanged. A turn takes about 6 min instead of about 15. Use one Playwright per thread and keep one request at a time per host. | M |
| S8 | **Done 2026-11-09, as a Ctrl+click on the card into a new tab** (a real click with the results page as referrer, after the scroll-and-pause), see "Listings open in a new tab" in `NOTES.md`. **Open listings with `goto(detail_url)` in a second tab** instead of click + `go_back`. This removes the go-back fragility and Duashpi's "cards moved" problem. | M |
| S9 | **Gazeta: plain HTTP first, the browser as fallback.** Its `__NEXT_DATA__` already arrives over HTTP. This needs an owner decision on the "look human" approach. The recommendation is low, honest request rates rather than more mimicry. | M |
| S10 | **Politeness:** read robots.txt and the terms once per site, honour `Retry-After`/429 with backoff, pause a site for an hour after repeated failures, and add random jitter to schedules. **20 agents means 20× the traffic** on the same sites. | S |
| S11 | **Data quality:** 3 Gazeta "rents" at sale prices, 20% of MerrJep prices stored as 0, unmerged condition wording, and addresses that are "." or empty. Add a `Tools/DataQuality.py` report. | S |

---

## Infrastructure options
| Option | Desktop app (today's product) | Hosted / multi-tenant (only if the product changes) |
|---|---|---|
| Redis + Celery/RQ/arq/Dramatiq | **No.** It's an extra server on every agent's PC, which breaks the "no extra servers" rule. | Yes. Dramatiq or arq for scrape and re-check jobs. |
| In-process queue / SQLite `jobs` table | **Yes, when needed.** Fits the re-check pass and price history. `auto_loop` is enough today. | Replaced by the queue above. |
| APScheduler | **Not needed.** `auto_loop` is small and correct; add working hours there. | Cron or Celery beat. |
| A child process per turn (`multiprocessing`, spawn, `freeze_support()`) | **Yes, later.** It isolates scraper globals and Chromium leaks, and lets quit kill a hung turn. | Containerised Playwright workers. |
| Parallel sites (threads) | **Yes** (S7). Mind about 300 MB+ per Chromium on agents' laptops. | Worker pool. |
| Postgres | **No.** One SQLite per agent was decided. `DB.py` stays the only place with SQL, so a later switch is cheap. | Yes. Listings are public data, so scraping **once centrally** and sharing it would cut the load on the sites 20×. That is a new product decision. |
| Docker / VMs / virtualisation | **No** for agents. Optional for a Linux dev box. | Yes: API, workers, DB. |
| Observability | Rotating UTF-8 logs, a `scrape_runs` table, a diagnostics zip. No telemetry. | Structured JSON logs, OpenTelemetry/Prometheus, Sentry. |
| Config | `config.py` for developer constants, `settings.json` for agent settings, DB tables for scraper state. | Environment variables plus a secrets manager. |

---

## Testing
The suggested layout:
```
tests/
  conftest.py              tmp_path SQLite, listing factory, TestClient(base_url="http://127.0.0.1:8000", Origin header)
  unit/test_derive.py      floor_in_text table, posted_date per site (incl. Duashpi ObjectId), bedrooms, rent outliers
  unit/test_search.py      every Filters field; places (Tirana/Durrësi, Mullet≠Tiranë, Ali≠spitalit); "pa ashensor";
                           AC/TV whole words; phones (2 numbers, spaced, +355); near misses never show exclusions;
                           range preferences; ALL vs EUR; posted_within_days with date.today patched
  unit/test_db.py          upsert keeps id/first_seen, JSON round-trip, ai_searches CRUD, listings_version, DBError
  api/test_server.py       fake Claude client; 400/422/429/503 paths; Host → 400, cross Origin → 403; spend cap; AI lock
  api/test_auto.py         fake Engine: play/pause/interval/overlap, the loop survives exceptions, shutdown waits
  scrapers/test_parsers.py pure parsers on saved, PII-scrubbed HTML fixtures (never live sites)
  ui/test_smoke.py         Playwright against a temp DB (use bypass_csp for string-eval helpers)
```
Coverage goals: Derive, Search, DB and Output ≥90%; Server ≥80%. `Tools/AIEval.py` stays a manual, paid check, never in CI. Run each question 3 times and add:
- "Tirana" in English
- a rent of 50000 with no currency (should mean lekë)
- "2+1 ose 3+1"
- "jo nga MerrJep"
- a "+355" phone
- a greeting (should give empty filters)
- a prompt injection (should be ignored)
- checks that forbidden fields stay empty

---

## Decisions for the owner
1. **API key for shipped installs.** The recommendation is **one Anthropic workspace and key per agent, with a monthly spend limit**, put into the user data folder when you install the app. That needs no server, a key can be revoked per agent, and an extracted key can only spend up to its cap.
   - Above about 20 agents, consider a tiny relay (e.g. a Cloudflare Worker) that holds the key and accepts only `{question, places, areas}`.
   - Never build the owner's key into the app.
2. **Privacy note for agents.** Pyet AI sends the question plus up to 80 area names from listing addresses to Anthropic, never the listings themselves. If an agent types a client's phone number, that goes too.
3. **Scraping approach** (S9 and S10): HTTP-first where a site already serves its data as JSON, polite fixed request rates, and whether to scrape centrally one day (Infrastructure table, Postgres row).
4. **The daily AI cap** is $2. Raise or lower `AI_DAILY_LIMIT_USD` in `Server.py`.

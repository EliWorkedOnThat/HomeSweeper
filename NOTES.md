# HomeSweeper project notes

## Purpose
HomeSweeper is an early-stage collection of web scrapers. Each target site has its own folder so its browser-based Playwright scraper and direct HTTP request scraper can be developed independently. The current examples target MerrJep.al; a `GazetaCelesi` folder is also present.

## Project layout
- `MerrJep.al/` — site-specific Playwright and HTTPX scraper scripts.
- `GazetaCelesi/` — another site-specific pair of scraper scripts; confirm and maintain its target URL and selectors as work continues.
- `Scraper/Engine.py` — intended shared entry point for coordinating scraping jobs. It currently contains an initial MerrJep.al request/parsing experiment.
- `Scraper/Output.py` — intended place for shared output handling. It currently parses listing cards from a saved HTML page and builds structured fields; it does not yet write a final output file.
- `Test/` — development utilities and test code, including an HTML analyzer and a test script. The HTML capture script can recreate `Test/HTML-Files/` when it runs.
- `Analyze.md` — a scratch area for sample URLs and analysis notes.

## Current state
The Playwright scraper in `MerrJep.al/` opens the listings page, builds page URLs, and detects listing cards. Listing extraction is still a placeholder. Its paired HTTPX script fetches a page with browser-like headers, but is not yet connected to the shared parser or output flow.

The shared parser in `Scraper/Output.py` expects saved HTML and extracts fields such as title, price, location, seller details, and image. Some fields are guesses based on listing text and should be checked against real examples. `Scraper/Engine.py` and `Scraper/Output.py` are prototypes, not a completed production orchestrator.

## Next steps
1. Agree on a consistent record format for listings.
2. Finish and validate extraction for one site, including missing or changed fields.
3. Connect fetching, parsing, and output through the shared engine.
4. Keep site-specific selectors and behavior in each site's folder; put reusable coordination and output logic in `Scraper/`.

## Repository housekeeping
`.gitignore` now excludes test directories and common Python test filename patterns. Ignore rules do not affect files already tracked by Git, so the tracked `Test/Test.py` remains tracked unless it is explicitly removed from the index. The scraper code has not been changed as part of these notes.

## For Alex's Claude 


## To run the Engine use the following command:
py -3.14 -m Scraper.Engine 

## Errors and Potential Errors / Fixes
At the moment we are testing each site and scraping each site one at a time to test errors exported data and such but later we must use asynchronous processes and unique playwright drivers to safely and quickly scrape each site in a unique instance.
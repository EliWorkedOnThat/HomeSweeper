#Dev tool: takes a URL, grabs its HTML and saves it to Tools/HTML/ so we can study a site's markup before writing a scraper for it.
#Run from the project root:
#   python3 -m Tools.FetchHTML <url>            (Windows: py -3.14 -m Tools.FetchHTML <url>)
#   python3 -m Tools.FetchHTML <url> --fast     Plain HTTP request instead of a browser (quicker, but no JavaScript runs)
#   python3 -m Tools.FetchHTML <url> --show     Show the browser window while it loads
#Leave out the URL and it will ask for one.
#Imports
import argparse
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from rich import print

#Global Variables
OUTPUT_DIR = Path(__file__).resolve().parent / "HTML" #Works on Windows, Linux and macOS
WAIT_AFTER_LOAD_MS = 5000 #Give JavaScript time to fill the page before we grab it
HEADERS = { #Same browser-like headers as the scrapers' Request files, so the plain request isn't flagged
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sq-AL,sq;q=0.9,en;q=0.8",
}

#Function to get the HTML with a real browser (runs the page's JavaScript, same as our scrapers see it)
def fetch_with_browser(url, show=False):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not show)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(WAIT_AFTER_LOAD_MS)
            return page.content()
        finally:
            browser.close()

#Function to get the HTML with a plain HTTP request (only the raw HTML the server sends)
def fetch_with_request(url):
    import httpx
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text

#Helper function to build a readable, unique filename like merrjep.al_2026-11-03_16-40-12.html
def build_filename(url):
    site = urlparse(url).netloc.removeprefix("www.") or "page"
    site = re.sub(r"[^\w.-]", "_", site) #Keep the name safe on every OS
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = OUTPUT_DIR / f"{site}_{timestamp}.html"
    counter = 2
    while filename.exists(): #Two grabs in the same second get _2, _3...
        filename = OUTPUT_DIR / f"{site}_{timestamp}_{counter}.html"
        counter += 1
    return filename

#Function to fetch the URL and save the HTML, returns the saved file path
def save_html(url, fast=False, show=False):
    if not urlparse(url).scheme:
        url = f"https://{url}" #Allow "merrjep.al/..." without typing https://

    print(f"[cyan]Fetching {url} ({'plain request' if fast else 'browser'})...[/cyan]")
    html = fetch_with_request(url) if fast else fetch_with_browser(url, show)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = build_filename(url)
    filename.write_text(html, encoding="utf-8")

    print(f"[green]Saved {len(html):,} characters to: {filename}[/green]")
    return filename

#Call each function as needed this will act as the orchestrator
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Save the HTML of a URL to Tools/HTML/")
    parser.add_argument("url", nargs="?", help="The page to grab (asks if left out)")
    parser.add_argument("--fast", action="store_true", help="Plain HTTP request instead of a browser")
    parser.add_argument("--show", action="store_true", help="Show the browser window")
    args = parser.parse_args()

    url = args.url or input("Enter the URL: ").strip()
    if not url:
        print("[red]No URL given.[/red]")
        raise SystemExit(1)

    try:
        save_html(url, fast=args.fast, show=args.show)
    except Exception as error:
        print(f"[red]Could not fetch {url}: {error}[/red]")
        raise SystemExit(1)

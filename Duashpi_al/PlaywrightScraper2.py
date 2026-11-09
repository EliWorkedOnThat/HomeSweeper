#Scraper for duashpi.al, returns listing dicts in the same shape as the other scrapers (see the scraper contract in CLAUDE.md)
#Works like MerrJep: the results page only has short cards, so each NEW listing is opened (click, read, go back).
#The listing page has two JSON-LD blocks: "Product" (title, description) and "RealEstateAgent" (seller name and phone),
#the rest (price, type, date, rooms, m², photos, licensed agent) is read from the page's HTML.
#Imports
from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from rich import print
from rich.markup import escape
import html
import json
import random
import re
import time
import traceback
from Scraper.config import Demo_Environment, Rest_Time, Headless, Listings_Per_Turn

#Global Variables
SITE_URL = "https://duashpi.al"
BASE_URL = f"{SITE_URL}/kerko-prona" #All listings, page_url(n) adds ?sort=new&page=n so every turn starts at the newest posts
SORT_ORDER = "new" #Newest first, page 1 must always hold the freshest posts for the turn logic to work
DEMO_LISTING_LIMIT = 5 #How many listings to scrape per turn when Demo_Environment is True
PROCESSED_UPIDS = set() #Everything this turn skips: already in the DB, scraped this turn, or remembered below
SCRAPED_LISTINGS = []

#Kept between turns while the program runs (reset_state() leaves them alone), a restart just starts from page 1 again
RESUME_PAGE = 1 #Deepest page reached so far, once a turn catches up with the new posts it jumps here for the older ones
AGENCY_UPIDS = set() #Agency listings we skipped, so "Jo Listime Agjensie" doesn't treat them as new every turn
FAILED_UPIDS = set() #Listings that failed (e.g. no JSON-LD), not reopened until the program restarts
STOP_CHECK = None #The engine's should_stop function for the current turn (None = never stop early)

#Duashpi's markup on the results page (check again with Tools.FetchHTML if the scraper stops finding listings)
CARD_SELECTOR = "a.property-item-list-v2" #A results card, its href is /prona/<upid>/<slug>.html
PROMOTED_SELECTOR = ".premium-badge" #Premium (paid) cards
#Only the normal cards: a page has ~30 of them sorted newest first, plus ~78 premium cards mixed in between that are NOT in date order
#and repeat across pages (their href ends in ?pt=1, ?pt=2...). Premium listings also show up as normal cards, so nothing is lost.
LISTING_SELECTOR = f"{CARD_SELECTOR}:not(:has({PROMOTED_SELECTOR}))"
PAGE_LINK_SELECTOR = "a[data-page]" #Pagination links, the highest data-page is the last page
#The cookie popup ("Cookies dhe privatësia") covers the page with a backdrop that blocks every click until it's closed
#The choice is kept in a cookie, so it only shows on the first page of each turn (each turn opens a fresh browser)
COOKIE_ACCEPT_SELECTOR = "#acceptAllButton" #"Prano dhe Mbyll"
COOKIE_WAIT_MS = 5000 #How long to wait for the popup to appear on the first page (it's added by JavaScript after the load)
CLICK_TIMEOUT_MS = 10000 #A card that is there but can't be clicked (e.g. a new popup covers it) fails after 10 s instead of Playwright's 30 s
UPID_PATTERN = re.compile(r"/prona/([0-9a-f]+)/") #The UPID is the hex id in the url, e.g. 6ad3547b8139a0c7c1013deb

#Duashpi's markup on a listing page
TITLE_SELECTOR = "h1"
LOCATION_SELECTOR = "h1 + h3" #"Rruga 5 Maji, Tirane", right under the title
FACTS_SELECTOR = "h1 ~ div ul li" #"DSH-368426", "75 m²", "2 Dhoma gjumi", "1 Tualet", "Kati 5", "812 Shikime"
TAGS_SELECTOR = 'h3:has-text("Detaje të tjera") + ul li' #"E mobiluar", "Ballkon", "I rinovuar", "1.7km nga qendra"
PRICE_SELECTOR = "span.text-2xl.font-semibold" #"500 € / në muaj", "12,000,000 LEK", "Cmimi sipas kerkeses."
DATE_SELECTOR = "span[title]" #The relative date ("8 orë më parë") has the exact one in its title: "04/11/2026 12:34"
DATE_PATTERN = re.compile(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})")
PHONE_SELECTOR = 'a:has-text("Telefono") span' #"Telefono +355695777733" on the call button
IMAGE_SELECTOR = 'img[src*="/uploads/images/main/"]' #Full size photos (the "similar listings" box uses /thumbnail/ ones)
BREADCRUMB_SELECTOR = 'nav[aria-label="Breadcrumb"] li' #"Kreu › Shtëpi me qera › <title>", the middle one is the category
#The seller is a JSON-LD block: "LocalBusiness" (category "Real Estate Agency") for agency accounts, "RealEstateAgent" for everyone else
SELLER_TYPES = {"RealEstateAgent", "LocalBusiness"}
BUSINESS_SELLER_TYPE = "LocalBusiness"
#Individual agents can also have a licence box ("Agjent i liçensuar, Nr. liçensës: 527"), those count as agencies too
#An agency posting from a personal account looks like a private seller, so a few agency listings still get through "Jo Listime Agjensie"
#Neither marker is on the results card, so an agency listing costs one page load before it's skipped (only once, it's remembered)
AGENCY_SELECTOR = 'a[href*="/profili/"]:has-text("liçensuar")'

#Human pacing, so the visit looks like a person reading the list (same idea as Gazeta)
SCAN_TIME = (1.0, 2.5) #Random seconds spent "reading" each card before clicking it
PAGE_REST_JITTER = 2.0 #Up to this many random extra seconds on top of Rest_Time, so pages don't load on an exact clock

#The listing page's facts are "<number> <label>" or "<label> <number>", the label changes with the count ("1 Tualet", "2 Tualete"),
#so each one is matched by how it starts and saved under one fixed name
FACT_LABELS = [
    (re.compile(r"^dhom", re.IGNORECASE), "Dhoma gjumi"),
    (re.compile(r"^tualet", re.IGNORECASE), "Tualete"),
    (re.compile(r"^kuzhin", re.IGNORECASE), "Kuzhina"),
    (re.compile(r"^ballkon", re.IGNORECASE), "Ballkone"),
    (re.compile(r"^kat", re.IGNORECASE), "Kati"),
]
SKIPPED_FACTS = {"Shikime"} #The view count changes on every visit, it isn't part of the listing

#Duashpi's labels mapped to our standard database columns (every site maps its own labels to these)
ATTRIBUTE_COLUMNS = {
    "Sipërfaqe": "size_m2",
    "Dhoma gjumi": "rooms",
}

#Duashpi's values rewritten to MerrJep's wording, so the UI filters show one option per meaning across sites
LISTING_TYPES = {"Me qera": "Jepet me qera", "Për shitje": "Shitet"}
RENT_MARKER = "në muaj" #Backup for the listing type: a price with "/ në muaj" is a rental
AGENCY_POSTED_BY = "Kompani" #Agency accounts and licensed agents, everyone else is left empty (we can't be sure they are private)
#The JSON-LD turns LEK prices into EUR (40,000 LEK becomes 440 EUR), so the price is read from the page as the seller wrote it
CURRENCIES = {"€": "EUR", "EUR": "EUR", "LEK": "ALL", "LEKE": "ALL", "ALL": "ALL"}
#Duashpi writes city names without ë/ç ("Tirane"), MerrJep writes them properly ("Tiranë"), unknown cities are kept as they are
CITY_NAMES = {
    "Tirane": "Tiranë", "Durres": "Durrës", "Vlore": "Vlorë", "Shkoder": "Shkodër", "Korce": "Korçë",
    "Sarande": "Sarandë", "Lezhe": "Lezhë", "Kavaje": "Kavajë", "Gjirokaster": "Gjirokastër", "Kruje": "Krujë",
    "Lushnje": "Lushnjë", "Kukes": "Kukës", "Permet": "Përmet", "Tepelene": "Tepelenë", "Gramsh": "Gramsh",
}

#Function to run one turn: scrape up to turn_limit() NEW listings, then hand them back so the engine can save them
#known_upids are the listings already in the database, they are skipped before clicking into them
#skip_agencies=True ignores listings posted by licensed agents (the "Jo Listime Agjensie" preference in the UI)
#should_stop is checked between listings, when it returns True (the UI's pause button) the turn ends early with what it has so far
#Whatever goes wrong, the listings collected so far are always handed back (see "Scraper error handling" in NOTES.md)
def scrape_duashpi_al(skip_agencies=False, known_upids=(), should_stop=None):
    global STOP_CHECK
    STOP_CHECK = should_stop
    reset_state(known_upids, skip_agencies) #Start fresh every turn, only RESUME_PAGE and the remembered UPIDs carry over
    try:
        with sync_playwright() as p:
            browser, page = launch_browser(p)
            try:
                run_turn(page, skip_agencies)
            finally:
                close_browser(browser)
        end_turn(PAUSED if stop_requested() else OK)
    except TurnStop as stop:
        end_turn(stop.kind, stop.reason)
    except Exception as error: #The safety net for anything nobody planned for, e.g. a bug in this file
        end_turn(classify(error), describe(error), show_traceback=True)
    print(f"[green]Turn done: {len(SCRAPED_LISTINGS)} new listings, next turn continues older listings from page {RESUME_PAGE}[/green]")

    #Hand a copy of the scraped listings back to whoever called this function (Engine.py)
    return list(SCRAPED_LISTINGS)

#Function that walks the results pages until the turn is full or paused. Errors are raised and handled by scrape_duashpi_al()
def run_turn(page, skip_agencies):
    page_number = 1 #Always start at the newest posts
    cookies_closed = False #The cookie popup only needs closing once per browser
    while not turn_over():
        url = page_url(page_number)
        print(f"[cyan]Navigating to: {url}[/cyan]")
        open_page(page, url)

        try:
            find_listing_container(page)
        except NoListings:
            #Every turn starts at page 1, which always has listings, so an empty page 1 means the markup changed.
            #An empty later page is past the last one: old listings expire, so keep RESUME_PAGE inside the real page count
            if page_number == 1:
                raise TurnStop(MARKUP, f"no listings on {url}, the site's markup may have changed")
            print(f"[yellow]No listings found on {url}, assuming last page reached.[/yellow]")
            clamp_resume_page(page_number - 1)
            break

        if not cookies_closed:
            accept_cookies(page)
            cookies_closed = True

        total_pages = read_total_pages(page) or page_number
        all_seen = process_listing_containers(page, skip_agencies)
        print(f"[green]Page {page_number}/{total_pages} processed. New this turn: {len(SCRAPED_LISTINGS)}/{turn_limit()}[/green]")

        page_number = next_page(page_number, all_seen)
        if page_number > total_pages:
            print("[green]Last page reached, every listing on the site has been seen.[/green]")
            clamp_resume_page(total_pages)
            break
        if turn_over():
            break

        rest(page, "next page")

#Function to close the cookie popup the way a person would: notice it, pause a moment, click "Prano dhe Mbyll"
#Does nothing if the popup doesn't show up (already accepted, or the site removed it). If it can't be closed the turn carries on:
#the clicks then fail, and MAX_FAILS_IN_A_ROW ends the turn with the reason in the log
def accept_cookies(page):
    button = page.locator(COOKIE_ACCEPT_SELECTOR)
    try:
        button.wait_for(state="visible", timeout=COOKIE_WAIT_MS)
    except PlaywrightTimeoutError:
        print("[cyan]No cookie popup shown.[/cyan]")
        return
    page.wait_for_timeout(random.uniform(*SCAN_TIME) * 1000)
    try:
        button.click(timeout=CLICK_TIMEOUT_MS)
        button.wait_for(state="hidden", timeout=5000)
    except PlaywrightError as error:
        if classify(error) == BROWSER_GONE:
            raise
        print(f"[yellow]Could not close the cookie popup ({escape(describe(error))}), carrying on.[/yellow]")
        return
    print("[cyan]Cookie popup closed.[/cyan]")

#---------- Error handling: the same block is copied into every scraper (see "Scraper error handling" in NOTES.md) ----------
#Every error is sorted into one kind, and each kind has one response. Whatever happens, the turn hands back what it collected.
PAGE_TIMEOUT_MS = 20000 #One try at loading a page
PAGE_ATTEMPTS = 2 #Tries per page load: a timeout or network error gets one more try
RETRY_WAIT_SECONDS = 10 #Pause between the two tries
CARDS_TIMEOUT_MS = 15000 #How long a loaded results page gets to show its listings
MAX_FAILS_IN_A_ROW = 3 #Listings failing back to back mean the site changed or is blocking us, so the turn ends
BLOCKED_STATUSES = (403, 429) #The site refuses us, trying again would only make it worse
BLOCK_PAGE_TITLES = ("just a moment", "attention required", "access denied", "captcha") #Cloudflare and captcha pages, often sent with HTTP 200
#How a turn can end. OK and PAUSED are normal endings, the rest are the kinds of error
OK, PAUSED, TEMPORARY, BLOCKED, MARKUP, BROWSER_GONE, UNEXPECTED = "ok", "paused", "temporary", "blocked", "markup", "browser_gone", "unexpected"
TEMPORARY_MESSAGES = ("net::err_", "execution context was destroyed", "frame was detached", "interrupted by another navigation")
BROWSER_GONE_MESSAGES = ("has been closed", "target closed", "browser has disconnected", "page crashed")
LAST_TURN = {"ended": None, "reason": None} #How the last turn ended (one of the kinds above) and why, for the log and later the UI
FAIL_STREAK = 0 #Listings that failed back to back this turn

#Raised to end the turn on purpose: kind is one of the kinds above, reason is the text for the log
class TurnStop(Exception):
    def __init__(self, kind, reason):
        super().__init__(reason)
        self.kind = kind
        self.reason = reason

#Raised for something worth one more try (a 5xx answer, a listing page that isn't the one we clicked)
class TemporaryError(Exception):
    pass

#Raised when a page loaded fine but what we read from it is missing, so the site's markup has probably changed
class MarkupError(Exception):
    pass

#Raised when a results page loaded but shows no listings (the caller decides: past the last page, or changed markup)
class NoListings(Exception):
    pass

#Function that sorts an error into one of the kinds above
def classify(error):
    if isinstance(error, TurnStop):
        return error.kind
    if isinstance(error, (PlaywrightTimeoutError, TemporaryError)):
        return TEMPORARY
    if isinstance(error, MarkupError):
        return MARKUP
    if isinstance(error, PlaywrightError):
        message = str(error).lower()
        if any(text in message for text in BROWSER_GONE_MESSAGES):
            return BROWSER_GONE
        if any(text in message for text in TEMPORARY_MESSAGES):
            return TEMPORARY
    return UNEXPECTED

#Helper function: an error in one line (Playwright adds a long call log after the first line)
def describe(error):
    lines = str(error).strip().splitlines()
    return f"{type(error).__name__}: {lines[0] if lines else ''}"[:300]

#Function to wait between two tries in short steps, so the pause button still works within half a second
def wait_or_stop(seconds):
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        if stop_requested():
            raise TurnStop(PAUSED, "paused while waiting to try again")
        time.sleep(min(0.5, left))

#Function that ends the turn when the site shows a block page (Cloudflare's "Just a moment", a captcha)
def check_blocked(page):
    try:
        title = page.title().lower()
    except PlaywrightError:
        return #The page can't be read, the caller's own error says why
    if any(text in title for text in BLOCK_PAGE_TITLES):
        raise TurnStop(BLOCKED, f"the site showed a block page ({title!r})")

#Function to load a page with up to PAGE_ATTEMPTS tries, returns the HTTP status. Raises TurnStop when it can't be loaded.
#A page that loads but has no listings isn't an error here: the caller decides if it's past the last page or changed markup.
def open_page(page, url):
    for attempt in range(1, PAGE_ATTEMPTS + 1):
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            status = response.status if response else 0
            if status in BLOCKED_STATUSES:
                raise TurnStop(BLOCKED, f"HTTP {status} from {url}")
            check_blocked(page)
            if status >= 500:
                raise TemporaryError(f"HTTP {status}")
            return status
        except TurnStop:
            raise
        except Exception as error:
            kind = classify(error)
            if kind != TEMPORARY:
                raise TurnStop(kind, f"could not load {url}: {describe(error)}") from error
            if attempt == PAGE_ATTEMPTS:
                raise TurnStop(TEMPORARY, f"could not load {url} after {PAGE_ATTEMPTS} tries: {describe(error)}") from error
            print(f"[yellow]Loading {url} failed ({escape(describe(error))}), trying again in {RETRY_WAIT_SECONDS}s...[/yellow]")
            wait_or_stop(RETRY_WAIT_SECONDS)

#Function to record a listing that failed and decide what happens next, called from inside an except block.
#Temporary failures get another chance next turn, markup and unexpected ones are remembered in FAILED_UPIDS until a restart.
#Raises TurnStop when the browser is gone, the site shows a block page, or MAX_FAILS_IN_A_ROW listings failed back to back.
def listing_failed(page, upid, error):
    global FAIL_STREAK
    kind = classify(error)
    if kind == BROWSER_GONE:
        raise TurnStop(kind, f"{upid}: {describe(error)}") from error
    check_blocked(page)
    PROCESSED_UPIDS.add(upid) #Not again this turn
    if kind == TEMPORARY:
        print(f"[yellow]Failed to scrape {upid} ({escape(describe(error))}), it will be tried again next turn.[/yellow]")
    else:
        FAILED_UPIDS.add(upid) #Trying again won't help, a broken listing would otherwise stop pages from ever counting as "seen"
        print(f"[red]Failed to scrape {upid} ({kind}): {escape(describe(error))}[/red]")
        if kind == UNEXPECTED:
            print(escape(traceback.format_exc()))
    FAIL_STREAK += 1
    if FAIL_STREAK >= MAX_FAILS_IN_A_ROW:
        raise TurnStop(kind, f"{FAIL_STREAK} listings failed in a row, the last one: {describe(error)}")

#Function to note a listing that worked, which resets the failure streak
def listing_ok():
    global FAIL_STREAK
    FAIL_STREAK = 0

#Function to record how the turn ended and log it (show_traceback only from inside an except block)
def end_turn(kind, reason=None, show_traceback=False):
    LAST_TURN.update(ended=kind, reason=reason)
    if kind in (OK, PAUSED):
        return
    print(f"[red]Turn ended early ({kind}): {escape(reason or '')}. The listings found so far are kept.[/red]")
    if show_traceback:
        print(escape(traceback.format_exc()))

#Function to close the browser without letting an error while closing hide what the turn found
def close_browser(browser):
    try:
        browser.close()
    except Exception as error:
        print(f"[yellow]Could not close the browser cleanly: {escape(describe(error))}[/yellow]")
#---------- End of the error handling block ----------

#Function to get back to the results page after a listing: the back button first, a fresh load if the cards don't come back
def return_to_results(page, results_url):
    try:
        page.go_back(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.locator(CARD_SELECTOR).first.wait_for(state="visible", timeout=CARDS_TIMEOUT_MS)
        return
    except PlaywrightError as error:
        if classify(error) == BROWSER_GONE:
            raise TurnStop(BROWSER_GONE, describe(error)) from error
        print("[yellow]Going back didn't show the results, reloading the results page.[/yellow]")
    open_page(page, results_url)
    try:
        page.locator(CARD_SELECTOR).first.wait_for(state="visible", timeout=CARDS_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise TurnStop(TEMPORARY, f"the results page {results_url} came back without listings") from None

#Function to find the listings on page, raises NoListings if the page shows none
def find_listing_container(page):
    listings = page.locator(LISTING_SELECTOR)
    try:
        listings.first.wait_for(state="visible", timeout=CARDS_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise NoListings() from None
    count = listings.count()
    print(f"[cyan]Found {count} listings on the page.[/cyan]")
    return listings

#Helper function to read how many results pages the site has from the pagination links (0 if there are none)
def read_total_pages(page):
    numbers = [link.get_attribute("data-page") or "" for link in page.locator(PAGE_LINK_SELECTOR).all()]
    return max((int(number) for number in numbers if number.isdigit()), default=0)

#Function to process each NEW listing card, returns True if the page had nothing new at all
def process_listing_containers(page, skip_agencies=False):
    listings = page.locator(LISTING_SELECTOR)
    hrefs = [listings.nth(i).get_attribute("href") or "" for i in range(listings.count())]
    listing_ids = list(dict.fromkeys(match.group(1) for match in map(UPID_PATTERN.search, hrefs) if match))
    results_url = page.url

    #Listings already in the database (or skipped before) are dropped here, before any click
    new_ids = [upid for upid in listing_ids if upid not in PROCESSED_UPIDS]
    print(f"[cyan]{len(new_ids)} new, {len(listing_ids) - len(new_ids)} already seen.[/cyan]")
    if not new_ids:
        scan_card(page, None) #Still scroll a little, so a page of known listings isn't loaded and left instantly
        return True

    opened = 0 #Listings opened on this page, the rest before each one is skipped for the first
    for i, upid in enumerate(new_ids):
        if turn_full():
            print(f"[yellow]Turn limit of {turn_limit()} new listings reached.[/yellow]")
            break
        if stop_requested():
            print("[yellow]Paused, ending this turn early with what we have.[/yellow]")
            break

        #New posts arrive every minute and push the older cards down, so while we work through a page its last cards move to the
        #next page. A card that's gone is left unmarked (not failed): the next page picks it up, instead of waiting for a click that can't happen
        card = page.locator(f'{LISTING_SELECTOR}[href*="/prona/{upid}/"]').first
        if not card.count():
            print(f"[yellow]{upid} moved to the next page (newer posts pushed it down), it will be picked up there.[/yellow]")
            continue

        if opened:
            rest(page, "next listing") #Every listing is a page load, so rest between them to avoid getting flagged
        opened += 1

        print(f"[cyan]Processing {i + 1}/{len(new_ids)}: {upid}[/cyan]")

        try:
            #Scroll down to the card and pause on it like a person reading the list, then open it
            scan_card(page, upid)
            card.click(timeout=CLICK_TIMEOUT_MS)
            page.wait_for_url(f"**/prona/{upid}/**", timeout=15000)

            #The agency markers are only on the listing page, so agency listings cost one page load (only once, they are remembered)
            if skip_agencies and is_agency(page, read_json_ld(page)[1]):
                print(f"[yellow]Skipping agency listing: {upid}[/yellow]")
                PROCESSED_UPIDS.add(upid)
                AGENCY_UPIDS.add(upid)
                listing_ok()
            else:
                data = pull_data_from_listing(page, upid)
                SCRAPED_LISTINGS.append(data)
                PROCESSED_UPIDS.add(upid)
                listing_ok()

                #Scraped text is escaped: rich would read a title like "[/b] SHITET" as markup and raise
                print(f"[green]Scraped: {escape(str(data['title']))}[/green]")
                print(f"[bright_green]Price: {data['price']} {escape(str(data['currency']))}[/bright_green]")
                print(f"[bright_green]Images: {len(data['images'])}[/bright_green]")
                print(f"[bright_green]Phone: {escape(str(data['phone'] or 'Not available'))}[/bright_green]")

        except TurnStop:
            raise
        except Exception as error:
            listing_failed(page, upid, error) #Raises TurnStop when the turn should end

        if page.url != results_url:
            return_to_results(page, results_url)

    return False

#Function to read the open listing page into our standard listing dict
def pull_data_from_listing(page, expected_upid):
    page.locator(TITLE_SELECTOR).first.wait_for(state="visible", timeout=15000)

    #Check we are on the listing we clicked (a mismatch means we landed on another listing, so it's tried again next turn)
    match = UPID_PATTERN.search(page.url)
    upid = match.group(1) if match else None
    if upid != expected_upid:
        raise TemporaryError(f"UPID mismatch: {expected_upid} != {upid}")

    #No product data on a page that loaded means the markup changed (remembered in FAILED_UPIDS, see listing_failed())
    product, agent = read_json_ld(page)
    if not product:
        raise MarkupError("No JSON-LD Product on the listing page")

    #Facts under the title, the "Detaje të tjera" tags and the category, kept in attributes so "All details" in the UI shows them
    attributes = read_facts(texts_of(page, FACTS_SELECTOR))
    tags = texts_of(page, TAGS_SELECTOR)
    if tags:
        attributes["Detaje të tjera"] = ", ".join(tags)
    breadcrumbs = texts_of(page, BREADCRUMB_SELECTOR)
    if len(breadcrumbs) >= 3:
        attributes["Kategoria"] = breadcrumbs[1].strip("› ") #"Shtëpi me qera"

    #Licence number of a licensed agent, kept in attributes
    licence = page.locator(f"{AGENCY_SELECTOR} b")
    if licence.count():
        attributes["Nr. liçensës"] = licence.first.inner_text().strip()

    #Price as the seller wrote it ("12,000,000 LEK"), the JSON-LD price is only a backup because it converts LEK to EUR
    price_text = first_text(page, PRICE_SELECTOR) or ""
    price, currency = parse_price(price_text)
    if not price_text:
        offers = product.get("offers") or {}
        price, currency = parse_price(f"{offers.get('price') or ''} {offers.get('priceCurrency') or ''}")

    #Listing type from the badge next to the date ("Me qera", "Për shitje"), the price's "/ në muaj" as a backup
    published_date, published_time, listing_type = read_date_and_type(page)
    if not listing_type:
        listing_type = "Me qera" if RENT_MARKER in price_text else None

    #"Rruga 5 Maji, Tirane": the last part is the city, sometimes there is only a street
    location = first_text(page, LOCATION_SELECTOR)
    city, address = split_location(location)

    #Two phone numbers can show up: the call button and the seller's account, combine_phones keeps both if they differ
    button_phone = (first_text(page, PHONE_SELECTOR) or "").replace("Telefono", "").strip()
    phones = combine_phones(button_phone or None, agent.get("telephone") or None)

    images = list(dict.fromkeys(
        image.get_attribute("src") for image in page.locator(IMAGE_SELECTOR).all() if image.get_attribute("src")
    ))
    if not images and product.get("image"):
        images = [product["image"]] if isinstance(product["image"], str) else list(product["image"])

    data = {
        "upid": upid,
        "url": page.url.split("?")[0],
        "title": first_text(page, TITLE_SELECTOR) or (product.get("name") or "").strip(),
        "price": price,
        "currency": currency,
        "description": clean_description(product.get("description")),
        "attributes": attributes,
        "images": images,
        "seller": (agent.get("name") or "").strip() or None,
        "phone": phones,
        "published_date": published_date,
        "published_time": published_time,
        "listing_type": LISTING_TYPES.get(listing_type, listing_type),#type: ignore
        "condition": None, #Duashpi has no condition field, tags like "I rinovuar" stay in attributes
        "city": city,
        "address": address,
        "posted_by": AGENCY_POSTED_BY if is_agency(page, agent) else None,
    }

    #Copy the important attributes into their own fields so they get their own database columns (attributes keeps everything as a backup)
    for label, column in ATTRIBUTE_COLUMNS.items():
        data[column] = attributes.get(label)
    data["size_m2"] = parse_number(data["size_m2"])
    data["rooms"] = parse_number(data["rooms"], whole=True)

    return data

#Helper function to read the listing's two JSON-LD blocks, returns (Product dict, seller dict), empty dicts if missing
def read_json_ld(page):
    product, agent = {}, {}
    for script in page.locator('script[type="application/ld+json"]').all_text_contents():
        try:
            parsed = json.loads(script)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if parsed.get("@type") == "Product" and not product:
            product = parsed
        elif parsed.get("@type") in SELLER_TYPES and not agent:
            agent = parsed
    return product, agent

#Helper function to check if the open listing was posted by an agency: an agency account, or a licensed agent
def is_agency(page, agent):
    return agent.get("@type") == BUSINESS_SELLER_TYPE or page.locator(AGENCY_SELECTOR).count() > 0

#Helper function to get the text of every element a selector matches, with spaces tidied and empty ones dropped
def texts_of(page, selector):
    texts = (" ".join(element.inner_text().split()) for element in page.locator(selector).all())
    return [text for text in texts if text]

#Helper function to get the text of the first element a selector matches, None if there is none
def first_text(page, selector):
    texts = texts_of(page, selector)
    return texts[0] if texts else None

#Helper function to turn the facts under the title into {label: value}
#"75 m2" -> Sipërfaqe, "2 Dhoma gjumi" -> Dhoma gjumi: 2, "Kati 5" -> Kati: 5, "DSH-368426" -> Kodi
def read_facts(texts):
    attributes = {}
    for text in texts:
        if re.fullmatch(r"DSH-\d+", text):
            attributes["Kodi"] = text
            continue
        if re.search(r"\bm\s*[2²]$", text):
            attributes["Sipërfaqe"] = re.sub(r"\s*m\s*[2²]$", " m²", text)
            continue

        number_first = re.fullmatch(r"(-?\d+(?:[.,]\d+)?)\s+(.+)", text) #"2 Dhoma gjumi"
        number_last = re.fullmatch(r"(.+?)\s+(-?\d+(?:[.,]\d+)?)", text) #"Kati 5"
        if number_first:
            value, label = number_first.group(1), number_first.group(2)
        elif number_last:
            label, value = number_last.group(1), number_last.group(2)
        else:
            attributes[text] = "Po" #A fact with no number, kept as a yes
            continue

        if label in SKIPPED_FACTS:
            continue
        for pattern, name in FACT_LABELS:
            if pattern.match(label):
                label = name
                break
        attributes[label] = value
    return attributes

#Helper function to read the exact publish date from the date badge's title, and the listing type badge right before it
#Returns (date "04/11/2026", time "12:34", type "Me qera"), the date is in the same format as Gazeta's
def read_date_and_type(page):
    for span in page.locator(DATE_SELECTOR).all():
        match = DATE_PATTERN.fullmatch((span.get_attribute("title") or "").strip())
        if not match:
            continue
        badge = span.locator("xpath=preceding-sibling::span[1]")
        listing_type = " ".join(badge.first.inner_text().split()) if badge.count() else None
        return match.group(1), match.group(2), listing_type or None
    return None, None, None

#Helper function to read the price text, returns (number, currency), (None, None) for "Cmimi sipas kerkeses." or anything without a number
def parse_price(text):
    price = parse_number(text)
    if not price:
        return None, None
    upper = text.upper()
    currency = next((code for symbol, code in CURRENCIES.items() if symbol in upper), None)
    return price, currency

#Helper function to split "Rruga 5 Maji, Tirane" into (city "Tiranë", address "Rruga 5 Maji")
#With only one part it's a city if we know the name, otherwise it's an address
def split_location(location):
    if not location:
        return None, None
    parts = [part.strip() for part in location.split(",") if part.strip()]
    last = parts[-1]
    if len(parts) == 1 and last not in CITY_NAMES and last not in CITY_NAMES.values():
        return None, location
    return CITY_NAMES.get(last, last), ", ".join(parts[:-1]) or None

#Helper function to wait Rest_Time plus a random bit before the next page load, so loads don't happen on an exact clock
def rest(page, before):
    seconds = Rest_Time + random.uniform(0, PAGE_REST_JITTER)
    print(f"[cyan]Resting {seconds:.1f}s before {before}...[/cyan]")
    page.wait_for_timeout(seconds * 1000)

#Helper function to open the browser, with no window when Headless is True (copied from the Gazeta scraper, see CLAUDE.md gotchas)
def launch_browser(p):
    if not Headless:
        browser = p.chromium.launch(headless=False)
        return browser, browser.new_page()

    #channel="chromium" uses Chrome's newer headless mode: the full browser with no window, so it keeps real plugins and the real GPU
    browser = p.chromium.launch(headless=True, channel="chromium")

    #Headless still says "HeadlessChrome" in its user agent, so read the real one and drop the "Headless" part (keeps the right version and OS)
    probe = browser.new_page()
    user_agent = probe.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
    probe.close()

    context = browser.new_context(user_agent=user_agent)
    return browser, context.new_page()

#Helper function to clear the last turn's state and load what this turn should skip
def reset_state(known_upids=(), skip_agencies=False):
    global FAIL_STREAK
    FAIL_STREAK = 0
    LAST_TURN.update(ended=None, reason=None)
    PROCESSED_UPIDS.clear()
    SCRAPED_LISTINGS.clear()
    PROCESSED_UPIDS.update(str(upid) for upid in known_upids)
    PROCESSED_UPIDS.update(FAILED_UPIDS)
    if skip_agencies: #With the preference off, agency listings must be scraped, so they only count as seen while it's on
        PROCESSED_UPIDS.update(AGENCY_UPIDS)

#Helper function for how many new listings one turn collects
def turn_limit():
    return DEMO_LISTING_LIMIT if Demo_Environment else Listings_Per_Turn

#Helper function to check if this turn has collected enough new listings
def turn_full():
    return len(SCRAPED_LISTINGS) >= turn_limit()

#Helper function to check if the user paused scraping (the engine passes the check in as should_stop)
def stop_requested():
    return bool(STOP_CHECK and STOP_CHECK())

#Helper function to check if this turn should end: enough new listings, or paused
def turn_over():
    return turn_full() or stop_requested()

#Helper function to build the url of a results page, newest first
def page_url(number):
    return f"{BASE_URL}?sort={SORT_ORDER}&page={number}"

#Helper function to pick the next page: normally the next one, but once a page had nothing new we have caught up with the
#fresh posts, so we jump straight to where the last turn stopped instead of walking through pages we already have
def next_page(current, all_seen):
    global RESUME_PAGE
    RESUME_PAGE = max(RESUME_PAGE, current)
    if all_seen and RESUME_PAGE > current:
        print(f"[cyan]Caught up with the new posts, jumping to page {RESUME_PAGE} for older listings.[/cyan]")
        return RESUME_PAGE
    return current + 1

#Helper function to keep RESUME_PAGE inside the site's page count (old listings expire, so the site gets fewer pages)
def clamp_resume_page(total_pages):
    global RESUME_PAGE
    RESUME_PAGE = max(1, min(RESUME_PAGE, total_pages))

#Helper function to scroll the card into view with real mouse-wheel steps, then wait a random SCAN_TIME on it
#On Duashpi this happens right before clicking into the listing, so the visit looks like a person scrolling the list
#upid=None just scrolls a little (used on pages with nothing new)
def scan_card(page, upid):
    box = None
    if upid:
        try:
            box = page.locator(f'{LISTING_SELECTOR}[href*="/prona/{upid}/"]').first.bounding_box(timeout=2000)
        except PlaywrightTimeoutError:
            box = None #Card not found (markup changed or it's lazy), just scroll a bit instead

    viewport_height = (page.viewport_size or {}).get("height", 720)
    distance = box["y"] - viewport_height * random.uniform(0.2, 0.4) if box else random.randint(200, 400)

    #Wheel in small uneven steps instead of one jump, like a mouse wheel or trackpad
    while distance > 0:
        step = min(distance, random.randint(80, 160))
        page.mouse.wheel(0, step)
        page.wait_for_timeout(random.randint(30, 90))
        distance -= step

    page.wait_for_timeout(random.uniform(*SCAN_TIME) * 1000)

#Helper function to turn an HTML description (<br>, <p>, &amp;...) into plain text with line breaks
def clean_description(text):
    if not text:
        return None
    text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

#Helper function to pull the number out of text like "75 m²", "500 €", "185.000 €" or "50,000Leke", returns None if there is no number
#Copied from MerrJep rather than Gazeta, because Duashpi's prices are text with thousands separators, not plain JSON numbers
def parse_number(text, whole=False):
    if not text:
        return None

    match = re.search(r"\d[\d.,]*", text)
    if not match:
        return None

    number = match.group().rstrip(".,")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", number):
        number = re.sub(r"[.,]", "", number) #"1.200" is twelve hundred, not one point two
    else:
        number = number.replace(",", ".")

    try:
        value = float(number)
    except ValueError:
        return None
    return int(value) if whole else value

#Helper function to merge both phone numbers: one if they match, both split by ";" if they don't, None if neither found
def combine_phones(phone_1, phone_2):
    def digits(number):
        return "".join(char for char in number if char.isdigit())[-9:] #Last 9 digits, so "+35569..." and "069..." count as the same number

    if phone_1 and phone_2:
        if digits(phone_1) == digits(phone_2):
            return phone_1
        return f"{phone_1}; {phone_2}"

    return phone_1 or phone_2

#Run this file on its own for a quick test: python -m Duashpi_al.PlaywrightScraper2
if __name__ == "__main__":
    for listing in scrape_duashpi_al() or []:
        print(escape(json.dumps(listing, indent=4, ensure_ascii=False)))

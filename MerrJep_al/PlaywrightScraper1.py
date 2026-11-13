#Imports
from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from rich import print
from rich.markup import escape
import json
import random
import re
import time
import traceback
from Scraper.Console import utf8_output
from Scraper.config import Demo_Environment, Rest_Time, Headless, Listings_Per_Turn, Window_Position, Window_Size, Use_Installed_Chrome

#Global Variables
BASE_URL = "https://www.merrjep.al/njoftime/imobiliare-vendbanime/ne-shitje-kerkoj-te-blej-me-qera-kerkohet-me-qera-nderrohet"
CARD_SELECTOR = "div.row.row-listing[data-product-id]" #A results card, data-product-id is the UPID
LINK_SELECTOR = "h2 a.Link_vis" #The listing's link inside a card, Ctrl+clicked to open it in a new tab
CLICK_TIMEOUT_MS = 10000 #A card that is there but can't be clicked fails after 10 s instead of Playwright's 30 s
SCAN_TIME = (1.0, 2.5) #Random seconds spent "reading" each card before opening it, so the visit looks human
DEMO_LISTING_LIMIT = 5 #How many listings to scrape per turn when Demo_Environment is True
PROCESSED_UPIDS = set() #Everything this turn skips: already in the DB, scraped this turn, or remembered below
SCRAPED_LISTINGS = []

#Kept between turns while the program runs (res et_state() leaves them alone), a restart just starts from page 1 again
RESUME_PAGE = 1 #Deepest page reached so far, once a turn catches up with the new posts it jumps here for the older ones
AGENCY_UPIDS = set() #Agency listings we skipped, so "Jo Listime Agjensie" doesn't treat them as new every turn
FAILED_UPIDS = set() #Listings that failed (e.g. no JSON-LD), not reopened until the program restarts
STOP_CHECK = None #The engine's should_stop function for the current turn (None = never stop early)
AGENCY_SELECTOR = "span.isstore" #MerrJep marks agency listings on the results card with <span class="label label-default isstore">Agjent për shitje të pronës</span>

#MerrJep's attribute labels mapped to our standard database columns (every site maps its own labels to these)
ATTRIBUTE_COLUMNS = {
    "Lloji i njoftimit": "listing_type",
    "Sipërfaqe": "size_m2",
    "Numri i dhomave": "rooms",
    "Gjendje": "condition",
    "Komuna": "city",
    "Adresa/Rruga": "address",
    "Njoftim nga": "posted_by",
}
#"Njoftim nga" says "Kompani" or "Person fizik". The shared value for a private seller is "Person privat" (Gazeta's word, and what
#the AI search and the dashboard filter on), so "Person fizik" is saved as that. Derive.posted_by_of() fixes rows saved before.
POSTED_BY_NAMES = {"Person fizik": "Person privat"}

#Function to create a browser instance and run one turn: scrape up to turn_limit() NEW listings, then hand them back so the engine can save them
#known_upids are the listings already in the database, they are skipped before clicking into them
#skip_agencies=True ignores listings posted by agencies (the "Jo Listime Agjensie" preference in the UI)
#should_stop is checked between listings, when it returns True (the UI's pause button) the turn ends early with what it has so far
#Whatever goes wrong, the listings collected so far are always handed back (see "Scraper error handling" in NOTES.md)
def create_browser_instance(skip_agencies=False, known_upids=(), should_stop=None):
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

#Function that walks the results pages until the turn is full or paused. Errors are raised and handled by create_browser_instance()
def run_turn(page, skip_agencies):
    page_number = 1 #Always start at the newest posts
    while not turn_over():
        url = page_url(page_number)
        print(f"[cyan]Navigating to: {url}[/cyan]")
        open_page(page, url)
        page.wait_for_timeout(5000)
        print(f"[cyan]{escape(page.title())}[/cyan]")
        try:
            all_seen = process_listing_containers(page, skip_agencies)
        except NoListings:
            #Every turn starts at page 1, which always has listings, so an empty page 1 means the markup changed.
            #An empty later page is past the last one: old listings expire, so keep RESUME_PAGE inside the real page count
            if page_number == 1:
                raise TurnStop(MARKUP, f"no listings on {url}, the site's markup may have changed")
            print(f"[yellow]No listings found on {url}, assuming last page reached.[/yellow]")
            clamp_resume_page(page_number - 1)
            break
        print(f"[green]Page {page_number} processed. New this turn: {len(SCRAPED_LISTINGS)}/{turn_limit()}[/green]")
        page_number = next_page(page_number, all_seen)

#Helper function to open the browser, with no window when Headless is True (see config.py)
def launch_browser(p):
    #Without this flag navigator.webdriver is true, which tells every script on the site "this browser is automated"
    args = ["--disable-blink-features=AutomationControlled"]
    if not Headless:
        #A normal, visible browser, opened where config.py says (Window_Position off screen keeps it out of the agent's way)
        if Window_Position:
            args.append(f"--window-position={Window_Position[0]},{Window_Position[1]}")
        if Window_Size:
            args.append(f"--window-size={Window_Size[0]},{Window_Size[1]}")
        browser = start_browser(p, args)
        return browser, browser.new_page()

    browser = start_browser(p, args)

    #Headless still says "HeadlessChrome" in its user agent, so read the real one and drop the "Headless" part (keeps the right version and OS)
    probe = browser.new_page()
    user_agent = probe.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
    probe.close()

    context = browser.new_context(user_agent=user_agent)
    return browser, context.new_page()

#Helper function to start the agent's own Google Chrome (Use_Installed_Chrome in config.py), or Playwright's Chromium when that's
#off or Chrome isn't installed. Real Chrome sends "Google Chrome" in its Sec-CH-UA header and Playwright's Chromium only
#"Chromium", which few real people use. Either way it gets a fresh, empty profile (never the agent's bookmarks or logins), and
#headless runs Chrome's newer headless mode: the full browser with no window, so it keeps real plugins and the real GPU.
def start_browser(p, args):
    if Use_Installed_Chrome:
        try:
            return p.chromium.launch(channel="chrome", headless=Headless, args=args)
        except PlaywrightError as error:
            print(f"[yellow]Google Chrome didn't start ({escape(str(error).splitlines()[0])}), using Playwright's Chromium instead[/yellow]")
    return p.chromium.launch(channel="chromium", headless=Headless, args=args)

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

#Helper function to build the url of a results page (page 1 is the plain BASE_URL)
def page_url(number):
    return BASE_URL if number == 1 else f"{BASE_URL}?Page={number}"

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
def clamp_resume_page(last_page):
    global RESUME_PAGE
    RESUME_PAGE = max(1, min(RESUME_PAGE, last_page))

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
    if isinstance(error, PlaywrightError): # pyright: ignore[reportArgumentType]  (Pylance misreads Playwright's Error alias as a TypeVar; it is a class)
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

#Function to open a listing the way a person opens several: Ctrl+click (Cmd+click on macOS) its card, so it opens in a new tab.
#The results tab never navigates, so its cards can't move while we work through them, and there's no "back" to wait for.
#The site sees a real click on its own card, with the results page as the referrer. Returns the new tab, in front, once the listing loaded.
#A new tab starts on about:blank, which already counts as loaded, so it waits for the url to change first: without that, anything
#read straight away (the agency check, the block page check) read an empty page. A tab that fails here is closed before the error goes on.
def open_listing_tab(page, link):
    with page.context.expect_page(timeout=CLICK_TIMEOUT_MS) as new_tab:
        link.click(modifiers=["ControlOrMeta"], timeout=CLICK_TIMEOUT_MS)
    tab = new_tab.value
    try:
        tab.bring_to_front()
        tab.wait_for_url(lambda url: url != "about:blank", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        check_blocked(tab)
    except Exception:
        close_listing_tab(page, tab)
        raise
    return tab

#Function to close a listing tab and switch back to the results, like a person going back to the list
def close_listing_tab(page, tab):
    try:
        tab.close()
        page.bring_to_front()
    except PlaywrightError as error:
        if classify(error) == BROWSER_GONE:
            raise TurnStop(BROWSER_GONE, describe(error)) from error

#Helper function to scroll the card into view with real mouse-wheel steps, then wait a random SCAN_TIME on it
#Done right before opening the listing, so the visit looks like a person scrolling the list (the same idea as Gazeta and Duashpi)
def scan_card(page, upid):
    box = None
    try:
        box = page.locator(f'{CARD_SELECTOR}[data-product-id="{upid}"]').first.bounding_box(timeout=2000)
    except PlaywrightTimeoutError:
        box = None #Card not found, just scroll a bit instead

    viewport_height = (page.viewport_size or {}).get("height", 720)
    distance = box["y"] - viewport_height * random.uniform(0.2, 0.4) if box else random.randint(200, 400)

    #Wheel in small uneven steps instead of one jump, like a mouse wheel or trackpad
    while distance > 0:
        step = min(distance, random.randint(80, 160))
        page.mouse.wheel(0, step)
        page.wait_for_timeout(random.randint(30, 90))
        distance -= step

    page.wait_for_timeout(random.uniform(*SCAN_TIME) * 1000)

#Function to get back to the results page if it ever left it. Listings open in their own tab, so this is only a safety net
#for a site whose script turns the Ctrl+click into a normal click: the back button first, a fresh load if the cards don't come back
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

#Function to find each listing container, raises NoListings if the page shows none
def find_listing_containers(page):
    listings = page.locator(CARD_SELECTOR)
    try:
        listings.first.wait_for(state="visible", timeout=CARDS_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise NoListings() from None
    count = listings.count()
    print(f"[cyan]Found {count} listings on the page.[/cyan]")
    return listings

#Function to process each NEW listing container, returns True if the page had nothing new at all
def process_listing_containers(page, skip_agencies=False):
    listings = find_listing_containers(page)
    listing_ids = list(dict.fromkeys(
        listings.nth(i).get_attribute("data-product-id")
        for i in range(listings.count())
    ))
    results_url = page.url

    #Listings already in the database (or skipped before) are dropped here, before any click
    new_ids = [upid for upid in listing_ids if upid and upid not in PROCESSED_UPIDS]
    print(f"[cyan]{len(new_ids)} new, {len(listing_ids) - len(new_ids)} already seen.[/cyan]")
    if not new_ids:
        return True

    for i, upid in enumerate(new_ids):
        if turn_full():
            print(f"[yellow]Turn limit of {turn_limit()} new listings reached.[/yellow]")
            break
        if stop_requested():
            print("[yellow]Paused, ending this turn early with what we have.[/yellow]")
            break

        #The results tab never navigates (listings open in their own tab), so every card read above is still on it
        listing = page.locator(f'{CARD_SELECTOR}[data-product-id="{upid}"]')
        print(f"[cyan]Processing {i + 1}/{len(new_ids)}: {upid}[/cyan]")

        tab = None
        try:
            #Agency check happens on the results card, before opening it, so a skipped listing costs no page load or rest
            if skip_agencies and listing.locator(AGENCY_SELECTOR).count():
                print(f"[yellow]Skipping agency listing: {upid}[/yellow]")
                PROCESSED_UPIDS.add(upid)
                AGENCY_UPIDS.add(upid)
                listing_ok()
                continue

            #Scroll down to the card and pause on it like a person reading the list, then open it in a new tab
            scan_card(page, upid)
            tab = open_listing_tab(page, listing.locator(LINK_SELECTOR).first)
            data = pull_data_from_listing(tab, upid)
            SCRAPED_LISTINGS.append(data)
            PROCESSED_UPIDS.add(upid)
            listing_ok()

            #Scraped text is escaped: rich would read a title like "[/b] SHITET" as markup and raise
            print(f"\n[bold green]PROPERTY DATA — UPID: {upid}[/bold green]")
            print(f"[green]Scraped: {escape(str(data['title']))}[/green]")
            print(f"[bright_green]Price: {data['price']} {escape(str(data['currency']))}[/bright_green]")
            print(f"[bright_green]Images: {len(data['images'])}[/bright_green]")
            print(f"[bright_green]Phone: {'found' if data['phone'] else 'Not available'}[/bright_green]")

        except TurnStop:
            raise
        except Exception as error:
            listing_failed(tab or page, upid, error) #Raises TurnStop when the turn should end
        finally:
            if tab is not None:
                close_listing_tab(page, tab)

        if page.url != results_url:
            return_to_results(page, results_url)

        #Rest before opening the next listing to avoid getting flagged
        print(f"[cyan]Resting {Rest_Time}s before next listing...[/cyan]")
        page.wait_for_timeout(Rest_Time * 1000)

    return False

#Function to open a listing container and extract data
def pull_data_from_listing(page, expected_upid):
    page.locator(".ad-details-page h1").wait_for(state="visible", timeout=15000)

    product = {}

    #Extract embedded JSON-LD
    scripts = page.locator('script[type="application/ld+json"]').all_text_contents()

    for script in scripts:
        try:
            parsed = json.loads(script)
            if isinstance(parsed, dict) and parsed.get("@type") == "Product":
                product = parsed
                break
        except json.JSONDecodeError:
            continue

    #Skip this listing if no JSON-LD product data was found (remembered in FAILED_UPIDS, see listing_failed())
    if not product:
        raise MarkupError("No JSON-LD Product on the listing page")

    #Verify the unique property ID (a mismatch means we landed on another listing, so it's tried again next turn)
    upid = str(product.get("productID") or expected_upid)

    if upid != expected_upid:
        raise TemporaryError(f"UPID mismatch: {expected_upid} != {upid}")

    #Extract HTML property attributes
    attributes = {}
    tags = page.locator(".tags-area .tag-item")

    for tag in tags.all():
        name = tag.locator("span")
        value = tag.locator("bdi")

        if name.count() and value.count():
            key = name.first.inner_text().strip().rstrip(":")
            attributes[key] = value.first.inner_text().strip()

    #Extract description
    description_element = page.locator(".description-area > span")
    description = (
        description_element.first.inner_text().strip()
        if description_element.count()
        else product.get("description")
    )

    #Extract image URLs
    images = []
    for image in product.get("image", []):
        if isinstance(image, dict) and image.get("contentUrl"):
            images.append(image["contentUrl"])
        elif isinstance(image, str):
            images.append(image)

    if not images:
        gallery = page.locator('#carouselExampleIndicators a[data-fancybox="gallery"]')
        images = [
            link.get_attribute("href")
            for link in gallery.all()
            if link.get_attribute("href")
        ]

    #Extract seller information
    seller = page.locator(".mobile-seller-info .user-name")
    seller_name = seller.first.inner_text().strip() if seller.count() else None

    #Extract seller phone number from the contact button (not every listing has one)
    phone = page.locator("button .main-contact bdi")
    phone_1 = phone.first.text_content().strip() if phone.count() else None

    #Extract seller phone number from the contact item link (second place the number can show up)
    contact = page.locator('.contact-item a[href^="tel:"]')
    phone_2 = None
    if contact.count():
        contact_bdi = contact.first.locator("bdi")
        if contact_bdi.count():
            phone_2 = contact_bdi.first.text_content().strip()
        else:
            phone_2 = contact.first.get_attribute("href").replace("tel:", "").strip()

    phones = combine_phones(phone_1 or None, phone_2 or None)

    #Extract publication information
    date = page.locator(".ad-publish-info-area .published-date")
    time = page.locator(".ad-publish-info-area .published-time")

    #Structure property data
    offers = product.get("offers") or {}

    data = {
        "upid": upid,
        "url": page.url,
        "title": product.get("name") or page.locator(".ad-details-page h1").inner_text().strip(),
        "price": offers.get("price"),
        "currency": offers.get("priceCurrency"),
        "description": description,
        "attributes": attributes,
        "images": images,
        "seller": seller_name,
        "phone": phones,
        "published_date": date.first.inner_text().strip() if date.count() else None,
        "published_time": time.first.inner_text().strip() if time.count() else None
    }

    #Copy the important attributes into their own fields so they get their own database columns (attributes keeps everything as a backup)
    for label, column in ATTRIBUTE_COLUMNS.items():
        data[column] = attributes.get(label)
    data["size_m2"] = parse_number(data["size_m2"])
    data["rooms"] = parse_number(data["rooms"], whole=True)
    data["posted_by"] = POSTED_BY_NAMES.get(data["posted_by"], data["posted_by"])

    return data

#Helper function to pull the number out of text like "70 m2", "1.200 m2" or "3+", returns None if there is no number
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
        return "".join(char for char in number if char.isdigit())

    if phone_1 and phone_2:
        if digits(phone_1) == digits(phone_2):
            return phone_1
        return f"{phone_1}; {phone_2}"

    return phone_1 or phone_2

#Call each function as needed this will act as the orchestrator
if __name__ == "__main__":
    utf8_output()
    for listing in create_browser_instance() or []:
        print(escape(json.dumps(listing, indent=4, ensure_ascii=False)))
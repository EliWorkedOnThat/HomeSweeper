#Scraper for njoftime.com, returns listing dicts in the same shape as the other scrapers (see the scraper contract in CLAUDE.md)
#njoftime is a XenForo forum: every listing is a thread, and the site has one forum per kind of property (FORUMS below).
#Works like Duashpi: the results page only has short cards, so each NEW listing is opened in a new tab and read there.
#The listing page has a JSON-LD "DiscussionForumPosting" (title, text, date, seller) and the forum's custom
#fields (type, m², rooms, floor, price, zone, street), the phone and photos are read from the page's HTML.
#Imports
from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from rich import print
from rich.markup import escape
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import math
import random
import re
import time
import traceback
import unicodedata
from Scraper.Console import utf8_output
from Scraper.config import Demo_Environment, Rest_Time, Headless, Listings_Per_Turn, Window_Position, Window_Size, Use_Installed_Chrome

#Global Variables
SITE_URL = "https://www.njoftime.com"
#The real estate forums, scraped in this order. Each turn shares its listings out between them (see run_turn()), so the huge
#sale forum (2.400+ pages) can't use up every turn. The forum decides the listing type, its name goes into "Kategoria"
FORUMS = [
    {"key": "shtepi-ne-shitje", "url": f"{SITE_URL}/forums/shtepi-ne-shitje.4/", "name": "Shtëpi në shitje", "listing_type": "Shitet"},
    {"key": "shtepi-me-qera", "url": f"{SITE_URL}/forums/shtepi-me-qera.36/", "name": "Shtëpi me qera", "listing_type": "Jepet me qera"},
    {"key": "biznes-ne-shitje", "url": f"{SITE_URL}/forums/ambjente-biznesi-ne-shitje.37/", "name": "Ambjente biznesi në shitje", "listing_type": "Shitet"},
    {"key": "biznes-me-qera", "url": f"{SITE_URL}/forums/ambjente-biznesi-me-qera.38/", "name": "Ambjente biznesi me qera", "listing_type": "Jepet me qera"},
    {"key": "truall", "url": f"{SITE_URL}/forums/truall.39/", "name": "Truall", "listing_type": "Shitet"},
]
DEMO_LISTING_LIMIT = 5 #How many listings to scrape per turn when Demo_Environment is True
PROCESSED_UPIDS = set() #Everything this turn skips: already in the DB, scraped this turn, or remembered below
SCRAPED_LISTINGS = []
FORUM_TARGET = 0 #How many listings SCRAPED_LISTINGS should hold when the current forum's share of the turn is done

#Kept between turns while the program runs (reset_state() leaves them alone), a restart just starts from page 1 again
RESUME_PAGES = {} #Deepest page reached so far in each forum (by key), once a turn catches up with a forum it jumps there
AGENCY_UPIDS = set() #Agency listings we skipped, so "Jo Listime Agjensie" doesn't treat them as new every turn
FAILED_UPIDS = set() #Listings that failed (e.g. no JSON-LD), not reopened until the program restarts
STOP_CHECK = None #The engine's should_stop function for the current turn (None = never stop early)

#njoftime's markup on the results page (check again with Tools.FetchHTML if the scraper stops finding listings)
#A forum page has 30 normal threads, sorted by the last time the seller "refreshed" them (not by posting date: agencies bump
#old listings to the top all day, so page 1 is mostly old listings and the pages shift every minute). Page 1 also has
#~10-30 sticky (paid) threads on top, XenForo only shows those on page 1. Both kinds are real listings and are scraped.
CARD_SELECTOR = ".structItem--thread" #A thread card, its class has js-threadListItem-<thread id>
TITLE_LINK_SELECTOR = '.structItem-title a[href*="/threads/"]' #The card's title link (a prefix label before it links to the forum)
BUMP_TIME_SELECTOR = ".structItem-startDate time" #"10.11.2026, 14:32", when the seller last refreshed the listing
#A seller with a paid business profile shows their logo on the card, everyone else gets the empty "--default" box
LOGO_SELECTOR = "a.company-logo:not(.company-logo--default)"
PAGE_LINK_SELECTOR = ".pageNav-main .pageNav-page a" #Pagination links, the highest number is the last page
CLICK_TIMEOUT_MS = 10000 #A card that is there but can't be clicked (e.g. a new popup covers it) fails after 10 s instead of Playwright's 30 s
THREAD_ID_PATTERN = re.compile(r"/threads/(?:[^/]*\.)?(\d+)/") #The UPID is the thread id at the end of the url, e.g. .../tirane-shitet-....1345346/
#Reads every card at once in the browser (one call instead of four per card), the selectors are passed in so they stay above
READ_CARDS_JS = """(cards, s) => cards.map(card => {
    const link = card.querySelector(s.link);
    const time = card.querySelector(s.time);
    return {
        href: link ? link.getAttribute("href") : "",
        author: card.getAttribute("data-author") || "",
        logo: Boolean(card.querySelector(s.logo)),
        bumped: time ? time.textContent.trim() : "",
    };
})"""

#njoftime's markup on a listing page
TITLE_SELECTOR = ".message-attribution h1" #The thread title
POST_SELECTOR = "article.message.first-post" #The listing itself (replies and the seller's signature are left out)
BODY_SELECTOR = f"{POST_SELECTOR} .message-body .bbWrapper" #The description as the seller wrote it
FIELD_SELECTOR = "dl[data-field]" #The forum's custom fields, <dt> is the label and <dd> the value (the city has no <dt>)
PHONE_SELECTOR = 'a[href^="tel:"]' #The call button, href="tel:+355686667271"
IMAGE_SELECTOR = ".js-thread-view-slider a.js-lbImage" #Photo slider, each link's href is the full size photo

#Agency check for "Jo Listime Agjensie" and posted_by: njoftime has no agency flag, so a seller counts as an agency when
#they have a company logo on the card (a paid business profile) OR their account name sounds like a business.
#Both are on the results card, so agency listings are skipped without opening them. A private seller with a logo is skipped
#too, and an agency with a plain name and no logo gets through.
AGENCY_NAME_PATTERN = re.compile(
    r"imobil|immobil|real ?estate|realt|propert|agjen[cs]|agenc|agjent|\bprona\b|\bhomes?\b|\bestate\b"
    r"|\bkompani|\bshpk\b|sh\.p\.k|\bgroup\b|\binvest|ndertim|construct"
)
AGENCY_POSTED_BY = "Kompani" #Agencies as decided above, everyone else is left empty (we can't be sure they are private)

#Human pacing, so the visit looks like a person reading the list (same idea as Gazeta and Duashpi)
SCAN_TIME = (1.0, 2.5) #Random seconds spent "reading" each card before clicking it
PAGE_REST_JITTER = 2.0 #Up to this many random extra seconds on top of Rest_Time, so pages don't load on an exact clock

#njoftime's field labels renamed for the "All details" view (labels not listed here are kept as the site writes them)
FIELD_NAMES = {
    "field-location": "Qyteti", #This one has no label, so it's matched by its data-field
    "Tipi": "Tipi",
    "Siperfaqja m²": "Sipërfaqe",
    "Nr dhoma": "Nr dhoma",
    "Kati": "Kati",
    "Cmimi Euro": "Çmimi në Euro",
    "Location Rruga + Nr.": "Rruga",
    "Nr. Njesia - Zona me e afert?": "Zona",
}

#njoftime's labels mapped to our standard database columns (every site maps its own labels to these)
ATTRIBUTE_COLUMNS = {
    "Sipërfaqe": "size_m2",
    "Nr dhoma": "rooms", #"2+1" is saved as 2 (bedrooms), like Duashpi's "Dhoma gjumi"
}

#The "Cmimi Euro" field is converted by the site (a "18,500,000 LEKE" listing shows "185.000 €"), so a LEK price in the
#title wins, it's the price as the seller wrote it. The site writes most titles itself from the fields, with the real currency
#(no \b after "€": it isn't a letter, so "65'000 €," would never match)
LEK_PRICE_PATTERN = re.compile(r"(\d[\d.,']*)\s*(?:lek[eë]?|all)\b", re.IGNORECASE)
EUR_PRICE_PATTERN = re.compile(r"(\d[\d.,']*)\s*(?:€|eur(?:o)?\b)", re.IGNORECASE)
CURRENCIES = {"€": "EUR", "EUR": "EUR", "LEK": "ALL", "LEKE": "ALL", "ALL": "ALL"}
#njoftime writes city names without ë/ç ("Tirane"), MerrJep writes them properly ("Tiranë"), unknown cities are kept as they are
CITY_NAMES = {
    "Tirane": "Tiranë", "Durres": "Durrës", "Vlore": "Vlorë", "Shkoder": "Shkodër", "Korce": "Korçë",
    "Sarande": "Sarandë", "Lezhe": "Lezhë", "Kavaje": "Kavajë", "Gjirokaster": "Gjirokastër", "Kruje": "Krujë",
    "Lushnje": "Lushnjë", "Kukes": "Kukës", "Permet": "Përmet", "Tepelene": "Tepelenë", "Gramsh": "Gramsh",
}

#The JSON-LD date is in UTC, the other sites show Tirana time. zoneinfo needs the tzdata package on Windows; without it a
#fixed +1 h is used (right in winter, an hour early in summer)
try:
    TIRANA_TIME = ZoneInfo("Europe/Tirane")
except ZoneInfoNotFoundError:
    TIRANA_TIME = timezone(timedelta(hours=1))

#Function to run one turn: scrape up to turn_limit() NEW listings, then hand them back so the engine can save them
#known_upids are the listings already in the database, they are skipped before clicking into them
#skip_agencies=True ignores listings posted by agencies (the "Jo Listime Agjensie" preference in the UI)
#should_stop is checked between listings, when it returns True (the UI's pause button) the turn ends early with what it has so far
#Whatever goes wrong, the listings collected so far are always handed back (see "Scraper error handling" in NOTES.md)
def scrape_njoftime(skip_agencies=False, known_upids=(), should_stop=None):
    global STOP_CHECK
    STOP_CHECK = should_stop
    reset_state(known_upids, skip_agencies) #Start fresh every turn, only RESUME_PAGES and the remembered UPIDs carry over
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
    print(f"[green]Turn done: {len(SCRAPED_LISTINGS)} new listings, next turn continues older listings from pages {RESUME_PAGES}[/green]")

    #Hand a copy of the scraped listings back to whoever called this function (Engine.py)
    return list(SCRAPED_LISTINGS)

#Function that runs each forum in turn, giving each one an equal share of what's left of the turn.
#A forum that runs out of new listings early leaves its share to the next ones (the share is worked out again for each forum)
def run_turn(page, skip_agencies):
    for index, forum in enumerate(FORUMS):
        if turn_over():
            break
        if index:
            rest(page, "next forum")
        share = math.ceil((turn_limit() - len(SCRAPED_LISTINGS)) / (len(FORUMS) - index))
        run_forum(page, forum, share, skip_agencies)

#Function that walks one forum's pages until its share is full or paused. Errors are raised and handled by scrape_njoftime()
def run_forum(page, forum, share, skip_agencies):
    global FORUM_TARGET
    FORUM_TARGET = len(SCRAPED_LISTINGS) + share
    print(f"[magenta]Forum {forum['name']}: up to {share} new listings.[/magenta]")
    page_number = 1 #Always start at the most recently refreshed listings
    while not forum_over():
        url = page_url(forum, page_number)
        print(f"[cyan]Navigating to: {url}[/cyan]")
        open_page(page, url)

        try:
            find_listing_container(page)
        except NoListings:
            #Every forum's page 1 has listings, so an empty page 1 means the markup changed.
            #An empty later page is past the last one: old listings expire, so keep the resume page inside the real page count
            if page_number == 1:
                raise TurnStop(MARKUP, f"no listings on {url}, the site's markup may have changed")
            print(f"[yellow]No listings found on {url}, assuming last page reached.[/yellow]")
            clamp_resume_page(forum, page_number - 1)
            break

        total_pages = read_total_pages(page) or page_number
        all_seen = process_listing_containers(page, forum, skip_agencies)
        print(f"[green]{forum['name']}: page {page_number}/{total_pages} processed. New this turn: {len(SCRAPED_LISTINGS)}/{turn_limit()}[/green]")

        page_number = next_page(forum, page_number, all_seen)
        if page_number > total_pages:
            print(f"[green]Last page of {forum['name']} reached, every listing in it has been seen.[/green]")
            clamp_resume_page(forum, total_pages)
            break
        if forum_over():
            break

        rest(page, "next page")

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

#Function to find the listings on page, raises NoListings if the page shows none
def find_listing_container(page):
    listings = page.locator(CARD_SELECTOR)
    try:
        listings.first.wait_for(state="visible", timeout=CARDS_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise NoListings() from None
    count = listings.count()
    print(f"[cyan]Found {count} listings on the page.[/cyan]")
    return listings

#Helper function to read how many pages the forum has from the pagination links (0 if there are none)
def read_total_pages(page):
    numbers = [link.inner_text().strip() for link in page.locator(PAGE_LINK_SELECTOR).all()]
    return max((int(number) for number in numbers if number.isdigit()), default=0)

#Function to read every card on the results page: [{upid, author, logo, bumped, agency}], each thread once, in page order
def read_cards(page):
    selectors = {"link": TITLE_LINK_SELECTOR, "time": BUMP_TIME_SELECTOR, "logo": LOGO_SELECTOR}
    cards = {}
    for card in page.locator(CARD_SELECTOR).evaluate_all(READ_CARDS_JS, selectors):
        match = THREAD_ID_PATTERN.search(card["href"] or "")
        if not match or match.group(1) in cards:
            continue
        card["upid"] = match.group(1)
        card["agency"] = is_agency(card["author"], card["logo"])
        cards[card["upid"]] = card
    return list(cards.values())

#Function to process each NEW listing card, returns True if the page had nothing new at all
def process_listing_containers(page, forum, skip_agencies=False):
    cards = read_cards(page)
    results_url = page.url

    #Listings already in the database (or skipped before) are dropped here, before any click
    new_cards = [card for card in cards if card["upid"] not in PROCESSED_UPIDS]
    print(f"[cyan]{len(new_cards)} new, {len(cards) - len(new_cards)} already seen.[/cyan]")

    #The agency check only needs the card, so agency listings are skipped without opening them (and remembered)
    if skip_agencies:
        agencies = [card for card in new_cards if card["agency"]]
        for card in agencies:
            PROCESSED_UPIDS.add(card["upid"])
            AGENCY_UPIDS.add(card["upid"])
        if agencies:
            print(f"[yellow]Skipping {len(agencies)} agency listings ({escape(', '.join(sorted({card['author'] for card in agencies})))}).[/yellow]")
        new_cards = [card for card in new_cards if not card["agency"]]

    if not new_cards:
        scan_card(page, None) #Still scroll a little, so a page of known listings isn't loaded and left instantly
        return True

    opened = 0 #Listings opened on this page, the rest before each one is skipped for the first
    for i, card in enumerate(new_cards):
        upid = card["upid"]
        if forum_full():
            print(f"[yellow]This forum's share of the turn is full ({len(SCRAPED_LISTINGS)}/{turn_limit()} new listings).[/yellow]")
            break
        if stop_requested():
            print("[yellow]Paused, ending this turn early with what we have.[/yellow]")
            break

        #Refreshed listings jump to page 1 every minute, but the results tab never navigates (listings open in their own tab),
        #so every card read above is still on it. A card that moved on the site shows up again on another page as seen
        link = page.locator(f"{card_selector(upid)} {TITLE_LINK_SELECTOR}").first

        if opened:
            rest(page, "next listing") #Every listing is a page load, so rest between them to avoid getting flagged
        opened += 1

        print(f"[cyan]Processing {i + 1}/{len(new_cards)}: {upid}[/cyan]")

        tab = None
        try:
            #Scroll down to the card and pause on it like a person reading the list, then open it in a new tab
            scan_card(page, upid)
            tab = open_listing_tab(page, link)

            data = pull_data_from_listing(tab, card, forum)
            SCRAPED_LISTINGS.append(data)
            PROCESSED_UPIDS.add(upid)
            listing_ok()

            #Scraped text is escaped: rich would read a title like "[/b] SHITET" as markup and raise
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

    return False

#Function to read the open listing page into our standard listing dict
#card is what the results page said about it (seller, logo, last refresh), forum is the forum it was listed in
def pull_data_from_listing(page, card, forum):
    page.locator(TITLE_SELECTOR).first.wait_for(state="visible", timeout=15000)

    #Check we are on the listing we clicked (a mismatch means we landed on another listing, so it's tried again next turn)
    match = THREAD_ID_PATTERN.search(page.url)
    upid = match.group(1) if match else None
    if upid != card["upid"]:
        raise TemporaryError(f"UPID mismatch: {card['upid']} != {upid}")

    #No posting data on a page that loaded means the markup changed (remembered in FAILED_UPIDS, see listing_failed())
    posting = read_json_ld(page)
    if not posting:
        raise MarkupError("No JSON-LD DiscussionForumPosting on the listing page")

    #The custom fields, the forum + type as the category, and the last refresh, kept in attributes so "All details" in the UI shows them
    attributes = read_fields(page)
    kind = attributes.get("Tipi")
    attributes["Kategoria"] = f"{forum['name']} · {kind}" if kind else forum["name"] #Derive.py reads the property type from it
    if card.get("bumped"):
        attributes["Rifreskuar"] = card["bumped"] #The time the results list is sorted by, usually the same as published_date

    title = first_text(page, TITLE_SELECTOR) or (posting.get("headline") or "").strip()
    price, currency = read_price(title, attributes.get("Çmimi në Euro"))
    published_date, published_time = read_published(posting.get("datePublished"))

    phone = page.locator(PHONE_SELECTOR).first.get_attribute("href") if page.locator(PHONE_SELECTOR).count() else None
    images = list(dict.fromkeys(
        link.get_attribute("href") for link in page.locator(IMAGE_SELECTOR).all() if link.get_attribute("href")
    ))
    if not images and posting.get("image"):
        images = [posting["image"]] if isinstance(posting["image"], str) else list(posting["image"])

    city = attributes.get("Qyteti")
    author = posting.get("author") or {}

    data = {
        "upid": upid,
        "url": page.url.split("?")[0].split("#")[0],
        "title": title,
        "price": price,
        "currency": currency,
        "description": read_description(page, posting),
        "attributes": attributes,
        "images": images,
        "seller": (author.get("name") or card.get("author") or "").strip() or None,
        "phone": phone.removeprefix("tel:").strip() or None if phone else None,
        "published_date": published_date,
        "published_time": published_time,
        "listing_type": forum["listing_type"],
        "condition": None, #njoftime has no condition field, the seller writes it in the description
        "city": CITY_NAMES.get(city, city) if city else None,
        "address": attributes.get("Rruga"),
        "posted_by": AGENCY_POSTED_BY if card.get("agency") else None,
    }

    #Copy the important attributes into their own fields so they get their own database columns (attributes keeps everything as a backup)
    for label, column in ATTRIBUTE_COLUMNS.items():
        data[column] = attributes.get(label)
    data["size_m2"] = parse_number(data["size_m2"])
    data["rooms"] = parse_number(data["rooms"], whole=True)

    return data

#Helper function to read the listing's JSON-LD, returns the DiscussionForumPosting dict (the page's "mainEntity"), empty if missing
def read_json_ld(page):
    for script in page.locator('script[type="application/ld+json"]').all_text_contents():
        try:
            parsed = json.loads(script)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        entity = parsed.get("mainEntity") if parsed.get("@type") == "WebPage" else parsed
        if isinstance(entity, dict) and entity.get("@type") == "DiscussionForumPosting":
            return entity
    return {}

#Helper function to read the forum's custom fields into {label: value}: "Tipi": "apartament", "Kati": "8", "Sipërfaqe": "60 m²"
def read_fields(page):
    fields = {}
    for field in page.locator(f"{POST_SELECTOR} {FIELD_SELECTOR}").all():
        label_element = field.locator("dt")
        label = " ".join(label_element.first.inner_text().split()) if label_element.count() else ""
        label = FIELD_NAMES.get(field.get_attribute("data-field") or "") or FIELD_NAMES.get(label, label)
        value = " ".join(field.locator("dd").first.inner_text().split()) if field.locator("dd").count() else ""
        if label == "Kati":
            value = re.sub(r"^kati\s*", "", value, flags=re.IGNORECASE) #The value repeats the label: "Kati 8"
        if label and value:
            fields[label] = value
    return fields

#Helper function to check if a seller is an agency: a company logo on their card, or a business-sounding account name
def is_agency(author, has_logo):
    return bool(has_logo) or bool(AGENCY_NAME_PATTERN.search(norm(author)))

#Helper function: lowercase without accents, so "Agjensia" and "AGJËNSIA" match the same pattern (same as Derive.norm)
def norm(text):
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn").lower()

#Helper function to read the price: a LEK amount in the title as the seller wrote it, else the "Cmimi Euro" field,
#else a € amount in a title the seller wrote by hand. Returns (number, currency), (None, None) when there is no price
def read_price(title, euro_field):
    match = LEK_PRICE_PATTERN.search(title or "")
    if match and (price := parse_number(match.group(1).replace("'", ""))):
        return price, "ALL"
    price, currency = parse_price(euro_field or "")
    if price:
        return price, currency
    match = EUR_PRICE_PATTERN.search(title or "")
    if match and (price := parse_number(match.group(1).replace("'", ""))):
        return price, "EUR"
    return None, None

#Helper function to read a price text like "185.000 €", returns (number, currency), (None, None) without a number
def parse_price(text):
    price = parse_number(text)
    if not price:
        return None, None
    upper = text.upper()
    currency = next((code for symbol, code in CURRENCIES.items() if symbol in upper), None)
    return price, currency

#Helper function to turn the JSON-LD date ("2026-11-10T10:17:53+00:00", UTC) into Tirana time ("10/11/2026", "11:17"),
#the same date format as Gazeta and Duashpi. A refresh resets this date too (checked 2026-11-10), and njoftime shows the
#original posting date nowhere, so an old listing refreshed today looks posted today. Only the thread id grows with age
def read_published(text):
    try:
        moment = datetime.fromisoformat(str(text)).astimezone(TIRANA_TIME)
    except ValueError:
        return None, None
    return moment.strftime("%d/%m/%Y"), moment.strftime("%H:%M")

#Helper function to read the description from the post (keeps the line breaks), the JSON-LD text is the backup
def read_description(page, posting):
    body = page.locator(BODY_SELECTOR)
    text = body.first.inner_text() if body.count() else posting.get("text") or ""
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

#Helper function to get the text of the first element a selector matches, with spaces tidied, None if there is none
def first_text(page, selector):
    for element in page.locator(selector).all():
        text = " ".join(element.inner_text().split())
        if text:
            return text
    return None

#Helper function: the CSS selector of one thread's card
def card_selector(upid):
    return f"{CARD_SELECTOR}.js-threadListItem-{upid}"

#Helper function to wait Rest_Time plus a random bit before the next page load, so loads don't happen on an exact clock
def rest(page, before):
    seconds = Rest_Time + random.uniform(0, PAGE_REST_JITTER)
    print(f"[cyan]Resting {seconds:.1f}s before {before}...[/cyan]")
    page.wait_for_timeout(seconds * 1000)

#Helper function to open the browser, with no window when Headless is True (copied from the Gazeta scraper, see CLAUDE.md gotchas)
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
    global FAIL_STREAK, FORUM_TARGET
    FAIL_STREAK = 0
    FORUM_TARGET = 0
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

#Helper function to check if the current forum has collected its share of the turn (or the whole turn is full)
def forum_full():
    return len(SCRAPED_LISTINGS) >= FORUM_TARGET or turn_full()

#Helper function to check if the user paused scraping (the engine passes the check in as should_stop)
def stop_requested():
    return bool(STOP_CHECK and STOP_CHECK())

#Helper function to check if this turn should end: enough new listings, or paused
def turn_over():
    return turn_full() or stop_requested()

#Helper function to check if the current forum is done: its share is full, or paused
def forum_over():
    return forum_full() or stop_requested()

#Helper function to build the url of a forum's results page (page 1 is the plain forum url, then .../page-2, .../page-3...)
def page_url(forum, number):
    return forum["url"] if number == 1 else f"{forum['url']}page-{number}"

#Helper function to pick the next page: normally the next one, but once a page had nothing new we have caught up with the
#fresh posts, so we jump straight to where the last turn stopped instead of walking through pages we already have
def next_page(forum, current, all_seen):
    resume = RESUME_PAGES[forum["key"]] = max(RESUME_PAGES.get(forum["key"], 1), current)
    if all_seen and resume > current:
        print(f"[cyan]Caught up with the new posts, jumping to page {resume} for older listings.[/cyan]")
        return resume
    return current + 1

#Helper function to keep a forum's resume page inside its page count (old listings expire, so the forum gets fewer pages)
def clamp_resume_page(forum, total_pages):
    RESUME_PAGES[forum["key"]] = max(1, min(RESUME_PAGES.get(forum["key"], 1), total_pages))

#Helper function to scroll the card into view with real mouse-wheel steps, then wait a random SCAN_TIME on it
#This happens right before clicking into the listing, so the visit looks like a person scrolling the list
#upid=None just scrolls a little (used on pages with nothing new)
def scan_card(page, upid):
    box = None
    if upid:
        try:
            box = page.locator(card_selector(upid)).first.bounding_box(timeout=2000)
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

#Helper function to pull the number out of text like "75 m²", "500 €", "185.000 €" or "50,000Leke", returns None if there is no number
#Copied from Duashpi, because njoftime's prices are text with thousands separators ("185.000 €", "18,500,000 LEKE")
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

#Run this file on its own for a quick test: python -m njoftime_al.PlaywrightScraper3
if __name__ == "__main__":
    utf8_output()
    for listing in scrape_njoftime() or []:
        print(escape(json.dumps(listing, indent=4, ensure_ascii=False)))

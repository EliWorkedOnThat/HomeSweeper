#Scraper for gazetacelesi.al, returns listing dicts in the same shape as the MerrJep scraper (see the scraper contract in CLAUDE.md)
#Gazeta Celesi is a Next.js site: every results page embeds its listings as JSON in <script id="__NEXT_DATA__">.
#That JSON holds the full listing (phone, description, all images, all properties), the same data the listing's own page has,
#so unlike MerrJep we never click into a listing: one page load gives us 20 complete listings.
#Imports
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from rich import print
from datetime import datetime, timedelta, timezone
import html
import json
import random
import re
from Scraper.config import Demo_Environment, Rest_Time, Headless, Listings_Per_Turn

#Global Variables
SITE_URL = "https://www.gazetacelesi.al"
BASE_URL = f"{SITE_URL}/shtepi/njoftime" #All real estate listings, newest first, pages are ?page=1, ?page=2...
DEMO_LISTING_LIMIT = 5 #How many listings to scrape per turn when Demo_Environment is True
PROCESSED_UPIDS = set() #Everything this turn skips: already in the DB, scraped this turn, or remembered below
SCRAPED_LISTINGS = []

#Kept between turns while the program runs (reset_state() leaves them alone), a restart just starts from page 1 again
RESUME_PAGE = 1 #Deepest page reached so far, once a turn catches up with the new posts it jumps here for the older ones
AGENCY_UPIDS = set() #Agency listings we skipped, so "Jo Listime Agjensie" doesn't treat them as new every turn
FAILED_UPIDS = set() #Listings that failed to map, not retried until the program restarts
STOP_CHECK = None #The engine's should_stop function for the current turn (None = never stop early)
AGENCY_USER_TYPE = "business" #Gazeta Celesi marks agency accounts with userType "business" (private sellers are "consumer")
NO_PRICE = 1 #Listings without a price show "1" as the price, we save those as None ("Çmimi me marrëveshje" in the UI)
CARD_SELECTOR = 'a[class*="ad-list-card_containerH"]' #A results card, its href ends in "-<upid>.html"
SCAN_TIME = (1.0, 2.5) #Random seconds spent "reading" each card, so the page isn't loaded and left instantly
PAGE_REST_JITTER = 2.0 #Up to this many random extra seconds on top of Rest_Time between pages, so pages don't load on an exact clock

#Gazeta Celesi's property labels mapped to our standard database columns (every site maps its own labels to these)
ATTRIBUTE_COLUMNS = {
    "Siperfaqe": "size_m2",
    "Dhome gjumi": "rooms",
    "Gjendja": "condition",
}

#Gazeta Celesi's values rewritten to MerrJep's wording, so the UI filters show one option per meaning across sites
LISTING_TYPES = {"NE SHITJE": "Shitet", "ME QERA": "Jepet me qera", "Shitje ne ankand": "Shitet në ankand"}
CURRENCIES = {"EURO": "EUR", "LEKE": "ALL"}
POSTED_BY = {"business": "Kompani", "consumer": "Person privat"}
#Gazeta Celesi writes city names without ë/ç ("Tirane"), MerrJep writes them properly ("Tiranë"), unknown cities are kept as they are
CITY_NAMES = {
    "Tirane": "Tiranë", "Durres": "Durrës", "Vlore": "Vlorë", "Shkoder": "Shkodër", "Korce": "Korçë",
    "Sarande": "Sarandë", "Lezhe": "Lezhë", "Kavaje": "Kavajë", "Gjirokaster": "Gjirokastër", "Kruje": "Krujë",
    "Lushnje": "Lushnjë", "Kukes": "Kukës", "Permet": "Përmet", "Tepelene": "Tepelenë", "Gramsh": "Gramsh",
}

#Function to run one turn: scrape up to turn_limit() NEW listings, then hand them back so the engine can save them
#known_upids are the listings already in the database, they are skipped without any work
#skip_agencies=True ignores listings posted by agencies (the "Jo Listime Agjensie" preference in the UI)
#should_stop is checked between listings, when it returns True (the UI's pause button) the turn ends early with what it has so far
def scrape_gazetacelesi(skip_agencies=False, known_upids=(), should_stop=None):
    global STOP_CHECK
    STOP_CHECK = should_stop
    reset_state(known_upids, skip_agencies) #Start fresh every turn, only RESUME_PAGE and the remembered UPIDs carry over
    page_number = 1 #Always start at the newest posts
    with sync_playwright() as p:
        browser, page = launch_browser(p)
        try:
            while not turn_over():
                url = page_url(page_number)
                print(f"[cyan]Navigating to: {url}[/cyan]")
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                try:
                    ads, meta = read_page_data(page)
                except (PlaywrightTimeoutError, ValueError) as error:
                    print(f"[yellow]Could not read listings on {url} ({error}), stopping.[/yellow]")
                    break

                total_pages = meta.get("totalPages") or page_number
                if not ads:
                    print(f"[yellow]No listings found on {url}, assuming last page reached.[/yellow]")
                    clamp_resume_page(total_pages)
                    break

                all_seen = process_ads(page, ads, skip_agencies)
                print(f"[green]Page {page_number}/{total_pages} processed. New this turn: {len(SCRAPED_LISTINGS)}/{turn_limit()}[/green]")

                page_number = next_page(page_number, all_seen)
                if page_number > total_pages:
                    print("[green]Last page reached, every listing on the site has been seen.[/green]")
                    clamp_resume_page(total_pages)
                    break
                if turn_over():
                    break

                #Rest before loading the next page to avoid getting flagged
                rest = Rest_Time + random.uniform(0, PAGE_REST_JITTER)
                print(f"[cyan]Resting {rest:.1f}s before next page...[/cyan]")
                page.wait_for_timeout(rest * 1000)
        finally:
            browser.close()
            print(f"[green]Turn done: {len(SCRAPED_LISTINGS)} new listings, next turn continues older listings from page {RESUME_PAGE}[/green]")

    #Hand a copy of the scraped listings back to whoever called this function (Engine.py)
    return list(SCRAPED_LISTINGS)

#Helper function to open the browser, with no window when Headless is True (copied from the MerrJep scraper, see CLAUDE.md gotchas)
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

#Helper function to build the url of a results page
def page_url(number):
    return f"{BASE_URL}?page={number}"

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

#Function to read the listings JSON the site embeds in the page, returns (list of ads, pagination info)
def read_page_data(page):
    #The script is in the server's HTML, so it's there as soon as the page loads (no fixed 5 s wait needed)
    script = page.locator("script#__NEXT_DATA__")
    script.wait_for(state="attached", timeout=15000)

    try:
        data = json.loads(script.text_content())
    except json.JSONDecodeError as error:
        raise ValueError(f"__NEXT_DATA__ is not valid JSON: {error}")

    #premiumAds (the promoted box repeated on every page) are left out on purpose, they are not part of the real results
    ads_list = data.get("props", {}).get("pageProps", {}).get("getAdsList") or {}
    payload = ads_list.get("data") or {}
    if "data" not in payload:
        raise ValueError("No getAdsList in __NEXT_DATA__, the site's markup may have changed")

    print(f"[cyan]Found {len(payload['data'])} listings on the page.[/cyan]")
    return payload["data"], payload.get("meta") or {}

#Function to turn each NEW ad on a results page into a listing dict, returns True if the page had nothing new at all
def process_ads(page, ads, skip_agencies=False):
    results_url = page.url
    new_ads = [ad for ad in ads if ad.get("id") and str(ad["id"]) not in PROCESSED_UPIDS]
    print(f"[cyan]{len(new_ads)} new, {len(ads) - len(new_ads)} already seen.[/cyan]")
    if not new_ads:
        scan_card(page, None) #Still scroll a little, so a page of known listings isn't loaded and left instantly
        return True

    for i, ad in enumerate(new_ads):
        if turn_full():
            print(f"[yellow]Turn limit of {turn_limit()} new listings reached.[/yellow]")
            break
        if stop_requested():
            print("[yellow]Paused, ending this turn early with what we have.[/yellow]")
            break

        upid = str(ad["id"])

        #Scroll down to the card and pause on it like a person reading the list (agency cards too, a person sees those as well)
        scan_card(page, upid)

        #Agency check comes straight from the JSON, so a skipped listing costs nothing
        if skip_agencies and ad.get("userType") == AGENCY_USER_TYPE:
            print(f"[yellow]Skipping agency listing: {upid}[/yellow]")
            PROCESSED_UPIDS.add(upid)
            AGENCY_UPIDS.add(upid)
            continue

        print(f"[cyan]Processing {i + 1}/{len(new_ads)}: {upid}[/cyan]")

        try:
            data = pull_data_from_ad(ad, results_url)
            SCRAPED_LISTINGS.append(data)
            PROCESSED_UPIDS.add(upid)

            print(f"[green]Scraped: {data['title']}[/green]")
            print(f"[bright_green]Price: {data['price']} {data['currency']}[/bright_green]")
            print(f"[bright_green]Images: {len(data['images'])}[/bright_green]")
            print(f"[bright_green]Phone: {data['phone'] or 'Not available'}[/bright_green]")

        except Exception as error:
            print(f"[red]Failed to scrape {upid}: {error}[/red]")
            PROCESSED_UPIDS.add(upid)
            FAILED_UPIDS.add(upid) #Don't retry it every turn, a broken listing would otherwise stop pages from ever counting as "seen"

    return False

#Helper function to scroll the card into view with real mouse-wheel steps, then wait a random SCAN_TIME on it
#The data already came from the JSON, this is only so the visit looks like a person scrolling the list
#upid=None just scrolls a little (used on pages with nothing new)
def scan_card(page, upid):
    box = None
    if upid:
        try:
            box = page.locator(f'{CARD_SELECTOR}[href$="-{upid}.html"]').first.bounding_box(timeout=2000)
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

#Function to map one ad from the site's JSON to our standard listing dict
def pull_data_from_ad(ad, results_url):
    attributes = read_properties(ad.get("properties") or [])

    #A few useful extras that aren't in "properties", kept in attributes so "All details" in the UI shows them
    zone = (ad.get("zone") or {}).get("name")
    if zone:
        attributes["Zona"] = zone
    categories = [category.get("name") for category in ad.get("categories") or [] if category.get("name")]
    if categories:
        attributes["Kategoria"] = categories[-1] #The most specific one, e.g. "Apartament 2+1"
    if ad.get("rejectAgencyContact"):
        attributes["Nuk pranon agjenci"] = "Po" #The seller asked not to be contacted by agencies

    location = ad.get("location") or {}
    city = str(location.get("name") or "") #Always a string, so the CITY_NAMES lookup below type-checks

    contact = ad.get("contact") or {}
    phones = None if ad.get("hidePhoneNumber") else combine_phones(contact.get("mobilePhone") or None, contact.get("phone") or None)
    published_date, published_time = to_tirana_time(ad.get("publishedAt"))

    path = ad.get("canonicalPath")
    data = {
        "upid": str(ad["id"]),
        "url": f"{SITE_URL}{path}" if path else results_url,
        "title": (ad.get("title") or "").strip(),
        "price": parse_price(ad.get("price")),
        "currency": CURRENCIES.get(ad.get("currency"), ad.get("currency")),
        "description": clean_description(ad.get("description")),
        "attributes": attributes,
        "images": [image["url"] for image in ad.get("images") or [] if isinstance(image, dict) and image.get("url")],
        "seller": ad.get("userDisplayName"),
        "phone": phones,
        "published_date": published_date,
        "published_time": published_time,
        "listing_type": LISTING_TYPES.get(ad.get("action"), ad.get("action")),
        "city": CITY_NAMES.get(city, city) or None,
        "address": location.get("address") or zone,
        "posted_by": POSTED_BY.get(ad.get("userType"), ad.get("userType")),
    }

    #Copy the important properties into their own fields so they get their own database columns (attributes keeps everything as a backup)
    for label, column in ATTRIBUTE_COLUMNS.items():
        data[column] = attributes.get(label)
    data["size_m2"] = parse_number(data["size_m2"])
    #Bedrooms, falling back to the category slug ("2-1" means a 2+1) when the seller didn't fill it in
    data["rooms"] = parse_number(data["rooms"], whole=True) or parse_number(ad.get("nestedSubChildCategoryName"), whole=True)

    return data

#Helper function to turn the ad's "properties" list into a {label: text} dict, like MerrJep's tags
def read_properties(properties):
    attributes = {}
    for prop in properties:
        label = (prop.get("label") or "").strip()
        value = prop.get("value")
        if not label or value in (None, "", []):
            continue

        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        elif prop.get("type") == "bool":
            value = "Po" if str(value).lower() == "true" else "Jo"
        attributes[label] = str(value).strip()
    return attributes

#Helper function to turn the HTML description (<br>, <p>, &amp;...) into plain text with line breaks
def clean_description(text):
    if not text:
        return None
    text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

#Helper function to read the price, returns None for the "1" placeholder and anything that isn't a number
def parse_price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > NO_PRICE else None

#Helper function to pull the first number out of text like "147", "72.5" or "2-1", returns None if there is no number
#Gazeta Celesi's values are plain numbers (no "1.200" thousands dots like MerrJep), so this is simpler than MerrJep's parse_number
def parse_number(text, whole=False):
    if text is None:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(text))
    if not match:
        return None
    value = float(match.group().replace(",", "."))
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

#Helper function to turn the site's UTC timestamp ("2026-11-04T12:51:50.307Z") into Tirana date and time as text ("04/11/2026", "13:51")
#The date is written the way the listing page shows it ("Publikuar më 04/11/2026"), the page shows no time but we keep it anyway
#Saved as plain text like MerrJep's date, the UI's "newest" sort uses first_seen so this format doesn't affect ordering
#Albania follows EU summer time: UTC+2 from the last Sunday of March to the last Sunday of October (01:00 UTC), UTC+1 otherwise
#Done by hand because Windows Python has no time zone data without the extra tzdata package
def to_tirana_time(timestamp):
    if not timestamp:
        return None, None
    try:
        utc = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return timestamp, None

    def last_sunday(month):
        day = datetime(utc.year, month, 31, 1, tzinfo=timezone.utc)
        return day - timedelta(days=(day.weekday() + 1) % 7)

    offset = 2 if last_sunday(3) <= utc < last_sunday(10) else 1
    local = utc + timedelta(hours=offset)
    return local.strftime("%d/%m/%Y"), local.strftime("%H:%M")

#Run this file on its own for a quick test: python -m GazetaCelesi.PlaywrightScraper
if __name__ == "__main__":
    for listing in scrape_gazetacelesi():
        print(json.dumps(listing, indent=4, ensure_ascii=False))

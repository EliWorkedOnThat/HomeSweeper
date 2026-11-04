#Imports
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from rich import print
import json
import re
from Scraper.config import Demo_Environment, Rest_Time, Headless, Listings_Per_Turn

#Global Variables
BASE_URL = "https://www.merrjep.al/njoftime/imobiliare-vendbanime/ne-shitje-kerkoj-te-blej-me-qera-kerkohet-me-qera-nderrohet"
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

#Function to create a browser instance and run one turn: scrape up to turn_limit() NEW listings, then hand them back so the engine can save them
#known_upids are the listings already in the database, they are skipped before clicking into them
#skip_agencies=True ignores listings posted by agencies (the "Jo Listime Agjensie" preference in the UI)
#should_stop is checked between listings, when it returns True (the UI's pause button) the turn ends early with what it has so far
def create_browser_instance(skip_agencies=False, known_upids=(), should_stop=None):
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
                page.goto(url, wait_until="domcontentloaded", timeout=30000) #Always wait for something to load to avoid timing errors
                page.wait_for_timeout(5000)
                print(f"[cyan]{page.title()}[/cyan]")
                try:
                    all_seen = process_listing_containers(page, skip_agencies)
                except PlaywrightTimeoutError:
                    print(f"[yellow]No listings found on {url}, assuming last page reached.[/yellow]")
                    clamp_resume_page(page_number - 1) #Old listings expire, so keep RESUME_PAGE inside the real page count
                    break
                print(f"[green]Page {page_number} processed. New this turn: {len(SCRAPED_LISTINGS)}/{turn_limit()}[/green]")
                page_number = next_page(page_number, all_seen)
        finally:
            browser.close()
            print(f"[green]Turn done: {len(SCRAPED_LISTINGS)} new listings, next turn continues older listings from page {RESUME_PAGE}[/green]")

    #Hand a copy of the scraped listings back to whoever called this function (Engine.py)
    return list(SCRAPED_LISTINGS)

#Helper function to open the browser, with no window when Headless is True (see config.py)
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

#Helper function to go back to previous page if needed
def go_back(page):
    page.go_back(wait_until="domcontentloaded")
    page.locator("div.row.row-listing[data-product-id]").first.wait_for(state="visible", timeout=15000)

#Function to find each listing container
def find_listing_containers(page):
    listings = page.locator("div.row.row-listing[data-product-id]")
    listings.first.wait_for(state = "visible", timeout=15000)  # Wait for the first listing to be visible
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

        print(f"[cyan]Processing {i + 1}/{len(new_ids)}: {upid}[/cyan]")

        try:
            listing = page.locator(
                f'div.row.row-listing[data-product-id="{upid}"]'
            )

            #Agency check happens on the results card, before clicking, so a skipped listing costs no page load or rest
            if skip_agencies and listing.locator(AGENCY_SELECTOR).count():
                print(f"[yellow]Skipping agency listing: {upid}[/yellow]")
                PROCESSED_UPIDS.add(upid)
                AGENCY_UPIDS.add(upid)
                continue

            listing.locator("h2 a.Link_vis").first.click()
            page.wait_for_url("**/njoftim/**", timeout=15000)

            data = pull_data_from_listing(page, upid)
            print(f"\n[bold green]PROPERTY DATA — UPID: {upid}[/bold green]")
            print(json.dumps(data, indent=4, ensure_ascii=False))

            SCRAPED_LISTINGS.append(data)
            PROCESSED_UPIDS.add(upid)

            print(f"[green]Scraped: {data['title']}[/green]")
            print(f"[bright_green]Price: {data['price']} {data['currency']}[/bright_green]")
            print(f"[bright_green]Images: {len(data['images'])}[/bright_green]")
            print(f"[bright_green]Phone: {data['phone'] or 'Not available'}[/bright_green]")

        except Exception as error:
            print(f"[red]Failed to scrape {upid}: {error}[/red]")
            PROCESSED_UPIDS.add(upid)
            FAILED_UPIDS.add(upid) #Don't reopen it every turn, a broken listing would otherwise stop pages from ever counting as "seen"

        finally:
            if page.url != results_url:
                go_back(page)

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

    #Skip this listing if no JSON-LD product data was found
    if not product:
        raise ValueError("No JSON found, skipping to next property")

    #Verify the unique property ID
    upid = str(product.get("productID") or expected_upid)

    if upid != expected_upid:
        raise ValueError(f"UPID mismatch: {expected_upid} != {upid}")

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
    create_browser_instance()
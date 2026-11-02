#Imports
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from rich import print
import json
from Scraper.config import Demo_Environment

#Global Variables
BASE_URL = "https://www.merrjep.al/njoftime/imobiliare-vendbanime/ne-shitje-kerkoj-te-blej-me-qera-kerkohet-me-qera-nderrohet"
PAGE_COUNT = 1
DEMO_LISTING_LIMIT = 5 #How many listings to scrape when Demo_Environment is True
URL_QUEUE = []
PROCESSED_UPIDS = set()
SCRAPED_LISTINGS = []

#Function to create a browser instance
def create_browser_instance():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        try:
            #Demo stops after DEMO_LISTING_LIMIT listings, production keeps going until we run out of pages
            while not demo_limit_reached():
                url = get_next_page_url(BASE_URL)
                print(f"[cyan]Navigating to: {url}[/cyan]")
                page.goto(url, wait_until="domcontentloaded", timeout=30000) #Always wait for something to load to avoid timing errors
                page.wait_for_timeout(5000)
                print(f"[cyan]{page.title()}[/cyan]")
                try:
                    process_listing_containers(page)
                except PlaywrightTimeoutError:
                    print(f"[yellow]No listings found on {url}, assuming last page reached.[/yellow]")
                    break
                remove_processed_url()
                print(f"[green]Page processed. Queue: {URL_QUEUE}[/green]")
        finally:
            browser.close()
            print(f"[green]Total unique properties scraped: {len(SCRAPED_LISTINGS)}[/green]")

    #Hand the scraped listings back to whoever called this function (Engine.py)
    return SCRAPED_LISTINGS

#Helper function to check if we hit the scrape limit while in demo mode
def demo_limit_reached():
    return Demo_Environment and len(SCRAPED_LISTINGS) >= DEMO_LISTING_LIMIT

#Helper function to craft url for next pages to fix pagination issues
def get_next_page_url(BASE_URL):
    global PAGE_COUNT

    if not URL_QUEUE:
        if PAGE_COUNT == 1:
            url = BASE_URL
        else:
            url = f"{BASE_URL}?Page={PAGE_COUNT}"
        URL_QUEUE.append(url)
        PAGE_COUNT += 1

    return URL_QUEUE[0]

#Helper function to remove a processed URL from memory
def remove_processed_url():
    if URL_QUEUE:
        return URL_QUEUE.pop(0)

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

#Function to process each listing container
def process_listing_containers(page):
    listings = find_listing_containers(page)
    listing_ids = list(dict.fromkeys(
        listings.nth(i).get_attribute("data-product-id")
        for i in range(listings.count())
    ))
    results_url = page.url

    for i, upid in enumerate(listing_ids):
        if demo_limit_reached():
            print(f"[yellow]Demo limit of {DEMO_LISTING_LIMIT} listings reached.[/yellow]")
            break

        if not upid or upid in PROCESSED_UPIDS:
            print(f"[cyan]Skipping duplicate: {upid}[/cyan]")
            continue

        print(f"[cyan]Processing {i + 1}/{len(listing_ids)}: {upid}[/cyan]")

        try:
            listing = page.locator(
                f'div.row.row-listing[data-product-id="{upid}"]'
            )

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

        finally:
            if page.url != results_url:
                go_back(page)

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

    return data

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
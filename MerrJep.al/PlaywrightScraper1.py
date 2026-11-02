#Imports
from playwright.sync_api import sync_playwright
from rich import print
import json

#Global Variables
BASE_URL = "https://www.merrjep.al/njoftime/imobiliare-vendbanime/ne-shitje-kerkoj-te-blej-me-qera-kerkohet-me-qera-nderrohet"
PAGE_COUNT = 1
SCRAPE_FINISH = 1 #This will dictate how many pages we want to scrape for now its a constant since its in testing
URL_QUEUE = []
PROCESSED_UPIDS = set()
SCRAPED_LISTINGS = []

#Function to create a browser instance
def create_browser_instance():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        try:
            for _ in range(SCRAPE_FINISH):
                url = get_next_page_url(BASE_URL)
                print(f"[cyan]Navigating to: {url}[/cyan]")
                page.goto(url, wait_until="domcontentloaded", timeout=30000) #Always wait for something to load to avoid timing errors
                page.wait_for_timeout(5000)
                print(f"[cyan]{page.title()}[/cyan]")
                process_listing_containers(page)
                remove_processed_url()
                print(f"[green]Page processed. Queue: {URL_QUEUE}[/green]")
        finally:
            browser.close()
            print(f"[green]Total unique properties scraped: {len(SCRAPED_LISTINGS)}[/green]")

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

    for i, upid in enumerate(listing_ids[:5]):
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
        "published_date": date.first.inner_text().strip() if date.count() else None,
        "published_time": time.first.inner_text().strip() if time.count() else None
    }

    return data

#Call each function as needed this will act as the orchestrator
if __name__ == "__main__":
    create_browser_instance()
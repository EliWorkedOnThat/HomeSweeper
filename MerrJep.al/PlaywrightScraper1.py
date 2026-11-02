#Imports
from playwright.sync_api import sync_playwright
from rich import print

#Global Variables
BASE_URL = "https://www.merrjep.al/njoftime/imobiliare-vendbanime/ne-shitje-kerkoj-te-blej-me-qera-kerkohet-me-qera-nderrohet"
PAGE_COUNT = 1
SCRAPE_FINISH = 10 #This will dictate how many pages we want to scrape for now its a constant since its in testing
URL_QUEUE = []

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
                print(page.title())
                find_listing_containers(page)
                remove_processed_url()
                print(f"[green]Page processed successfully. Queue: {URL_QUEUE}[/green]")
        finally:
            browser.close()

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
    page.go_back()

#Function to find each listing container
def find_listing_containers(page):
    listings = page.locator("div.row.row-listing[data-product-id]")
    listings.first.wait_for(state = "visible", timeout=15000)  # Wait for the first listing to be visible
    count = listings.count()
    print(f"Found {count} listings on the page.")
    return listings

#Function to open a listing container and extract data
def pull_data_from_listing():
    pass

#Call each function as needed this will act as the orchestrator
if __name__ == "__main__":
    create_browser_instance()

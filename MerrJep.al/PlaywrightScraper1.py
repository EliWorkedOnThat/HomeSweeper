#Imports
from playwright.sync_api import sync_playwright
from rich import print

#Function to create a browser instance
def create_browser_instance():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto("https://www.merrjep.al/njoftime/imobiliare-vendbanime/ne-shitje-kerkoj-te-blej-me-qera-kerkohet-me-qera-nderrohet", wait_until="domcontentloaded") #Always wait for something to load to avoid timing errors
        print(page.title())
        page.wait_for_timeout(5000)  #Adjust as needed for sleep time
        browser.close()

#Call each function as needed this will act as the orchestrator
if __name__ == "__main__":
    create_browser_instance()
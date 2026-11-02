#This file will act as the orchestrator for the scraper and will call the necessary functions from the other files to perform the scraping tasks. It will also handle any errors that may occur during the scraping process and log them for later analysis.
from MerrJep_al.PlaywrightScraper1 import create_browser_instance

def main():
    listings = create_browser_instance()
    print(f"Engine received {len(listings)} properties!")

if __name__ == "__main__":
    main()
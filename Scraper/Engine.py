#This file will act as the orchestrator for the scraper and will call the necessary functions from the other files to perform the scraping tasks. It will also handle any errors that may occur during the scraping process and log them for later analysis.
from rich import print
from MerrJep_al.PlaywrightScraper1 import create_browser_instance
from Scraper.Output import save_listings as export_to_excel
from Scraper.DB import connect_to_db, create_tables, save_listing

#This will just call all the scripts at once its really not that difficult atm
def main():
    connection = connect_to_db()
    if connection is None:
        print("[red]Engine stopped: could not open the database.[/red]")
        return

    try:
        create_tables(connection)

        listings = create_browser_instance()
        print(f"Engine received {len(listings)} properties!")

        #Save each listing to the database (the main store)
        saved = sum(save_listing(connection, listing, site="merrjep") for listing in listings)
        print(f"[green]Database: {saved} saved, {len(listings) - saved} failed[/green]")

        #Excel is just a report for now, if it fails the data is already safe in the database
        export_to_excel(listings, site="merrjep")
    finally:
        connection.close()

if __name__ == "__main__":
    main()

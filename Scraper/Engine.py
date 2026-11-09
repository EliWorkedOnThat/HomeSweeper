#This file will act as the orchestrator for the scraper and will call the necessary functions from the other files to perform the scraping tasks. It will also handle any errors that may occur during the scraping process and log them for later analysis.
from rich import print
from MerrJep_al.PlaywrightScraper1 import create_browser_instance as scrape_merrjep
from GazetaCelesi.PlaywrightScraper import scrape_gazetacelesi
from Duashpi_al.PlaywrightScraper2 import scrape_duashpi_al
from Scraper.Console import utf8_output
from Scraper.Output import save_listings as export_to_excel
from Scraper.DB import connect_to_db, create_tables, save_listing, get_known_upids

#Every site we scrape: (site key saved in the database, function that returns the list of listing dicts)
#They run one after another, never at the same time (the server's SCRAPE_LOCK covers the whole run)
SCRAPERS = [
    ("merrjep", scrape_merrjep),
    ("gazetacelesi", scrape_gazetacelesi),
    ("duashpi", scrape_duashpi_al),
]

#Runs one turn: each site scrapes up to Listings_Per_Turn NEW listings (config.py), which are saved before the next site starts
#Newer posts are picked up first every turn, older ones are filled in a bit more each turn, auto mode just repeats turns
#Returns a small summary dict so the web UI can show what happened (None if the database couldn't be opened)
#skip_agencies=True ignores agency listings (set from the UI preference, the terminal run keeps everything)
#should_stop is a function the scrapers check between listings (the UI's pause button), what was found before it is still saved
def main(skip_agencies=False, should_stop=None):
    connection = connect_to_db()
    if connection is None:
        print("[red]Engine stopped: could not open the database.[/red]")
        return None

    stopped = should_stop or (lambda: False)
    summary = {"found": 0, "saved": 0, "failed": 0, "sites": {}, "paused": False}
    try:
        create_tables(connection)

        for site, scrape in SCRAPERS:
            if stopped():
                print("[yellow]Paused, skipping the remaining sites this turn.[/yellow]")
                summary["paused"] = True
                break
            result = run_site(connection, site, scrape, skip_agencies, stopped)
            summary["sites"][site] = result
            for key in ("found", "saved", "failed"):
                summary[key] += result[key]

        summary["paused"] = summary["paused"] or stopped()
        return summary
    finally:
        connection.close()

#Function to run one site's scraper and save what it found, a crash on one site doesn't stop the others
def run_site(connection, site, scrape, skip_agencies, should_stop):
    known = get_known_upids(connection, site) #Listings already saved are skipped by the scraper before it opens them
    print(f"[bold cyan]Scraping {site} ({len(known)} listings already saved)...[/bold cyan]")
    try:
        listings = scrape(skip_agencies=skip_agencies, known_upids=known, should_stop=should_stop)
    except Exception as error:
        print(f"[red]{site} scraper crashed: {error}[/red]")
        return {"found": 0, "saved": 0, "failed": 0, "error": str(error)}
    print(f"Engine received {len(listings)} properties from {site}!")

    #Save each listing to the database (the main store). One bad listing must never cost the rest of the turn.
    saved = 0
    for listing in listings:
        try:
            saved += bool(save_listing(connection, listing, site=site))
        except Exception as error:
            print(f"[red]Could not save a {site} listing ({listing.get('upid')}): {error!r}[/red]")
    print(f"[green]Database ({site}): {saved} saved, {len(listings) - saved} failed[/green]")

    #Excel is just a report for now, if it fails the data is already safe in the database, so it must never stop the turn
    try:
        export_to_excel(listings, site=site)
    except Exception as error:
        print(f"[red]Excel export for {site} failed, the listings are still saved in the database: {error}[/red]")
    return {"found": len(listings), "saved": saved, "failed": len(listings) - saved}

if __name__ == "__main__":
    utf8_output()
    main()

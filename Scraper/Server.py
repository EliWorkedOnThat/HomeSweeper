#This file is the local web server for the UI. It's a thin layer: it only exposes Engine and DB functions as URLs, no scraping logic lives here.
#Run from the project root with: python3 -m Scraper.Server   (Windows: py -3.14 -m Scraper.Server), then open http://127.0.0.1:8000
#Imports
import threading
from datetime import datetime, timezone
from pathlib import Path
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from rich import print
from Scraper.DB import connect_to_db, create_tables, get_listings
from Scraper.Engine import main as run_engine
from Scraper.Output import build_workbook

#Global Variables
HOST = "127.0.0.1" #Only this PC can reach the server, other devices on the network can't
PORT = 8000
UI_DIR = Path(__file__).resolve().parent.parent / "UI"

app = FastAPI(title="HomeSweeper")

#Only one scrape at a time, the lock is held for as long as the scrape thread runs
SCRAPE_LOCK = threading.Lock()
SCRAPE_STATUS = {"state": "idle", "started_at": None, "finished_at": None, "result": None, "error": None}

#Helper function for UTC timestamps (same as the database)
def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

#Helper function to open the database for one request and make sure the table exists
def open_db():
    connection = connect_to_db()
    if connection is None:
        raise HTTPException(status_code=500, detail="Could not open the database")
    create_tables(connection)
    return connection

#Function that runs the Engine in a background thread (Playwright's sync API can't run inside FastAPI's async code)
def run_scrape():
    try:
        result = run_engine()
        if result is None:
            raise RuntimeError("Could not open the database")
        SCRAPE_STATUS.update(state="done", result=result)
    except Exception as error:
        print(f"[red]Scrape failed: {error}[/red]")
        SCRAPE_STATUS.update(state="error", error=str(error))
    finally:
        SCRAPE_STATUS["finished_at"] = now()
        SCRAPE_LOCK.release()

#Endpoint: every stored listing (optionally ?site=merrjep)
@app.get("/api/listings")
def api_listings(site: str | None = None):
    connection = open_db()
    try:
        return get_listings(connection, site)
    finally:
        connection.close()

#Endpoint: start a scrape, returns straight away while the scrape runs in the background
@app.post("/api/scrape/start")
def api_scrape_start():
    if not SCRAPE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A scrape is already running")
    SCRAPE_STATUS.update(state="running", started_at=now(), finished_at=None, result=None, error=None)
    threading.Thread(target=run_scrape, daemon=True).start()
    return SCRAPE_STATUS

#Endpoint: what the current/last scrape is doing (the page polls this every few seconds)
@app.get("/api/scrape/status")
def api_scrape_status():
    return SCRAPE_STATUS

#Endpoint: download every stored listing as an Excel file, built fresh from the database
@app.get("/api/export")
def api_export():
    connection = open_db()
    try:
        listings = get_listings(connection)
    finally:
        connection.close()

    filename = f"homesweeper_listings_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return Response(
        content=build_workbook(listings),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

#Serve the UI folder (index.html, style.css, app.js) at the root, must come after the /api routes
app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")

if __name__ == "__main__":
    print(f"[green]HomeSweeper running at http://{HOST}:{PORT} — press Ctrl+C to stop it.[/green]")
    uvicorn.run(app, host=HOST, port=PORT)

#This file is the local web server for the UI. It's a thin layer: it only exposes Engine and DB functions as URLs, no scraping logic lives here.
#Run from the project root with: python3 -m Scraper.Server   (Windows: py -3.14 -m Scraper.Server), then open http://127.0.0.1:8000
#Imports
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from rich import print
from Scraper.DB import connect_to_db, create_tables, get_listings
from Scraper.Engine import main as run_engine
from Scraper.Output import build_workbook

#Global Variables
HOST = "127.0.0.1" #Only this PC can reach the server, other devices on the network can't
PORT = 8000
UI_DIR = Path(__file__).resolve().parent.parent / "UI"
AUTO_INTERVALS = (1, 5, 10, 15, 30, 60) #Minutes the user can pick for automatic scraping (the UI dropdown shows these)
BUSY_RETRY_SECONDS = 5 #If an automatic run is due while a scrape is still going, check again after this long

#Only one scrape at a time, the lock is held for as long as the scrape thread runs (see "How the scrape lock works" in NOTES.md)
SCRAPE_LOCK = threading.Lock()
#Guards SCRAPE_STATUS and AUTO, which the request threads, the scrape thread and the auto thread all touch
STATE_LOCK = threading.RLock()
#Wakes the auto thread early when something changes (auto turned on/off, a scrape finished)
AUTO_WAKE = threading.Event()

SCRAPE_STATUS = {"state": "idle", "source": None, "started_at": None, "finished_at": None, "result": None, "error": None}
AUTO = {"enabled": False, "interval_minutes": None, "next_run": None, "last_finished": None} #next_run/last_finished are UTC datetimes

#Helper function for the current UTC time
def utc_now():
    return datetime.now(timezone.utc)

#Helper function to turn a datetime into a UTC timestamp string (same format as the database)
def stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else None

#Helper function to open the database for one request and make sure the table exists
def open_db():
    connection = connect_to_db()
    if connection is None:
        raise HTTPException(status_code=500, detail="Could not open the database")
    create_tables(connection)
    return connection

#Helper function for what /api/scrape/status sends to the page
def status_payload():
    with STATE_LOCK:
        return {
            **SCRAPE_STATUS,
            "auto": {
                "enabled": AUTO["enabled"],
                "interval_minutes": AUTO["interval_minutes"],
                "next_run_at": stamp(AUTO["next_run"]),
                "intervals": AUTO_INTERVALS,
            },
        }

#Function to start a scrape in the background if none is running, returns False if one already is
#source is "manual" (the button) or "auto" (the timer), both go through the same lock
def start_scrape(source):
    if not SCRAPE_LOCK.acquire(blocking=False):
        return False
    with STATE_LOCK:
        SCRAPE_STATUS.update(state="running", source=source, started_at=stamp(utc_now()), finished_at=None, result=None, error=None)
    threading.Thread(target=run_scrape, daemon=True, name=f"scrape-{source}").start()
    return True

#Function that runs the Engine in a background thread (Playwright's sync API can't run inside FastAPI's async code)
def run_scrape():
    outcome = {"state": "error", "error": "The scrape stopped unexpectedly"}
    try:
        result = run_engine()
        if result is None:
            raise RuntimeError("Could not open the database")
        outcome = {"state": "done", "result": result}
    except Exception as error:
        print(f"[red]Scrape failed: {error}[/red]")
        outcome = {"state": "error", "error": str(error)}
    finally:
        finished = utc_now()
        with STATE_LOCK:
            SCRAPE_STATUS.update(outcome, finished_at=stamp(finished))
            AUTO["last_finished"] = finished
            #The wait is counted from when a run finishes, so a slow scrape never overlaps the next one
            if AUTO["enabled"]:
                AUTO["next_run"] = finished + timedelta(minutes=AUTO["interval_minutes"])
        SCRAPE_LOCK.release()
        AUTO_WAKE.set()

#Function the auto thread runs for the whole life of the server: sleeps until the next run is due, then starts it
def auto_loop():
    while True:
        with STATE_LOCK:
            wait = None #None means sleep until something wakes us
            if AUTO["enabled"] and AUTO["next_run"] is not None:
                remaining = (AUTO["next_run"] - utc_now()).total_seconds()
                if remaining > 0:
                    wait = remaining
                elif start_scrape("auto"):
                    AUTO["next_run"] = None #run_scrape sets the next one when this run finishes
                    print(f"[cyan]Automatic scrape started (every {AUTO['interval_minutes']} min)[/cyan]")
                else:
                    wait = BUSY_RETRY_SECONDS #A manual scrape is running, skip this round and check again shortly
        AUTO_WAKE.wait(wait)
        AUTO_WAKE.clear()

#Start the auto thread together with the server
@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=auto_loop, daemon=True, name="auto-scrape").start()
    yield

app = FastAPI(title="HomeSweeper", lifespan=lifespan)

class AutoRequest(BaseModel):
    interval_minutes: int

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
    if not start_scrape("manual"):
        raise HTTPException(status_code=409, detail="A scrape is already running")
    return status_payload()

#Endpoint: what the current/last scrape and auto mode are doing (the page polls this every few seconds)
@app.get("/api/scrape/status")
def api_scrape_status():
    return status_payload()

#Endpoint: turn automatic scraping on, or change its interval while it's on
@app.post("/api/auto/start")
def api_auto_start(body: AutoRequest):
    if body.interval_minutes not in AUTO_INTERVALS:
        raise HTTPException(status_code=400, detail=f"interval_minutes must be one of {AUTO_INTERVALS}")

    with STATE_LOCK:
        was_enabled = AUTO["enabled"]
        AUTO.update(enabled=True, interval_minutes=body.interval_minutes)
        if SCRAPE_STATUS["state"] == "running":
            AUTO["next_run"] = None #The running scrape schedules the next one when it finishes
        elif was_enabled and AUTO["last_finished"]:
            AUTO["next_run"] = AUTO["last_finished"] + timedelta(minutes=body.interval_minutes) #New interval, same last run
        else:
            AUTO["next_run"] = utc_now() #Just turned on: scrape straight away
    print(f"[cyan]Automatic scraping on: every {body.interval_minutes} min[/cyan]")
    AUTO_WAKE.set()
    return status_payload()

#Endpoint: turn automatic scraping off (a scrape that is already running is allowed to finish)
@app.post("/api/auto/stop")
def api_auto_stop():
    with STATE_LOCK:
        AUTO.update(enabled=False, next_run=None)
    print("[cyan]Automatic scraping off[/cyan]")
    AUTO_WAKE.set()
    return status_payload()

#Endpoint: download every stored listing as an Excel file, built fresh from the database
@app.get("/api/export")
def api_export():
    connection = open_db()
    try:
        listings = get_listings(connection)
    finally:
        connection.close()

    filename = f"njoftime_homesweeper_{datetime.now().strftime('%Y-%m-%d')}.xlsx" #Staff see this name, so it's Albanian
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

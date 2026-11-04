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
AUTO_INTERVALS = (1, 5, 10, 15, 30, 60) #Minutes of break between turns the user can pick (the UI dropdown shows these)
BUSY_RETRY_SECONDS = 5 #If an automatic run is due while a scrape is still going, check again after this long

#Only one scrape at a time, the lock is held for as long as the scrape thread runs (see "How the scrape lock works" in NOTES.md)
SCRAPE_LOCK = threading.Lock()
#Guards SCRAPE_STATUS, AUTO and PREFERENCES, which the request threads, the scrape thread and the auto thread all touch
STATE_LOCK = threading.RLock()
#Wakes the auto thread early when something changes (auto turned on/off, a scrape finished)
AUTO_WAKE = threading.Event()
#Set by the pause button: the running turn ends early (between listings) and saves what it found. Cleared by play.
PAUSE_EVENT = threading.Event()

SCRAPE_STATUS = {"state": "idle", "started_at": None, "finished_at": None, "result": None, "error": None}
#Continuous mode (the UI's play/pause button): one turn after another with a break of interval_minutes in between, until paused
#next_run/last_finished are UTC datetimes, turns/new_listings count what this session has done since play was pressed
AUTO = {"enabled": False, "interval_minutes": None, "next_run": None, "last_finished": None, "turns": 0, "new_listings": 0}
#Scrape preferences picked in the UI, read when each turn starts. Lives in memory like AUTO.
PREFERENCES = {"skip_agencies": False} #True = "Jo Listime Agjensie", agency listings are ignored

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
            "pausing": SCRAPE_STATUS["state"] == "running" and PAUSE_EVENT.is_set(), #Paused, the turn is wrapping up
            "auto": {
                "enabled": AUTO["enabled"],
                "interval_minutes": AUTO["interval_minutes"],
                "next_run_at": stamp(AUTO["next_run"]),
                "intervals": AUTO_INTERVALS,
                "turns": AUTO["turns"],
                "new_listings": AUTO["new_listings"],
            },
            "preferences": dict(PREFERENCES),
        }

#Function to start one turn in the background if none is running, returns False if one already is
#The only way a scrape starts (auto_loop() calls it), so the lock always covers it
def start_scrape():
    if not SCRAPE_LOCK.acquire(blocking=False):
        return False
    PAUSE_EVENT.clear() #A new turn starts unpaused
    with STATE_LOCK:
        SCRAPE_STATUS.update(state="running", started_at=stamp(utc_now()), finished_at=None, result=None, error=None)
        preferences = dict(PREFERENCES) #Snapshot, so changing the preference mid-scrape only affects the next turn
    threading.Thread(target=run_scrape, args=(preferences,), daemon=True, name="scrape-turn").start()
    return True

#Function that runs the Engine in a background thread (Playwright's sync API can't run inside FastAPI's async code)
def run_scrape(preferences):
    outcome = {"state": "error", "error": "The scrape stopped unexpectedly"}
    try:
        result = run_engine(skip_agencies=preferences["skip_agencies"], should_stop=PAUSE_EVENT.is_set)
        if result is None:
            raise RuntimeError("Could not open the database")
        outcome = {"state": "done", "result": result}
        with STATE_LOCK:
            AUTO["turns"] += 1
            AUTO["new_listings"] += result["saved"]
    except Exception as error:
        print(f"[red]Scrape failed: {error}[/red]")
        outcome = {"state": "error", "error": str(error)}
    finally:
        finished = utc_now()
        with STATE_LOCK:
            SCRAPE_STATUS.update(outcome, finished_at=stamp(finished))
            AUTO["last_finished"] = finished
            #The wait is counted from when a run finishes, so a slow scrape never overlaps the next one
            #A turn cut short by pause, then resumed with play before it ended, goes again straight away
            if AUTO["enabled"]:
                cut_short = (outcome.get("result") or {}).get("paused")
                AUTO["next_run"] = finished if cut_short else finished + timedelta(minutes=AUTO["interval_minutes"])
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
                elif start_scrape():
                    AUTO["next_run"] = None #run_scrape sets the next one when this run finishes
                    print(f"[cyan]Turn {AUTO['turns'] + 1} started ({AUTO['interval_minutes']} min break between turns)[/cyan]")
                else:
                    wait = BUSY_RETRY_SECONDS #Safety net: the last turn hasn't released the lock yet, check again shortly
        AUTO_WAKE.wait(wait)
        AUTO_WAKE.clear()

#Start the auto thread together with the server
@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=auto_loop, daemon=True, name="auto-scrape").start()
    yield

app = FastAPI(title="HomeSweeper", lifespan=lifespan)

#Tell the browser to re-check the UI files on every load, otherwise it can keep an old style.css/app.js after an update
#"no-cache" still allows the cache, it just asks the server first (unchanged files come back as a tiny 304)
@app.middleware("http")
async def no_stale_ui(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response

class AutoRequest(BaseModel):
    interval_minutes: int

class PreferencesRequest(BaseModel):
    skip_agencies: bool

#Endpoint: every stored listing (optionally ?site=merrjep)
@app.get("/api/listings")
def api_listings(site: str | None = None):
    connection = open_db()
    try:
        return get_listings(connection, site)
    finally:
        connection.close()

#Endpoint: what the current/last scrape and auto mode are doing (the page polls this every few seconds)
@app.get("/api/scrape/status")
def api_scrape_status():
    return status_payload()

#Endpoint: play, start continuous scraping (one turn after another), or change the break between turns while it's on
@app.post("/api/auto/start")
def api_auto_start(body: AutoRequest):
    if body.interval_minutes not in AUTO_INTERVALS:
        raise HTTPException(status_code=400, detail=f"interval_minutes must be one of {AUTO_INTERVALS}")

    with STATE_LOCK:
        was_enabled = AUTO["enabled"]
        AUTO.update(enabled=True, interval_minutes=body.interval_minutes)
        if not was_enabled:
            AUTO.update(turns=0, new_listings=0) #Count this session from zero
        PAUSE_EVENT.clear() #Play while a paused turn is still wrapping up: let it carry on instead
        if SCRAPE_STATUS["state"] == "running":
            AUTO["next_run"] = None #The running scrape schedules the next one when it finishes
        elif was_enabled and AUTO["last_finished"]:
            AUTO["next_run"] = AUTO["last_finished"] + timedelta(minutes=body.interval_minutes) #New interval, same last run
        else:
            AUTO["next_run"] = utc_now() #Just turned on: scrape straight away
    print(f"[cyan]Automatic scraping on: every {body.interval_minutes} min[/cyan]")
    AUTO_WAKE.set()
    return status_payload()

#Endpoint: pause, stop continuous scraping, a running turn ends early (between listings) and still saves what it found
@app.post("/api/auto/stop")
def api_auto_stop():
    with STATE_LOCK:
        AUTO.update(enabled=False, next_run=None)
        PAUSE_EVENT.set()
    print("[cyan]Scraping paused[/cyan]")
    AUTO_WAKE.set()
    return status_payload()

#Endpoint: change the scrape preferences, they apply from the next scrape (a running one keeps its settings)
@app.post("/api/preferences")
def api_preferences(body: PreferencesRequest):
    with STATE_LOCK:
        PREFERENCES.update(skip_agencies=body.skip_agencies)
    print(f"[cyan]Preferences: skip agency listings = {body.skip_agencies}[/cyan]")
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

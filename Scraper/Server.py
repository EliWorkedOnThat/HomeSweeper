#This file is the local web server for the UI. It's a thin layer: it only exposes Engine and DB functions as URLs, no scraping logic lives here.
#Run from the project root with: python3 -m Scraper.Server   (Windows: py -3.14 -m Scraper.Server), then open the address it prints
#(http://127.0.0.1:8000, or the next free port if another program has 8000). Only one can run: a second start is refused.
#Imports
import json
import os
import re
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, StrictInt
from rich import print
from starlette.middleware.trustedhost import TrustedHostMiddleware
from Scraper import AI, Instance
from Scraper.Console import utf8_output
from Scraper.DB import (DB_FILE, DBError, add_blocked_seller, ai_cost_today, connect_to_db, create_tables, delete_ai_search,
                        delete_blocked_seller, get_ai_search, get_ai_searches, get_blocked_sellers, get_known_places, get_listings,
                        listings_version, record_ai_usage, save_ai_search, set_favourite)
from Scraper.Derive import block_rule_of, blocked_rules, is_blocked, with_derived
from Scraper.Engine import main as run_engine
from Scraper.Output import build_workbook
from Scraper.Search import Filters, known_areas, match_reasons, near_misses, search
from Scraper.Version import get_version_and_updates

#Global Variables
HOST = "127.0.0.1" #Only this PC can reach the server, other devices on the network can't (the port is picked by Instance.py)
UI_DIR = Path(__file__).resolve().parent.parent / "UI"
AUTO_INTERVALS = (1, 5, 10, 15, 30, 60) #Minutes of break between turns the user can pick (the UI dropdown shows these)
BUSY_RETRY_SECONDS = 5 #If an automatic run is due while a scrape is still going, check again after this long
SETTINGS_FILE = DB_FILE.parent / "settings.json" #Settings the agent changes that must survive a restart (for now the daily start)
SCHEDULE_CHECK_SECONDS = 30 #The auto thread looks at the clock at least this often, a PC waking from sleep can make a long wait overshoot
TIME_OF_DAY = re.compile(r"([01]\d|2[0-3]):[0-5]\d") #"08:30", what <input type="time"> sends
LOCAL_HOSTS = ("127.0.0.1", "localhost") #The only Host headers accepted (blocks DNS rebinding, see the middleware below)
#For a demo through a tunnel only: the tunnel's public name, e.g. HOMESWEEPER_PUBLIC_HOST=name.ts.net. Never set in the shipped app.
PUBLIC_HOST = os.environ.get("HOMESWEEPER_PUBLIC_HOST", "").strip()
SHUTDOWN_WAIT_SECONDS = 30 #On quit, how long to wait for a running turn to stop and save what it has
AI_DAILY_LIMIT_USD = 2.00 #Spending cap for Pyet AI per day (UTC); about 100-200 questions. Protects against loops and abuse.
#Security headers for every response. The UI has no inline scripts or styles and loads photos from the sites' CDNs.
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: http: data:; "
                                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

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
#Daily start: at `time` (the PC's clock) continuous mode turns on by itself, as if play was pressed, and runs until paused. Saved in SETTINGS_FILE.
SCHEDULE = {"enabled": False, "time": "08:00", "interval_minutes": 5, "next_run": None}
#Scrape preferences picked in the UI, read when each turn starts. Lives in memory like AUTO.
PREFERENCES = {"skip_agencies": False} #True = "Jo Listime Agjensie", agency listings are ignored

#Helper function for the current UTC time
def utc_now():
    return datetime.now(timezone.utc)

#Helper function to turn a datetime into a UTC timestamp string (same format as the database)
def stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else None

#Helper function to open the database for one request (the tables are created once, when the server starts)
def open_db():
    connection = connect_to_db(quiet=True)
    if connection is None:
        raise HTTPException(status_code=503, detail="Databaza nuk u hap. Rinis HomeSweeper.")
    return connection

#The listings with their derived values, kept in memory and rebuilt only when the DB changed (a scrape saved something).
#Loading and deriving every listing on each request gets slow with thousands of listings; the dashboard, the AI search
#and the re-runs all share this copy. Callers must treat the list as read-only.
LISTINGS_CACHE = {"version": None, "listings": []}
LISTINGS_LOCK = threading.Lock()

def cached_listings(connection):
    version = listings_version(connection)
    with LISTINGS_LOCK:
        if LISTINGS_CACHE["version"] != version:
            LISTINGS_CACHE.update(version=version, listings=with_derived(get_listings(connection)))
        return LISTINGS_CACHE["listings"]

#Helper function: copy a favourite (the heart) into the cached listing. listings_version() can't see this change (it only
#notices new listings and saves), and rebuilding the whole cache for one true/false made the next load re-derive every listing.
#Call it after the change is committed: a rebuild that runs first already reads the new value, setting it again is harmless.
def set_cached_favourite(listing_id, favourite):
    with LISTINGS_LOCK:
        for listing in LISTINGS_CACHE["listings"]:
            if listing.get("id") == listing_id:
                listing["favourite"] = favourite
                break

#Helper function: the given listings without the ones from blocked sellers. Used by everything that shows listings.
def without_blocked(connection, listings):
    rules = blocked_rules(get_blocked_sellers(connection))
    return [listing for listing in listings if not is_blocked(listing, rules)] if any(rules) else listings

#Only one AI question at a time: a double click or a stuck page can't send (and pay for) the same question twice in parallel
AI_LOCK = threading.Lock()

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
            "schedule": {key: SCHEDULE[key] for key in ("enabled", "time", "interval_minutes")},
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
                cut_short = (outcome.get("result") or {}).get("paused") #type: ignore
                AUTO["next_run"] = finished if cut_short else finished + timedelta(minutes=AUTO["interval_minutes"])
        SCRAPE_LOCK.release()
        AUTO_WAKE.set()

#Function to turn continuous mode on, or change the break while it's on. The caller holds STATE_LOCK.
def begin_auto(interval_minutes):
    was_enabled = AUTO["enabled"]
    AUTO.update(enabled=True, interval_minutes=interval_minutes)
    if not was_enabled:
        AUTO.update(turns=0, new_listings=0) #Count this session from zero
    PAUSE_EVENT.clear() #Play while a paused turn is still wrapping up: let it carry on instead
    if SCRAPE_STATUS["state"] == "running":
        AUTO["next_run"] = None #The running scrape schedules the next one when it finishes
    elif was_enabled and AUTO["last_finished"]:
        AUTO["next_run"] = AUTO["last_finished"] + timedelta(minutes=interval_minutes) #New interval, same last run
    else:
        AUTO["next_run"] = utc_now() #Just turned on: scrape straight away

#Helper function for the next time the clock shows "HH:MM", as a UTC datetime
def next_schedule_run(time_of_day):
    hour, minute = map(int, time_of_day.split(":"))
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc) #Worked out from the local date, so a daylight-saving change keeps the same clock time

#Function that applies a daily start setting. The caller holds STATE_LOCK (or the server hasn't started its threads yet).
def apply_schedule(enabled, time_of_day, interval_minutes):
    SCHEDULE.update(enabled=enabled, time=time_of_day, interval_minutes=interval_minutes,
                    next_run=next_schedule_run(time_of_day) if enabled else None)

#Function to load the daily start saved in SETTINGS_FILE. A missing or damaged file just means no daily start.
def load_schedule():
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("schedule")
        if saved is None:
            return #Never set (the file may only hold the version)
        if TIME_OF_DAY.fullmatch(saved["time"]) and saved["interval_minutes"] in AUTO_INTERVALS:
            apply_schedule(bool(saved["enabled"]), saved["time"], saved["interval_minutes"])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"[yellow]Could not read {SETTINGS_FILE.name}, the daily start stays off: {error!r}[/yellow]")

#Function to write SETTINGS_FILE: keeps what's already saved, applies the changes and stamps the running version (as info, so
#the file shows which version last wrote it). Written to a temporary file first, so a crash can't leave half a settings file.
def save_settings(**changes):
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {} #Missing or damaged: start again (load_schedule() already ignored a damaged file)
    settings.update(changes, version=check_version())
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.replace(temporary, SETTINGS_FILE)

#Function to save the daily start
def save_schedule(enabled, time_of_day, interval_minutes):
    save_settings(schedule={"enabled": enabled, "time": time_of_day, "interval_minutes": interval_minutes})

#Function to get version of tool and check for updates later on
def check_version():
    version = get_version_and_updates()
    return version

#Function the auto thread runs for the whole life of the server: sleeps until the next run is due, then starts it
def auto_loop():
    while True:
        wait = None #None means sleep until something wakes us
        try:
            with STATE_LOCK:
                if SCHEDULE["next_run"] is not None and SCHEDULE["next_run"] <= utc_now():
                    SCHEDULE["next_run"] = next_schedule_run(SCHEDULE["time"])
                    if not AUTO["enabled"]: #Already running: leave its break alone
                        begin_auto(SCHEDULE["interval_minutes"])
                        print(f"[cyan]Daily start: automatic scraping on at {SCHEDULE['time']}[/cyan]")
                if AUTO["enabled"] and AUTO["next_run"] is not None:
                    remaining = (AUTO["next_run"] - utc_now()).total_seconds()
                    if remaining > 0:
                        wait = remaining
                    elif start_scrape():
                        AUTO["next_run"] = None #run_scrape sets the next one when this run finishes
                        print(f"[cyan]Turn {AUTO['turns'] + 1} started ({AUTO['interval_minutes']} min break between turns)[/cyan]")
                    else:
                        wait = BUSY_RETRY_SECONDS #Safety net: the last turn hasn't released the lock yet, check again shortly
        except Exception as error:
            #This thread runs for the server's whole life; if it died, auto mode would silently stop while the UI says it's on
            print(f"[red]Auto loop error, retrying: {error!r}[/red]")
            wait = BUSY_RETRY_SECONDS
        with STATE_LOCK:
            if SCHEDULE["next_run"] is not None:
                until_start = max(0, (SCHEDULE["next_run"] - utc_now()).total_seconds())
                wait = min(until_start, SCHEDULE_CHECK_SECONDS, SCHEDULE_CHECK_SECONDS if wait is None else wait)
        AUTO_WAKE.wait(wait)
        AUTO_WAKE.clear()

#Start the auto thread together with the server. On quit, pause a running turn and give it time to save what it found
#(the scrape thread is a daemon, so without this it would be killed mid-listing and its Chromium could be left behind).
@asynccontextmanager
async def lifespan(app):
    connection = connect_to_db()
    if connection is not None:
        create_tables(connection)
        connection.close()
    load_schedule()
    try:
        save_settings() #Stamp the running version, so after an update the file shows the new one
    except OSError as error:
        print(f"[yellow]Could not write the version to {SETTINGS_FILE.name}: {error!r}[/yellow]")
    print(f"[green]HomeSweeper version {check_version()}[/green]")
    threading.Thread(target=auto_loop, daemon=True, name="auto-scrape").start()
    yield
    with STATE_LOCK:
        AUTO.update(enabled=False, next_run=None)
    PAUSE_EVENT.set()
    if SCRAPE_LOCK.acquire(timeout=SHUTDOWN_WAIT_SECONDS):
        SCRAPE_LOCK.release()
    else:
        print("[yellow]The running turn didn't stop in time; quitting anyway.[/yellow]")

#The interactive API docs are handy in development but have no place in the shipped app
FROZEN = getattr(sys, "frozen", False)
app = FastAPI(title="HomeSweeper", lifespan=lifespan,
              docs_url=None if FROZEN else "/docs", redoc_url=None, openapi_url=None if FROZEN else "/openapi.json")

#Tell the browser to re-check the UI files on every load, otherwise it can keep an old style.css/app.js after an update
#"no-cache" still allows the cache, it just asks the server first (unchanged files come back as a tiny 304)
@app.middleware("http")
async def no_stale_ui(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response

#Cross-site requests: the server only listens on this PC, but any website open in the agent's browser can still send requests
#to 127.0.0.1. Every request that changes something (start/stop scraping, ask the AI, delete history) must come from our own page,
#which browsers prove with the Origin header. Reads are protected by the Host check below.
@app.middleware("http")
async def same_origin_only(request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if not origin or urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Kërkesa u bllokua: vjen nga një faqe tjetër."}, status_code=403)
    return await call_next(request)

#Windows network paths: StaticFiles joins the URL path onto UI_DIR and calls realpath() BEFORE checking it stays inside UI_DIR.
#"/%5c%5cevil.com%5cshare" decodes to \\evil.com\share, so realpath() would open an SMB connection to that server (leaking the
#agent's NTLM hash) and hang a worker thread for ~40 s. Any website can trigger it with an <img> pointing at 127.0.0.1, because a
#GET passes the Host check. No real URL of ours has a backslash, a colon (drive letters) or a NUL byte, so refuse them up front.
#scope["path"] is the percent-decoded path, the same string StaticFiles uses.
@app.middleware("http")
async def no_windows_paths(request, call_next):
    path = request.scope["path"]
    if "\\" in path or ":" in path or "\x00" in path:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)

#DNS rebinding: a website can point its own domain at 127.0.0.1 and read our API as "same origin". Its requests still carry
#its own domain in the Host header, so only accept ours. Added last, so it runs first.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[*LOCAL_HOSTS, *([PUBLIC_HOST] if PUBLIC_HOST else [])])
app.add_middleware(GZipMiddleware, minimum_size=2000) #The listings JSON shrinks about 8-10x

#A database that can't be read shows as an error, never as an empty dashboard
@app.exception_handler(DBError)
async def db_error(request: Request, error: DBError):
    return JSONResponse({"detail": "Databaza nuk u lexua. Provo përsëri pas pak."}, status_code=503)

#Request bodies use strict types: in pydantic's lax mode true would pass as the number 1 and "5" as 5
class AutoRequest(BaseModel):
    interval_minutes: StrictInt

class PreferencesRequest(BaseModel):
    skip_agencies: StrictBool

class ScheduleRequest(BaseModel):
    enabled: StrictBool
    time: str
    interval_minutes: StrictInt

class BlockRequest(BaseModel):
    listing_id: StrictInt

class FavouriteRequest(BaseModel):
    listing_id: Annotated[StrictInt, Field(ge=1, le=2**63 - 1)] #Same limits as RowId below, SQLite can't take a bigger number
    favourite: StrictBool #The value to set, not a toggle, so a double click or a retried request can't flip it back

#An id from the URL. SQLite ids fit in 64 bits; a bigger number raised OverflowError (a 500) instead of a clean 404/422.
RowId = Annotated[int, PathParam(ge=1, le=2**63 - 1)]

class AskRequest(BaseModel):
    question: str

class SearchRequest(BaseModel):
    filters: Filters

AI_QUESTION_MAX = 1000 #Characters; a question is a sentence or two, this only stops accidental pastes

#Endpoint: every stored listing (optionally ?site=merrjep), with the derived filter values (Derive.py) under "derived"
@app.get("/api/listings")
def api_listings(site: str | None = None):
    connection = open_db()
    try:
        listings = without_blocked(connection, cached_listings(connection))
        return [listing for listing in listings if listing["site"] == site] if site else listings
    finally:
        connection.close()

#Endpoint: only the favourite listings (the favourites page), minus blocked sellers, read straight from the DB.
#A handful of rows: no need for the listings cache, and the page doesn't download every listing to show a few.
@app.get("/api/favourites")
def api_favourites():
    connection = open_db()
    try:
        return without_blocked(connection, with_derived(get_listings(connection, favourites_only=True)))
    finally:
        connection.close()

#Helper function: run a search over every listing; returns the matching ids in result order,
#and why each one matched the soft wishes ({id: [Albanian reasons]}), which the result cards show
#Also returns the near misses (listings failing exactly one requirement) when the search found little:
#{"ids": [...], "reasons": {id: [...]}, "near": [{"id": 12, "missed": "Kati: 3"}, ...]}
def run_search(listings, filters):
    found = search(listings, filters)
    reasons = match_reasons(found, filters)
    near = near_misses(listings, filters, len(found))
    reasons.update({k: v for k, v in match_reasons([listing for listing, _ in near], filters).items() if k not in reasons})
    return {
        "ids": [listing["id"] for listing in found],
        "reasons": {str(k): v for k, v in reasons.items()},
        "near": [{"id": listing["id"], "missed": missed} for listing, missed in near],
    }

#Endpoint: is the AI ready (an API key is set)? The page shows a setup note when it isn't
@app.get("/api/ai/status")
def api_ai_status():
    return {"configured": AI.is_configured(), "model": AI.MODEL}

#Endpoint: "Pyet AI". Claude turns the question into filters, the search runs here, and the question is saved to the history
#A plain def, so FastAPI runs it in a worker thread and the slow API call doesn't block other requests
@app.post("/api/ai/ask")
def api_ai_ask(body: AskRequest):
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Shkruaj një pyetje.")
    if len(question) > AI_QUESTION_MAX:
        raise HTTPException(status_code=400, detail=f"Pyetja është shumë e gjatë (maksimumi {AI_QUESTION_MAX} shkronja).")

    if not AI_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Një pyetje tjetër po përpunohet. Prit sa të mbarojë.")
    connection = None
    try:
        connection = open_db()
        if ai_cost_today(connection) >= AI_DAILY_LIMIT_USD:
            raise HTTPException(status_code=429, detail=(f"U arrit kufiri ditor i shpenzimeve për AI ({AI_DAILY_LIMIT_USD:.2f} $). "
                                                         "Kërkimet e ruajtura te historia hapen ende falas."))
        listings = without_blocked(connection, cached_listings(connection))
        try:
            answer = AI.question_to_filters(question, get_known_places(connection), known_areas(listings))
        except AI.AIError as error:
            if error.usage: #Claude answered, so the call was paid even though it failed: it counts toward the cap
                record_ai_usage(connection, error.model, error.usage)
            raise HTTPException(status_code=error.status, detail=error.message_sq)
        record_ai_usage(connection, answer["model"], answer["usage"])
        results = run_search(listings, answer["filters"])
        filters_json = answer["filters"].model_dump_json()
        search_id = save_ai_search(connection, question, filters_json, answer["understood"], answer["not_supported"],
                                   len(results["ids"]), answer["model"], answer["usage"])
        return {"search": get_ai_search(connection, search_id) if search_id else None, **results}
    finally:
        if connection is not None:
            connection.close()
        AI_LOCK.release()

#Endpoint: run a set of filters without the AI (removing a filter chip, or re-opening a search from the history). Free.
@app.post("/api/ai/search")
def api_ai_search(body: SearchRequest):
    connection = open_db()
    try:
        return run_search(without_blocked(connection, cached_listings(connection)), body.filters)
    finally:
        connection.close()

#Endpoint: the saved AI questions, newest first
@app.get("/api/ai/history")
def api_ai_history():
    connection = open_db()
    try:
        return get_ai_searches(connection)
    finally:
        connection.close()

#Endpoint: one saved AI question, so a search has its own link (#/ai/12) that survives a page reload
@app.get("/api/ai/history/{search_id}")
def api_ai_history_one(search_id: RowId):
    connection = open_db()
    try:
        found = get_ai_search(connection, search_id)
        if not found:
            raise HTTPException(status_code=404, detail="Kjo pyetje nuk u gjet.")
        return found
    finally:
        connection.close()

#Endpoint: delete one saved AI question
@app.delete("/api/ai/history/{search_id}")
def api_ai_history_delete(search_id: RowId):
    connection = open_db()
    try:
        if not delete_ai_search(connection, search_id):
            raise HTTPException(status_code=404, detail="Kjo pyetje nuk u gjet.")
        return {"deleted": search_id}
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
        begin_auto(body.interval_minutes)
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

#Endpoint: turn the daily start on or off, or change its time. Saved at once, so it survives a restart.
@app.post("/api/schedule")
def api_schedule(body: ScheduleRequest):
    if not TIME_OF_DAY.fullmatch(body.time):
        raise HTTPException(status_code=400, detail="Ora e nisjes nuk është e saktë.")
    if body.interval_minutes not in AUTO_INTERVALS:
        raise HTTPException(status_code=400, detail=f"interval_minutes must be one of {AUTO_INTERVALS}")
    with STATE_LOCK:
        try:
            save_schedule(body.enabled, body.time, body.interval_minutes)
        except OSError as error:
            print(f"[red]Could not save the daily start: {error!r}[/red]")
            raise HTTPException(status_code=500, detail="Orari nuk u ruajt. Provo përsëri.")
        apply_schedule(body.enabled, body.time, body.interval_minutes)
    print(f"[cyan]Daily start: {'on at ' + body.time if body.enabled else 'off'}[/cyan]")
    AUTO_WAKE.set()
    return status_payload()

#Helper function: find one stored listing (with its derived values) or answer 404
def stored_listing(connection, listing_id):
    found = next((listing for listing in cached_listings(connection) if listing["id"] == listing_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="Ky njoftim nuk u gjet.")
    return found

#Endpoint: the blocked sellers and how many stored listings each one hides
@app.get("/api/blocked")
def api_blocked():
    connection = open_db()
    try:
        listings = cached_listings(connection)
        return [{"id": row["id"], "name": row["name"], "created_at": row["created_at"],
                 "hidden": sum(is_blocked(listing, blocked_rules([row])) for listing in listings)}
                for row in get_blocked_sellers(connection)]
    finally:
        connection.close()

#Endpoint: who blocking this listing's seller would block and how many listings that hides, for the question the agent confirms
@app.get("/api/blocked/preview/{listing_id}")
def api_blocked_preview(listing_id: RowId):
    connection = open_db()
    try:
        rule = block_rule_of(stored_listing(connection, listing_id))
        visible = without_blocked(connection, cached_listings(connection))
        return {"name": rule["name"], "hidden": sum(is_blocked(listing, blocked_rules([rule])) for listing in visible)}
    finally:
        connection.close()

#Endpoint: block the seller of a listing. Their listings disappear everywhere, including ones scraped later.
@app.post("/api/blocked")
def api_blocked_add(body: BlockRequest):
    connection = open_db()
    try:
        rule = block_rule_of(stored_listing(connection, body.listing_id))
        if not rule["match_name"] and not rule["phones"]:
            raise HTTPException(status_code=400, detail="Ky njoftim nuk ka as emër shitësi as telefon.")
        blocked_id = add_blocked_seller(connection, rule["name"], rule["match_name"], rule["phones"])
        if blocked_id is None:
            raise HTTPException(status_code=503, detail="Bllokimi nuk u ruajt. Provo përsëri.")
        return {"id": blocked_id, "name": rule["name"]}
    finally:
        connection.close()

#Endpoint: unblock a seller, their listings come back
@app.delete("/api/blocked/{blocked_id}")
def api_blocked_delete(blocked_id: RowId):
    connection = open_db()
    try:
        if not delete_blocked_seller(connection, blocked_id):
            raise HTTPException(status_code=404, detail="Ky shitës nuk është i bllokuar.")
        return {"deleted": blocked_id}
    finally:
        connection.close()

#Endpoint: mark a listing as a favourite or not (the heart on the card)
@app.post("/api/favourite")
def api_favourite(body: FavouriteRequest):
    connection = open_db()
    try:
        if not set_favourite(connection, body.listing_id, body.favourite):
            raise HTTPException(status_code=404, detail="Ky njoftim nuk u gjet.")
    finally:
        connection.close()
    set_cached_favourite(body.listing_id, body.favourite) #The cache can't see this change by itself
    return {"id": body.listing_id, "favourite": body.favourite}

#Endpoint: download every stored listing as an Excel file, built fresh from the database
@app.get("/api/export")
def api_export():
    connection = open_db()
    try:
        listings = without_blocked(connection, get_listings(connection))
    finally:
        connection.close()

    filename = f"njoftime_homesweeper_{datetime.now().strftime('%Y-%m-%d')}.xlsx" #Staff see this name, so it's Albanian
    return Response(
        content=build_workbook(listings),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

#Endpoint: the favourites page at the short address /favourites (the mount below also serves it as /favourites.html)
@app.get("/favourites")
def serve_favourite_page():
    return FileResponse(UI_DIR / "favourites.html")

#Serve the UI folder (index.html, style.css, app.js) at the root, must come after the /api routes
app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")

#Start the server: only one per PC, on the first free port from 8000. A second start is refused (it doesn't touch the running one).
if __name__ == "__main__":
    utf8_output()
    lock = Instance.acquire_lock()
    if lock is None:
        Instance.refuse_second_run()
    Instance.exit_cleanly_on_signals()
    sock = None
    try:
        try:
            sock = Instance.bind_free_port(HOST)
        except OSError as error:
            print(f"[red]Could not start HomeSweeper: {error}[/red]")
            sys.exit(1)
        port = sock.getsockname()[1]
        url = f"http://{HOST}:{port}"
        Instance.save_address(url)
        if port == Instance.START_PORT + 1:
            print(f"[yellow]Port {Instance.START_PORT} is used by another program, using {port} instead.[/yellow]")
        elif port != Instance.START_PORT:
            print(f"[yellow]Ports {Instance.START_PORT}-{port - 1} are used by other programs, using {port} instead.[/yellow]")
        print(f"[green]HomeSweeper running at {url} — press Ctrl+C to stop it.[/green]")
        uvicorn.Server(uvicorn.Config(app, host=HOST, port=port)).run(sockets=[sock])
    finally:
        #Free the port first, then the lock: a new start must never get the lock while our port is still held
        if sock is not None:
            sock.close()
        Instance.release_lock(lock)

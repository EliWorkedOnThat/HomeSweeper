#This file will act as the database manager for the scraper but also house the preset database functions.
#Imports
import sqlite3 as sqlite
import json
from pathlib import Path
from rich import print
from rich.markup import escape
from Scraper.config import Database_Name

#Global Variables
DB_FILE = Path(__file__).resolve().parent.parent / "Data" / Database_Name

#Raised when the database can't be read, so a broken or locked DB shows as an error instead of as "no listings"
class DBError(Exception):
    pass

#Function to connect to the database. quiet=True skips the log line (the server opens a connection for every request).
def connect_to_db(db_file=DB_FILE, quiet=False):
    try:
        db_file.parent.mkdir(parents=True, exist_ok=True) #Create the Data folder if it doesn't exist yet
        connection = sqlite.connect(db_file)
        connection.execute("PRAGMA journal_mode=WAL") #Lets the UI read while a scrape is writing
        connection.execute("PRAGMA busy_timeout=5000") #Wait up to 5 s for the scrape's write to finish instead of failing at once
        connection.execute("PRAGMA synchronous=NORMAL") #Safe with WAL and much faster than FULL
        if not quiet:
            print(f"[green]Connected to database: {db_file}[/green]")
        return connection
    except sqlite.Error as e:
        print(f"[red]Error connecting to database: {e}[/red]")
        return None

#Function to create the tables in the database (safe to call on every run, existing tables are left alone)
def create_tables(connection):
    try:
        with connection: #Commits on success, rolls back if something fails
            connection.execute("""
                CREATE TABLE IF NOT EXISTS listings (
                    id              INTEGER PRIMARY KEY,
                    site            TEXT NOT NULL,
                    upid            TEXT NOT NULL,
                    url             TEXT,
                    title           TEXT,
                    description     TEXT,
                    price           REAL,
                    currency        TEXT,
                    phone           TEXT,
                    seller          TEXT,
                    published_date  TEXT,
                    published_time  TEXT,
                    listing_type    TEXT,    --For sale / for rent...
                    size_m2         REAL,    --Number only so it can be sorted and filtered
                    rooms           INTEGER,
                    condition       TEXT,    --New / used...
                    city            TEXT,
                    address         TEXT,
                    posted_by       TEXT,    --Company / private
                    images          TEXT, --JSON list of image URLs
                    attributes      TEXT, --JSON dict of the listing's tags (size, rooms, city...)
                    first_seen      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    favourite       BOOLEAN NOT NULL DEFAULT FALSE,
                    UNIQUE (site, upid) --The same listing can never be saved twice
                )
            """)
            #CREATE TABLE IF NOT EXISTS never changes a table that already exists, so columns added later are added here.
            #Checks the real columns, so it's safe on a new DB (column already there) and on an old one (column missing).
            columns = {row[1] for row in connection.execute("PRAGMA table_info(listings)")}
            if "favourite" not in columns:
                connection.execute("ALTER TABLE listings ADD COLUMN favourite BOOLEAN NOT NULL DEFAULT FALSE")
                print("[green]Added the favourite column to the listings table[/green]")
            #Every "Pyet AI" question, so the History page can show it and re-run its filters without asking the AI again
            connection.execute("""
                CREATE TABLE IF NOT EXISTS ai_searches (
                    id              INTEGER PRIMARY KEY,
                    question        TEXT NOT NULL,
                    filters         TEXT NOT NULL, --JSON of Search.Filters, what the AI understood
                    understood      TEXT,          --The AI's one-sentence summary, Albanian
                    not_supported   TEXT,          --Parts of the question the filters couldn't express
                    result_count    INTEGER,       --How many listings matched when it was asked
                    model           TEXT,
                    input_tokens    INTEGER,
                    output_tokens   INTEGER,
                    cost_usd        REAL,
                    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            #What every paid AI call cost, including ones that failed after Claude answered (refusal, max_tokens, unreadable).
            #The daily spending cap reads this, not ai_searches, so deleting questions from the history can't reset the cap.
            #Rows are never deleted by the app.
            connection.execute("""
                CREATE TABLE IF NOT EXISTS ai_usage (
                    id              INTEGER PRIMARY KEY,
                    model           TEXT,
                    input_tokens    INTEGER,
                    output_tokens   INTEGER,
                    cost_usd        REAL NOT NULL DEFAULT 0,
                    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            #Sellers the agent doesn't want to see. Their listings stay stored, the server just leaves them out (Derive.is_blocked)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS blocked_sellers (
                    id              INTEGER PRIMARY KEY,
                    name            TEXT NOT NULL,             --What the blocked list shows
                    match_name      TEXT NOT NULL DEFAULT '',  --Normalised name to match on, empty = match on the phones only
                    phones          TEXT NOT NULL DEFAULT '[]', --JSON list of phone numbers without prefix
                    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        print(f"[green]Tables ready in database: {DB_FILE.name}[/green]")
    except sqlite.Error as e:
        print(f"[red]Error creating tables: {e}[/red]")

#Function to save a listing to the database, inserts new ones and updates existing ones (keeps first_seen and id)
def save_listing(connection, listing, site):
    row = {key: listing.get(key) for key in (
        "upid", "url", "title", "description", "price", "currency",
        "phone", "seller", "published_date", "published_time",
        "listing_type", "size_m2", "rooms", "condition", "city", "address", "posted_by",
    )}
    row["site"] = site
    row["upid"] = str(row["upid"])
    row["images"] = json.dumps(listing.get("images") or [], ensure_ascii=False) #SQLite can't store lists, save as JSON text
    row["attributes"] = json.dumps(listing.get("attributes") or {}, ensure_ascii=False) #Same for dicts

    try:
        with connection:
            connection.execute("""
                INSERT INTO listings (
                    site, upid, url, title, description, price, currency, phone,
                    seller, published_date, published_time,
                    listing_type, size_m2, rooms, condition, city, address, posted_by,
                    images, attributes
                ) VALUES (
                    :site, :upid, :url, :title, :description, :price, :currency, :phone,
                    :seller, :published_date, :published_time,
                    :listing_type, :size_m2, :rooms, :condition, :city, :address, :posted_by,
                    :images, :attributes
                )
                ON CONFLICT(site, upid) DO UPDATE SET
                    url         = excluded.url,
                    title       = excluded.title,
                    description = excluded.description,
                    price       = excluded.price,
                    currency    = excluded.currency,
                    phone       = excluded.phone,
                    seller      = excluded.seller,
                    listing_type = excluded.listing_type,
                    size_m2     = excluded.size_m2,
                    rooms       = excluded.rooms,
                    condition   = excluded.condition,
                    city        = excluded.city,
                    address     = excluded.address,
                    posted_by   = excluded.posted_by,
                    images      = excluded.images,
                    attributes  = excluded.attributes,
                    last_seen   = CURRENT_TIMESTAMP
            """, row)
        #escape(): a scraped title like "[/b] SHITET" would otherwise be read as rich markup and raise
        print(f"[green]Listing saved: {escape(str(row['title']))}[/green]")
        return True
    except sqlite.Error as e:
        print(f"[red]Error saving listing {row['upid']}: {e}[/red]")
        return False

#Function to get the UPIDs of one site's listings already in the database, so the scrapers can skip them before opening them
def get_known_upids(connection, site):
    try:
        rows = connection.execute("SELECT upid FROM listings WHERE site = ?", (site,))
        return {str(upid) for (upid,) in rows}
    except sqlite.Error as e:
        print(f"[red]Error reading known listings for {site}: {e}[/red]")
        return set()

#Function that tells whether the listings changed since last time (count, newest id, latest save), so the server can keep
#the derived listings in memory and only rebuild them after a scrape saved something
def listings_version(connection):
    try:
        return tuple(connection.execute("SELECT COUNT(*), MAX(id), MAX(last_seen) FROM listings").fetchone())
    except sqlite.Error as e:
        raise DBError(str(e)) from e

#Function that returns what the AI calls cost today (UTC day), for the daily spending limit. Reads the ai_usage ledger,
#which the history page can't delete from.
def ai_cost_today(connection):
    try:
        row = connection.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM ai_usage WHERE created_at >= date('now')").fetchone()
        return float(row[0] or 0)
    except sqlite.Error as e:
        raise DBError(str(e)) from e

#Function to record what one paid AI call cost (usage as returned by AI.usage_of). Raises DBError if it can't be saved,
#so a call whose cost wasn't counted is never treated as free.
def record_ai_usage(connection, model, usage):
    try:
        with connection:
            connection.execute("INSERT INTO ai_usage (model, input_tokens, output_tokens, cost_usd) VALUES (?, ?, ?, ?)",
                               (model, usage.get("input_tokens"), usage.get("output_tokens"), usage.get("cost_usd") or 0))
    except sqlite.Error as e:
        raise DBError(str(e)) from e

#Function to get the stored listings (newest first) for the UI and the Excel export, optionally only one site
def get_listings(connection, site=None):
    query = "SELECT * FROM listings"
    params = ()
    if site:
        query += " WHERE site = ?"
        params = (site,)
    query += " ORDER BY last_seen DESC, id DESC"

    try:
        cursor = connection.cursor()
        cursor.row_factory = sqlite.Row #Rows behave like dicts so we get column names
        listings = []
        for row in cursor.execute(query, params):
            listing = dict(row)
            listing["images"] = json.loads(listing["images"] or "[]") #Turn the JSON text back into a list
            listing["attributes"] = json.loads(listing["attributes"] or "{}") #And back into a dict
            listing["favourite"] = bool(listing["favourite"]) #SQLite stores booleans as 0/1, the UI gets true/false
            listings.append(listing)
        return listings
    except sqlite.Error as e:
        print(f"[red]Error reading listings: {e}[/red]")
        raise DBError(str(e)) from e

#Function to mark a listing as a favourite or not (the heart on the card). Returns True if the listing exists.
#Raises DBError, so a failed save never looks like "listing not found". save_listing() never touches this column,
#so a favourite stays one when the listing is scraped again.
def set_favourite(connection, listing_id, favourite):
    try:
        with connection:
            return connection.execute("UPDATE listings SET favourite = ? WHERE id = ?", (favourite, listing_id)).rowcount > 0
    except sqlite.Error as e:
        print(f"[red]Error saving favourite for listing {listing_id}: {e}[/red]")
        raise DBError(str(e)) from e

#Function to get the blocked sellers, oldest first. Raises DBError, so a read failure never shows blocked sellers again by accident.
def get_blocked_sellers(connection):
    try:
        rows = connection.execute("SELECT id, name, match_name, phones, created_at FROM blocked_sellers ORDER BY id").fetchall()
    except sqlite.Error as e:
        print(f"[red]Error reading blocked sellers: {e}[/red]")
        raise DBError(str(e)) from e
    return [{"id": id, "name": name, "match_name": match_name, "phones": json.loads(phones or "[]"), "created_at": created_at}
            for id, name, match_name, phones, created_at in rows]

#Function to block a seller, returns the new row id (None on error)
def add_blocked_seller(connection, name, match_name, phones):
    try:
        with connection:
            cursor = connection.execute("INSERT INTO blocked_sellers (name, match_name, phones) VALUES (?, ?, ?)",
                                        (name, match_name, json.dumps(phones)))
        return cursor.lastrowid
    except sqlite.Error as e:
        print(f"[red]Error blocking seller: {e}[/red]")
        return None

#Function to unblock a seller, returns True if a row was removed
def delete_blocked_seller(connection, blocked_id):
    try:
        with connection:
            return connection.execute("DELETE FROM blocked_sellers WHERE id = ?", (blocked_id,)).rowcount > 0
    except sqlite.Error as e:
        print(f"[red]Error unblocking seller {blocked_id}: {e}[/red]")
        return False

#Function to get the distinct cities in the database, so the AI spells places the way the listings do
def get_known_places(connection):
    try:
        rows = connection.execute("SELECT DISTINCT city FROM listings WHERE city IS NOT NULL AND city != '' ORDER BY city")
        return [city for (city,) in rows]
    except sqlite.Error as e:
        print(f"[red]Error reading cities: {e}[/red]")
        return []

#Function to save one AI question with what it was turned into, returns the new row id (None on error)
def save_ai_search(connection, question, filters_json, understood, not_supported, result_count, model, usage):
    try:
        with connection:
            cursor = connection.execute("""
                INSERT INTO ai_searches (question, filters, understood, not_supported, result_count, model,
                                         input_tokens, output_tokens, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (question, filters_json, understood, not_supported, result_count, model,
                  usage.get("input_tokens"), usage.get("output_tokens"), usage.get("cost_usd")))
        return cursor.lastrowid
    except sqlite.Error as e:
        print(f"[red]Error saving AI search: {e}[/red]")
        return None

#Helper function: one ai_searches row as a dict, with the filters JSON decoded
def ai_search_row(cursor, row):
    item = {column[0]: value for column, value in zip(cursor.description, row)}
    item["filters"] = json.loads(item["filters"] or "{}")
    return item

#Function to get the saved AI questions, newest first
def get_ai_searches(connection, limit=200):
    try:
        cursor = connection.cursor()
        cursor.row_factory = ai_search_row
        return list(cursor.execute("SELECT * FROM ai_searches ORDER BY id DESC LIMIT ?", (limit,)))
    except sqlite.Error as e:
        print(f"[red]Error reading AI searches: {e}[/red]")
        return []

#Function to get one saved AI question by id, None if it doesn't exist
def get_ai_search(connection, search_id):
    try:
        cursor = connection.cursor()
        cursor.row_factory = ai_search_row
        return cursor.execute("SELECT * FROM ai_searches WHERE id = ?", (search_id,)).fetchone()
    except sqlite.Error as e:
        print(f"[red]Error reading AI search {search_id}: {e}[/red]")
        return None

#Function to delete one saved AI question, returns True if a row was removed
def delete_ai_search(connection, search_id):
    try:
        with connection:
            return connection.execute("DELETE FROM ai_searches WHERE id = ?", (search_id,)).rowcount > 0
    except sqlite.Error as e:
        print(f"[red]Error deleting AI search {search_id}: {e}[/red]")
        return False
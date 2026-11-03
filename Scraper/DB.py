#This file will act as the database manager for the scraper but also house the preset database functions.
#Imports
import sqlite3 as sqlite
import json
from pathlib import Path
from rich import print
from Scraper.config import Database_Name

#Global Variables
DB_FILE = Path(__file__).resolve().parent.parent / "Data" / Database_Name

#Function to connect to the database
def connect_to_db(db_file=DB_FILE):
    try:
        db_file.parent.mkdir(parents=True, exist_ok=True) #Create the Data folder if it doesn't exist yet
        connection = sqlite.connect(db_file)
        connection.execute("PRAGMA journal_mode=WAL") #Lets the UI read while a scrape is writing
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
                    UNIQUE (site, upid) --The same listing can never be saved twice
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
        print(f"[green]Listing saved: {row['title']}[/green]")
        return True
    except sqlite.Error as e:
        print(f"[red]Error saving listing {row['upid']}: {e}[/red]")
        return False

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
            listings.append(listing)
        return listings
    except sqlite.Error as e:
        print(f"[red]Error reading listings: {e}[/red]")
        return []
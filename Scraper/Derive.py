#This file works out the shared filter values that no site gives us as a clean column: property type, a merged condition,
#furnishing, features, floor, bathrooms, the posting date and the price in EUR. Each site words these differently and several only exist inside
#the raw `attributes` tags, so they're derived here, read-only, every time listings are loaded. Nothing is written to the DB.
#The UI (app.js) and the AI search (Search.py) both use these values, so this is the one place the rules live.
#Imports
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

#Global Variables
EUR_TO_ALL = 100 #Rough rate, only used to compare prices across EUR and ALL. UI/app.js has the same number for the price slider.

#Helper function: a seller's name in a form that ignores capitals and extra spaces
def seller_key(name):
    return " ".join(str(name or "").split()).casefold()

#Helper function: every phone number of a listing without its prefix, so "+355691234567" and "0691234567" are the same number
def phone_keys(phone):
    keys = set()
    for number in re.split(r"[;,/]", str(phone or "")):
        digits = re.sub(r"\D", "", number).removeprefix("00").removeprefix("355").lstrip("0")
        if len(digits) >= 6:
            keys.add(digits)
    return keys

#Function that works out how to recognise a listing's seller, for the blocked sellers list
#The phone is the reliable part. The name only counts for companies (they use several numbers under one name), or when there is
#no phone at all: private sellers are often just "Frida" or "Klient", and blocking one must not hide every other Frida.
def block_rule_of(listing):
    seller = " ".join(str(listing.get("seller") or "").split())
    phones = sorted(phone_keys(listing.get("phone")))
    posted_by = (listing.get("derived") or {}).get("posted_by", listing.get("posted_by"))
    return {
        "name": seller or str(listing.get("phone") or "").strip(),
        "match_name": seller_key(seller) if posted_by == "Kompani" or not phones else "",
        "phones": phones,
    }

#Helper function: the blocked sellers as one (names, phones) pair for is_blocked()
def blocked_rules(blocked):
    return ({row["match_name"] for row in blocked if row["match_name"]}, {phone for row in blocked for phone in row["phones"]})

#Function that tells whether a listing belongs to a blocked seller
def is_blocked(listing, rules):
    names, phones = rules
    return seller_key(listing.get("seller")) in names or not phones.isdisjoint(phone_keys(listing.get("phone")))

#Every value below is shown to agents, so it's Albanian
PROPERTY_TYPES = ("Apartament", "Garsoniere", "Vilë / Shtëpi private", "Tokë", "Ambient biznesi")
CONDITIONS = ("E re", "Si e re", "Në gjendje të mirë", "E rinovuar", "Në ndërtim")
FURNISHINGS = ("E mobiluar", "Pjesërisht e mobiluar", "E pamobiluar")
FEATURES = ("Ashensor", "Parkim / Garazh", "Ballkon / Tarracë")

#The kind named EARLIEST in the text wins: titles say what the property is first and where it is after, so in
#"Apartament 2+1, Vila L" the "Vila" is a building's name. On a tie (two kinds at the same spot) the order below decides.
#Patterns run on norm() text (no accents). Place names hide kind words, so match from the start of a word ("kinostudio")
PROPERTY_PATTERNS = (
    ("Ambient biznesi", re.compile(r"ambient|ambjent|biznes|dyqan|\bzyr[ae]|magazin|\blokal|hapesire|kapanon")),
    ("Tokë", re.compile(r"\btoke\b|\btruall|\bterren")),
    ("Garsoniere", re.compile(r"garsonier|garsionier|gasonier|\bstudio|papafingo")),
    ("Vilë / Shtëpi private", re.compile(r"\bvil[ae]\b|pjese vile|shtepi private|shtepi \d+ kat|shtepi me oborr|\d+ katesh")),
    ("Apartament", re.compile(r"apartament|aprtament|apartment|\d\s*\+\s*\d|duplex|dupleks|penthouse|\bpenthause")),
)

#Helper function: lowercase without accents, so "Tokë", "TOKE" and "toke" all match "toke"
def norm(text):
    if text is None:
        return ""
    decomposed = unicodedata.normalize("NFD", str(text))
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()

#Gazeta's and Duashpi's own category comes first (it's structured). MerrJep has none, so its title decides.
#Every kind found in the text is collected with where it starts, and the earliest one wins (see PROPERTY_PATTERNS)
def property_type(listing):
    category = norm((listing.get("attributes") or {}).get("Kategoria"))
    for text in (category, norm(listing.get("title"))):
        hits = [(match.start(), name) for name, pattern in PROPERTY_PATTERNS if (match := pattern.search(text))]
        if hits:
            #min() keeps the first of equal positions, and hits are in PROPERTY_PATTERNS order, so that order breaks ties
            return min(hits, key=lambda hit: hit[0])[1]
    return None

#MerrJep says "I ri" / "I përdorur - Si i ri", Gazeta "E re" / "Ne gjendje te mire", Duashpi only has tags
def condition_of(listing, tags):
    c = norm(listing.get("condition")).strip()
    if re.search(r"si i ri|si e re", c):
        return "Si e re"
    if re.search(r"rikonstru|rinovu", c):
        return "E rinovuar"
    if "ndertim" in c:
        return "Në ndërtim"
    if re.search(r"gjendje te mire|perdorur", c):
        return "Në gjendje të mirë"
    if c in ("i ri", "e re"):
        return "E re"
    if c:
        return listing.get("condition")
    if re.search(r"rinovuar|rikonstruktuar", tags):
        return "E rinovuar"
    if "ndertese e re" in tags:
        return "E re"
    return None

#Gazeta "Mobilimi", MerrJep "Për vendbanimin: …E mobiluar", Duashpi "Detaje të tjera: E pa mobiluar, …"
def furnishing_of(tags):
    if "pjeserisht e mobiluar" in tags:
        return "Pjesërisht e mobiluar"
    if re.search(r"pa ?mobiluar", tags):
        return "E pamobiluar"
    if "mobiluar" in tags:
        return "E mobiluar"
    return None

def features_of(tags):
    features = []
    if "ashensor" in tags and "ashensor: jo" not in tags:
        features.append("Ashensor")
    if re.search(r"parkim|garazh", tags) and not re.search(r"parkim[^:|]*: 0\b", tags):
        features.append("Parkim / Garazh")
    if re.search(r"ballkon|terrac|tarrac|veranda", tags):
        features.append("Ballkon / Tarracë")
    return features

#Duashpi tags give the distance to the city centre: "2.4km nga qendra", "982m nga qendra"
CENTER_DISTANCE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(km|m)\s+nga\s+qendra")

#Function that reads the distance to the centre in km from the tags, None when the site doesn't give one
def center_km(tags):
    match = CENTER_DISTANCE.search(tags)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return round(value if match.group(2) == "km" else value / 1000, 2)

#"3 kat" -> 3, "Përdhe" -> 0 (ground floor), nothing usable -> None
def first_int(value):
    if value is None or value == "":
        return None
    if "perdhe" in norm(value):
        return 0
    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else None

#Floor written in the title/description, for listings whose site has no floor field (most of MerrJep, half of Gazeta):
#"kati 4", "kati: 5", "kati i 4-t", "në katin e dytë", "kati i 9-te", "kati II", "kati i, e kompletuar" (= 1st)
ORDINALS = {"pare": 1, "pari": 1, "dyte": 2, "trete": 3, "katert": 4, "peste": 5, "gjashte": 6, "shtate": 7, "tete": 8,
            "nente": 9, "dhjete": 10}
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
FLOOR_IN_TEXT = re.compile(
    r"\bkat(?:i|in)\b\s*:?\s*(?:(?:i|e)\s+(?=\d|perdhe|" + "|".join(ORDINALS) + r"))?"
    r"(?P<value>perdhe|\d{1,2}(?![\d.,]*\s*(?:m2|m²|m\b|kate|katesh|\+))|(?:" + "|".join(ORDINALS) + r")\b|i{1,3}(?=\s*[,.;)]|\s+(?:dhe|me|i|e)\b))"
)

#Function that reads the floor from the listing's own words, None when it doesn't say one
def floor_in_text(text):
    match = FLOOR_IN_TEXT.search(text)
    if not match:
        return None
    value = match.group("value")
    if value == "perdhe":
        return 0
    if value.isdigit():
        return int(value)
    return ORDINALS.get(value, ROMAN.get(value))

#The day a listing was posted, as an ISO date. Gazeta and Duashpi write "04/11/2026", MerrJep "nën 04 2026".
MONTHS = {"jan": 1, "shk": 2, "mar": 3, "pri": 4, "maj": 5, "qer": 6, "kor": 7, "gus": 8, "sht": 9, "tet": 10, "nen": 11, "dhj": 12}

#Duashpi's UPID is a MongoDB ObjectId: its first 8 hex digits are the second the listing was created. Its published_date is
#the last time the listing was bumped/renewed (often weeks later), so "posted this week" must use the ObjectId instead.
OBJECT_ID = re.compile(r"[0-9a-f]{24}")
TIRANA_OFFSET = timedelta(hours=1) #Close enough for a day boundary (CET; summer time is off by an hour at midnight only)

def posted_date(listing):
    upid = str(listing.get("upid") or "")
    if listing.get("site") == "duashpi" and OBJECT_ID.fullmatch(upid):
        created = datetime.fromtimestamp(int(upid[:8], 16), tz=timezone.utc) + TIRANA_OFFSET
        return created.date().isoformat()
    text = norm(listing.get("published_date")).strip()
    try:
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if match:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1))).isoformat()
        match = re.fullmatch(r"([a-z]{3})\w*\s+(\d{1,2})\s+(\d{4})", text)
        if match and match.group(1) in MONTHS:
            return date(int(match.group(3)), MONTHS[match.group(1)], int(match.group(2))).isoformat()
    except ValueError:
        pass
    seen = listing.get("first_seen") #Unknown format: the day we first saw it is close enough
    return str(seen)[:10] if seen else None

#Bedrooms: "2+1" means 2 bedrooms + a living room. MerrJep's "rooms" column counts the living room too on some listings
#(a "1+1" stored as 2 rooms), so the N in "N+M" from the category or title wins, and the rooms column is the fallback.
BEDROOMS = re.compile(r"(?<!\d)(\d)\s*\+\s*\d(?!\d)")

def bedrooms_of(listing):
    for text in ((listing.get("attributes") or {}).get("Kategoria"), listing.get("title")):
        match = BEDROOMS.search(str(text or ""))
        if match:
            return int(match.group(1))
    rooms = listing.get("rooms")
    return rooms if isinstance(rooms, (int, float)) else None

#Duashpi's scraper only marks agencies ("Kompani") and leaves private sellers empty, so empty means private there
def posted_by_of(listing):
    if listing.get("posted_by"):
        return listing["posted_by"]
    return "Person privat" if listing.get("site") == "duashpi" else None

#Prices that can't be real for the deal, usually a sale typed as a rent or a missing zero: a monthly rent under 30 € or over
#20.000 €. They count as "no price" for filters, sorting and statistics, so "the cheapest rents" doesn't start with a 3 € one.
RENT_EUR_RANGE = (30, 20000)

#Function that returns the derived values for one listing (as loaded by DB.get_listings)
def derive(listing):
    attributes = listing.get("attributes") or {}
    tags = norm(" | ".join(f"{key}: {value}" for key, value in attributes.items()))
    price = listing.get("price")
    #0 means "me marrëveshje" (price on request), so it counts as no price
    price_eur = None
    if isinstance(price, (int, float)) and price > 0:
        price_eur = price / EUR_TO_ALL if listing.get("currency") == "ALL" else float(price)
        if listing.get("listing_type") == "Jepet me qera" and not RENT_EUR_RANGE[0] <= price_eur <= RENT_EUR_RANGE[1]:
            price_eur = None
    size = listing.get("size_m2")
    floor = first_int(attributes.get("Kati", attributes.get("Kati (Numri i kateve)")))
    floor_from_text = False
    if floor is None:
        floor = floor_in_text(norm(f"{listing.get('title') or ''}\n{listing.get('description') or ''}"))
        floor_from_text = floor is not None
    return {
        "property_type": property_type(listing),
        "condition": condition_of(listing, tags),
        "furnishing": furnishing_of(tags),
        "features": features_of(tags),
        "bedrooms": bedrooms_of(listing),
        "posted_by": posted_by_of(listing),
        "floor": floor,
        "floor_from_text": floor_from_text,
        "baths": first_int(attributes.get("Banjo", attributes.get("Tualete"))),
        "center_km": center_km(tags),
        "posted": posted_date(listing),
        "price_eur": price_eur,
        "price_per_m2": price_eur / size if price_eur and isinstance(size, (int, float)) and size > 0 else None,
    }

#Function that adds the derived values to every listing under "derived" (the raw columns stay as scraped)
def with_derived(listings):
    for listing in listings:
        listing["derived"] = derive(listing)
    return listings

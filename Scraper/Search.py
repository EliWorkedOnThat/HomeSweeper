#This file runs a structured search over the saved listings. The AI (AI.py) only turns a question into a Filters object;
#the actual matching happens here, in plain Python, so the AI never touches the database and a saved search can be re-run for free.
#Listings come from DB.get_listings() with Derive.with_derived() applied.
#Imports
import collections
import html
import re
from datetime import date, timedelta
from typing import Literal
from pydantic import BaseModel, field_validator
from Scraper.Derive import CONDITIONS, EUR_TO_ALL, FEATURES, FURNISHINGS, PROPERTY_TYPES, norm

#Global Variables
LISTING_TYPES = ("Shitet", "Jepet me qera")
POSTED_BY = ("Kompani", "Person privat")
SITES = ("merrjep", "gazetacelesi", "duashpi")
#"best_match" ranks by the soft preferences and targets below; the others sort on one value
SORTS = ("best_match", "newest", "price_asc", "price_desc", "price_per_m2_asc", "size_desc", "size_asc", "rooms_desc")
PREFERENCE_KINDS = ("keywords", "condition", "feature", "furnishing", "many_photos", "near_center", "range")
RANGE_FIELDS = ("price", "price_per_m2", "size", "rooms", "baths", "floor") #What a "range" preference can look at
PREFIX_MIN_LENGTH = 3 #Words this long match from the start of a word ("ballkon" hits "ballkoni"); shorter ones ("AC", "TV") only as whole words
#Albanian city names in their other forms (definite, genitive, English), so "Tirana", "Durrësi" or "in Vlora" find the city
CITY_ALIASES = {"tirana": "tirane", "tiranes": "tirane", "durresi": "durres", "durresit": "durres", "durazzo": "durres",
                "vlora": "vlore", "vlores": "vlore", "shkodra": "shkoder", "shkodres": "shkoder", "korca": "korce", "korces": "korce",
                "elbasani": "elbasan", "elbasanit": "elbasan", "fieri": "fier", "fierit": "fier", "saranda": "sarande",
                "sarandes": "sarande", "berati": "berat", "lushnja": "lushnje", "kavaja": "kavaje", "pogradeci": "pogradec",
                "gjirokastra": "gjirokaster", "lezha": "lezhe", "kukesi": "kukes"}
#Tag values that mean the listing does NOT have the thing ("Ashensor: Jo"), left out of the searchable text so "ashensor" doesn't match
NEGATIVE_VALUE = re.compile(r"^(jo|nuk ka|s'ka|ska|pa|0|no|none|false|-)$")
MANY_PHOTOS = 5 #"many_photos" preference: at least this many photos
TARGET_POINTS = 2 #A listing exactly on a price/size target scores this much, falling to 0 at twice the distance of the target itself
MAX_KNOWN_AREAS = 80 #Area names sent to the AI with each question
CENTER_KM_DEFAULT = 1.5 #"In the centre" for listings that give a distance (Duashpi), when the AI doesn't set one
#The distance tag itself says "nga qendra", so it's removed from the searchable text or every Duashpi listing would match "qendra"
CENTER_TAG = re.compile(r"\d+(?:[.,]\d+)?\s*k?m\s+nga\s+qendra")
NEAR_MISS_WHEN = 6 #Fewer results than this: also show listings that miss exactly one requirement
NEAR_MISS_MAX = 12
FAR_MISSES = ("Vendi", "Larg qendrës", "Nuk thuhet që është në qendër")

#A soft wish: listings that satisfy it rank higher, nothing is filtered out. The AI writes these for vague requests
#("looks good" -> condition new/renovated + words like "modern" + many photos), with a short Albanian reason shown on the card.
class Preference(BaseModel):
    kind: Literal[PREFERENCE_KINDS] #type: ignore[valid-type]
    values: list[str] = [] #Words for "keywords"/"near_center"; option names for condition/feature/furnishing; unused for many_photos
    weight: int = 1 #1 = nice to have, 3 = what they mostly care about
    reason_sq: str = "" #Shown on matching cards, e.g. "E rinovuar ose e re"
    #"range" only: a number the listing should preferably have ("kat i lartë" -> floor >= 5, "e lirë" -> price <= 400 €)
    field: Literal[RANGE_FIELDS] | None = None #type: ignore[valid-type]
    min: float | None = None
    max: float | None = None

    @field_validator("weight")
    @classmethod
    def clamp_weight(cls, value):
        return max(1, min(3, value))

#The search a question turns into. Every field is optional: an empty list or None means "no limit".
#Lists of options match ANY of them, except `features`, where a listing needs ALL of them.
class Filters(BaseModel):
    listing_types: list[Literal[LISTING_TYPES]] = [] #type: ignore[valid-type]
    property_types: list[Literal[PROPERTY_TYPES]] = [] #type: ignore[valid-type]
    places: list[str] = [] #City, area or street words, matched against the city and the address
    exclude_places: list[str] = [] #"Jo në Kombinat": listings whose city/address contains any of these are dropped
    conditions: list[Literal[CONDITIONS]] = [] #type: ignore[valid-type]
    furnishing: list[Literal[FURNISHINGS]] = [] #type: ignore[valid-type]
    features: list[Literal[FEATURES]] = [] #type: ignore[valid-type]
    posted_by: list[Literal[POSTED_BY]] = [] #type: ignore[valid-type]
    sites: list[Literal[SITES]] = [] #type: ignore[valid-type]
    price_min: float | None = None
    price_max: float | None = None
    price_currency: Literal["EUR", "ALL"] = "EUR" #Unit of price_min/price_max; listings in the other currency are converted
    price_per_m2_min_eur: float | None = None
    price_per_m2_max_eur: float | None = None
    size_min: float | None = None
    size_max: float | None = None
    rooms_min: int | None = None
    rooms_max: int | None = None
    baths_min: int | None = None
    baths_max: int | None = None
    floor_min: int | None = None
    floor_max: int | None = None
    #Free-text search in the title, description, address and tags. Every group must match; inside a group any word is enough,
    #so [["det", "plazh"], ["oborr", "kopsht"]] means (sea OR beach) AND (yard OR garden). Words match from the start of a word.
    keyword_groups: list[list[str]] = []
    exclude_keywords: list[str] = [] #Listings containing any of these are dropped
    has_photos: bool | None = None
    has_price: bool | None = None #True drops "me marrëveshje" listings
    posted_within_days: int | None = None #"Postuar sot" -> 0, "këtë javë" -> 7 (the site's posting date)
    include_missing: bool = False #Keep listings that don't have a filtered number (no floor, no m²...) instead of dropping them
    #"Near the centre": listings that give a distance (Duashpi) need center_km <= center_max_km; the others need one of
    #center_words (central areas/streets, "qendër"...) in their title, address, description or tags. Set either to turn it on.
    center_max_km: float | None = None
    center_words: list[str] = []
    preferences: list[Preference] = [] #Soft wishes, they only change the order (sort "best_match")
    price_target: float | None = None #"Around 120.000" -> rank listings closest to it first (in price_currency)
    size_target: float | None = None #"Around 100 m²"
    sort: Literal[SORTS] = "newest" #type: ignore[valid-type]
    limit: int | None = None #"The 5 cheapest" -> 5

    #The AI's answer is already paid for when it's checked, so out-of-range numbers are corrected instead of rejected
    @field_validator("limit")
    @classmethod
    def positive_limit(cls, value):
        return value if value is None or value >= 1 else None

    @field_validator("posted_within_days")
    @classmethod
    def not_negative(cls, value):
        return value if value is None else max(0, value)

#Helper function: one regex that matches any of the words at the start of a word, accents and case ignored
def word_pattern(words):
    cleaned = [w for w in (norm(w).strip() for w in words) if w]
    if not cleaned:
        return None
    parts = [re.escape(w) if len(w) >= PREFIX_MIN_LENGTH else re.escape(w) + r"(?![a-z0-9])" for w in cleaned]
    #Not right after "pa"/"nuk ka"/"s'ka": "pa ashensor" (no elevator) must not match "ashensor"
    return re.compile(r"(?<![a-z0-9])(?<!\bpa )(?<!\bnuk ka )(?<!\bs'ka )(?<!\bska )(?:" + "|".join(parts) + ")")

#Helper function: one place the agent named -> a test for a listing. A city ("Tiranë", "Tirana", "Durrësi") is compared with the
#city column, so "autostrada Tiranë-Elbasan" in a Mullet address isn't Tirana; an area or street ("Blloku", "Don Bosko") must
#start a word in the address or title, so "Ali" doesn't match "spitalit".
def place_test(place, cities):
    name = norm(place).strip()
    name = CITY_ALIASES.get(name, name)
    if not name:
        return None
    if name in cities:
        return lambda listing: norm(listing.get("city")).strip() == name
    pattern = word_pattern([name])
    return lambda listing: bool(pattern.search(norm(f"{listing.get('address') or ''} {listing.get('title') or ''}")))

def place_tests(places, listings):
    cities = {norm(l.get("city")).strip() for l in listings} | set(CITY_ALIASES.values())
    return [test for test in (place_test(p, cities) for p in places) if test]

#Helper function: the normalised text the keywords are searched in
def searchable_text(listing):
    attributes = listing.get("attributes") or {}
    #Every phone (some listings have two, "…; …") goes in as digits, with and without the 355 country code
    phones = []
    for number in re.split(r"[;,/]", listing.get("phone") or ""):
        digits = re.sub(r"\D", "", number)
        if digits:
            phones += [digits, "0" + digits[3:] if digits.startswith("355") else digits]
    #Tags that say "no" ("Ashensor: Jo") are left out, the rest go in as "key value"
    tags = [f"{key} {value}" for key, value in attributes.items() if not NEGATIVE_VALUE.match(norm(value).strip())]
    parts = [listing.get("title"), listing.get("description"), listing.get("address"), listing.get("city"), listing.get("seller"),
             *phones, *tags]
    text = CENTER_TAG.sub(" ", norm(" ".join(str(p) for p in parts if p)))
    #Numbers written with spaces in descriptions ("069 721 1116") also searchable as one run of digits
    joined = re.sub(r"(?<=\d)[ .\-](?=\d)", "", text)
    return text if joined == text else f"{text}\n{joined}"

#Function that checks "near the centre": the distance when the listing has one, otherwise the central words
def near_center(listing, max_km, words, text):
    distance = (listing.get("derived") or {}).get("center_km")
    if distance is not None:
        return distance <= (max_km if max_km is not None else CENTER_KM_DEFAULT)
    pattern = word_pattern(words)
    return bool(pattern and pattern.search(text))

#Helper function: the value a number filter looks at, None when the listing doesn't have it
def number_of(listing, field):
    derived = listing.get("derived") or {}
    value = {
        "price": derived.get("price_eur"),
        "price_per_m2": derived.get("price_per_m2"),
        "size": listing.get("size_m2"),
        "rooms": derived.get("bedrooms", listing.get("rooms")),
        "baths": derived.get("baths"),
        "floor": derived.get("floor"),
    }[field]
    return value if isinstance(value, (int, float)) else None

def in_range(value, low, high, include_missing):
    if low is None and high is None:
        return True
    if value is None:
        return include_missing
    return (low is None or value >= low - 1e-9) and (high is None or value <= high + 1e-9)

#Helper function: how a listing's value is shown in a near-miss note ("Kati: 3", "Çmimi: 650 €")
def shown_value(value, unit=""):
    if value is None:
        return "pa të dhëna"
    if isinstance(value, float) and value >= 100:
        text = f"{value:,.0f}".replace(",", ".")
    else:
        text = f"{round(value, 1):g}".replace(".", ",")
    return f"{text}{unit}"

#Sale/rent and the property type are never relaxed: a listing failing them is LOCKED, so it can't be a near miss
LOCKED = "locked"

#Function that lists the requirements a listing fails, as short Albanian notes ([] = it matches).
#stop_after: stop once this many have failed (a plain match check only needs the first one, a near miss needs to know it's 1).
def failures(listing, f, ctx, stop_after=None):
    derived = listing.get("derived") or {}
    failed = []

    def fail(note):
        failed.append(note)
        return stop_after is not None and len(failed) >= stop_after

    def any_of(wanted, value):
        return not wanted or value in wanted

    #Never relaxed: sale/rent, the kind of property, and anything the agent explicitly ruled out (a place, a word, agencies, a site)
    if (not any_of(f.listing_types, listing.get("listing_type")) or not any_of(f.property_types, derived.get("property_type"))
            or not any_of(f.posted_by, derived.get("posted_by", listing.get("posted_by"))) or not any_of(f.sites, listing.get("site"))
            or any(test(listing) for test in ctx["exclude_places"])):
        return [LOCKED]
    if ctx["exclude"] and ctx["exclude"].search(ctx["text"](listing)):
        return [LOCKED]
    lists = (
        (f.conditions, derived.get("condition"), "Gjendja"),
        (f.furnishing, derived.get("furnishing"), "Mobilimi"),
    )
    for wanted, value, label in lists:
        if not any_of(wanted, value) and fail(f"{label}: {value or 'pa të dhëna'}"):
            return failed
    if f.features:
        missing = [feature for feature in f.features if feature not in (derived.get("features") or [])]
        if missing and fail(f"Pa {', '.join(missing).lower()}"):
            return failed
    if ctx["places"] and not any(test(listing) for test in ctx["places"]):
        if fail(f"Vendi: {listing.get('address') or listing.get('city') or 'pa të dhëna'}"):
            return failed

    #Price limits come in price_currency, listings are compared in EUR
    to_eur = (1 / EUR_TO_ALL) if f.price_currency == "ALL" else 1
    price_min = f.price_min * to_eur if f.price_min is not None else None
    price_max = f.price_max * to_eur if f.price_max is not None else None
    ranges = (
        ("price", price_min, price_max, "Çmimi", " €"),
        ("price_per_m2", f.price_per_m2_min_eur, f.price_per_m2_max_eur, "Çmimi për m²", " €/m²"),
        ("size", f.size_min, f.size_max, "Sipërfaqja", " m²"),
        ("rooms", f.rooms_min, f.rooms_max, "Dhoma", ""),
        ("baths", f.baths_min, f.baths_max, "Banjo", ""),
        ("floor", f.floor_min, f.floor_max, "Kati", ""),
    )
    for field, low, high, label, unit in ranges:
        value = number_of(listing, field)
        if not in_range(value, low, high, f.include_missing) and fail(f"{label}: {shown_value(value, unit)}"):
            return failed

    if f.has_photos is not None and bool(listing.get("images")) != f.has_photos:
        if fail("Pa foto" if f.has_photos else "Ka foto"):
            return failed
    if f.has_price is not None and (derived.get("price_eur") is not None) != f.has_price:
        if fail("Pa çmim" if f.has_price else "Ka çmim"):
            return failed
    if f.posted_within_days is not None:
        posted = derived.get("posted")
        if not posted or posted < (date.today() - timedelta(days=f.posted_within_days)).isoformat():
            if fail(f"Postuar më {posted or 'datë të panjohur'}"):
                return failed

    near_center_on = f.center_max_km is not None or bool(f.center_words)
    if ctx["keywords"] or near_center_on:
        text = ctx["text"](listing)
        if near_center_on and not near_center(listing, f.center_max_km, f.center_words, text):
            distance = derived.get("center_km")
            if fail(f"Larg qendrës ({shown_value(distance, ' km')})" if distance is not None else "Nuk thuhet që është në qendër"):
                return failed
        for group, pattern in ctx["keywords"]:
            if not pattern.search(text) and fail(f"Nuk përmend: {' / '.join(group[:3])}"):
                return failed
    return failed

#Function that checks one listing against the filters (ctx comes from prepare(), compiled once per search)
def matches(listing, f, ctx):
    return not failures(listing, f, ctx, stop_after=1)

#Helper function: everything a search compiles once instead of per listing. Keyword groups keep their words so a near miss can
#say which idea it lacked. The searchable text is built at most once per listing per search.
def prepare(filters, listings):
    keywords = [(group, word_pattern(group)) for group in filters.keyword_groups]
    texts = {}

    def text(listing):
        if listing["id"] not in texts:
            texts[listing["id"]] = searchable_text(listing)
        return texts[listing["id"]]

    return {
        "keywords": [(group, pattern) for group, pattern in keywords if pattern],
        "exclude": word_pattern(filters.exclude_keywords),
        "places": place_tests(filters.places, listings),
        "exclude_places": place_tests(filters.exclude_places, listings),
        "text": text,
    }

#---------- Ranking by soft preferences and targets ----------

#Function that checks one preference against a listing
def preference_met(listing, preference, text, max_km=None):
    derived = listing.get("derived") or {}
    if preference.kind == "keywords":
        pattern = word_pattern(preference.values)
        return bool(pattern and pattern.search(text))
    if preference.kind == "condition":
        return derived.get("condition") in preference.values
    if preference.kind == "feature":
        return any(feature in (derived.get("features") or []) for feature in preference.values)
    if preference.kind == "furnishing":
        return derived.get("furnishing") in preference.values
    if preference.kind == "many_photos":
        return len(listing.get("images") or []) >= MANY_PHOTOS
    if preference.kind == "near_center":
        return near_center(listing, max_km, preference.values, text)
    if preference.kind == "range" and preference.field:
        value = number_of(listing, preference.field)
        if value is None or (preference.min is None and preference.max is None):
            return False
        return (preference.min is None or value >= preference.min - 1e-9) and (preference.max is None or value <= preference.max + 1e-9)
    return False

#Points for being close to a target: TARGET_POINTS on the target, 0 once it's off by the target's own size
def closeness(value, target):
    if value is None or not target or target <= 0:
        return 0.0
    return max(0.0, 1 - abs(value - target) / target) * TARGET_POINTS

#Function that scores a listing for "best_match" and says why, returns (score, [reasons in Albanian])
def score(listing, filters, text=None):
    text = text if text is not None else searchable_text(listing)
    points, reasons = 0.0, []
    for preference in filters.preferences:
        if preference_met(listing, preference, text, filters.center_max_km):
            points += preference.weight
            if preference.reason_sq:
                reasons.append(preference.reason_sq)
    to_eur = (1 / EUR_TO_ALL) if filters.price_currency == "ALL" else 1
    if filters.price_target:
        near = closeness(number_of(listing, "price"), filters.price_target * to_eur)
        points += near
        if near >= TARGET_POINTS * 0.85:
            reasons.append("Çmimi afër kërkesës")
    if filters.size_target:
        near = closeness(number_of(listing, "size"), filters.size_target)
        points += near
        if near >= TARGET_POINTS * 0.85:
            reasons.append("Sipërfaqja afër kërkesës")
    return points, reasons

#Function that returns why each found listing matched the wishes: {listing id: [reasons]}, only listings with a reason
def match_reasons(listings, filters):
    if not filters.preferences and not filters.price_target and not filters.size_target:
        return {}
    reasons = {}
    for listing in listings:
        found = score(listing, filters)[1]
        if found:
            reasons[listing["id"]] = found
    return reasons

#Function that lists the area names in the listings (Gazeta's "Zona", address parts), most common first,
#so the AI can tell which ones a wish like "in the centre" should point at, spelled the way the listings spell them
def known_areas(listings, limit=MAX_KNOWN_AREAS):
    cities = {norm(l.get("city")).strip() for l in listings} | set(CITY_ALIASES) | set(CITY_ALIASES.values())
    noise = cities | {"albania", "shqiperi", "tirana county", "durres county", "qarku tirane"}
    counts = collections.Counter()
    spelling = {} #The first spelling seen for each name ("Golem" and "golem" are one area)
    for listing in listings:
        attributes = listing.get("attributes") or {}
        parts = [attributes.get("Zona")] + re.split(r"[,;/–\-|]", html.unescape(listing.get("address") or ""))
        for part in parts:
            name = (part or "").strip(" .\"'&;")
            key = norm(name)
            if 3 <= len(name) <= 40 and not any(ch.isdigit() for ch in name) and key not in noise:
                spelling.setdefault(key, name)
                counts[key] += 1
    return [spelling[key] for key, _ in counts.most_common(limit)]

#Sort key per sort option: listings missing the value always go last
def sort_listings(listings, sort, filters=None):
    if sort == "best_match":
        newest = sort_listings(listings, "newest") #Ties keep the newest first
        return sorted(newest, key=lambda l: score(l, filters)[0], reverse=True)
    if sort == "newest":
        return sorted(listings, key=lambda l: ((l.get("derived") or {}).get("posted") or "", l.get("first_seen") or "", l.get("id") or 0),
                      reverse=True)
    field, descending = {
        "price_asc": ("price", False),
        "price_desc": ("price", True),
        "price_per_m2_asc": ("price_per_m2", False),
        "size_desc": ("size", True),
        "size_asc": ("size", False),
        "rooms_desc": ("rooms", True),
    }[sort]
    have = [l for l in listings if number_of(l, field) is not None]
    missing = [l for l in listings if number_of(l, field) is None]
    return sorted(have, key=lambda l: number_of(l, field), reverse=descending) + missing

#Function that runs a search: returns the matching listings, sorted and cut to `limit`
def search(listings, filters):
    ctx = prepare(filters, listings)
    found = [l for l in listings if matches(l, filters, ctx)]
    found = sort_listings(found, filters.sort, filters)
    return found[: filters.limit] if filters.limit else found

#Function that finds the listings missing exactly one requirement (never sale/rent or the property type), for when a
#search finds little: returns [(listing, "Kati: 3"), ...], best match first. Empty when the search found enough.
def near_misses(listings, filters, found_count):
    if found_count >= NEAR_MISS_WHEN or filters.limit:
        return []
    ctx = prepare(filters, listings)
    misses = {}
    for listing in listings:
        failed = failures(listing, filters, ctx, stop_after=2)
        if len(failed) == 1 and failed[0] != LOCKED:
            misses[listing["id"]] = failed[0]
    candidates = [listing for listing in listings if listing["id"] in misses]
    ranked = sort_listings(candidates, "best_match" if filters.sort == "newest" else filters.sort, filters)
    #Being in the wrong place is a bigger miss than being one floor off, so those go last
    ranked.sort(key=lambda listing: misses[listing["id"]].startswith(FAR_MISSES))
    return [(listing, misses[listing["id"]]) for listing in ranked[:NEAR_MISS_MAX]]

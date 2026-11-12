#This file turns an agent's question ("2+1 në Tiranë, kati 4, të paktën 50 m²") into a Search.Filters object with Claude.
#Claude only fills in the filters (structured JSON output). It never writes SQL and never sees the listings,
#so only the question and the list of known places leave this PC. Search.py does the matching.
#The API key comes from the ANTHROPIC_API_KEY environment variable, or from Data/anthropic_api_key.txt (git-ignored).
#Imports
import json
import os
from pathlib import Path
import anthropic
from pydantic import ValidationError
from rich import print
from Scraper.Derive import CONDITIONS, EUR_TO_ALL, FEATURES, FURNISHINGS, PROPERTY_TYPES
from Scraper.Search import LISTING_TYPES, POSTED_BY, PREFERENCE_KINDS, RANGE_FIELDS, SITES, SORTS, Filters

#Global Variables
MODEL = "claude-opus-5-5"
EFFORT = "low" #Turning a question into filters is a small task; low effort keeps it fast and cheap
MAX_TOKENS = 8000 #Includes the model's thinking, so leave room even though the JSON itself is short
TIMEOUT_SECONDS = 90
KEY_FILE = Path(__file__).resolve().parent.parent / "Data" / "anthropic_api_key.txt"
FALLBACK_BETA = "server-side-fallback-2026-07-01" #If Opus 5.5 declines a question, the API retries it on Anthropic's recommended model
#USD per million tokens for MODEL, used to show what each question cost (cache writes are 1.25x input)
PRICE_INPUT, PRICE_OUTPUT, PRICE_CACHE_READ, PRICE_CACHE_WRITE = 4.00, 20.00, 0.20, 5.00

#Shown to agents when something goes wrong, so it's Albanian
#usage/model are set when Claude did answer (so the call was paid) but the answer couldn't be used; the server still counts its cost
class AIError(Exception):
    def __init__(self, message_sq, status=502, usage=None, model=None):
        super().__init__(message_sq)
        self.message_sq = message_sq
        self.status = status
        self.usage = usage
        self.model = model

#Function that finds the API key, None if there isn't one yet
def api_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None

def is_configured():
    return api_key() is not None

#---------- The output schema ----------
#Written by hand (instead of generated from Filters) so every field carries a description Claude reads.
#Structured outputs need every object closed (additionalProperties: false); every field is required and "no limit" is null or [].
#The API allows at most 16 fields that can be "a value or null" per schema, which is why the 12 number limits are one `ranges` list
#(2 nullable fields instead of 12). Nullable fields inside `preferences` also made the compiled schema too large for the API, so
#its range fields use 'none' and -1 for "not set". expand_ranges() turns all of this back into what Filters expects. Count the anyOfs after adding a nullable field.

NO_FIELD = "none" #A preference that isn't a "range" has no field

def nullable(kind, description):
    return {"anyOf": [{"type": kind}, {"type": "null"}], "description": description}

def options(values, description):
    return {"type": "array", "items": {"type": "string", "enum": list(values)}, "description": description}

FILTER_PROPERTIES = {
    "listing_types": options(LISTING_TYPES, "Sale (Shitet) or rent (Jepet me qera). Empty = both."),
    "property_types": options(PROPERTY_TYPES, "Kind of property. 'Shtëpi' in a question usually means any home: leave empty unless they clearly mean a villa/private house."),
    "places": {"type": "array", "items": {"type": "string"},
               "description": ("Cities are compared with the listing's city: write them exactly as in KNOWN PLACES (Tiranë, Durrës, Vlorë; "
                               "not Tirana/Durrësi). Areas and streets must start a word in the address or title (Blloku, Don Bosko).")},
    "exclude_places": {"type": "array", "items": {"type": "string"},
                       "description": "'Jo në Kombinat', 'përveç Kashar': listings whose city/address contains any of these are dropped."},
    "conditions": options(CONDITIONS, "Condition of the property."),
    "furnishing": options(FURNISHINGS, "Furnishing. 'I mobiluar' -> E mobiluar (add Pjesërisht e mobiluar only if they accept partly furnished)."),
    "features": options(FEATURES, "Features the property must ALL have."),
    "posted_by": options(POSTED_BY, "'Jo agjenci', 'pronar', 'privat' -> Person privat. 'Agjenci' -> Kompani."),
    "sites": options(SITES, "Only when they name a website."),
    "ranges": {
        "type": "array",
        "description": ("Hard limits on numbers, at most one entry per field; leave a field out when it has no limit. price is in price_currency, "
                        "price_per_m2 is in EUR. 'Deri 500' -> price max 500. '2+1' means 2 bedrooms + living room, so rooms min = max = 2. "
                        "Ground floor (përdhe) is 0. 'Kati i 4' -> floor min = max = 4."),
        "items": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": list(RANGE_FIELDS), "description": "price, price_per_m2, size (m²), rooms (bedrooms), baths or floor."},
                "min": nullable("number", "Lowest value, or null."),
                "max": nullable("number", "Highest value, or null."),
            },
            "required": ["field", "min", "max"],
            "additionalProperties": False,
        },
    },
    "price_currency": {"type": "string", "enum": ["EUR", "ALL"], "description": "Currency of the price range and price_target: € / euro -> EUR, lek / lekë / ALL -> ALL. EUR when no currency is said."},
    "keyword_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                       "description": ("Free-text search in title, description, address and tags, for anything the other fields can't express "
                                       "(sea view, garden, pool, near a school, new building...). Each inner list is one idea written as several "
                                       "Albanian words and word forms (with and without ë/ç, common misspellings, and the English word if listings "
                                       "might use it); a listing must match every group, and any one word inside a group. Words match from the "
                                       "start of a word, so 'det' would also hit 'detaje': prefer full forms ('deti', 'detit', 'bregdet'). "
                                       "Minimum 3 letters per word.")},
    "exclude_keywords": {"type": "array", "items": {"type": "string"}, "description": "Listings mentioning any of these words are dropped (same matching rules)."},
    "has_photos": nullable("boolean", "true = only listings with photos."),
    "has_price": nullable("boolean", "true = drop listings without a price (me marrëveshje)."),
    "posted_within_days": nullable("integer", "Only listings posted in the last N days: 'sot' -> 0, 'dje e sot' -> 1, 'këtë javë' / 'të rejat' -> 7, 'këtë muaj' -> 30."),
    "include_missing": {"type": "boolean", "description": "true keeps listings that lack a filtered number (no floor, no m²...). Default false; true only if they ask not to lose such listings."},
    "center_max_km": nullable("number", ("Hard 'near the centre' filter: listings that give a distance to the city centre (Duashpi) must be within "
                                         "this many km. Typical: 1.5 for 'në qendër', 1 for 'në zemër të qytetit', 3 for 'jo larg qendrës'. "
                                         "Set together with center_words.")),
    "center_words": {"type": "array", "items": {"type": "string"},
                     "description": ("Hard 'near the centre' filter for listings without a distance: they must mention one of these words "
                                     "(qender, qendra, qendres, zone qendrore, plus the central area and street names). Empty = off.")},
    "preferences": {
        "type": "array",
        "description": ("Soft wishes: listings that satisfy more of them rank higher, NOTHING is filtered out by them. "
                        "Use these for vague or taste-based requests (looks good, bright, quiet, modern, in the centre when it's a wish rather than a must) "
                        "by translating the wish into concrete signals."),
        "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(PREFERENCE_KINDS),
                         "description": ("keywords: any of `values` appears in the text (same word rules as keyword_groups). "
                                         f"condition: derived condition is one of `values` ({', '.join(CONDITIONS)}). "
                                         f"feature: has any of `values` ({', '.join(FEATURES)}). "
                                         f"furnishing: one of `values` ({', '.join(FURNISHINGS)}). "
                                         "many_photos: has 5+ photos (`values` empty). "
                                         "near_center: within center_max_km (default 1.5 km) when the listing gives a distance, otherwise mentions "
                                         "one of `values` (central words/areas). "
                                         "range: the listing's `field` lies between `min` and `max` (either may be null), e.g. a high floor or a low price "
                                         "as a wish rather than a must.")},
                "values": {"type": "array", "items": {"type": "string"}},
                "weight": {"type": "integer", "description": "1 = small plus, 2 = clear plus, 3 = the main thing they want."},
                "reason_sq": {"type": "string", "description": "2-5 Albanian words shown on matching cards, e.g. 'E rinovuar ose e re', 'Afër qendrës'."},
                "field": {"type": "string", "enum": [*RANGE_FIELDS, NO_FIELD],
                          "description": "range only: price (EUR), price_per_m2 (EUR), size (m²), rooms (bedrooms), baths or floor. 'none' for other kinds."},
                "min": {"type": "number", "description": "range only: lowest value. -1 = no lower limit, and for other kinds."},
                "max": {"type": "number", "description": "range only: highest value. -1 = no upper limit, and for other kinds."},
            },
            "required": ["kind", "values", "weight", "reason_sq", "field", "min", "max"],
            "additionalProperties": False,
        },
    },
    "price_target": nullable("number", "'Rreth 120 mijë' -> 120000 (in price_currency). Also add a price range about 30% either side. Ranks the closest first."),
    "size_target": nullable("number", "'Rreth 100 m²' -> 100. Also add a size range about 30% either side. Ranks the closest first."),
    "sort": {"type": "string", "enum": list(SORTS),
             "description": ("best_match whenever there are preferences or targets; 'më të lirat' -> price_asc, 'më të mëdhatë' -> size_desc, "
                             "best value -> price_per_m2_asc. Otherwise newest.")},
    "limit": nullable("integer", "Only when they ask for a number of results ('5 më të lirat' -> 5)."),
}

#Which Filters fields each entry of `ranges` fills in
RANGE_TARGETS = {"price": ("price_min", "price_max"), "price_per_m2": ("price_per_m2_min_eur", "price_per_m2_max_eur"), "size": ("size_min", "size_max"),
                 "rooms": ("rooms_min", "rooms_max"), "baths": ("baths_min", "baths_max"), "floor": ("floor_min", "floor_max")}
WHOLE_NUMBER_RANGES = ("rooms", "baths", "floor")

#Function that turns the AI's `ranges` list into the flat fields Filters uses, and 'none'/-1 in the preferences into None
def expand_ranges(filters_data):
    expanded = {key: value for key, value in filters_data.items() if key != "ranges"}
    expanded["preferences"] = [
        {**preference, "field": None if preference.get("field") == NO_FIELD else preference.get("field"),
         "min": None if (preference.get("min") or 0) < 0 else preference.get("min"),
         "max": None if (preference.get("max") or 0) < 0 else preference.get("max")}
        for preference in filters_data.get("preferences") or []
    ]
    for item in filters_data.get("ranges") or []:
        whole = item["field"] in WHOLE_NUMBER_RANGES
        for name, value in zip(RANGE_TARGETS[item["field"]], (item["min"], item["max"])):
            expanded[name] = round(value) if whole and value is not None else value
    return expanded

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "filters": {"type": "object", "properties": FILTER_PROPERTIES, "required": list(FILTER_PROPERTIES), "additionalProperties": False},
        "understood_sq": {"type": "string", "description": "One short Albanian sentence saying what you are searching for."},
        "not_supported_sq": nullable("string", "Albanian note naming any part of the question the filters can't express (e.g. distance in minutes), else null."),
    },
    "required": ["filters", "understood_sq", "not_supported_sq"],
    "additionalProperties": False,
}

#Stays byte-identical between calls so it can be cached; anything that changes (known places, the question) goes in the user message
SYSTEM_PROMPT = f"""You turn questions from Albanian real-estate agents into search filters for HomeSweeper, a local database of property \
listings scraped from MerrJep.al, Gazeta Celësi, Duashpi.al and Njoftime.com. You only fill in the filters; a program runs the search.

How to read the questions:
- Agents write Albanian, often informally and without ë/ç ("kati 4", "deri 500 euro", "jo agjenci", "2+1 ne tirane"). They may also write English.
- "Qira", "me qera", "jepet" mean rent (Jepet me qera); "shitje", "shitet", "blej" mean sale (Shitet).
- "Mijë" / "k" = thousand, "milion" = million. Prices with no currency are EUR, except that bare numbers far above normal EUR prices \
for that kind of deal (e.g. a rent of 50000) are ALL (lekë). 1 EUR is about {EUR_TO_ALL} ALL.
- "2+1" / "1+1" give the number of bedrooms (the first number). "Garsoniere" is a studio.
- "Të paktën" / "minimum" / "mbi" = at least; "deri" / "maksimumi" / "nën" = at most.
- "Rreth" / "afërsisht" / "around" a price or size: set the target AND a range about 30% either side, and sort best_match \
(the closest come first, so the range can be generous).

Requirements versus wishes - think about what the agent actually means:
- A clear, checkable requirement ("kati 4", "deri 600 €", "me ashensor", "jo agjenci", "të paktën 80 m²") becomes a hard filter.
- A vague or taste-based wish ("diçka që duket bukur", "i ndritshëm", "zonë e qetë", "luksoz", "investim i mirë", "në qendër") becomes \
preferences, so good listings are ranked first and nothing is thrown away because a description didn't use the exact word. \
Translate each wish into the concrete signals a listing would show, with a weight and a short reason. Examples:
  - looks good / beautiful / nice: condition E re, Si e re or E rinovuar (weight 2); keywords like modern, luks, lux, design, i rinovuar, \
cilësi, mobilim i ri, pamje (weight 1-2); many_photos (weight 1).
  - bright: keywords drit, ndricim, diell, me dritare, panoramik, pamje (weight 2).
  - quiet: keywords qete, e qete, rruge e qete, zone e qete, rezidencial, kompleks (weight 2).
  - city centre: never use keyword_groups for it (some sites write "X km nga qendra" on every listing). If being central is the \
main point ("diçka në qendër"), set center_max_km (about 1.5) and center_words: "qender", "qendra", "qendres", "zone qendrore" (never the bare stem "qendror": it also matches "ngrohje qendrore") plus the \
central areas and streets of that city, preferring names that appear in KNOWN AREAS (for Tirana e.g. Blloku, Skënderbej, Toptani, \
Myslym Shyri, Pazari i Ri, Rruga e Durrësit, Rruga e Kavajës, Rruga e Dibrës, 21 Dhjetori, Ambasada Amerikane, Sheshi Avni Rustemi; \
for Durrës the centre, Shëtitorja, Porti). If it's one wish among others, use a near_center preference with the same words instead.
  - good for a family: a range preference rooms min 2 (weight 2) unless they state the bedrooms, plus keywords shkoll/kopsht/park/lojra (weight 1).
  - good value / investment: listing_types Shitet unless they talk about renting, sort price_per_m2_asc, plus a keywords preference for investim, rendiment, qira e garantuar (weight 1). Price per m² only means something for sales; never sort rents by it.
  - a number as a wish ("mundësisht kat i lartë", "jo shumë i shtrenjtë", "e lirë dhe e bukur"): a range preference, e.g. {{kind: range, field: floor, min: 5, max: -1}}, or {{kind: range, field: price, min: -1, max: <a sensible ceiling in EUR>}} next to the "looks good" preferences, with sort best_match. Use hard limits only when they are clearly required.
- Every preference needs a reason_sq the agent will see on matching cards; keep it short and concrete ("E rinovuar ose e re", "Afër qendrës").
- Use the dedicated fields first. Other must-have things a listing's text could mention go into keyword_groups, one group per idea, \
each with several word forms. Examples: sea -> ["deti", "detit", "bregdet", "plazh", "vija e pare"]; garden -> ["kopsht", "oborr", "lulishte"]; \
pool -> ["pishin", "pishina", "pool"]; near a school -> ["shkoll", "shkolla", "shkolles"].
- Never invent hard limits the question didn't ask for. If something can't be expressed at all, say so in not_supported_sq.
- More request types and how to express them:
  - Floor: "jo përdhe" / "jo kati i parë" -> a floor range with min 1 (ground floor is 0, the first floor above it is 1); "kat i lartë" -> floor min 5 (a range preference if it's only a wish); "kat i ulët" -> floor max 2; "kati i fundit" / "penthouse" -> keyword_groups ["kati i fundit", "kat i fundit", "penthouse", "papafingo"]. Many listings only state the floor in their text; it is read from there, so floor filters are reliable.
  - Dates: "postuar sot", "të rejat", "këtë javë" -> posted_within_days. "Më të fundit" alone is just sort newest.
  - Exclusions: "jo në X" / "përveç X" -> exclude_places; "pa papafingo", "jo bodrum" -> exclude_keywords.
  - A phone number or seller name ("njoftimet e 069 123 4567", "çfarë ka postuar Bushi") -> keyword_groups with the digits written together without spaces or + (e.g. ["0691234567"]) or the name.
  - Building/legal/extra things go into keyword_groups: heating ["ngrohje", "kaldaja", "ngrohje qendrore"], air conditioning ["kondicioner", "klime", "kondicionim"], ownership papers ["certifikat", "hipotek", "dokumentacion"], pets ["kafsh", "qen", "mace"], students ["student", "studente"], sea view ["pamje nga deti", "pamje deti", "pamje te detit"], new building ["pallat i ri", "ndertim i ri"], duplex ["duplex", "dupleks"]. When it is a wish rather than a must, use a keywords preference instead.
  - "Lirë" / "e lirë" with no number -> sort price_asc (and the price_per_m2 version for sales when they talk about value).
  - Statistics or market questions ("sa kushton mesatarisht një 2+1 në Blloku?", "sa njoftime ka në Durrës?"): set the filters for that group; the results page shows the count, the price range, the median price and the median €/m². Say so in understood_sq.
  - Comparisons between groups ("Tirana apo Durrësi më lirë?"): search the first group only and explain in not_supported_sq that the agent can ask again for the other.
  - Things the data can't know (distance in minutes, price history, whether a listing is still online, exact GPS): say so in not_supported_sq and still search for the rest.
  - English, typos and missing accents are normal: read them the same way.
- understood_sq is shown to the agent above the results: one or two short, plain Albanian sentences saying what is required and \
how you read any vague wish (e.g. "Me qira në Tiranë. 'Duket bukur' e lexova si: e rinovuar ose e re, fjalë si modern/luks, shumë foto.")."""

#Function that asks Claude to turn the question into filters
#known_places: the cities in the database; known_areas: area/street names from the listings (Search.known_areas),
#so Claude spells them the way the listings do and knows which ones exist for wishes like "in the centre"
def question_to_filters(question, known_places, known_areas=()):
    key = api_key()
    if not key:
        raise AIError("AI nuk është lidhur ende: mungon çelësi i API-së.", status=503)

    places = ", ".join(known_places) if known_places else "(none yet)"
    areas = ", ".join(known_areas) if known_areas else "(none yet)"
    client = anthropic.Anthropic(api_key=key, timeout=TIMEOUT_SECONDS, max_retries=1) #A dead network fails in ~3 min, not ~6
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"KNOWN PLACES: {places}\nKNOWN AREAS (from the listings' addresses): {areas}\n\nQUESTION: {question}"}],
        )
    except anthropic.AuthenticationError:
        raise AIError("Çelësi i API-së nuk është i vlefshëm.", status=503)
    except anthropic.PermissionDeniedError:
        raise AIError("Çelësi i API-së nuk ka leje për këtë model.", status=503)
    except anthropic.RateLimitError:
        raise AIError("Shumë pyetje njëherësh. Provo përsëri pas një minute.", status=429)
    except anthropic.APIConnectionError:
        raise AIError("Nuk ka lidhje me internetin ose me shërbimin e AI.")
    except anthropic.APIStatusError as error:
        print(f"[red]Claude API error {error.status_code}: {error.message} (request {getattr(error, 'request_id', None)})[/red]")
        if error.status_code == 400 and "credit" in str(error.message).lower():
            raise AIError("Kreditet e API-së kanë mbaruar. Shto kredite te console.anthropic.com.", status=503)
        raise AIError("Shërbimi i AI ktheu një gabim. Provo përsëri.")

    usage = usage_of(response)
    model = getattr(response, "model", MODEL)
    if response.stop_reason == "refusal":
        raise AIError("AI nuk pranoi ta përpunojë këtë pyetje. Provo ta shkruash ndryshe.", status=422, usage=usage, model=model)
    if response.stop_reason == "max_tokens":
        raise AIError("Pyetja ishte shumë e gjatë për t'u përpunuar. Provo ta shkurtosh.", status=422, usage=usage, model=model)

    text = next((block.text for block in response.content if block.type == "text"), None)
    try:
        data = json.loads(text or "")
        filters = Filters.model_validate(expand_ranges(data["filters"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, ValidationError) as error:
        print(f"[red]Could not read Claude's filters: {error}\n{text}[/red]")
        raise AIError("AI ktheu një përgjigje që nuk u kuptua. Provo përsëri.", usage=usage, model=model)

    print(f"[cyan]AI search: {question!r} -> {filters.model_dump(exclude_defaults=True)} ({usage['cost_usd']:.4f} USD)[/cyan]")
    return {
        "filters": filters,
        "understood": data.get("understood_sq") or "",
        "not_supported": data.get("not_supported_sq"),
        "model": model,
        "usage": usage,
    }

#Function that reads the token counts off a response and prices them
def usage_of(response):
    u = response.usage
    input_tokens = getattr(u, "input_tokens", 0) or 0
    output_tokens = getattr(u, "output_tokens", 0) or 0
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    cost = (input_tokens * PRICE_INPUT + output_tokens * PRICE_OUTPUT
            + cache_read * PRICE_CACHE_READ + cache_write * PRICE_CACHE_WRITE) / 1_000_000
    return {"input_tokens": input_tokens + cache_read + cache_write, "output_tokens": output_tokens, "cost_usd": round(cost, 6)}

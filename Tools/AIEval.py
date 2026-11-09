#This file checks that the AI turns typical agent questions into the right filters. It calls the real Claude API, so it costs money
#(about 1-2 cents per question with Opus 5.5 at low effort; the full set of 39 is a few tens of cents). It needs the API key (see Scraper/AI.py).
#Run from the project root with: python3 -m Tools.AIEval   (Windows: py -3.14 -m Tools.AIEval), add a number to run only the first N
#Each case lists only what must be true; anything else the AI adds is allowed. Re-run it after changing the prompt, the schema or the model.
#Imports
import sys
from rich import print
from Scraper import AI
from Scraper.DB import connect_to_db, create_tables, get_known_places, get_listings
from Scraper.Derive import norm, with_derived
from Scraper.Search import known_areas

#Global Variables
#(question, {field: expected}). Lists must contain every expected value; numbers must match exactly.
#"keywords_any" is special: at least one keyword group must contain one of these words (accents and case ignored).
CASES = [
    ("Apartament 2+1 me qira në Tiranë deri në 600 €",
     {"listing_types": ["Jepet me qera"], "rooms_min": 2, "rooms_max": 2, "price_max": 600, "price_currency": "EUR", "places": ["Tiranë"]}),
    ("2+1 ne tirane me qera deri 600 euro", {"listing_types": ["Jepet me qera"], "rooms_min": 2, "price_max": 600}),
    ("të paktën 50 m2 në katin e 4", {"size_min": 50, "floor_min": 4, "floor_max": 4}),
    ("kati i katert, minimum 50 metra katrore", {"size_min": 50, "floor_min": 4, "floor_max": 4}),
    ("apartament në përdhe me oborr", {"floor_min": 0, "floor_max": 0, "keywords_any": ["oborr"]}),
    ("afër detit në Durrës, i mobiluar", {"places": ["Durrës"], "furnishing": ["E mobiluar"], "keywords_any": ["deti", "detit", "plazh"]}),
    ("vilë në shitje me pishinë", {"listing_types": ["Shitet"], "property_types": ["Vilë / Shtëpi private"], "keywords_any": ["pishin", "pishina", "pishine"]}),
    ("jo agjenci, me qira, nën 40.000 lekë", {"posted_by": ["Person privat"], "listing_types": ["Jepet me qera"], "price_max": 40000, "price_currency": "ALL"}),
    ("vetëm nga pronari, qira deri 50 mijë lek", {"posted_by": ["Person privat"], "price_max": 50000, "price_currency": "ALL"}),
    ("5 apartamentet më të lira për m² në shitje", {"listing_types": ["Shitet"], "sort": "price_per_m2_asc", "limit": 5}),
    ("garsoniere me qera", {"property_types": ["Garsoniere"], "listing_types": ["Jepet me qera"]}),
    ("tokë në shitje mbi 1000 m2", {"property_types": ["Tokë"], "listing_types": ["Shitet"], "size_min": 1000}),
    ("dyqan ose zyrë me qera në Tiranë", {"property_types": ["Ambient biznesi"], "listing_types": ["Jepet me qera"]}),
    ("apartament i ri me ashensor dhe parkim", {"features": ["Ashensor", "Parkim / Garazh"]}),
    ("me ballkon, i pamobiluar, 2 banjo", {"furnishing": ["E pamobiluar"], "baths_min": 2}),
    ("shitje deri 120 mijë euro, të paktën 3 dhoma gjumi", {"listing_types": ["Shitet"], "price_max": 120000, "rooms_min": 3}),
    ("rreth 100 m2", {}),  #Only checks that "around" gives a range; see the size check below
    ("me pamje nga deti, jo në katin e parë", {"keywords_any": ["pamje", "deti"]}),
    ("2 bedroom flat for rent in Tirana under 700 euros", {"listing_types": ["Jepet me qera"], "rooms_min": 2, "price_max": 700}),
    ("vetëm nga Gazeta Celësi, me foto", {"sites": ["gazetacelesi"], "has_photos": True}),
    #Vague wishes: they should become ranked preferences, not hard filters that throw listings away
    ("dua diçka që duket bukur, me qira në Tiranë",
     {"listing_types": ["Jepet me qera"], "sort": "best_match", "preference_kinds": ["condition"], "not_set": ["conditions"]}),
    ("apartament në qendër të Tiranës", {"places": ["Tiranë"], "words_anywhere": ["qend"], "not_set": ["keyword_groups"], "center_set": True}),
    ("2+1 me qira, mundësisht afër qendrës, deri 700 €", {"rooms_min": 2, "price_max": 700, "preference_kinds": ["near_center"]}),
    ("diçka në katin e 4", {"floor_min": 4, "floor_max": 4}),
    ("rreth 120 mijë euro dhe rreth 100 m2", {"price_target": 120000, "size_target": 100, "sort": "best_match"}),
    ("2+1 i ndritshëm dhe në zonë të qetë", {"rooms_min": 2, "words_anywhere": ["drit", "qet"], "not_set": ["keyword_groups"]}),
    ("diçka luksoze me pamje", {"sort": "best_match", "words_anywhere": ["luks", "lux", "pamje"]}),
    #Floors, dates, exclusions, contacts, extras, statistics and things the data can't answer
    ("apartament me qira, jo përdhe", {"listing_types": ["Jepet me qera"], "floor_min": 1}),
    ("apartament në katin e fundit", {"keywords_any": ["kati i fundit", "kat i fundit"]}),
    ("njoftimet e postuara sot në Durrës", {"posted_within_days": 0, "places": ["Durrës"]}),
    ("të rejat e kësaj jave me qira", {"posted_within_days": 7, "listing_types": ["Jepet me qera"]}),
    ("apartament në Tiranë, jo në Kombinat", {"places": ["Tiranë"], "exclude_places": ["Kombinat"]}),
    ("çfarë ka postuar 069 721 1116", {"keywords_any": ["0697211116"]}),
    ("me ngrohje qendrore dhe kondicioner", {"words_anywhere": ["ngrohj", "kondicion"]}),
    ("apartament me certifikatë pronësie, gati për kredi", {"words_anywhere": ["certifikat"]}),
    ("pranohen kafshë shtëpiake", {"words_anywhere": ["kafsh"]}),
    ("sa kushton mesatarisht një 2+1 në shitje në Tiranë?", {"listing_types": ["Shitet"], "rooms_min": 2, "places": ["Tiranë"]}),
    ("apartament 10 minuta në këmbë nga Sheshi Skënderbej", {"supported_note": True}),
    ("diçka e lirë me qira në Durrës", {"listing_types": ["Jepet me qera"], "places": ["Durrës"], "sort": "price_asc"}),
]

#Function that checks one answer, returns the list of problems (empty = pass)
def check(filters, expected, question, answer_note=None):
    f = filters.model_dump()
    problems = []
    for field, want in expected.items():
        if field == "preference_kinds":
            kinds = {p["kind"] for p in f["preferences"]}
            missing = [k for k in want if k not in kinds]
            if missing:
                problems.append(f"preferences {sorted(kinds)} lack kinds {missing}")
        elif field == "words_anywhere":
            words = {norm(w) for group in f["keyword_groups"] for w in group}
            words |= {norm(v) for p in f["preferences"] if p["kind"] in ("keywords", "near_center") for v in p["values"]}
            words |= {norm(v) for v in f["center_words"]}
            for w in want:
                if not any(x.startswith(norm(w)) or norm(w) in x for x in words):
                    problems.append(f"no keyword or preference word containing '{w}'")
        elif field == "not_set":
            for name in want:
                if f[name]:
                    problems.append(f"{name} should stay empty for a vague wish, got {f[name]}")
        elif field == "center_set":
            if f["center_max_km"] is None or not f["center_words"]:
                problems.append(f"centre should set center_max_km and center_words, got {f['center_max_km']} / {f['center_words']}")
        elif field == "supported_note":
            if not answer_note:
                problems.append("not_supported_sq should explain what can't be searched")
        elif field == "keywords_any":
            words = {norm(w) for group in f["keyword_groups"] for w in group}
            if not any(any(x.startswith(norm(w)) for x in words) for w in want):
                problems.append(f"keyword_groups {f['keyword_groups']} lacks any of {want}")
        elif isinstance(want, list):
            got = [str(v) for v in f[field]]
            missing = [w for w in want if w not in got]
            if missing:
                problems.append(f"{field} = {got}, missing {missing}")
        elif f[field] != want:
            problems.append(f"{field} = {f[field]!r}, expected {want!r}")
    if question.startswith("rreth 100"):
        if not (f["size_min"] and f["size_max"] and f["size_min"] < 100 < f["size_max"]):
            problems.append(f"'rreth 100 m2' should give a range around 100, got {f['size_min']}–{f['size_max']}")
    return problems

def main(limit=None):
    if not AI.is_configured():
        print("[red]No API key: set ANTHROPIC_API_KEY or put the key in Data/anthropic_api_key.txt[/red]")
        return
    connection = connect_to_db()
    create_tables(connection)
    places = get_known_places(connection)
    areas = known_areas(with_derived(get_listings(connection)))
    connection.close()

    passed, total_cost = 0, 0.0
    cases = CASES[:limit] if limit else CASES
    for question, expected in cases:
        try:
            answer = AI.question_to_filters(question, places, areas)
        except AI.AIError as error:
            print(f"[red]ERROR[/red] {question}\n   {error.message_sq}")
            continue
        total_cost += answer["usage"]["cost_usd"]
        problems = check(answer["filters"], expected, question, answer["not_supported"])
        if problems:
            print(f"[red]FAIL[/red] {question}\n   " + "\n   ".join(problems) + f"\n   understood: {answer['understood']}")
        else:
            passed += 1
            print(f"[green]PASS[/green] {question}  [dim]({answer['understood']})[/dim]")
    print(f"\n[bold]{passed}/{len(cases)} passed[/bold], cost ≈ {total_cost:.3f} USD")

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)

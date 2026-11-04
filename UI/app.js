// HomeSweeper UI — talks to the FastAPI server in Scraper/Server.py
// All filtering and sorting happens here in the browser, the server just hands over every listing.
// Every DOM node is built with textContent (never innerHTML) so scraped text can't inject HTML into the page.
// All text the user sees must be in Albanian (see CLAUDE.md). Code and comments stay in English.

const POLL_MS = 3000; // How often to check the scrape status while one is running
const TIME_ZONE = "Europe/Tirane"; // The database stores UTC, staff see Albanian time

// Rough EUR -> ALL rate. Only used to compare, filter and sort prices across the two currencies, never shown as a real price.
const EUR_TO_ALL = 100;

const SITE_NAMES = { merrjep: "MerrJep", gazetacelesi: "Gazeta Celësi", duashpi: "Duashpi" };
const MISSING = ""; // Facet key for "this listing doesn't have the field"
const MISSING_LABEL = "Pa të dhëna";

// Multi-select filters. Values are derived in enrich(). `all: true` means a listing must have every ticked value (features),
// otherwise it needs any one of them. A listing missing the field shows up under "Pa të dhëna".
const FACETS = [
    { id: "type", label: "Lloji", get: (l) => l.listing_type },
    { id: "ptype", label: "Prona", get: (l) => l._type },
    { id: "city", label: "Qyteti", get: (l) => l.city },
    { id: "cond", label: "Gjendja", get: (l) => l._condition },
    { id: "furn", label: "Mobilimi", get: (l) => l._furnishing },
    { id: "feat", label: "Karakteristikat", get: (l) => l._features, all: true },
    { id: "posted", label: "Postuar nga", get: (l) => l.posted_by },
    { id: "site", label: "Faqja", get: (l) => SITE_NAMES[l.site] || l.site },
];

// Range sliders. `log` sliders suit values that span from hundreds to millions (rent vs sale), `int` steps one by one.
// Money values are stored in EUR and shown in the currency picked on the price slider.
// `presets` are the fixed ranges shown as buttons, [lo, hi] in the stored unit (EUR for money), null = no limit.
// The free slider stays folded until "Diapazon i lirë" is turned on.
const RENT_PRESETS = [[null, 300], [300, 500], [500, 800], [800, 1200], [1200, null]];
const SALE_PRESETS = [[null, 50000], [50000, 100000], [100000, 150000], [150000, 250000], [250000, 500000], [500000, null]];
const RANGES = [
    { id: "price", label: "Çmimi", get: (l) => l._priceEur, scale: "log", money: true, presets: pricePresets },
    { id: "size", label: "Sipërfaqja", unit: "m²", get: (l) => l.size_m2, scale: "log",
        presets: () => [[null, 50], [50, 80], [80, 120], [120, 200], [200, null]] },
    { id: "perm2", label: "Çmimi për m²", get: (l) => l._perM2, scale: "log", money: true, perM2: true,
        presets: () => [[null, 1000], [1000, 1500], [1500, 2000], [2000, 3000], [3000, null]] },
    { id: "rooms", label: "Dhoma gjumi", get: (l) => l.rooms, scale: "int", presets: () => [[1, 1], [2, 2], [3, 3], [4, null]] },
    { id: "baths", label: "Banjo", get: (l) => l._baths, scale: "int", presets: () => [[1, 1], [2, 2], [3, null]] },
    { id: "floor", label: "Kati", get: (l) => l._floor, scale: "int", presets: () => [[0, 0], [1, 3], [4, 7], [8, null]] },
];

// Rents and sale prices live on different scales, so the price buttons follow the "Lloji" filter
function pricePresets() {
    const types = filters.facets.type;
    const rentOnly = types.size === 1 && types.has("Jepet me qera");
    const saleOnly = types.size === 1 && types.has("Shitet");
    if (rentOnly) return RENT_PRESETS;
    if (saleOnly) return SALE_PRESETS;
    return [["Qira"], ...RENT_PRESETS, ["Shitje"], ...SALE_PRESETS]; // A one-item entry is a group heading
}
const LOG_STEPS = 1000;

// Sort dropdown value -> [listing field, direction]
const SORTS = {
    "newest": ["first_seen", "desc"],
    "oldest": ["first_seen", "asc"],
    "price-asc": ["_priceEur", "asc"],
    "price-desc": ["_priceEur", "desc"],
    "perm2-asc": ["_perM2", "asc"],
    "size-desc": ["size_m2", "desc"],
    "rooms-desc": ["rooms", "desc"],
    "title": ["title", "asc"],
};

const filters = {
    facets: Object.fromEntries(FACETS.map((f) => [f.id, new Set()])),
    ranges: Object.fromEntries(RANGES.map((r) => [r.id, { lo: null, hi: null }])), // null = no limit on that side
    includeMissing: false,
    currency: "EUR",
};

let allListings = [];
let pollTimer = null;
let lastFinished; // finished_at of the last scrape we've seen, a change means new listings to load
let autoState = null; // The "auto" part of /api/scrape/status
let scrapeState = "idle";

const $ = (id) => document.getElementById(id);
const dialog = $("preview");

// ---------- Small helpers ----------

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
}

function isEmpty(value) {
    return value === null || value === undefined || value === "";
}

// Albanian style by hand ("185.000", "72,5"), because browsers don't reliably ship Albanian locale data
function formatNumber(n) {
    const value = Math.round(Number(n) * 100) / 100;
    const [whole, decimals] = String(Math.abs(value)).split(".");
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return `${value < 0 ? "-" : ""}${grouped}${decimals ? `,${decimals}` : ""}`;
}

function formatPrice(price, currency) {
    if (isEmpty(price) || price <= 0) return null; // 0 is how sites mark "me marrëveshje"
    return `${formatNumber(price)} ${currency || ""}`.trim();
}

function formatSize(size) {
    return isEmpty(size) ? null : `${formatNumber(size)} m²`;
}

function joinParts(...parts) {
    const kept = parts.filter((p) => !isEmpty(p));
    return kept.length ? kept.join(" · ") : null;
}

// "2026-11-03 15:01:00" (UTC, from the database/server) -> Date
function parseUtc(timestamp) {
    return new Date(`${timestamp.replace(" ", "T")}Z`);
}

// "2026-11-03 15:01:00" (UTC, from the database) -> "03.11.2026, 16:01" in Albanian time
function localTime(timestamp) {
    if (isEmpty(timestamp)) return null;
    const date = parseUtc(timestamp);
    if (isNaN(date)) return timestamp;
    // Only take the numbers from Intl (converted to Tirana time) and lay them out ourselves, same reason as formatNumber
    const parts = Object.fromEntries(
        new Intl.DateTimeFormat("en-GB", {
            timeZone: TIME_ZONE,
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
            hourCycle: "h23",
        }).formatToParts(date).map((p) => [p.type, p.value]),
    );
    return `${parts.day}.${parts.month}.${parts.year}, ${parts.hour}:${parts.minute}`;
}

function debounce(fn, ms) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), ms);
    };
}

function image(src, className) {
    const img = el("img", className);
    img.src = src;
    img.loading = "lazy";
    img.alt = "";
    img.referrerPolicy = "no-referrer";
    return img;
}

function externalLink(className, text, href) {
    const link = el("a", className, text);
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
}

// ---------- Loading data ----------

async function loadListings() {
    const response = await fetch("/api/listings");
    allListings = (await response.json()).map(enrich);
    computeBounds();
    FACETS.forEach(fillFacetOptions);
    RANGES.forEach((r) => syncRange(r));
    render();
}

// ---------- Deriving filter fields ----------
// Each site words things its own way and some fields only live inside the raw tags, so the UI works out one shared
// value per filter here. Nothing is written back to the database.

// Lowercase without accents, so "Tokë", "TOKE" and "toke" all match /toke/
function norm(text) {
    return String(text ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

// First match wins, so the more specific kinds come first
const PROPERTY_TYPES = [
    ["Ambient biznesi", /ambient|ambjent|biznes|dyqan|\bzyr[ae]|magazin|\blokal|hapesire|kapanon/],
    ["Tokë", /\btoke\b|\btruall|\bterren/],
    ["Garsoniere", /garsonier|garsionier|gasonier|studio|papafingo/],
    ["Vilë / Shtëpi private", /\bvil[ae]\b|pjese vile|shtepi private|shtepi \d+ kat|shtepi me oborr|\d+ katesh/],
    ["Apartament", /apartament|aprtament|apartment|\d\s*\+\s*\d|duplex|dupleks|penthouse|\bpenthause/],
];

// Gazeta's and Duashpi's own category comes first (it's structured). MerrJep has none, so its title decides.
function propertyType(l) {
    const category = norm((l.attributes || {})["Kategoria"]);
    for (const text of [category, norm(l.title)]) {
        const hit = PROPERTY_TYPES.find(([, pattern]) => pattern.test(text));
        if (hit) return hit[0];
    }
    return null;
}

// MerrJep says "I ri" / "I përdorur - Si i ri", Gazeta "E re" / "Ne gjendje te mire", Duashpi only has tags
function conditionOf(l, tags) {
    const c = norm(l.condition);
    if (/si i ri|si e re/.test(c)) return "Si e re";
    if (/rikonstru|rinovu/.test(c)) return "E rinovuar";
    if (/ndertim/.test(c)) return "Në ndërtim";
    if (/gjendje te mire|perdorur/.test(c)) return "Në gjendje të mirë";
    if (/^(i ri|e re)$/.test(c.trim())) return "E re";
    if (c) return l.condition;
    if (/rinovuar|rikonstruktuar/.test(tags)) return "E rinovuar";
    if (/ndertese e re/.test(tags)) return "E re";
    return null;
}

// Gazeta "Mobilimi", MerrJep "Për vendbanimin: …E mobiluar", Duashpi "Detaje të tjera: E pa mobiluar, …"
function furnishingOf(tags) {
    if (/pjeserisht e mobiluar/.test(tags)) return "Pjesërisht e mobiluar";
    if (/pa ?mobiluar/.test(tags)) return "E pamobiluar";
    if (/mobiluar/.test(tags)) return "E mobiluar";
    return null;
}

function featuresOf(tags) {
    const features = [];
    if (/ashensor/.test(tags) && !/ashensor: jo/.test(tags)) features.push("Ashensor");
    if (/parkim|garazh/.test(tags) && !/parkim[^:|]*: 0\b/.test(tags)) features.push("Parkim / Garazh");
    if (/ballkon|terrac|tarrac|veranda/.test(tags)) features.push("Ballkon / Tarracë");
    return features;
}

function firstInt(value) {
    if (isEmpty(value)) return null;
    if (/perdhe/.test(norm(value))) return 0; // Ground floor
    const n = parseInt(String(value).match(/-?\d+/)?.[0], 10);
    return isNaN(n) ? null : n;
}

// Adds the _fields the filters use. The raw columns stay as scraped.
function enrich(l) {
    const a = l.attributes || {};
    const tags = norm(Object.entries(a).map(([k, v]) => `${k}: ${v}`).join(" | "));
    l._type = propertyType(l);
    l._condition = conditionOf(l, tags);
    l._furnishing = furnishingOf(tags);
    l._features = featuresOf(tags);
    l._floor = firstInt(a["Kati"] ?? a["Kati (Numri i kateve)"]);
    l._baths = firstInt(a["Banjo"] ?? a["Tualete"]);
    l._priceEur = l.price > 0 ? (l.currency === "ALL" ? l.price / EUR_TO_ALL : l.price) : null; // 0 means "me marrëveshje"
    l._perM2 = l._priceEur && l.size_m2 > 0 ? l._priceEur / l.size_m2 : null;
    l._haystack = [l.title, l.description, l.address, l.city, l.phone, l.seller, l.upid, ...Object.entries(a).flat()]
        .join(" ")
        .toLowerCase();
    return l;
}

// ---------- Filtering and sorting ----------

function facetKeys(f, l) {
    const value = f.get(l);
    if (Array.isArray(value)) return value;
    return isEmpty(value) ? [MISSING] : [String(value)];
}

const labelOf = (key) => (key === MISSING ? MISSING_LABEL : key);

// Does a listing pass every filter? `skipFacet` leaves one facet out, to count what each of its options would give.
function matches(l, skipFacet) {
    for (const f of FACETS) {
        const wanted = filters.facets[f.id];
        if (f.id === skipFacet || !wanted.size) continue;
        const keys = facetKeys(f, l);
        const ok = f.all ? [...wanted].every((k) => keys.includes(k)) : keys.some((k) => wanted.has(k));
        if (!ok) return false;
    }
    for (const r of RANGES) {
        const { lo, hi } = filters.ranges[r.id];
        if (lo === null && hi === null) continue;
        const v = r.get(l);
        if (typeof v !== "number" || !isFinite(v)) {
            if (filters.includeMissing) continue;
            return false;
        }
        if (lo !== null && v < lo - 1e-9) return false;
        if (hi !== null && v > hi + 1e-9) return false;
    }
    const search = $("f-search").value.trim().toLowerCase();
    return !search || l._haystack.includes(search);
}

function filteredListings() {
    return allListings.filter((l) => matches(l));
}

// ---------- Multi-select filters ----------

function buildFacet(f) {
    const root = el("details", "ms");
    const summary = el("summary");
    f.valueEl = el("span", "ms-value");
    summary.append(el("span", "ms-label", f.label), f.valueEl);
    f.panel = el("div", "ms-panel");
    root.append(summary, f.panel);
    root.addEventListener("toggle", () => {
        if (root.open) FACETS.forEach((o) => o !== f && (o.root.open = false)); // One open at a time
    });
    f.root = root;
    f.options = new Map();
    return root;
}

// Rebuild the checkboxes from the loaded listings, keeping what's ticked
function fillFacetOptions(f) {
    const keys = new Set();
    allListings.forEach((l) => facetKeys(f, l).forEach((k) => keys.add(k)));
    const sorted = [...keys].sort((a, b) => (a === MISSING ? 1 : b === MISSING ? -1 : a.localeCompare(b, "sq")));
    const wanted = filters.facets[f.id];
    [...wanted].forEach((k) => keys.has(k) || wanted.delete(k));

    const head = el("div", "ms-head");
    const clear = el("button", "ms-clear", "Pastro");
    clear.type = "button";
    clear.addEventListener("click", () => {
        wanted.clear();
        syncFacet(f);
        render();
    });
    head.append(el("span", "muted", f.all ? "Duhet t'i ketë të gjitha" : "Zgjidh një ose më shumë"), clear);

    const list = el("div", "ms-list");
    f.options = new Map();
    sorted.forEach((k) => {
        const row = el("label", "ms-opt");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.addEventListener("change", () => {
            box.checked ? wanted.add(k) : wanted.delete(k);
            syncFacet(f);
            render();
        });
        const count = el("span", "ms-count");
        row.append(box, el("span", `ms-name${k === MISSING ? " muted" : ""}`, labelOf(k)), count);
        f.options.set(k, { box, count, row });
        list.append(row);
    });
    if (!sorted.length) list.append(el("span", "muted", "Ende pa të dhëna"));
    f.panel.replaceChildren(head, list);
    syncFacet(f);
}

function syncFacet(f) {
    const wanted = filters.facets[f.id];
    f.valueEl.textContent = !wanted.size ? (f.all ? "Çdo" : "Të gjitha")
        : wanted.size === 1 ? labelOf([...wanted][0])
        : `${wanted.size} të zgjedhura`;
    f.root.classList.toggle("active", wanted.size > 0);
    f.options.forEach(({ box }, k) => (box.checked = wanted.has(k)));
}

// How many listings each option would show, given every other filter
function updateFacetCounts() {
    FACETS.forEach((f) => {
        const counts = new Map();
        allListings.forEach((l) => {
            if (matches(l, f.id)) facetKeys(f, l).forEach((k) => counts.set(k, (counts.get(k) || 0) + 1));
        });
        f.options.forEach(({ count, row }, k) => {
            const n = counts.get(k) || 0;
            count.textContent = formatNumber(n);
            row.classList.toggle("ms-zero", n === 0 && !filters.facets[f.id].has(k));
        });
    });
}

// ---------- Range sliders ----------

const factor = (r) => (r.money && filters.currency === "ALL" ? EUR_TO_ALL : 1); // EUR -> shown currency
const posMin = (r) => (r.scale === "log" ? 0 : r.min);
const posMax = (r) => (r.scale === "log" ? LOG_STEPS : r.max);

function unitOf(r) {
    if (!r.money) return r.unit || "";
    const currency = filters.currency === "ALL" ? "Lekë" : "€";
    return r.perM2 ? `${currency}/m²` : currency;
}

// Keep two significant digits on the log sliders so they land on round numbers (12.000, 130.000…)
function niceRound(v) {
    if (v < 10) return Math.round(v * 10) / 10;
    const step = 10 ** (Math.floor(Math.log10(v)) - 1);
    return Math.round(v / step) * step;
}

const roundShown = (v) => (Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 10) / 10);

// Slider position -> value in the shown unit
function posToValue(r, pos) {
    if (r.scale !== "log") return pos;
    const f = factor(r);
    const lo = Math.log(r.min * f), hi = Math.log(r.max * f);
    return niceRound(Math.exp(lo + ((hi - lo) * pos) / LOG_STEPS));
}

function valueToPos(r, shown) {
    if (r.scale !== "log") return Math.min(r.max, Math.max(r.min, Math.round(shown)));
    const f = factor(r);
    const lo = Math.log(r.min * f), hi = Math.log(r.max * f);
    if (hi === lo) return 0;
    const t = (Math.log(Math.max(shown, 1e-9)) - lo) / (hi - lo);
    return Math.round(Math.min(1, Math.max(0, t)) * LOG_STEPS);
}

// Slider ends come from the data: the smallest and largest value any listing has
function computeBounds() {
    RANGES.forEach((r) => {
        const values = allListings.map(r.get).filter((v) => typeof v === "number" && isFinite(v) && (r.scale !== "log" || v > 0));
        r.count = values.length;
        r.min = values.length ? values.reduce((a, b) => Math.min(a, b)) : 0;
        r.max = values.length ? values.reduce((a, b) => Math.max(a, b)) : 0;
        if (r.scale === "int") {
            r.min = Math.floor(r.min);
            r.max = Math.ceil(r.max);
        }
    });
}

function buildRange(r) {
    const wrap = el("div", "range");
    const head = el("div", "range-head");
    head.append(el("span", "range-label", r.label));
    if (r.money && !r.perM2) {
        const currency = el("select", "range-currency");
        currency.id = "f-currency";
        currency.title = "Monedha e filtrit. Çmimet në lekë dhe euro krahasohen me kursin 1 € ≈ " + EUR_TO_ALL + " lekë.";
        currency.setAttribute("aria-label", "Monedha");
        currency.append(new Option("€", "EUR"), new Option("Lekë", "ALL"));
        currency.addEventListener("change", () => {
            filters.currency = currency.value;
            RANGES.filter((x) => x.money).forEach((x) => syncRange(x));
            renderActive();
        });
        head.append(currency);
    }
    r.coverEl = el("span", "range-cover");
    r.coverEl.title = "Sa njoftime e kanë këtë të dhënë";
    head.append(r.coverEl);

    r.presetsEl = el("div", "range-presets");
    r.customBtn = el("button", "preset preset-custom", "Diapazon i lirë");
    r.customBtn.type = "button";
    r.customBtn.title = "Zgjidh vetë minimumin dhe maksimumin me rrëshqitës";
    r.customBtn.addEventListener("click", () => {
        r.custom = !r.custom;
        const state = filters.ranges[r.id];
        const onPreset = r.presets().some((p) => p.length === 2 && sameLimit(state.lo, p[0]) && sameLimit(state.hi, p[1]));
        if (!r.custom && !onPreset && (state.lo !== null || state.hi !== null)) {
            state.lo = state.hi = null; // Turning the slider off drops the hand-picked range
            syncRange(r);
            render();
            return;
        }
        syncRange(r);
    });

    const slider = el("div", "range-slider");
    const track = el("div", "range-track");
    r.fillEl = el("div", "range-fill");
    track.append(r.fillEl);
    r.loIn = sliderInput(r, "lo");
    r.hiIn = sliderInput(r, "hi");
    slider.append(track, r.loIn, r.hiIn);

    const inputs = el("div", "range-inputs");
    r.loText = rangeText(r, "lo");
    r.hiText = rangeText(r, "hi");
    r.unitEl = el("span", "range-unit");
    inputs.append(r.loText, el("span", "muted", "–"), r.hiText, r.unitEl);

    r.customEl = el("div", "range-custom");
    r.customEl.append(slider, inputs);
    wrap.append(head, r.presetsEl, r.customEl);
    r.wrap = wrap;
    return wrap;
}

const sameLimit = (a, b) => (a === null || b === null ? a === b : Math.abs(a - b) < 1e-6);

function presetLabel(r, [lo, hi]) {
    const f = factor(r);
    const show = (v) => formatNumber(roundShown(v * f));
    if (r.id === "floor" && lo === 0 && hi === 0) return "Përdhe";
    const unit = r.scale === "int" ? "" : ` ${unitOf(r)}`;
    if (lo !== null && hi !== null) return lo === hi ? show(lo) : `${show(lo)}–${show(hi)}${unit}`;
    return lo !== null ? `${show(lo)}+${unit}` : `deri ${show(hi)}${unit}`;
}

// Fixed-range buttons: one click sets the range, a second click on the same one clears it
function renderPresets(r) {
    const state = filters.ranges[r.id];
    const buttons = r.presets().map((p) => {
        if (p.length === 1) return el("span", "preset-group", p[0]);
        const [lo, hi] = p;
        const on = sameLimit(state.lo, lo) && sameLimit(state.hi, hi);
        const b = el("button", `preset${on ? " on" : ""}`, presetLabel(r, p));
        b.type = "button";
        b.setAttribute("aria-pressed", String(on));
        b.addEventListener("click", () => {
            state.lo = on ? null : lo;
            state.hi = on ? null : hi;
            syncRange(r);
            render();
        });
        return b;
    });
    r.presetsEl.replaceChildren(...buttons, r.customBtn);
    return r.presets().some((p) => p.length === 2 && sameLimit(state.lo, p[0]) && sameLimit(state.hi, p[1]));
}

function sliderInput(r, side) {
    const input = document.createElement("input");
    input.type = "range";
    input.step = "1";
    input.setAttribute("aria-label", `${r.label} ${side === "lo" ? "minimumi" : "maksimumi"}`);
    input.addEventListener("input", () => {
        let lo = Number(r.loIn.value), hi = Number(r.hiIn.value);
        if (lo > hi) { // Thumbs can't cross
            if (side === "lo") r.loIn.value = lo = hi;
            else r.hiIn.value = hi = lo;
        }
        const state = filters.ranges[r.id];
        state.lo = lo <= posMin(r) ? null : posToValue(r, lo) / factor(r);
        state.hi = hi >= posMax(r) ? null : posToValue(r, hi) / factor(r);
        syncRange(r, false);
        renderSoon();
    });
    return input;
}

// Typed values use Albanian style: "185.000" or "72,5"
function rangeText(r, side) {
    const input = el("input", "range-num");
    input.type = "text";
    input.inputMode = "decimal";
    input.setAttribute("aria-label", `${r.label} ${side === "lo" ? "minimumi" : "maksimumi"}`);
    input.addEventListener("change", () => {
        const raw = input.value.trim().replace(/\./g, "").replace(",", ".").replace(/[^\d.]/g, "");
        const n = raw === "" ? null : Number(raw);
        const state = filters.ranges[r.id];
        state[side] = n === null || isNaN(n) ? null : n / factor(r);
        if (state.lo !== null && state.hi !== null && state.lo > state.hi) [state.lo, state.hi] = [state.hi, state.lo];
        syncRange(r);
        render();
    });
    return input;
}

// Put the thumbs, the blue fill, the typed values and the unit in line with the filter state
function syncRange(r, moveThumbs = true) {
    const state = filters.ranges[r.id];
    const f = factor(r);
    const min = posMin(r), max = posMax(r);
    [r.loIn, r.hiIn].forEach((i) => {
        i.min = min;
        i.max = max;
        i.disabled = !r.count;
    });
    if (moveThumbs) {
        r.loIn.value = state.lo === null ? min : valueToPos(r, state.lo * f);
        r.hiIn.value = state.hi === null ? max : valueToPos(r, state.hi * f);
    }
    const span = max - min || 1;
    const a = ((r.loIn.value - min) / span) * 100;
    const b = ((r.hiIn.value - min) / span) * 100;
    r.fillEl.style.left = `${a}%`;
    r.fillEl.style.right = `${100 - b}%`;
    r.loIn.style.zIndex = a > 95 ? 3 : 1; // When both thumbs sit at the right end, the min one must stay grabbable

    r.loText.value = state.lo === null ? "" : formatNumber(roundShown(state.lo * f));
    r.hiText.value = state.hi === null ? "" : formatNumber(roundShown(state.hi * f));
    r.loText.placeholder = r.count ? formatNumber(roundShown(r.min * f)) : "Min";
    r.hiText.placeholder = r.count ? formatNumber(roundShown(r.max * f)) : "Maks";
    r.unitEl.textContent = unitOf(r);
    r.coverEl.textContent = `${formatNumber(r.count || 0)} nga ${formatNumber(allListings.length)}`;
    const active = state.lo !== null || state.hi !== null;
    r.wrap.classList.toggle("active", active);

    // A range set by hand that matches no button keeps the slider open, so you can see what's applied
    const onPreset = renderPresets(r);
    if (active && !onPreset) r.custom = true;
    r.customEl.hidden = !r.custom;
    r.customBtn.classList.toggle("on", Boolean(r.custom));
    r.customBtn.setAttribute("aria-pressed", String(Boolean(r.custom)));
}

function describeRange(r) {
    const { lo, hi } = filters.ranges[r.id];
    const f = factor(r);
    const show = (v) => formatNumber(roundShown(v * f));
    if (r.id === "floor" && lo === 0 && hi === 0) return "Përdhe";
    const text = lo !== null && hi !== null ? (sameLimit(lo, hi) ? show(lo) : `${show(lo)}–${show(hi)}`)
        : lo !== null ? `≥ ${show(lo)}` : `≤ ${show(hi)}`;
    return `${text} ${unitOf(r)}`.trim();
}

// ---------- Active filter chips ----------

function renderActive() {
    const chips = [];
    const search = $("f-search").value.trim();
    if (search) chips.push([`Kërko: “${search}”`, () => ($("f-search").value = "")]);
    FACETS.forEach((f) => filters.facets[f.id].forEach((k) => chips.push([`${f.label}: ${labelOf(k)}`, () => {
        filters.facets[f.id].delete(k);
        syncFacet(f);
    }])));
    RANGES.forEach((r) => {
        const state = filters.ranges[r.id];
        if (state.lo === null && state.hi === null) return;
        chips.push([`${r.label}: ${describeRange(r)}`, () => {
            state.lo = state.hi = null;
            syncRange(r);
        }]);
    });

    const bar = $("active-filters");
    bar.hidden = !chips.length;
    bar.replaceChildren(...chips.map(([text, remove]) => {
        const chip = el("button", "fchip", `${text} ✕`);
        chip.type = "button";
        chip.title = "Hiqe këtë filtër";
        chip.addEventListener("click", () => {
            remove();
            render();
        });
        return chip;
    }));
    if (chips.length > 1) {
        const all = el("button", "fchip fchip-clear", "Pastro të gjitha");
        all.type = "button";
        all.addEventListener("click", clearFilters);
        bar.append(all);
    }
    $("filters-count").textContent = chips.length ? String(chips.length) : "";
}

function clearFilters() {
    $("f-search").value = "";
    $("f-sort").value = "newest";
    $("f-missing").checked = filters.includeMissing = false;
    FACETS.forEach((f) => {
        filters.facets[f.id].clear();
        syncFacet(f);
    });
    RANGES.forEach((r) => {
        filters.ranges[r.id] = { lo: null, hi: null };
        syncRange(r);
    });
    render();
}

function sortListings(listings) {
    const [key, direction] = SORTS[$("f-sort").value] || SORTS.newest;
    const dir = direction === "asc" ? 1 : -1;
    return listings.sort((a, b) => {
        const x = a[key], y = b[key];
        if (isEmpty(x) && isEmpty(y)) return 0;
        if (isEmpty(x)) return 1; // Empty values always go last
        if (isEmpty(y)) return -1;
        if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
        return String(x).localeCompare(String(y), "sq") * dir;
    });
}

// ---------- Drawing the cards ----------

function render() {
    const listings = sortListings(filteredListings());
    $("cards").replaceChildren(...listings.map(buildCard));
    updateFacetCounts();
    renderActive();
    renderPresets(RANGES.find((r) => r.id === "price")); // Its buttons follow the "Lloji" filter

    $("empty").hidden = allListings.length > 0;
    $("no-match").hidden = allListings.length === 0 || listings.length > 0;
    $("stat-total").textContent = formatNumber(allListings.length);
    $("stat-shown").textContent = formatNumber(listings.length);
    $("stat-phone").textContent = formatNumber(allListings.filter((l) => l.phone).length);

    requestAnimationFrame(() => markOverflow($("cards"))); // Measure once the cards are laid out
}

// One labelled box of info. Text is cut after `lines` lines; if that hides anything the box opens a preview.
function box(label, value, { lines = 1, wide = false, extraClass = "", previewTitle = label } = {}) {
    const b = el("div", ["box", wide && "box-wide", extraClass].filter(Boolean).join(" "));
    if (label) b.append(el("span", "box-label", label));

    const empty = isEmpty(value);
    const v = el("span", "box-value clamp", empty ? "–" : String(value));
    v.style.setProperty("--lines", lines);
    if (empty) v.classList.add("muted");
    b.append(v);

    if (!empty) b.preview = () => openTextPreview(previewTitle, String(value));
    return b;
}

// Every tag the site showed (size, rooms, city, floor…), kept as a backup of the columns above
function tagsBox(attributes) {
    const entries = Object.entries(attributes || {});
    const b = el("div", "box box-wide");
    b.append(el("span", "box-label", `Të gjitha etiketat (${entries.length})`));

    const list = el("div", "clamp-block");
    if (entries.length) {
        entries.forEach(([key, value]) => list.append(attrRow(key, value, true)));
        b.preview = () => openPreview("Të gjitha etiketat", buildTagsTable(entries));
    } else {
        list.append(el("span", "muted", "–"));
    }
    b.append(list);
    return b;
}

function attrRow(key, value, truncate) {
    const row = el("div", "attr-row");
    row.append(
        el("span", `attr-key${truncate ? " ellipsis" : ""}`, key),
        el("span", `attr-val${truncate ? " ellipsis" : ""}`, isEmpty(value) ? "–" : value),
    );
    return row;
}

function buildTagsTable(entries) {
    const table = el("div", "preview-attrs");
    entries.forEach(([key, value]) => table.append(attrRow(key, value, false)));
    return table;
}

// Line icons, drawn as inline SVG so they follow the text colour. Paths are fixed constants, never scraped data.
const ICONS = {
    area: "M4 9V4h5M20 15v5h-5M4 4l6 6M20 20l-6-6",
    bed: "M3 18v-7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v7M3 14h18M7 9V6h10v3",
    bath: "M4 12h16v3a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4zM6 12V6a2 2 0 0 1 4 0M7 19l-1 2M17 19l1 2",
    floor: "M4 20h4v-4h4v-4h4V8h4",
    pin: "M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11zM12 7.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
    phone: "M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2",
    camera: "M4 8h3l2-3h6l2 3h3v11H4zM12 10a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z",
    external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
    image: "M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M15 9.5a1.5 1.5 0 1 0 0 .01",
};

function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", ICONS[name]);
    svg.append(path);
    return svg;
}

function withIcon(name, className, text) {
    const node = el("span", className);
    node.append(icon(name), el("span", null, text));
    return node;
}

// Sale / rent and the source site, laid over the photo
function photoBadges(l) {
    const badges = el("div", "photo-badges");
    if (l.listing_type) {
        const kind = norm(l.listing_type).includes("qera") ? "rent" : "sale";
        badges.append(el("span", `badge-type badge-${kind}`, l.listing_type));
    }
    badges.append(el("span", "badge-site", SITE_NAMES[l.site] || l.site));
    return badges;
}

function buildPhoto(l) {
    const photo = el("div", "card-photo");
    const images = l.images || [];
    photo.append(photoBadges(l));
    if (!images.length) {
        photo.append(withIcon("image", "photo-empty", "Pa foto"));
        return photo;
    }

    const img = image(images[0]);
    img.addEventListener("error", () => img.replaceWith(withIcon("image", "photo-empty", "Fotoja nuk u ngarkua")));
    photo.prepend(img);
    photo.append(withIcon("camera", "photo-count", String(images.length)));
    photo.classList.add("clickable");
    photo.title = "Shiko fotot";
    photo.addEventListener("click", () => openGallery(l, 0));
    return photo;
}

function buildCard(l) {
    const card = el("article", "card");
    const body = el("div", "card-body");

    // Price, then the title and where it is
    const head = el("div", "card-head");
    const priceRow = el("div", "card-price-row");
    priceRow.append(el("div", "card-price", formatPrice(l.price, l.currency) || "Çmimi me marrëveshje"));
    const priceNote = joinParts(
        l.currency === "ALL" && l._priceEur ? `≈ ${formatNumber(Math.round(l._priceEur))} €` : null,
        l._perM2 ? `${formatNumber(Math.round(l._perM2))} €/m²` : null,
    );
    if (priceNote) priceRow.append(el("div", "card-price-note", priceNote));
    const where = box(null, joinParts(l.city, l.address), { extraClass: "box-plain box-loc", previewTitle: "Adresa" });
    where.prepend(icon("pin"));
    head.append(priceRow, box(null, l.title || "(pa titull)", { lines: 2, extraClass: "box-plain box-title", previewTitle: "Titulli" }), where);

    // The numbers agents compare first
    const specs = el("div", "specs");
    [
        ["area", formatSize(l.size_m2)],
        ["bed", isEmpty(l.rooms) ? null : `${l.rooms} dhoma`],
        ["bath", isEmpty(l._baths) ? null : `${l._baths} banjo`],
        ["floor", isEmpty(l._floor) ? null : l._floor === 0 ? "Përdhe" : `Kati ${l._floor}`],
    ].filter(([, text]) => text).forEach(([name, text]) => specs.append(withIcon(name, "spec", text)));
    if (!specs.children.length) specs.append(el("span", "spec muted", "Pa të dhëna për përmasat"));

    // Quick chips: kind of property, condition, furnishing, who posted it (sale/rent and the site sit on the photo)
    const chips = el("div", "chips");
    [l._type, l._condition, l._furnishing, l.posted_by]
        .filter((c) => !isEmpty(c))
        .forEach((c) => chips.append(el("span", "chip", c)));

    // Every other scraped field gets its own box
    const facts = el("div", "facts");
    facts.append(
        box("Telefoni", l.phone),
        box("Shitësi", l.seller),
        box("Publikuar", joinParts(l.published_date, l.published_time)),
        box("Parë për herë të parë", localTime(l.first_seen)),
        box("Parë për herë të fundit", localTime(l.last_seen)),
        box("Përshkrimi", l.description, { lines: 4, wide: true }),
        tagsBox(l.attributes),
    );

    // Footer: ID, link to the original listing, full details
    const footer = el("div", "card-footer");
    footer.append(el("span", "card-id", `${l.site || ""} #${l.upid}`));
    const links = el("div", "card-links");
    const tel = (l.phone || "").split(/[,;/]/)[0].replace(/[^\d+]/g, "");
    if (tel) {
        const call = el("a", "btn btn-small btn-call");
        call.href = `tel:${tel}`;
        call.title = `Telefono ${tel}`;
        call.append(icon("phone"), el("span", null, "Telefono"));
        links.append(call);
    }
    if (l.url) {
        const open = externalLink("btn btn-small", null, l.url);
        open.append(el("span", null, "Hap në faqe"), icon("external"));
        links.append(open);
    }
    const details = el("button", "btn btn-small btn-primary", "Të gjitha detajet");
    details.addEventListener("click", () => openDetails(l));
    links.append(details);
    footer.append(links);

    body.append(head, specs, chips, facts, footer);
    card.append(buildPhoto(l), body);
    return card;
}

// Mark every box whose content is cut off, so it shows the ⤢ icon and opens a preview when clicked
function markOverflow(root) {
    root.querySelectorAll(".box").forEach((b) => {
        const value = b.querySelector(".clamp, .clamp-block");
        const cut = value && (value.scrollHeight > value.clientHeight + 1 || value.scrollWidth > value.clientWidth + 1);
        const cutRow = [...b.querySelectorAll(".ellipsis")].some((e) => e.scrollWidth > e.clientWidth + 1);
        const expandable = Boolean(b.preview) && (cut || cutRow);

        b.classList.toggle("expandable", expandable);
        if (expandable) {
            b.tabIndex = 0;
            b.setAttribute("role", "button");
            b.title = "Kliko për të parë të gjithë tekstin";
        } else {
            b.removeAttribute("tabindex");
            b.removeAttribute("role");
            b.removeAttribute("title");
        }
    });
}

// ---------- Previews ----------

function openPreview(title, content) {
    dialog.onkeydown = null; // Each preview sets its own keys (the gallery uses the arrow keys)
    $("preview-title").textContent = title;
    $("preview-body").replaceChildren(content);
    $("preview-body").scrollTop = 0;
    if (!dialog.open) dialog.showModal();
    document.body.classList.add("no-scroll");
}

function openTextPreview(title, text) {
    openPreview(title, el("div", "preview-text", text));
}

function openGallery(l, startIndex) {
    const images = l.images || [];
    if (!images.length) return;

    const gallery = el("div", "gallery");
    const stage = el("div", "gallery-stage");
    const main = el("img", "gallery-main");
    main.alt = "";
    main.referrerPolicy = "no-referrer";
    stage.append(main);

    const counter = el("div", "gallery-counter");
    const thumbs = el("div", "thumbs");
    let current = startIndex;

    function show(i) {
        current = (i + images.length) % images.length;
        main.src = images[current];
        counter.textContent = `${current + 1} nga ${images.length}`;
        [...thumbs.children].forEach((t, n) => t.classList.toggle("active", n === current));
        thumbs.children[current]?.scrollIntoView({ block: "nearest", inline: "nearest" });
    }

    if (images.length > 1) {
        const prev = el("button", "gallery-nav gallery-prev", "‹");
        const next = el("button", "gallery-nav gallery-next", "›");
        prev.setAttribute("aria-label", "Fotoja e mëparshme");
        next.setAttribute("aria-label", "Fotoja tjetër");
        prev.addEventListener("click", () => show(current - 1));
        next.addEventListener("click", () => show(current + 1));
        stage.append(prev, next);
    }

    images.forEach((src, i) => {
        const t = image(src);
        t.addEventListener("click", () => show(i));
        thumbs.append(t);
    });

    gallery.append(stage, counter, thumbs);
    openPreview(`Fotot — ${l.title || "njoftimi"}`, gallery);
    dialog.onkeydown = (e) => {
        if (e.key === "ArrowLeft") show(current - 1);
        if (e.key === "ArrowRight") show(current + 1);
    };
    show(startIndex);
}

// Everything we have on one listing, nothing cut off
function openDetails(l) {
    const wrap = el("div");

    const images = l.images || [];
    if (images.length) {
        const thumbs = el("div", "thumbs");
        images.forEach((src, i) => {
            const t = image(src);
            t.title = "Hap foton";
            t.addEventListener("click", () => openGallery(l, i));
            thumbs.append(t);
        });
        wrap.append(thumbs);
    }

    const rows = el("div", "detail-rows");
    [
        ["Çmimi", formatPrice(l.price, l.currency) || "Çmimi me marrëveshje"],
        ["Çmimi në euro (përafërsisht)", l._priceEur ? `${formatNumber(Math.round(l._priceEur))} €` : null],
        ["Çmimi për m²", l._perM2 ? `${formatNumber(Math.round(l._perM2))} €/m²` : null],
        ["Lloji", l.listing_type],
        ["Prona", l._type],
        ["Gjendja", l._condition],
        ["Mobilimi", l._furnishing],
        ["Karakteristikat", l._features.join(", ")],
        ["Sipërfaqja", formatSize(l.size_m2)],
        ["Dhoma", l.rooms],
        ["Banjo", l._baths],
        ["Kati", l._floor],
        ["Qyteti", l.city],
        ["Adresa", l.address],
        ["Postuar nga", l.posted_by],
        ["Telefoni", l.phone],
        ["Shitësi", l.seller],
        ["Publikuar", joinParts(l.published_date, l.published_time)],
        ["Faqja", l.site],
        ["ID e njoftimit", l.upid],
        ["Parë për herë të parë", localTime(l.first_seen)],
        ["Parë për herë të fundit", localTime(l.last_seen)],
    ].forEach(([label, value]) => {
        const b = box(label, value);
        b.querySelector(".box-value").classList.remove("clamp"); // Show everything here
        rows.append(b);
    });
    wrap.append(rows);

    const descSection = el("div", "preview-section");
    descSection.append(el("h3", null, "Përshkrimi"), el("div", `preview-text${l.description ? "" : " muted"}`, l.description || "–"));
    wrap.append(descSection);

    const entries = Object.entries(l.attributes || {});
    const tagSection = el("div", "preview-section");
    tagSection.append(el("h3", null, `Të gjitha etiketat (${entries.length})`), entries.length ? buildTagsTable(entries) : el("span", "muted", "–"));
    wrap.append(tagSection);

    if (l.url) {
        const linkSection = el("div", "preview-section");
        linkSection.append(externalLink("btn", "Hap njoftimin në faqe ↗", l.url));
        wrap.append(linkSection);
    }

    openPreview(l.title || "Njoftimi", wrap);
}

// ---------- Scraping ----------

// Polls while a scrape runs or auto mode is on, and reloads the listings whenever a scrape finishes
async function checkStatus() {
    clearTimeout(pollTimer);
    let status;
    try {
        status = await (await fetch("/api/scrape/status")).json();
    } catch {
        showOffline();
        pollTimer = setTimeout(checkStatus, POLL_MS); // Keep trying until the server is back
        return;
    }

    showStatus(status);
    showAuto(status.auto);
    showPreferences(status.preferences);

    if (lastFinished !== undefined && status.finished_at !== lastFinished) {
        loadListings(); // A turn just finished (or was paused), pick up what it saved
    }
    lastFinished = status.finished_at;

    if (status.state === "running" || status.auto.enabled) {
        pollTimer = setTimeout(checkStatus, POLL_MS);
    }
}

function showStatus(status) {
    scrapeState = status.state;
    const pill = $("status");
    const saved = status.result ? formatNumber(status.result.saved) : null;
    const labels = {
        idle: "Në pritje",
        running: status.pausing ? "Duke ndaluar… ruhen të gjeturat" : `Duke mbledhur · raundi ${(status.auto?.turns || 0) + 1}`,
        done: !status.result ? "Përfundoi"
            : status.result.paused ? `Në pauzë — ${saved} të reja u ruajtën`
            : `Raundi përfundoi — ${saved} të reja`,
        error: "Gabim",
    };
    pill.textContent = labels[status.state] || status.state;
    pill.className = `status status-${status.state}`;
    pill.title = status.state === "error" ? "Mbledhja dështoi. Shiko konsolën e serverit për detaje." : "";
    $("stat-last").textContent = localTime(status.finished_at)?.split(", ")[1] || "–";
}

function showOffline() {
    const pill = $("status");
    pill.textContent = "Serveri nuk përgjigjet";
    pill.className = "status status-error";
    pill.title = "Sigurohu që serveri është i ndezur.";
}

// ---------- Continuous scraping (play / pause) ----------

// One button: ▶ starts turn after turn, ⏸ pauses (a running turn stops early and still saves what it found)
function showAuto(auto) {
    autoState = auto;
    const btn = $("scrape-btn");
    btn.textContent = auto.enabled ? "⏸ Pauzë" : "▶ Nis mbledhjen";
    btn.title = auto.enabled
        ? "Ndalo mbledhjen. Njoftimet e gjetura deri tani ruhen."
        : "Mbledhja vazhdon raund pas raundi derisa ta ndalosh";
    btn.classList.toggle("btn-on", auto.enabled);
    if (auto.enabled && auto.interval_minutes) {
        $("auto-interval").value = String(auto.interval_minutes); // Show what the server is actually using
    }
    updateCountdown();
}

// "Raundi tjetër pas 4:32 · 120 të reja" — ticks every second from the next_run_at the server gave us
function updateCountdown() {
    const pill = $("auto-status");
    if (!autoState || !autoState.enabled) {
        pill.hidden = true;
        return;
    }
    pill.hidden = false;

    const total = `${formatNumber(autoState.new_listings || 0)} të reja`;
    if (scrapeState === "running") {
        pill.textContent = `Në punë · ${total}`;
    } else if (autoState.next_run_at) {
        const seconds = Math.max(0, Math.round((parseUtc(autoState.next_run_at) - Date.now()) / 1000));
        pill.textContent = seconds > 0 ? `Raundi tjetër pas ${formatDuration(seconds)} · ${total}` : `Po fillon raundi… · ${total}`;
    } else {
        pill.textContent = `Në pritje… · ${total}`;
    }
    pill.title = `Që kur u nis: ${formatNumber(autoState.turns || 0)} raunde, ${total}. ${$("auto-interval").selectedOptions[0].textContent} mes raundeve.`;
}

// 272 -> "4:32", 3725 -> "1:02:05"
function formatDuration(totalSeconds) {
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = String(totalSeconds % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

async function startAuto() {
    return fetch("/api/auto/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interval_minutes: Number($("auto-interval").value) }),
    });
}

async function toggleAuto() {
    const btn = $("scrape-btn");
    btn.disabled = true;
    try {
        const response = autoState && autoState.enabled
            ? await fetch("/api/auto/stop", { method: "POST" })
            : await startAuto();
        if (!response.ok) alert("Mbledhja nuk mund të nisej ose të ndalej. Provo përsëri.");
    } catch {
        alert("Serveri nuk përgjigjet. Sigurohu që është i ndezur.");
    } finally {
        btn.disabled = false;
        checkStatus();
    }
}

// Changing the interval while auto mode is on applies it straight away
async function changeAutoInterval() {
    if (!autoState || !autoState.enabled) return;
    try {
        await startAuto();
    } finally {
        checkStatus();
    }
}

// ---------- Scrape preferences ----------

// Show what the server is actually using (it keeps the preference in memory, so a restart resets it)
function showPreferences(preferences) {
    if (!preferences) return;
    $("pref-agency").value = preferences.skip_agencies ? "no-agency" : "all";
    markPreference();
}

// Highlight the preference pill while agency listings are being skipped
function markPreference() {
    $("pref-agency").classList.toggle("pref-on", $("pref-agency").value === "no-agency");
}

async function changePreferences() {
    const select = $("pref-agency");
    markPreference(); // Show the new look straight away, checkStatus() corrects it if the save failed
    select.disabled = true;
    try {
        const response = await fetch("/api/preferences", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ skip_agencies: select.value === "no-agency" }),
        });
        if (!response.ok) alert("Preferenca nuk mund të ruhej. Provo përsëri.");
        else if (scrapeState === "running") alert("Preferenca do të zbatohet në mbledhjen e radhës.");
    } catch {
        alert("Serveri nuk përgjigjet. Sigurohu që është i ndezur.");
    } finally {
        select.disabled = false;
        checkStatus();
    }
}

// ---------- Wiring ----------

// Clicking (or Enter/Space on) a cut-off box opens its preview
$("cards").addEventListener("click", (e) => {
    const b = e.target.closest(".box.expandable");
    if (b && b.preview) b.preview();
});
$("cards").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const b = e.target.closest(".box.expandable");
    if (b && b.preview) {
        e.preventDefault();
        b.preview();
    }
});

// Closing the preview: ✕ button, Esc (built into <dialog>) or clicking the dark backdrop
$("preview-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
});
dialog.addEventListener("close", () => {
    dialog.onkeydown = null;
    document.body.classList.remove("no-scroll");
    $("preview-body").replaceChildren();
});

const renderSoon = debounce(render, 150);
$("f-search").addEventListener("input", renderSoon);
$("f-sort").addEventListener("change", render);
$("f-missing").addEventListener("change", () => {
    filters.includeMissing = $("f-missing").checked;
    render();
});
$("f-clear").addEventListener("click", clearFilters);

// Phones: the filter panel folds away behind one button
$("filters-toggle").addEventListener("click", () => {
    const open = $("filters-panel").classList.toggle("open");
    $("filters-toggle").setAttribute("aria-expanded", String(open));
});

// A multi-select closes when you click anywhere else or press Esc
document.addEventListener("click", (e) => {
    FACETS.forEach((f) => f.root.open && !f.root.contains(e.target) && (f.root.open = false));
});
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") FACETS.forEach((f) => (f.root.open = false));
});

// Re-check which boxes are cut off when the window size changes
window.addEventListener("resize", debounce(() => markOverflow($("cards")), 150));

$("scrape-btn").addEventListener("click", toggleAuto);
$("auto-interval").addEventListener("change", changeAutoInterval);
$("pref-agency").addEventListener("change", changePreferences);
setInterval(updateCountdown, 1000);

$("facets").replaceChildren(...FACETS.map(buildFacet));
$("ranges").replaceChildren(...RANGES.map(buildRange));
loadListings();
checkStatus();

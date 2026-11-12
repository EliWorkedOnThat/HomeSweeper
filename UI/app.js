// HomeSweeper UI — talks to the FastAPI server in Scraper/Server.py
// All filtering and sorting happens here in the browser, the server just hands over every listing.
// Every DOM node is built with textContent (never innerHTML) so scraped text can't inject HTML into the page.
// All text the user sees must be in Albanian (see CLAUDE.md). Code and comments stay in English.

const POLL_MS = 3000; // How often to check the scrape status while one is running
const TIME_ZONE = "Europe/Tirane"; // The database stores UTC, staff see Albanian time

// Rough EUR -> ALL rate, only used to show the price slider in lekë. Must match EUR_TO_ALL in Scraper/Derive.py.
const EUR_TO_ALL = 100;

const SITE_NAMES = { merrjep: "MerrJep", gazetacelesi: "Gazeta Celësi", duashpi: "Duashpi", njoftime: "Njoftime" };
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
    { id: "posted", label: "Postuar nga", get: (l) => l._postedBy },
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
    { id: "rooms", label: "Dhoma gjumi", get: (l) => l._bedrooms, scale: "int", presets: () => [[1, 1], [2, 2], [3, 3], [4, null]] },
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
    "newest": ["_posted", "desc"], // The posting date (Duashpi: when it was created, not bumped)
    "oldest": ["_posted", "asc"],
    "price-asc": ["_priceEur", "asc"],
    "price-desc": ["_priceEur", "desc"],
    "perm2-asc": ["_perM2", "asc"],
    "size-desc": ["size_m2", "desc"],
    "rooms-desc": ["_bedrooms", "desc"],
    "title": ["title", "asc"],
};

const filters = {
    facets: Object.fromEntries(FACETS.map((f) => [f.id, new Set()])),
    ranges: Object.fromEntries(RANGES.map((r) => [r.id, { lo: null, hi: null }])), // null = no limit on that side
    includeMissing: true, // On by default: most sites leave some fields empty, and a filter shouldn't silently hide those listings
    currency: "EUR",
    search: "", // The search box, already lowercased and without accents (read once per keystroke, not per listing)
};

let allListings = [];
let pollTimer = null;
let lastFinished; // finished_at of the last scrape we've seen, a change means new listings to load
let autoState = null; // The "auto" part of /api/scrape/status
let scheduleState = null; // The "schedule" part of /api/scrape/status
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

const CURRENCY_LABELS = { EUR: "€", ALL: "Lekë" };

function formatPrice(price, currency) {
    if (isEmpty(price) || price <= 0) return null; // 0 is how sites mark "me marrëveshje"
    return `${formatNumber(price)} ${CURRENCY_LABELS[currency] || currency || ""}`.trim();
}

// "+355697211116" -> "069 721 1116", the way agents write and read Albanian mobile numbers
function formatPhone(phone) {
    const digits = String(phone || "").replace(/\D/g, "");
    const local = digits.startsWith("355") ? `0${digits.slice(3)}` : digits;
    return /^0\d{9}$/.test(local) ? `${local.slice(0, 3)} ${local.slice(3, 6)} ${local.slice(6)}` : String(phone || "").trim();
}

// Links come from scraped data: only real web addresses become clickable
function safeUrl(url) {
    return /^https?:\/\//i.test(url || "") ? url : null;
}

// A short message at the bottom of the screen ("Numri u kopjua")
let toastTimer;
function toast(message) {
    const box = $("toast");
    box.textContent = message;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (box.hidden = true), 2600);
}

function formatSize(size) {
    return isEmpty(size) ? null : `${formatNumber(size)} m²`;
}

// "2026-11-04" -> "Postuar 04.11.2026"
function shortDate(iso) {
    const [y, m, d] = String(iso).split("-");
    return d ? `Postuar ${d}.${m}.${y}` : null;
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
    img.src = safeUrl(src) || "";
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    img.referrerPolicy = "no-referrer";
    return img;
}

function externalLink(className, text, href) {
    const link = el("a", className, text);
    link.href = safeUrl(href) || "#";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
}

// ---------- Loading data ----------

async function loadListings() {
    let data;
    try {
        const response = await fetch("/api/listings");
        if (!response.ok) throw new Error(await errorText(response));
        data = await response.json();
        $("load-error").hidden = true;
    } catch (error) {
        $("load-error-text").textContent = error.message && !error.message.includes("fetch")
            ? error.message
            : "Njoftimet nuk u ngarkuan. Sigurohu që HomeSweeper është i ndezur.";
        $("load-error").hidden = false;
        return;
    }
    allListings = data.map(enrich);
    listingsById = new Map(allListings.map((l) => [l.id, l]));
    computeBounds();
    FACETS.forEach(fillFacetOptions);
    RANGES.forEach((r) => syncRange(r));
    draw();
}

// ---------- Filter fields ----------
// The shared filter values (property type, merged condition, furnishing, features, floor, bathrooms, price in EUR) are worked
// out on the server by Scraper/Derive.py and arrive under l.derived, so the dashboard and the AI search use the same rules.

// Lowercase without accents, so "Tokë", "TOKE" and "toke" all match /toke/
function norm(text) {
    return String(text ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

// Copies the server's derived values onto the _fields the filters and cards use. The raw columns stay as scraped.
function enrich(l) {
    const d = l.derived || {};
    const a = l.attributes || {};
    l._type = d.property_type ?? null;
    l._condition = d.condition ?? null;
    l._furnishing = d.furnishing ?? null;
    l._features = d.features || [];
    l._floor = d.floor ?? null;
    l._baths = d.baths ?? null;
    l._centerKm = d.center_km ?? null; // Only Duashpi gives it ("1.2km nga qendra")
    l._posted = d.posted ?? null; // ISO date the listing was posted (Derive.posted_date)
    l._bedrooms = d.bedrooms ?? l.rooms ?? null; // "2+1" -> 2, MerrJep's rooms column sometimes counts the living room too
    l._postedBy = d.posted_by ?? l.posted_by ?? null; // Duashpi leaves private sellers empty
    l._priceEur = d.price_eur ?? null;
    l._perM2 = d.price_per_m2 ?? null;
    // Searched without accents (agents often type "tirane"), and the phone as digits with and without 355
    const digits = (l.phone || "").replace(/\D/g, "");
    const phones = digits ? [digits, digits.startsWith("355") ? `0${digits.slice(3)}` : digits] : [];
    l._haystack = norm([l.title, l.description, l.address, l.city, l.phone, ...phones, l.seller, l.upid, ...Object.entries(a).flat()]
        .join(" "));
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
    return !filters.search || l._haystack.includes(filters.search);
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
        // The thumbs may pass each other, the lower one is always the minimum. So two thumbs on the same spot can never get stuck:
        // the top one is the one grabbed, and it can go either way.
        const lo = Math.min(Number(r.loIn.value), Number(r.hiIn.value));
        const hi = Math.max(Number(r.loIn.value), Number(r.hiIn.value));
        const state = filters.ranges[r.id];
        state.lo = lo <= posMin(r) ? null : posToValue(r, lo) / factor(r);
        state.hi = hi >= posMax(r) ? null : posToValue(r, hi) / factor(r);
        syncRange(r, false);
        renderSoon();
    });
    input.addEventListener("change", () => { // Released: thumbs that crossed go back to their own sides
        if (Number(r.loIn.value) > Number(r.hiIn.value)) [r.loIn.value, r.hiIn.value] = [r.hiIn.value, r.loIn.value];
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
    const a = ((Math.min(r.loIn.value, r.hiIn.value) - min) / span) * 100;
    const b = ((Math.max(r.loIn.value, r.hiIn.value) - min) / span) * 100;
    r.fillEl.style.left = `${a}%`;
    r.fillEl.style.right = `${100 - b}%`;

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
    filters.search = "";
    $("f-sort").value = "newest";
    $("f-missing").checked = filters.includeMissing = true; // Back to the default
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

// Pagination: the matching listings are split into pages, one page sits in the horizontal strip
const PAGE_SIZES = [12, 24, 48];
let page = 1;
let pageSize = PAGE_SIZES[0];

// A filter or sort change starts again from page 1
function render() {
    page = 1;
    draw();
}

// Draw the current page. Used directly when new listings arrive, so the agent stays on the page they're reading.
function draw() {
    const listings = sortListings(filteredListings());
    const pages = Math.max(1, Math.ceil(listings.length / pageSize));
    page = Math.min(Math.max(1, page), pages);
    const start = (page - 1) * pageSize;
    $("cards").replaceChildren(...listings.slice(start, start + pageSize).map(buildCard));
    $("cards").scrollLeft = 0;
    renderPager(listings.length, pages, start);
    updateFacetCounts();
    renderActive();
    renderPresets(RANGES.find((r) => r.id === "price")); // Its buttons follow the "Lloji" filter

    $("empty").hidden = allListings.length > 0;
    $("no-match").hidden = allListings.length === 0 || listings.length > 0;
    $("cards-wrap").hidden = listings.length === 0;
    $("stat-total").textContent = formatNumber(allListings.length);
    $("stat-shown").textContent = formatNumber(listings.length);
    $("stat-phone").textContent = formatNumber(allListings.filter((l) => l.phone).length);

    requestAnimationFrame(() => {
        markOverflow($("cards")); // Measure once the cards are laid out
        updateStripNav();
    });
}

function goToPage(n) {
    page = n;
    draw();
    $("cards-wrap").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// "1–12 nga 150", ‹ 1 … 4 5 6 … 13 ›, and how many per page
function renderPager(total, pages, start) {
    const pager = $("pager");
    pager.hidden = total === 0;
    if (!total) return;

    const info = el("span", "pager-info", `${formatNumber(start + 1)}–${formatNumber(Math.min(start + pageSize, total))} nga ${formatNumber(total)}`);

    const nav = el("div", "pager-pages");
    const button = (label, target, { current = false, aria } = {}) => {
        const b = el("button", `pager-btn${current ? " on" : ""}`, label);
        b.type = "button";
        b.disabled = target < 1 || target > pages;
        if (current) b.setAttribute("aria-current", "page");
        b.setAttribute("aria-label", aria || `Faqja ${target}`);
        if (!current) b.addEventListener("click", () => goToPage(target));
        return b;
    };
    nav.append(button("‹", page - 1, { aria: "Faqja e mëparshme" }));
    // Always the first and last page, plus two either side of the current one
    const shown = [...new Set([1, page - 2, page - 1, page, page + 1, page + 2, pages])].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
    shown.forEach((n, i) => {
        if (i && n - shown[i - 1] > 1) nav.append(el("span", "pager-gap", "…"));
        nav.append(button(formatNumber(n), n, { current: n === page }));
    });
    nav.append(button("›", page + 1, { aria: "Faqja tjetër" }));

    const size = el("select", "pager-size");
    size.setAttribute("aria-label", "Njoftime për faqe");
    PAGE_SIZES.forEach((n) => size.append(new Option(`${n} për faqe`, String(n))));
    size.value = String(pageSize);
    size.addEventListener("change", () => {
        const first = (page - 1) * pageSize; // Keep the first listing on screen in view
        pageSize = Number(size.value);
        goToPage(Math.floor(first / pageSize) + 1);
    });

    pager.replaceChildren(info, nav, size);
}

// The ‹ › arrows on the strip scroll one screen of cards, and grey out at either end
function scrollStrip(direction) {
    const strip = $("cards");
    strip.scrollBy({ left: direction * strip.clientWidth * 0.9, behavior: "smooth" });
}

function updateStripNav() {
    const strip = $("cards");
    const max = strip.scrollWidth - strip.clientWidth;
    $("cards-prev").disabled = strip.scrollLeft <= 2;
    $("cards-next").disabled = strip.scrollLeft >= max - 2;
    $("cards-wrap").classList.toggle("fits", max <= 2); // Everything visible, no arrows needed
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
    ban: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM5.6 5.6l12.8 12.8",
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
        l._perM2 >= 1 && l.listing_type !== "Jepet me qera" ? `${formatNumber(Math.round(l._perM2))} €/m²` : null,
    );
    if (priceNote) priceRow.append(el("div", "card-price-note", priceNote));
    const where = box(null, joinParts(l.city, l.address), { extraClass: "box-plain box-loc", previewTitle: "Adresa" });
    where.prepend(icon("pin"));
    head.append(priceRow, box(null, l.title || "(pa titull)", { lines: 2, extraClass: "box-plain box-title", previewTitle: "Titulli" }), where);

    // The numbers agents compare first
    const specs = el("div", "specs");
    [
        ["area", formatSize(l.size_m2)],
        ["bed", isEmpty(l._bedrooms) ? null : `${l._bedrooms} dhoma gjumi`],
        ["bath", isEmpty(l._baths) ? null : `${l._baths} banjo`],
        ["floor", isEmpty(l._floor) ? null : l._floor === 0 ? "Përdhe" : `Kati ${l._floor}`],
        ["pin", isEmpty(l._centerKm) ? null : `${formatNumber(l._centerKm)} km nga qendra`],
    ].filter(([, text]) => text).forEach(([name, text]) => specs.append(withIcon(name, "spec", text)));
    if (!specs.children.length) specs.append(el("span", "spec muted", "Pa të dhëna për përmasat"));

    // Quick chips: kind of property, condition, furnishing, who posted it (sale/rent and the site sit on the photo)
    const chips = el("div", "chips");
    [l._type, l._condition, l._furnishing, l._postedBy]
        .filter((c) => !isEmpty(c))
        .forEach((c) => chips.append(el("span", "chip", c)));

    // Every other scraped field gets its own box
    const facts = el("div", "facts");
    facts.append(
        // Most useful first: the card has a fixed height, so the last boxes fade out (all of it is in "Detajet")
        box("Telefoni", (l.phone || "").split(/[,;]/).map(formatPhone).filter(Boolean).join(", ") || null),
        box("Shitësi", l.seller),
        box("Përshkrimi", l.description, { lines: 3, wide: true }),
        box("Publikuar", joinParts(l.published_date, l.published_time)),
        box("Parë për herë të parë", localTime(l.first_seen)),
        box("Parë për herë të fundit", localTime(l.last_seen)),
        tagsBox(l.attributes),
    );

    // Footer: ID, link to the original listing, full details
    const footer = el("div", "card-footer");
    footer.append(el("span", "card-id", joinParts(SITE_NAMES[l.site] || l.site, l._posted ? shortDate(l._posted) : null)));
    if (canBlock(l)) footer.append(blockButton(l));
    const links = el("div", "card-links");
    const tel = (l.phone || "").split(/[,;/]/)[0].replace(/[^\d+]/g, "");
    if (tel) {
        const call = el("a", "btn btn-small btn-call");
        // Phones dial; on a computer tel: opens Teams/Skype or nothing, so the number is copied instead
        call.href = `tel:${tel}`;
        call.title = `Telefono ${formatPhone(tel)}`;
        call.setAttribute("aria-label", `Telefono ${formatPhone(tel)}`);
        call.append(icon("phone"), el("span", null, "Telefono"));
        call.addEventListener("click", (e) => {
            if (!matchMedia("(pointer: fine)").matches || !navigator.clipboard) return;
            e.preventDefault();
            navigator.clipboard.writeText(formatPhone(tel)).then(
                () => toast(`Numri u kopjua: ${formatPhone(tel)}`),
                () => (location.href = `tel:${tel}`),
            );
        });
        links.append(call);
    }
    if (l.url) {
        const open = externalLink("btn btn-small", null, l.url);
        open.append(el("span", null, "Hap në faqe"), icon("external"));
        open.title = "Hap njoftimin në faqen origjinale";
        open.setAttribute("aria-label", "Hap në faqe");
        links.append(open);
    }
    const details = el("button", "btn btn-small btn-primary", "Detajet");
    details.title = "Të gjitha detajet e njoftimit";
    details.addEventListener("click", () => openDetails(l));
    links.append(details);
    footer.append(links);

    body.append(head, specs, chips, facts, footer);
    card.append(buildPhoto(l), body);
    openCardOnClick(card, l);
    return card;
}

// Cards have a fixed size and anything that doesn't fit fades out, so a click or tap anywhere on one opens everything
// ("Detajet"), on PCs and phones alike. Buttons, links and the photo (gallery) keep their own click, and selecting text
// (to copy it) doesn't open it. Swiping the strip on a phone doesn't count as a tap, so it still scrolls.
function openCardOnClick(card, l) {
    card.addEventListener("click", (e) => {
        if (e.target.closest("a, button, .card-photo")) return;
        if (String(getSelection() || "")) return;
        openDetails(l);
    });
}

// Mark every box whose content is cut off, so it shows the ⤢ icon and opens a preview when clicked
// Boxes inside a card never get it: the whole card opens instead (openCardOnClick)
function markOverflow(root) {
    root.querySelectorAll(".box").forEach((b) => {
        const value = b.querySelector(".clamp, .clamp-block");
        const cut = value && (value.scrollHeight > value.clientHeight + 1 || value.scrollWidth > value.clientWidth + 1);
        const cutRow = [...b.querySelectorAll(".ellipsis")].some((e) => e.scrollWidth > e.clientWidth + 1);
        const expandable = Boolean(b.preview) && (cut || cutRow) && !b.closest(".card");

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
        main.src = safeUrl(images[current]) || "";
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
        ["Dhoma gjumi", l._bedrooms],
        ["Banjo", l._baths],
        ["Kati", l._floor === 0 ? "Përdhe" : l._floor],
        ["Larg qendrës", isEmpty(l._centerKm) ? null : `${formatNumber(l._centerKm)} km`],
        ["Qyteti", l.city],
        ["Adresa", l.address],
        ["Postuar nga", l._postedBy],
        ["Telefoni", l.phone],
        ["Shitësi", l.seller],
        ["Publikuar", joinParts(l.published_date, l.published_time)],
        ["Faqja", SITE_NAMES[l.site] || l.site],
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

    if (canBlock(l)) {
        const blockSection = el("div", "preview-section");
        blockSection.append(blockButton(l));
        wrap.append(blockSection);
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
    showSchedule(status.schedule, status.auto);
    showAuto(status.auto);
    showPreferences(status.preferences);

    if (lastFinished !== undefined && status.finished_at !== lastFinished) {
        loadListings(); // A turn just finished (or was paused), pick up what it saved
    }
    lastFinished = status.finished_at;

    if (status.state === "running" || status.auto.enabled || status.schedule.enabled) { // A daily start can begin at any moment
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
        pill.hidden = !scheduleState?.enabled;
        if (scheduleState?.enabled) {
            pill.textContent = `Niset çdo ditë në ${scheduleState.time}`;
            pill.title = "Mbledhja niset vetë në këtë orë, për sa kohë HomeSweeper është i hapur.";
        }
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
    if (scheduleState?.enabled) await saveSchedule(); // The daily start uses this break too
    if (!autoState || !autoState.enabled) return;
    try {
        await startAuto();
    } finally {
        checkStatus();
    }
}

// ---------- Daily start ----------

function showSchedule(schedule, auto) {
    scheduleState = schedule;
    $("schedule-on").checked = schedule.enabled;
    if (document.activeElement !== $("schedule-time")) $("schedule-time").value = schedule.time; // Never change it under the agent's fingers
    $("schedule-group").classList.toggle("on", schedule.enabled);
    if (schedule.enabled && !auto.enabled) $("auto-interval").value = String(schedule.interval_minutes);
}

async function saveSchedule() {
    const time = $("schedule-time").value;
    if (!time) { // The time box was emptied
        $("schedule-on").checked = Boolean(scheduleState?.enabled);
        toast("Zgjidh orën e nisjes.");
        return;
    }
    try {
        await postJSON("/api/schedule", {
            enabled: $("schedule-on").checked,
            time,
            interval_minutes: Number($("auto-interval").value),
        });
    } catch {
        alert("Orari nuk u ruajt. Provo përsëri.");
    }
    await checkStatus();
}

// ---------- Blocked sellers ----------

const countListings = (n) => `${formatNumber(n)} ${n === 1 ? "njoftim" : "njoftime"}`;
const canBlock = (l) => !isEmpty(l.seller) || !isEmpty(l.phone);

function blockButton(l) {
    const button = el("button", "card-block");
    button.type = "button";
    button.title = `Fshih njoftimet e ${l.seller || "këtij shitësi"}`;
    button.append(icon("ban"), el("span", null, "Blloko shitësin"));
    button.addEventListener("click", () => blockSeller(l));
    return button;
}

// The server decides who the seller is and counts what would go, so the agent sees it before agreeing
async function blockSeller(l) {
    try {
        const response = await fetch(`/api/blocked/preview/${l.id}`);
        if (!response.ok) throw new Error(await errorText(response));
        const { name, hidden } = await response.json();
        if (!confirm(`Të bllokohet “${name}”?\n\nDo të fshihen ${countListings(hidden)}, si dhe ato që do të vijnë më vonë. Mund ta zhbllokosh te Filtrat.`)) return;
        await postJSON("/api/blocked", { listing_id: l.id });
        if (dialog.open) dialog.close();
        await refreshAfterBlock();
        toast(`“${name}” u bllokua.`);
    } catch (error) {
        alert(error.message && !error.message.includes("fetch") ? error.message : "Serveri nuk përgjigjet. Sigurohu që është i ndezur.");
    }
}

async function unblockSeller(item) {
    try {
        const response = await fetch(`/api/blocked/${item.id}`, { method: "DELETE" });
        if (!response.ok) throw new Error(await errorText(response));
        await refreshAfterBlock();
        toast(`“${item.name}” u zhbllokua.`);
    } catch (error) {
        alert(error.message && !error.message.includes("fetch") ? error.message : "Serveri nuk përgjigjet. Sigurohu që është i ndezur.");
    }
}

// The server leaves blocked sellers out of every result, so everything on screen is fetched again
async function refreshAfterBlock() {
    listingsReady = loadListings();
    await Promise.all([listingsReady, loadBlocked()]);
    if (aiState) rerunAIFilters();
}

async function loadBlocked() {
    let items;
    try {
        const response = await fetch("/api/blocked");
        if (!response.ok) return;
        items = await response.json();
    } catch {
        return; // The offline pill already says the server is gone
    }
    if (!items.length) {
        $("blocked-list").replaceChildren(el("span", "muted", "Asnjë shitës i bllokuar. Përdor “Blloko shitësin” te një njoftim."));
        return;
    }
    $("blocked-list").replaceChildren(...items.map((item) => {
        const row = el("div", "blocked-item");
        const unblock = el("button", "btn btn-small", "Zhblloko");
        unblock.type = "button";
        unblock.addEventListener("click", () => unblockSeller(item));
        row.append(el("span", "blocked-name", item.name), el("span", "blocked-hidden", countListings(item.hidden)), unblock);
        return row;
    }));
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

// ---------- Views (Njoftimet / Pyet AI) ----------

// The URL hash picks the view: #/ is the dashboard, #/ai the question page, #/ai/12 one saved search.
// So the browser's back button, bookmarks and a page reload all keep the search the agent was looking at.
function route() {
    const hash = location.hash;
    if (hash === "#/historia") {
        location.replace("#/ai"); // Old link: the history now sits beside the AI results
        return { view: "ai", searchId: null };
    }
    const match = hash.match(/^#\/ai(?:\/(\d+))?$/);
    if (match) return { view: "ai", searchId: match[1] ? Number(match[1]) : null };
    return { view: "listings", searchId: null };
}

function showView() {
    const { view, searchId } = route();
    ["listings", "ai"].forEach((name) => ($(`view-${name}`).hidden = name !== view));
    document.querySelectorAll(".view-tab").forEach((tab) => {
        if (tab.dataset.view === view) tab.setAttribute("aria-current", "page");
        else tab.removeAttribute("aria-current");
    });
    if (view === "listings") {
        requestAnimationFrame(() => markOverflow($("cards")));
        return;
    }
    checkAIStatus();
    loadHistory();
    if (searchId === null) showWelcome();
    else if (aiState?.search?.id !== searchId) openSearch(searchId);
}

// ---------- Pyet AI ----------

const AI_PAGE = 24; // Result cards shown at first and per "Shfaq më shumë"

// "Çfarë mund të pyesësh": the kinds of questions the AI understands, each with one example to try
const AI_CAPABILITIES = [
    ["pin", "Vendi dhe qendra", "Qytet, zonë, rrugë, “në qendër”, “jo në Kombinat”", "Apartament në qendër të Tiranës, jo në Kombinat"],
    ["tag", "Çmimi dhe sipërfaqja", "Deri, mbi, “rreth”, € ose lekë, çmimi për m²", "Rreth 120 mijë euro dhe rreth 100 m² në shitje"],
    ["floor", "Kati, dhomat, banjot", "“2+1”, “kati i 4”, “jo përdhe”, “2 banjo”", "2+1 me qira, kati 4, jo përdhe, deri 600 €"],
    ["sparkle", "Dëshira të paqarta", "“Duket bukur”, “i ndritshëm”, “zonë e qetë”, “për familje”", "Dua diçka që duket bukur dhe e qetë, me qira në Tiranë"],
    ["text", "Fjalë në përshkrim", "Pamje nga deti, oborr, ngrohje, certifikatë pronësie", "Afër detit në Durrës, me pamje nga deti dhe kondicioner"],
    ["clock", "Data dhe kontakti", "“Postuar sot”, “këtë javë”, një numër telefoni, jo agjenci", "Të rejat e kësaj jave me qira, vetëm nga pronari"],
    ["chart", "Tregu", "Sa njoftime ka, çmimi mesatar, çmimi për m²", "Sa kushton mesatarisht një 2+1 në shitje në Tiranë?"],
    ["sort", "Renditje dhe sasi", "Më të lirat, më të mëdhatë, “5 më të lirat për m²”", "5 apartamentet më të lira për m² në shitje"],
];

// Extra line icons for the AI page (fixed paths, never scraped data)
const AI_ICONS = {
    tag: "M3 12V4h8l10 10-8 8zM7.5 7.5h.01",
    floor: "M4 21V8l8-5 8 5v13M4 13h16M4 17h16M9 21v-4M15 21v-4",
    sparkle: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z",
    text: "M4 6h16M4 10h16M4 14h10M4 18h7",
    clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2",
    chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
    sort: "M7 4v16M3 16l4 4 4-4M17 20V4M13 8l4-4 4 4",
    trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
};

function aiIcon(name) {
    if (ICONS[name]) return icon(name);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", AI_ICONS[name] || AI_ICONS.sparkle);
    svg.append(path);
    return svg;
}

// Sort options on the results page; changing one re-runs the search for free
const AI_SORTS = [
    ["best_match", "Përputhja më e mirë"],
    ["newest", "Më të rejat"],
    ["price_asc", "Më të lirat"],
    ["price_desc", "Më të shtrenjtat"],
    ["price_per_m2_asc", "Më të lirat për m²"],
    ["size_desc", "Më të mëdhatë"],
    ["size_asc", "Më të voglat"],
    ["rooms_desc", "Më shumë dhoma"],
];

let listingsReady; // Resolves once the listings are loaded, AI results are drawn from them
let listingsById = new Map(); // Built once per load in loadListings()
let aiState = null; // { search, filters, ids, reasons, near, shown }
let historyItems = []; // The saved searches, newest first

function listingById() {
    return listingsById;
}

async function checkAIStatus() {
    try {
        const status = await (await fetch("/api/ai/status")).json();
        $("ai-setup").hidden = status.configured;
        $("ask-btn").disabled = !status.configured;
        $("ask-btn").title = status.configured ? "" : "AI nuk është lidhur ende (mungon çelësi i API-së)";
    } catch {
        $("ai-setup").hidden = true; // Offline: the status pill already says so
    }
}

// Server errors come back as {detail: "..."} in Albanian
async function errorText(response) {
    try {
        const body = await response.json();
        if (typeof body.detail === "string") return body.detail;
    } catch { /* not JSON */ }
    return "Diçka shkoi keq. Provo përsëri.";
}

async function postJSON(url, body) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!response.ok) {
        const error = new Error(await errorText(response));
        error.status = response.status;
        throw error;
    }
    return response.json();
}

function setLoading(question) {
    $("ask-btn").disabled = Boolean(question) || !$("ai-setup").hidden;
    $("ai-loading").hidden = !question;
    $("ai-loading-question").textContent = question ? `“${question}”` : "";
}

async function askAI(event) {
    event?.preventDefault();
    const question = $("ask-input").value.trim();
    if (!question) {
        $("ask-input").focus();
        return;
    }
    $("ai-error").hidden = true;
    setLoading(question);
    try {
        const { search, ...results } = await postJSON("/api/ai/ask", { question });
        await listingsReady;
        showAIResults(search, results);
        $("ask-input").value = "";
        if (search?.id) location.hash = `#/ai/${search.id}`; // showView sees it's already open and doesn't fetch again
        loadHistory();
    } catch (error) {
        showAIError(error.status ? error.message : "Serveri nuk përgjigjet. Sigurohu që është i ndezur.");
        if (error.status === 503) checkAIStatus();
    } finally {
        setLoading(null);
    }
}

function showAIError(message) {
    $("ai-error").textContent = message;
    $("ai-error").hidden = false;
}

// No search open: the capabilities with examples instead of results
function showWelcome() {
    aiState = null;
    $("ai-results").hidden = true;
    $("ai-welcome").hidden = false;
    markActiveHistory();
}

// Open a saved search by id: re-run its filters on today's listings (free, the AI isn't asked again)
async function openSearch(id) {
    $("ai-error").hidden = true;
    try {
        const response = await fetch(`/api/ai/history/${id}`);
        if (!response.ok) throw new Error(await errorText(response));
        const search = await response.json();
        const results = await postJSON("/api/ai/search", { filters: search.filters });
        await listingsReady;
        if (route().searchId !== id) return; // The agent clicked something else meanwhile
        showAIResults(search, results);
    } catch (error) {
        showWelcome();
        showAIError(error.message || "Kërkimi nuk u hap. Provo përsëri.");
    }
}

// Show a search: what was asked, what the AI understood, the numbers, the filter chips and the cards.
// results: { ids, reasons: {listing id: ["E rinovuar ose e re", ...]}, near: [{id, missed}] }
function showAIResults(search, results) {
    aiState = { search, filters: structuredClone(search.filters), shown: AI_PAGE };
    setResults(results);
    $("ai-question").textContent = `“${search.question}”`;
    $("ai-when").textContent = search.created_at ? `Pyetur më ${localTime(search.created_at)}` : "";
    $("ai-understood").textContent = search.understood || "AI e ktheu pyetjen në filtrat më poshtë.";
    $("ai-unsupported").textContent = search.not_supported ? `Nuk mund të kërkohet: ${search.not_supported}` : "";
    $("ai-unsupported").hidden = !search.not_supported;
    $("ai-cost").textContent = isEmpty(search.cost_usd) ? "" : `Kjo pyetje kushtoi ≈ ${formatCost(search.cost_usd)}`;
    $("ai-error").hidden = true;
    $("ai-welcome").hidden = true;
    $("ai-results").hidden = false;
    drawAIResults();
    markActiveHistory();
    $("ai-results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function setResults({ ids, reasons, near }) {
    aiState.ids = ids || [];
    aiState.reasons = reasons || {};
    aiState.near = near || [];
}

// Has the agent changed the AI's filters (removed a chip, changed the sort)?
function filtersChanged() {
    return JSON.stringify(aiState.filters) !== JSON.stringify(aiState.search.filters);
}

function drawAIResults() {
    const { ids, shown, near } = aiState;
    const byId = listingById();
    const listings = ids.map((id) => byId.get(id)).filter(Boolean);
    const ranked = aiState.filters.sort === "best_match";

    $("ai-count").textContent = listings.length === 1 ? "1 njoftim" : `${formatNumber(listings.length)} njoftime`;
    $("ai-sort").value = aiState.filters.sort || "newest";
    $("ai-reset").hidden = !filtersChanged();
    renderStats(listings);
    renderAIChips();

    $("ai-cards").replaceChildren(...listings.slice(0, shown).map((l, i) => {
        const card = withReasons(buildCard(l), aiState.reasons[l.id]);
        if (ranked && i < 3 && aiState.reasons[l.id]) card.querySelector(".card-photo").append(el("span", "rank-badge", `#${i + 1}`));
        return card;
    }));
    $("ai-none").hidden = listings.length > 0;
    $("ai-none-hint").textContent = near.length
        ? "Më poshtë janë njoftimet që i mungon vetëm një kërkesë. Ose hiq një kërkesë më sipër."
        : "Hiq një nga kërkesat më sipër, ose shkruaje pyetjen ndryshe.";
    $("ai-more").hidden = listings.length <= shown;
    $("ai-more").textContent = `Shfaq më shumë (${formatNumber(listings.length - shown)} të tjera)`;

    const nearCards = near.map(({ id, missed }) => {
        const l = byId.get(id);
        return l ? withMiss(withReasons(buildCard(l), aiState.reasons[id]), missed) : null;
    }).filter(Boolean);
    $("ai-near").hidden = !nearCards.length;
    $("ai-near-cards").replaceChildren(...nearCards);
    requestAnimationFrame(() => {
        markOverflow($("ai-cards"));
        markOverflow($("ai-near-cards"));
    });
}

// ---------- The numbers above the results ----------

function median(values) {
    const sorted = values.filter((v) => typeof v === "number" && isFinite(v)).sort((a, b) => a - b);
    if (!sorted.length) return null;
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// Round to something an agent would say: 1.234 -> 1.230, 123.456 -> 123.000
function roughly(n) {
    if (n === null) return null;
    const step = n >= 100000 ? 1000 : n >= 10000 ? 500 : n >= 1000 ? 10 : 1;
    return Math.round(n / step) * step;
}

function statTile(label, value, sub, title) {
    const tile = el("div", "rs-stat");
    if (title) tile.title = title;
    tile.append(el("span", "rs-stat-label", label), el("strong", "rs-stat-value", value ?? "–"));
    if (sub) tile.append(el("span", "rs-stat-sub", sub));
    return tile;
}

// Count, typical price (rent and sale apart, they're different markets), price per m², size, and which sites they came from
function renderStats(listings) {
    $("ai-stats").hidden = !listings.length; // Nothing to sum up; the empty-state box says it instead
    const tiles = [];
    const sites = Object.entries(listings.reduce((acc, l) => ({ ...acc, [l.site]: (acc[l.site] || 0) + 1 }), {}))
        .sort((a, b) => b[1] - a[1]);
    tiles.push(statTile("Njoftime", formatNumber(listings.length),
        sites.map(([site, n]) => `${SITE_NAMES[site] || site} ${n}`).join(" · ") || null));

    const groups = [["Jepet me qera", "Qiraja tipike"], ["Shitet", "Çmimi tipik (shitje)"]];
    groups.forEach(([type, label]) => {
        const prices = listings.filter((l) => l.listing_type === type).map((l) => l._priceEur);
        const typical = roughly(median(prices));
        if (typical === null) return;
        const known = prices.filter((p) => typeof p === "number");
        tiles.push(statTile(label, `${formatNumber(typical)} €`,
            `${formatNumber(roughly(Math.min(...known)))} – ${formatNumber(roughly(Math.max(...known)))} €`,
            `Mediana e ${known.length} njoftimeve me çmim`));
    });

    // €/m² means the most for sales; fall back to rents when there are no sales
    const perM2Type = listings.some((l) => l.listing_type === "Shitet" && l._perM2) ? "Shitet" : "Jepet me qera";
    const perM2 = roughly(median(listings.filter((l) => l.listing_type === perM2Type).map((l) => l._perM2)));
    if (perM2 !== null) tiles.push(statTile("Për m²", `${formatNumber(perM2)} €/m²`, perM2Type === "Shitet" ? "shitje" : "qira në muaj"));

    const size = median(listings.map((l) => l.size_m2));
    if (size !== null) tiles.push(statTile("Sipërfaqja tipike", `${formatNumber(Math.round(size))} m²`));
    $("ai-stats").replaceChildren(...tiles);
}

// ---------- Filter chips ----------

// Removing a chip or changing the sort re-runs the search on the server without the AI, so it costs nothing
async function rerunAIFilters() {
    try {
        setResults(await postJSON("/api/ai/search", { filters: aiState.filters }));
        aiState.shown = AI_PAGE;
        drawAIResults();
    } catch (error) {
        showAIError(error.message || "Kërkimi dështoi. Provo përsëri.");
    }
}

function formatCost(usd) {
    return `${usd < 0.001 ? "< 0,001" : String(Math.round(usd * 1000) / 1000).replace(".", ",")} $`;
}

// "300–600 €", "≥ 50 m²", "≤ 4" in the same style as the dashboard chips
function rangeLabel(lo, hi, unit = "") {
    const show = (v) => formatNumber(v);
    const text = lo !== null && hi !== null ? (lo === hi ? show(lo) : `${show(lo)}–${show(hi)}`)
        : lo !== null ? `≥ ${show(lo)}` : `≤ ${show(hi)}`;
    return `${text}${unit ? ` ${unit}` : ""}`;
}

const POSTED_LABELS = { 0: "Postuar sot", 1: "Postuar dje ose sot", 7: "Postuar këtë javë", 30: "Postuar këtë muaj" };

// One chip per thing the AI searched on; each knows how to remove itself from the filters.
// Returns { hard: [...], soft: [...] }: requirements filter listings out, wishes only change the order.
function aiChips(f) {
    const hard = [];
    const soft = [];
    const has = (v) => v !== null && v !== undefined;
    const list = (field, label) => (f[field] || []).forEach((value) =>
        hard.push([`${label}: ${value}`, () => (f[field] = f[field].filter((v) => v !== value))]));
    const range = (lo, hi, label, unit, format = (a, b) => rangeLabel(a, b, unit)) => {
        if (!has(f[lo]) && !has(f[hi])) return;
        hard.push([`${label}: ${format(f[lo] ?? null, f[hi] ?? null)}`, () => { f[lo] = null; f[hi] = null; }]);
    };

    list("listing_types", "Lloji");
    list("property_types", "Prona");
    list("places", "Vendi");
    (f.exclude_places || []).forEach((value) =>
        hard.push([`Jo në: ${value}`, () => (f.exclude_places = f.exclude_places.filter((v) => v !== value))]));
    list("conditions", "Gjendja");
    list("furnishing", "Mobilimi");
    list("features", "Me");
    list("posted_by", "Postuar nga");
    list("sites", "Faqja");
    range("price_min", "price_max", "Çmimi", f.price_currency === "ALL" ? "Lekë" : "€");
    range("price_per_m2_min_eur", "price_per_m2_max_eur", "Çmimi për m²", "€/m²");
    range("size_min", "size_max", "Sipërfaqja", "m²");
    range("rooms_min", "rooms_max", "Dhoma gjumi", "");
    range("baths_min", "baths_max", "Banjo", "");
    range("floor_min", "floor_max", "Kati", "", (lo, hi) => (lo === 0 && hi === 0 ? "Përdhe" : rangeLabel(lo, hi)));
    if (has(f.posted_within_days)) {
        hard.push([POSTED_LABELS[f.posted_within_days] || `Postuar në ${f.posted_within_days} ditët e fundit`, () => (f.posted_within_days = null)]);
    }
    if (has(f.center_max_km) || (f.center_words || []).length) {
        const parts = [has(f.center_max_km) ? `≤ ${formatNumber(f.center_max_km)} km` : null,
            (f.center_words || []).length ? `ose përmend ${f.center_words.slice(0, 3).join(", ")}${f.center_words.length > 3 ? "…" : ""}` : null];
        hard.push([`Në qendër: ${parts.filter(Boolean).join(" ")}`, () => { f.center_max_km = null; f.center_words = []; }]);
    }
    (f.keyword_groups || []).forEach((group, i) => {
        const words = group.slice(0, 4).join(" / ") + (group.length > 4 ? ` +${group.length - 4}` : "");
        hard.push([`Përmend: ${words}`, () => (f.keyword_groups = f.keyword_groups.filter((_, n) => n !== i))]);
    });
    if ((f.exclude_keywords || []).length) hard.push([`Pa fjalët: ${f.exclude_keywords.join(", ")}`, () => (f.exclude_keywords = [])]);
    if (has(f.has_photos)) hard.push([f.has_photos ? "Me foto" : "Pa foto", () => (f.has_photos = null)]);
    if (has(f.has_price)) hard.push([f.has_price ? "Me çmim" : "Pa çmim", () => (f.has_price = null)]);
    if (f.include_missing) hard.push(["Përfshin njoftimet pa të dhëna", () => (f.include_missing = false)]);
    if (f.limit) hard.push([`Vetëm ${f.limit} të parat`, () => (f.limit = null)]);

    (f.preferences || []).forEach((pref, i) => {
        const label = pref.reason_sq || pref.values.slice(0, 3).join(" / ") || pref.kind;
        soft.push([label, () => (f.preferences = f.preferences.filter((_, n) => n !== i))]);
    });
    if (has(f.price_target)) {
        soft.push([`Afër ${formatNumber(f.price_target)} ${f.price_currency === "ALL" ? "Lekë" : "€"}`, () => (f.price_target = null)]);
    }
    if (has(f.size_target)) soft.push([`Afër ${formatNumber(f.size_target)} m²`, () => (f.size_target = null)]);
    return { hard, soft };
}

function chipButton([text, remove], className) {
    const chip = el("button", `fchip ${className}`);
    chip.type = "button";
    chip.title = "Hiqe dhe kërko përsëri (falas)";
    chip.append(el("span", "fchip-text", text), el("span", "fchip-x", "✕"));
    chip.addEventListener("click", () => {
        remove();
        rerunAIFilters();
    });
    return chip;
}

function renderAIChips() {
    const { hard, soft } = aiChips(aiState.filters);
    $("ai-chips-hard").replaceChildren(...hard.map((c) => chipButton(c, "chip-hard")));
    if (!hard.length) $("ai-chips-hard").append(el("span", "muted", "Asnjë: shfaqen të gjitha njoftimet."));
    $("ai-chips-soft").replaceChildren(...soft.map((c) => chipButton(c, "chip-soft")));
    $("ai-soft-group").hidden = !soft.length;
}

// The green "why it matched" line under the title of an AI result card
function withReasons(card, reasons) {
    if (!reasons || !reasons.length) return card;
    const line = el("div", "match-reasons");
    line.title = "Pse u rendit lart: këto dëshira i plotëson";
    reasons.forEach((reason) => line.append(el("span", "match-reason", `✓ ${reason}`)));
    card.querySelector(".card-head").after(line);
    return card;
}

// The red "what it lacks" line on a near-miss card
function withMiss(card, missed) {
    card.classList.add("card-near");
    const line = el("div", "miss-reason", `✗ ${missed}`);
    line.title = "Kjo është e vetmja kërkesë që nuk e plotëson";
    card.querySelector(".card-head").after(line);
    return card;
}

// ---------- Historia (the sidebar) ----------

async function loadHistory() {
    try {
        const response = await fetch("/api/ai/history");
        if (!response.ok) throw new Error();
        historyItems = await response.json();
    } catch {
        $("history-list").replaceChildren(el("p", "side-empty", "Historia nuk u ngarkua. Sigurohu që serveri është i ndezur."));
        return;
    }
    renderHistory();
}

// "2026-11-06" in Tirana time, to group the history by day
function tiranaDay(date) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: TIME_ZONE, day: "2-digit", month: "2-digit", year: "numeric" })
        .formatToParts(date).map((p) => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
}

function dayGroup(timestamp) {
    const day = tiranaDay(parseUtc(timestamp));
    const today = new Date();
    if (day === tiranaDay(today)) return "Sot";
    if (day === tiranaDay(new Date(today - 86400000))) return "Dje";
    if (day >= tiranaDay(new Date(today - 6 * 86400000))) return "Këtë javë";
    return "Më herët";
}

function renderHistory() {
    const query = norm($("history-filter").value.trim());
    const shown = historyItems.filter((s) => !query || norm(`${s.question} ${s.understood || ""}`).includes(query));
    const nodes = [];
    let lastGroup = null;
    shown.forEach((s) => {
        const group = dayGroup(s.created_at);
        if (group !== lastGroup) nodes.push(el("h3", "side-group", group));
        lastGroup = group;
        nodes.push(historyItem(s));
    });
    $("history-list").replaceChildren(...nodes);
    $("history-empty").hidden = historyItems.length > 0;
    if (historyItems.length && !shown.length) $("history-list").append(el("p", "side-empty", "Asnjë pyetje me këto fjalë."));
    $("side-count").textContent = historyItems.length ? String(historyItems.length) : "";
    const total = historyItems.reduce((sum, s) => sum + (s.cost_usd || 0), 0);
    $("history-total").textContent = historyItems.length
        ? `${formatNumber(historyItems.length)} pyetje · kosto gjithsej ≈ ${formatCost(total)}`
        : "";
    markActiveHistory();
}

function historyItem(s) {
    const item = el("div", "side-item");
    item.dataset.id = s.id;
    const open = el("a", "side-open");
    open.href = `#/ai/${s.id}`;
    open.title = "Hap këtë kërkim te njoftimet e sotme (falas, pa e pyetur AI-n)";
    const time = localTime(s.created_at)?.split(", ")[1] || "";
    open.append(
        el("span", "side-q", s.question),
        el("span", "side-meta", joinParts(time, `${formatNumber(s.result_count ?? 0)} rezultate`)),
    );
    open.addEventListener("click", closeSideOnPhone);

    const remove = el("button", "side-delete");
    remove.type = "button";
    remove.title = "Fshi nga historia";
    remove.setAttribute("aria-label", "Fshi nga historia");
    remove.append(aiIcon("trash"));
    remove.addEventListener("click", async () => {
        if (!confirm("Ta fshij këtë pyetje nga historia?")) return;
        const response = await fetch(`/api/ai/history/${s.id}`, { method: "DELETE" });
        if (!response.ok) {
            alert(await errorText(response));
            return;
        }
        if (aiState?.search?.id === s.id) location.hash = "#/ai";
        loadHistory();
    });
    item.append(open, remove);
    return item;
}

function closeSideOnPhone() {
    document.body.classList.remove("side-open-mobile");
    $("side-toggle").setAttribute("aria-expanded", "false");
}

function markActiveHistory() {
    const id = aiState?.search?.id;
    document.querySelectorAll(".side-item").forEach((item) => {
        const active = Number(item.dataset.id) === id;
        item.classList.toggle("active", active);
        const link = item.querySelector(".side-open");
        if (active) link.setAttribute("aria-current", "true");
        else link.removeAttribute("aria-current");
    });
}

function buildCapabilities() {
    $("ai-capabilities").replaceChildren(...AI_CAPABILITIES.map(([iconName, title, text, example]) => {
        const card = el("button", "capability");
        card.type = "button";
        card.title = "Vendose këtë shembull te pyetja (nuk dërgohet derisa të shtypësh Kërko)";
        const head = el("span", "capability-head");
        head.append(el("span", "capability-icon"), el("strong", null, title));
        head.firstChild.append(aiIcon(iconName));
        card.append(head, el("span", "capability-text", text), el("span", "capability-example", `“${example}”`));
        card.addEventListener("click", () => {
            $("ask-input").value = example;
            $("ask-input").focus();
            $("ask-card").scrollIntoView({ behavior: "smooth", block: "start" });
        });
        return card;
    }));
}

// ---------- Wiring ----------

// Clicking (or Enter/Space on) a cut-off box opens its preview, in every list of cards
document.addEventListener("click", (e) => {
    const b = e.target.closest(".box.expandable");
    if (b && b.preview) b.preview();
});
document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const b = e.target.closest(".box.expandable");
    if (b && b.preview) {
        e.preventDefault();
        b.preview();
    }
});
$("load-retry").addEventListener("click", () => (listingsReady = loadListings()));

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
$("f-search").addEventListener("input", () => {
    filters.search = norm($("f-search").value.trim());
    renderSoon();
});
$("f-sort").addEventListener("change", render);
$("f-missing").addEventListener("change", () => {
    filters.includeMissing = $("f-missing").checked;
    render();
});
$("f-clear").addEventListener("click", clearFilters);

// The filter panel folds away behind one button
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
window.addEventListener("resize", debounce(() => {
    markOverflow($("cards"));
    updateStripNav();
}, 150));

$("cards-prev").addEventListener("click", () => scrollStrip(-1));
$("cards-next").addEventListener("click", () => scrollStrip(1));
$("cards").addEventListener("scroll", debounce(updateStripNav, 60), { passive: true });

$("scrape-btn").addEventListener("click", toggleAuto);
$("auto-interval").addEventListener("change", changeAutoInterval);
$("schedule-on").addEventListener("change", saveSchedule);
$("schedule-time").addEventListener("change", saveSchedule);
$("pref-agency").addEventListener("change", changePreferences);
setInterval(updateCountdown, 1000);

$("facets").replaceChildren(...FACETS.map(buildFacet));
$("ranges").replaceChildren(...RANGES.map(buildRange));
// Pyet AI and its history
$("ask-form").addEventListener("submit", askAI);
$("ask-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) askAI(e);
});
buildCapabilities();
$("ai-sort").replaceChildren(...AI_SORTS.map(([value, label]) => {
    const option = el("option", null, label);
    option.value = value;
    return option;
}));
$("ai-sort").addEventListener("change", () => {
    aiState.filters.sort = $("ai-sort").value;
    rerunAIFilters();
});
$("ai-reset").addEventListener("click", () => {
    aiState.filters = structuredClone(aiState.search.filters);
    rerunAIFilters();
});
$("ai-edit").addEventListener("click", () => {
    $("ask-input").value = aiState.search.question;
    $("ask-card").scrollIntoView({ behavior: "smooth", block: "start" });
    $("ask-input").focus({ preventScroll: true });
});
$("new-search").addEventListener("click", () => {
    location.hash = "#/ai";
    closeSideOnPhone();
    $("ask-input").focus();
});
$("history-filter").addEventListener("input", debounce(renderHistory, 120));
// Phones: the history folds away behind one button above the question box
$("side-toggle").addEventListener("click", () => {
    const open = document.body.classList.toggle("side-open-mobile");
    $("side-toggle").setAttribute("aria-expanded", String(open));
});
$("ai-more").addEventListener("click", () => {
    aiState.shown += AI_PAGE;
    drawAIResults();
});
window.addEventListener("resize", debounce(() => {
    markOverflow($("ai-cards"));
    markOverflow($("ai-near-cards"));
}, 150));
window.addEventListener("hashchange", showView);

listingsReady = loadListings();
loadBlocked();
checkStatus();
showView();

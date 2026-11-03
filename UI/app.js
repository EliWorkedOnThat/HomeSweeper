// HomeSweeper UI — talks to the FastAPI server in Scraper/Server.py
// All filtering and sorting happens here in the browser, the server just hands over every listing.
// Every DOM node is built with textContent (never innerHTML) so scraped text can't inject HTML into the page.
// All text the user sees must be in Albanian (see CLAUDE.md). Code and comments stay in English.

const POLL_MS = 3000; // How often to check the scrape status while one is running
const TIME_ZONE = "Europe/Tirane"; // The database stores UTC, staff see Albanian time

// Dropdown filters: [element id, listing field, "all" label]
const SELECT_FILTERS = [
    ["f-type", "listing_type", "Të gjitha llojet"],
    ["f-city", "city", "Të gjitha qytetet"],
    ["f-condition", "condition", "Çdo gjendje"],
    ["f-posted", "posted_by", "Të gjithë postuesit"],
];

// Sort dropdown value -> [listing field, direction]
const SORTS = {
    "newest": ["first_seen", "desc"],
    "oldest": ["first_seen", "asc"],
    "price-asc": ["price", "asc"],
    "price-desc": ["price", "desc"],
    "size-desc": ["size_m2", "desc"],
    "rooms-desc": ["rooms", "desc"],
    "title": ["title", "asc"],
};

let allListings = [];
let pollTimer = null;

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
    if (isEmpty(price)) return null;
    return `${formatNumber(price)} ${currency || ""}`.trim();
}

function formatSize(size) {
    return isEmpty(size) ? null : `${formatNumber(size)} m²`;
}

function joinParts(...parts) {
    const kept = parts.filter((p) => !isEmpty(p));
    return kept.length ? kept.join(" · ") : null;
}

// "2026-11-03 15:01:00" (UTC, from the database) -> "03.11.2026, 16:01" in Albanian time
function localTime(timestamp) {
    if (isEmpty(timestamp)) return null;
    const date = new Date(`${timestamp.replace(" ", "T")}Z`);
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
    allListings = await response.json();
    SELECT_FILTERS.forEach(([id, field, label]) => fillSelect($(id), field, label));
    render();
}

// Fill a dropdown with every distinct value of one field, keeping the current choice
function fillSelect(select, field, allLabel) {
    const current = select.value;
    const values = [...new Set(allListings.map((l) => l[field]).filter((v) => !isEmpty(v)))].sort((a, b) => String(a).localeCompare(String(b), "sq"));
    select.replaceChildren(new Option(allLabel, ""), ...values.map((v) => new Option(v, v)));
    select.value = values.includes(current) ? current : "";
}

// ---------- Filtering and sorting ----------

function filteredListings() {
    const search = $("f-search").value.trim().toLowerCase();
    const min = parseFloat($("f-min").value);
    const max = parseFloat($("f-max").value);

    return allListings.filter((l) => {
        for (const [id, field] of SELECT_FILTERS) {
            const wanted = $(id).value;
            if (wanted && l[field] !== wanted) return false;
        }
        if (!isNaN(min) && !(l.price >= min)) return false;
        if (!isNaN(max) && !(l.price <= max)) return false;
        if (search) {
            const tags = Object.entries(l.attributes || {}).flat();
            const haystack = [l.title, l.description, l.address, l.city, l.phone, l.seller, l.upid, ...tags]
                .join(" ")
                .toLowerCase();
            if (!haystack.includes(search)) return false;
        }
        return true;
    });
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

function buildPhoto(l) {
    const photo = el("div", "card-photo");
    const images = l.images || [];
    if (!images.length) {
        photo.append(el("span", null, "Pa foto"));
        return photo;
    }

    const img = image(images[0]);
    img.addEventListener("error", () => img.replaceWith(el("span", null, "Fotoja nuk u ngarkua")));
    photo.append(img, el("span", "photo-count", `📷 ${images.length}`));
    photo.classList.add("clickable");
    photo.title = "Shiko fotot";
    photo.addEventListener("click", () => openGallery(l, 0));
    return photo;
}

function buildCard(l) {
    const card = el("article", "card");
    const body = el("div", "card-body");

    // Title + price
    const head = el("div", "card-head");
    head.append(
        box(null, l.title || "(pa titull)", { lines: 2, extraClass: "box-plain box-title", previewTitle: "Titulli" }),
        el("div", "card-price", formatPrice(l.price, l.currency) || "Çmimi me marrëveshje"),
    );

    // Quick chips: type, condition, who posted it, which site
    const chips = el("div", "chips");
    [l.listing_type, l.condition, l.posted_by, l.site].filter((c) => !isEmpty(c)).forEach((c) => chips.append(el("span", "chip", c)));

    // Every scraped field gets its own box
    const facts = el("div", "facts");
    facts.append(
        box("Sipërfaqja", formatSize(l.size_m2)),
        box("Dhoma", l.rooms),
        box("Qyteti", l.city),
        box("Adresa", l.address, { wide: true }),
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
    if (l.url) links.append(externalLink("btn btn-small", "Hap në faqe ↗", l.url));
    const details = el("button", "btn btn-small btn-primary", "Të gjitha detajet");
    details.addEventListener("click", () => openDetails(l));
    links.append(details);
    footer.append(links);

    body.append(head, chips, facts, footer);
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
        ["Lloji", l.listing_type],
        ["Gjendja", l.condition],
        ["Sipërfaqja", formatSize(l.size_m2)],
        ["Dhoma", l.rooms],
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

async function startScrape() {
    $("scrape-btn").disabled = true;
    const response = await fetch("/api/scrape/start", { method: "POST" });
    if (!response.ok && response.status !== 409) {
        alert("Mbledhja e njoftimeve nuk mund të fillonte. Provo përsëri.");
    }
    checkStatus();
}

async function checkStatus() {
    const status = await (await fetch("/api/scrape/status")).json();
    showStatus(status);

    clearTimeout(pollTimer);
    if (status.state === "running") {
        pollTimer = setTimeout(checkStatus, POLL_MS);
    } else if (status.state === "done" || status.state === "error") {
        loadListings(); // Pick up whatever the scrape saved
    }
}

function showStatus(status) {
    const pill = $("status");
    const labels = {
        idle: "Në pritje",
        running: "Duke mbledhur…",
        done: status.result ? `Përfundoi — ${status.result.saved} u ruajtën` : "Përfundoi",
        error: "Gabim",
    };
    pill.textContent = labels[status.state] || status.state;
    pill.className = `status status-${status.state}`;
    pill.title = status.state === "error" ? "Mbledhja dështoi. Shiko konsolën e serverit për detaje." : "";
    $("scrape-btn").disabled = status.state === "running";
    $("stat-last").textContent = localTime(status.finished_at)?.split(", ")[1] || "–";
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
["f-min", "f-max"].forEach((id) => $(id).addEventListener("input", renderSoon));
[...SELECT_FILTERS.map(([id]) => id), "f-sort"].forEach((id) => $(id).addEventListener("change", render));

$("f-clear").addEventListener("click", () => {
    ["f-search", "f-min", "f-max", ...SELECT_FILTERS.map(([id]) => id)].forEach((id) => ($(id).value = ""));
    $("f-sort").value = "newest";
    render();
});

// Re-check which boxes are cut off when the window size changes
window.addEventListener("resize", debounce(() => markOverflow($("cards")), 150));

$("scrape-btn").addEventListener("click", startScrape);

SELECT_FILTERS.forEach(([id, , label]) => $(id).replaceChildren(new Option(label, "")));
loadListings();
checkStatus();

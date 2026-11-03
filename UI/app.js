// HomeSweeper UI — talks to the FastAPI server in Scraper/Server.py
// All filtering and sorting happens here in the browser, the server just hands over every listing.

const POLL_MS = 3000; // How often to check the scrape status while one is running

let allListings = [];
let sortKey = "first_seen";
let sortDir = "desc";
let pollTimer = null;

const $ = (id) => document.getElementById(id);

// ---------- Loading data ----------

async function loadListings() {
    const response = await fetch("/api/listings");
    allListings = await response.json();
    fillSelect($("f-type"), "listing_type", "All types");
    fillSelect($("f-city"), "city", "All cities");
    render();
}

// Fill a dropdown with every distinct value of one field, keeping the current choice
function fillSelect(select, field, allLabel) {
    const current = select.value;
    const values = [...new Set(allListings.map((l) => l[field]).filter(Boolean))].sort();
    select.replaceChildren(new Option(allLabel, ""), ...values.map((v) => new Option(v, v)));
    select.value = values.includes(current) ? current : "";
}

// ---------- Filtering, sorting, drawing ----------

function filteredListings() {
    const search = $("f-search").value.trim().toLowerCase();
    const type = $("f-type").value;
    const city = $("f-city").value;
    const min = parseFloat($("f-min").value);
    const max = parseFloat($("f-max").value);

    return allListings.filter((l) => {
        if (type && l.listing_type !== type) return false;
        if (city && l.city !== city) return false;
        if (!isNaN(min) && !(l.price >= min)) return false;
        if (!isNaN(max) && !(l.price <= max)) return false;
        if (search) {
            const haystack = [l.title, l.description, l.address, l.phone, l.seller].join(" ").toLowerCase();
            if (!haystack.includes(search)) return false;
        }
        return true;
    });
}

function sortListings(listings) {
    const dir = sortDir === "asc" ? 1 : -1;
    return listings.sort((a, b) => {
        const x = a[sortKey], y = b[sortKey];
        if (x == null && y == null) return 0;
        if (x == null) return 1; // Empty values always go last
        if (y == null) return -1;
        if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
        return String(x).localeCompare(String(y), "sq") * dir;
    });
}

function render() {
    const listings = sortListings(filteredListings());
    const tbody = document.querySelector("#listings tbody");
    tbody.replaceChildren(...listings.map(buildRow));

    $("empty").hidden = allListings.length > 0;
    $("stat-total").textContent = allListings.length;
    $("stat-shown").textContent = listings.length;
    $("stat-phone").textContent = allListings.filter((l) => l.phone).length;

    document.querySelectorAll("th[data-sort]").forEach((th) => {
        th.classList.toggle("sorted-asc", th.dataset.sort === sortKey && sortDir === "asc");
        th.classList.toggle("sorted-desc", th.dataset.sort === sortKey && sortDir === "desc");
    });
}

// Build a cell with textContent (never innerHTML) so scraped text can't inject HTML into the page
function cell(text, className) {
    const td = document.createElement("td");
    td.textContent = text ?? "–";
    if (text == null) td.classList.add("muted");
    if (className) td.classList.add(className);
    return td;
}

function buildRow(l) {
    const tr = document.createElement("tr");

    const thumbCell = document.createElement("td");
    if (l.images && l.images.length) {
        const img = document.createElement("img");
        img.className = "thumb";
        img.src = l.images[0];
        img.loading = "lazy";
        img.alt = "";
        thumbCell.append(img);
    }

    const titleCell = document.createElement("td");
    titleCell.className = "title-cell";
    const link = document.createElement("a");
    link.href = l.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = l.title || "(no title)";
    titleCell.append(link);

    tr.append(
        thumbCell,
        titleCell,
        cell(formatPrice(l.price, l.currency), "nowrap"),
        cell(l.listing_type),
        cell(l.size_m2),
        cell(l.rooms),
        cell(l.city),
        cell(l.phone, "nowrap"),
        cell(l.seller),
        cell(l.first_seen ? l.first_seen.slice(0, 10) : null, "nowrap"),
    );
    return tr;
}

function formatPrice(price, currency) {
    if (price == null) return null;
    return `${Number(price).toLocaleString("en-US")} ${currency || ""}`.trim();
}

// ---------- Scraping ----------

async function startScrape() {
    $("scrape-btn").disabled = true;
    const response = await fetch("/api/scrape/start", { method: "POST" });
    if (!response.ok && response.status !== 409) {
        alert("Could not start the scrape.");
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
        idle: "Idle",
        running: "Scraping…",
        done: status.result ? `Done — ${status.result.saved} saved` : "Done",
        error: "Error",
    };
    pill.textContent = labels[status.state] || status.state;
    pill.className = `status status-${status.state}`;
    pill.title = status.error || "";
    $("scrape-btn").disabled = status.state === "running";
    $("stat-last").textContent = status.finished_at ? `${status.finished_at.slice(11, 16)} UTC` : "–";
}

// ---------- Wiring ----------

document.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
        if (sortKey === th.dataset.sort) {
            sortDir = sortDir === "asc" ? "desc" : "asc";
        } else {
            sortKey = th.dataset.sort;
            sortDir = "asc";
        }
        render();
    });
});

["f-search", "f-type", "f-city", "f-min", "f-max"].forEach((id) => $(id).addEventListener("input", render));

$("f-clear").addEventListener("click", () => {
    ["f-search", "f-type", "f-city", "f-min", "f-max"].forEach((id) => ($(id).value = ""));
    render();
});

$("scrape-btn").addEventListener("click", startScrape);

loadListings();
checkStatus();

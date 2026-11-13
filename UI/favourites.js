// The favourites page (favourites.html): the listings the agent saved with the heart on a card.
// Loaded after app.js and reuses its cards (buildCard), previews, heart button and helpers. app.js sees
// <body data-page="favourites"> and skips the dashboard's startup, so this file starts the page itself.
// All text the user sees is Albanian (see CLAUDE.md).

let favourites = []; // The favourite listings, enriched like on the dashboard
let favSearch = ""; // The search box, lowercased and without accents (norm), like filters.search on the dashboard

// Load every listing and keep the favourites. /api/listings already leaves out blocked sellers.
async function loadFavourites() {
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
    favourites = data.filter((l) => l.favourite).map(enrich);
    drawFavourites();
}

// Search and sort the favourites, then draw them as the same cards as the dashboard
function drawFavourites() {
    const shown = sortListings(favourites.filter((l) => !favSearch || l._haystack.includes(favSearch)), $("fav-sort").value);
    $("fav-total").textContent = formatNumber(favourites.length);
    $("fav-cards").replaceChildren(...shown.map(buildCard));
    markOverflow($("fav-cards"));
    $("fav-empty").hidden = favourites.length > 0;
    $("fav-no-match").hidden = !favourites.length || shown.length > 0;
}

// Wiring
const drawSoon = debounce(drawFavourites, 150);
$("fav-search").addEventListener("input", () => {
    favSearch = norm($("fav-search").value.trim());
    drawSoon();
});
$("fav-sort").addEventListener("change", drawFavourites);
$("load-retry").addEventListener("click", loadFavourites);

// The heart was taken off a card here: it isn't a favourite any more, so its card goes
document.addEventListener("favourite-changed", (e) => {
    if (e.detail.favourite) return;
    favourites = favourites.filter((l) => l !== e.detail);
    drawFavourites();
});
// A seller was blocked from a card here: the server hides their listings now, so load the favourites again
document.addEventListener("listings-changed", loadFavourites);

loadFavourites();

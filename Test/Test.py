#This file may change periodically as the tests are made and updated

import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from rich import print
from rich.table import Table

BASE = "https://www.merrjep.al"
HTML_FILE = Path("merrjep_page1.html")


def guess_transaction(title: str) -> str:
    """Heuristic from the title only. Replace with a real field if one turns up."""
    t = title.lower()
    if re.search(r"k[eë]rkoj|k[eë]rkohet", t):
        return "wanted"
    if re.search(r"n[dë]rroj|k[eë]mbej", t):
        return "exchange"
    if re.search(r"\bqira\b|me qera|\bqera\b", t):
        return "rent"
    if re.search(r"shitet|shitje", t):
        return "sale"
    return "unknown"


def parse_card(card) -> dict:
    title_a = card.select_one("h2 a")
    href = title_a["href"] if title_a else None
    title = title_a.get_text(strip=True) if title_a else None

    price_el = card.select_one(".list-price")
    price_text = price_el.get_text(strip=True) if price_el else None
    digits = re.sub(r"[^\d]", "", price_text or "")
    cur = re.search(r"[A-Za-z]+", price_text or "")

    posted = card.select_one(".span-col-title > span.pull-right")
    store_link = card.select_one("a.store-label-link")
    store_label = store_link.select_one("span.label") if store_link else None
    links = card.select(".span-col-title a.link-html")
    advertiser = card.select_one("[data-advertiser-id]")
    img = card.select_one("img.img-polaroid")
    area = re.search(r"(\d[\d.,]*)\s*(?:m2|m²|m\^2)", title or "", re.I)
    rooms = re.search(r"\b(\d)\s*\+\s*(\d)\b", title or "")

    return {
        "id": card.get("data-product-id") or (href or "").rstrip("/").split("/")[-1],
        "url": urljoin(BASE, href) if href else None,
        "title": title,
        "price": int(digits) if digits else None,
        "currency": cur.group(0).upper() if cur else None,
        "posted_text": posted.get_text(strip=True) if posted else None,
        "property_type": links[0].get_text(strip=True) if len(links) > 0 else None,
        "city": links[1].get_text(strip=True) if len(links) > 1 else None,
        "seller_label": store_label.get_text(strip=True) if store_label else None,
        "store_slug": store_link["href"].rstrip("/").split("/")[-1] if store_link else None,
        "advertiser_id": advertiser["data-advertiser-id"] if advertiser else None,
        "trusted_seller": card.select_one(".badge-trusted-seller") is not None,
        "top_badge": card.select_one("img[src*='toprated-ad-icon']") is not None,
        "image": (img.get("data-src") or img.get("src")) if img else None,
        "area_m2_guess": area.group(1) if area else None,
        "rooms_guess": f"{rooms.group(1)}+{rooms.group(2)}" if rooms else None,
        "transaction_guess": guess_transaction(title or ""),
    }


def jsonld_ids(soup) -> dict:
    """id -> price from the JSON-LD ItemList, used to cross-check the cards."""
    out = {}
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if node.get("@type") == "Product" and node.get("url"):
                    out[node["url"].rstrip("/").split("/")[-1]] = (node.get("offers") or {}).get("price")
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return out


def parse_page(html: str):
    soup = BeautifulSoup(html, "html.parser")
    cards = [parse_card(c) for c in soup.select("div.row-listing[data-product-id]")]
    return cards, jsonld_ids(soup)


def main():
    cards, ld = parse_page(HTML_FILE.read_text(encoding="utf-8"))
    card_ids = {c["id"] for c in cards}
    print(f"Cards: {len(cards)} | JSON-LD products: {len(ld)}")
    print(f"In cards but not JSON-LD: {sorted(card_ids - set(ld))[:10]}")
    print(f"In JSON-LD but not cards: {sorted(set(ld) - card_ids)[:10]}")

    price_mismatch = [c["id"] for c in cards if c["id"] in ld and ld[c["id"]] and c["price"] != int(ld[c["id"]])]
    print(f"Price mismatches card vs JSON-LD: {len(price_mismatch)} {price_mismatch[:5]}")

    table = Table(show_lines=False)
    for col in ("id", "title", "price", "city", "type", "seller", "posted", "top", "txn"):
        table.add_column(col, overflow="fold")
    for c in cards[:8]:
        table.add_row(c["id"], (c["title"] or "")[:40], f'{c["price"]} {c["currency"] or ""}',
                      c["city"] or "-", c["property_type"] or "-", c["seller_label"] or "(none)",
                      c["posted_text"] or "-", "Y" if c["top_badge"] else "", c["transaction_guess"])
    print(table)

    n = max(len(cards), 1)
    for field in ("price", "city", "property_type", "posted_text", "seller_label", "advertiser_id"):
        print(f"{field}: present on {sum(1 for c in cards if c[field])}/{n}")
    print("top_badge:", sum(c["top_badge"] for c in cards), "| trusted_seller:", sum(c["trusted_seller"] for c in cards))
    print("transaction_guess:", dict(Counter(c["transaction_guess"] for c in cards)))
    print("posted_text first word:", dict(Counter((c["posted_text"] or "?").split()[0] for c in cards)))


if __name__ == "__main__":
    main()
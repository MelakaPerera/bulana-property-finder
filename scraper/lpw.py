"""LankaPropertyWeb scraper.

The site is small enough to walk its three national sections (sale, land,
rentals) page by page. Each card names a town; we map towns to districts
using ikman's location list plus the aliases in config.json, then keep only
the districts the agent asked for.
"""
from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from .common import Fetcher, clean, norm, parse_int, parse_land_size, parse_price, total_price

BASE = "https://www.lankapropertyweb.com"

# section path -> deal
SECTIONS = {"sale": "sale", "land": "sale", "rentals": "rent"}

_TYPE_WORDS = (
    ("apartment", ("apartment", "studio", "penthouse", "condo")),
    ("house", ("house", "villa", "bungalow", "townhouse")),
    ("room", ("room", "annex", "hostel")),
)


def _property_type(section: str, label: str) -> str | None:
    if section == "land":
        return "land"
    low = label.lower()
    if "land" in low:
        return "land"
    for ptype, words in _TYPE_WORDS:
        if any(w in low for w in words):
            return ptype
    return None  # commercial, office, hotel, warehouse ...


def _district_for(city: str, addr: str, locations: dict, aliases: dict) -> str | None:
    towns = locations["town_to_district"]
    for text in (city, addr):
        n = norm(text)
        if not n:
            continue
        if n in aliases:
            return aliases[n]
        if n in towns:
            return towns[n]
        # "Ethul Kotte" -> Kotte, "Collingwood Place, Colombo 6" -> Colombo 6
        words = n.split()
        for size in (3, 2, 1):
            for i in range(len(words) - size + 1):
                chunk = " ".join(words[i:i + size])
                if len(chunk) >= 4 and chunk in aliases:
                    return aliases[chunk]
                if len(chunk) >= 4 and chunk in towns:
                    return towns[chunk]
    return None


def _parse_card(card, section: str) -> dict | None:
    ad_id = card.get("data-ad-id")
    if not ad_id:
        return None

    def text(selector: str) -> str:
        el = card.select_one(selector)
        return clean(el.get_text(" ", strip=True)) if el else ""

    beds = baths = perches = sqft = None
    for li in card.select(".listing-summery ul li"):
        if li.select_one(".type"):
            continue
        img = li.find("img")
        alt = (img.get("alt") or "").lower() if img else ""
        value = clean(li.get_text(" ", strip=True))
        low = value.lower()
        if "bed" in alt:
            beds = parse_int(value)
        elif "bath" in alt:
            baths = parse_int(value)
        elif "sqft" in low or "sq" in low:
            sqft = parse_int(value)
        else:
            perches = parse_land_size(value) or perches

    link = card.select_one("a.listing-header")
    href = link.get("href") if link else f"/{section}/property_details-{ad_id}.html"
    img = card.select_one("a.listing-header img")
    img_url = (img.get("data-src") or "") if img else ""
    if img_url.startswith("/"):
        img_url = BASE + img_url
    agent = card.select_one(".agent-logo img")
    price_el = card.select_one(".listing-price")
    price, unit = parse_price(price_el.get_text(" ", strip=True) if price_el else "")
    title = re.sub(r"\s*(\.\.\.|…)$", "…", text(".listing-title"))

    return {
        "id": "lpw:" + ad_id,
        "src": "lpw",
        "url": BASE + href if href.startswith("/") else href,
        "title": title,
        "deal": SECTIONS[section],
        "type": _property_type(section, text(".listing-summery .type")),
        "city": text("a.listing-header .location"),
        "district": "",
        "addr": text(".listing-address"),
        "price": price,
        "unit": unit,
        "total": total_price(price, unit, perches),
        "beds": beds,
        "baths": baths,
        "perches": perches,
        "sqft": sqft if sqft and sqft < 200000 else None,
        "img": img_url,
        "seller": "Agent" if agent is not None else "",
    }


def _scrape_section(fetcher: Fetcher, section: str, max_pages: int, log) -> list[dict]:
    out = []
    for page in range(1, max_pages + 1):
        html = fetcher.get(f"{BASE}/{section}/index.php?page={page}")
        if not html:
            break
        cards = BeautifulSoup(html, "html.parser").select("article.listing-item")
        if not cards:
            break
        for card in cards:
            item = _parse_card(card, section)
            if item:
                out.append(item)
        if page % 50 == 0:
            log(f"  lpw/{section}: page {page}, {len(out)} cards")
    log(f"  lpw/{section}: finished with {len(out)} cards")
    return out


def scrape(config: dict, locations: dict, fetcher: Fetcher, log=print) -> list[dict]:
    max_pages = config["lpw_max_pages_per_section"]
    with ThreadPoolExecutor(max_workers=len(SECTIONS)) as pool:
        futures = [pool.submit(_scrape_section, fetcher, s, max_pages, log) for s in SECTIONS]
        cards = [item for fut in futures for item in fut.result()]

    aliases = {norm(k): v for k, v in config.get("lpw_city_aliases", {}).items()}
    wanted_types = set(config["property_types"])
    wanted_districts = set(config["districts"])
    results: dict[str, dict] = {}
    unmatched: Counter = Counter()
    for item in cards:
        if item["type"] not in wanted_types:
            continue
        district = _district_for(item["city"], item["addr"], locations, aliases)
        if district is None:
            unmatched[item["city"] or "(blank)"] += 1
            continue
        if district not in wanted_districts:
            continue
        item["district"] = locations["districts"][district]["name"]
        results.setdefault(item["id"], item)

    if unmatched:
        top = ", ".join(f"{c} ({n})" for c, n in unmatched.most_common(25))
        log(f"  lpw: {sum(unmatched.values())} listings in towns with no known district "
            f"(add to lpw_city_aliases in config.json if wanted): {top}")
    return list(results.values())

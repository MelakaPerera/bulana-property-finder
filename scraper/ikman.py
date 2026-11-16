"""ikman.lk scraper.

Listing pages embed their data as JSON in `window.initialData`, so no HTML
parsing is needed. District pages only report the district as the location,
so we walk each town (sub-location) of the chosen districts to get real
town names.
"""
from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor

from .common import Fetcher, clean, norm, parse_int, parse_land_size, parse_price, total_price

BASE = "https://ikman.lk/en/ads"

# ikman category slug -> (deal, property type)
CATEGORIES = {
    "houses-for-sale": ("sale", "house"),
    "land-for-sale": ("sale", "land"),
    "apartments-for-sale": ("sale", "apartment"),
    "house-rentals": ("rent", "house"),
    "apartment-rentals": ("rent", "apartment"),
    "room-annex-rentals": ("rent", "room"),
}

_DATA = re.compile(r"window\.initialData\s*=\s*(\{.*?\})\s*</script>", re.S)


def _page_data(html: str | None) -> dict | None:
    if not html:
        return None
    m = _DATA.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))["serp"]["ads"]["data"]
    except (ValueError, KeyError, TypeError):
        return None


def load_locations(fetcher: Fetcher) -> dict:
    """Return {'districts': {slug: {'name', 'towns': [{'slug','name'}]}},
    'town_to_district': {normalised town name: district slug}}."""
    data = _page_data(fetcher.get(f"{BASE}/colombo/houses-for-sale"))
    if not data or not data.get("locations"):
        raise RuntimeError("Could not read ikman's location list")
    by_id = {v["id"]: v for v in data["locations"].values() if isinstance(v, dict) and "id" in v}
    districts, town_to_district = {}, {}
    for loc in by_id.values():
        if loc.get("parentId") != -1 or loc.get("id") == -1:
            continue
        towns = [by_id[c] for c in loc.get("children", []) if c in by_id]
        districts[loc["slug"]] = {
            "name": loc["name"],
            "towns": [{"slug": t["slug"], "name": t["name"]} for t in towns],
        }
        town_to_district[norm(loc["name"])] = loc["slug"]
    # Towns win over district names where the two collide (e.g. Kandy/Kandy).
    for slug, d in districts.items():
        for t in d["towns"]:
            town_to_district[norm(t["name"])] = slug
    return {"districts": districts, "town_to_district": town_to_district}


def _parse_ad(ad: dict, deal: str, ptype: str, district: str, locations: dict) -> dict | None:
    if not ad.get("id") or not ad.get("slug"):
        return None
    details = clean(ad.get("details"))
    beds = baths = perches = None
    for part in details.split("|"):
        low = part.lower()
        if "bed" in low:
            beds = parse_int(part)
        elif "bath" in low:
            baths = parse_int(part)
        elif "perch" in low or "acre" in low:
            perches = parse_land_size(part)
    price, unit = parse_price(ad.get("price"))
    city = clean(ad.get("location")) or locations["districts"][district]["name"]
    ad_district = locations["town_to_district"].get(norm(city), district)
    return {
        "id": "ik:" + str(ad["id"]),
        "src": "ikman",
        "url": "https://ikman.lk/en/ad/" + ad["slug"],
        "title": clean(ad.get("title")),
        "deal": deal,
        "type": ptype,
        "city": city,
        "district": locations["districts"].get(ad_district, {}).get("name", city),
        "addr": "",
        "price": price,
        "unit": unit,
        "total": total_price(price, unit, perches),
        "beds": beds,
        "baths": baths,
        "perches": perches,
        "sqft": None,
        "img": ad.get("imgUrl") or "",
        "seller": clean(ad.get("shopName")),
    }


def _scrape_area(fetcher, town_slug, category, district, locations, max_pages, wanted_districts):
    deal, ptype = CATEGORIES[category]
    out, page, last_page = [], 1, 1
    while page <= last_page and page <= max_pages:
        url = f"{BASE}/{town_slug}/{category}" + (f"?page={page}" if page > 1 else "")
        data = _page_data(fetcher.get(url))
        if not data:
            break
        ads = (data.get("ads") or []) + (data.get("topAds") or [])
        if not data.get("ads"):
            break
        for ad in ads:
            item = _parse_ad(ad, deal, ptype, district, locations)
            if item and norm(item["district"]) in wanted_districts:
                out.append(item)
        pag = data.get("paginationData") or {}
        if page == 1:
            size = pag.get("pageSize") or 25
            last_page = max(1, math.ceil((pag.get("total") or 0) / size))
        page += 1
    return out


def scrape(config: dict, locations: dict, fetcher: Fetcher, log=print) -> list[dict]:
    wanted_types = set(config["property_types"])
    categories = [c for c, (_, t) in CATEGORIES.items() if t in wanted_types]
    wanted_districts = set()
    jobs = []
    for district in config["districts"]:
        info = locations["districts"].get(district)
        if not info:
            log(f"  ! ikman has no district called '{district}' - skipped")
            continue
        wanted_districts.add(norm(info["name"]))
        for town in info["towns"]:
            for category in categories:
                jobs.append((town["slug"], category, district))

    max_pages = config["ikman_max_pages_per_area"]
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=config["workers"]) as pool:
        futures = [
            pool.submit(_scrape_area, fetcher, t, c, d, locations, max_pages, wanted_districts)
            for t, c, d in jobs
        ]
        for n, fut in enumerate(futures, 1):
            for item in fut.result():
                results.setdefault(item["id"], item)
            if n % 50 == 0 or n == len(futures):
                log(f"  ikman: {n}/{len(futures)} town/category pages done, {len(results)} listings")
    return list(results.values())

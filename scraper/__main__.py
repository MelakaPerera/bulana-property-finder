"""Daily run: scrape both sites, merge with yesterday's data, write the file
the website reads.

    python -m scraper                 # full run
    python -m scraper --max-pages 2   # quick test run
    python -m scraper --only lpw      # one source only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import ikman, lpw
from .common import Fetcher, norm

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "site" / "data" / "listings.json"

# Column order of the compact rows in listings.json (the website reads this).
COLUMNS = [
    "id", "src", "url", "title", "deal", "type", "city", "district", "addr",
    "price", "unit", "total", "beds", "baths", "perches", "sqft", "img",
    "seller", "first", "last", "prev", "changed", "dup",
]


IK_AD = "https://ikman.lk/en/ad/"
IK_IMG = "https://i.ikman-st.com/"
IK_IMG_TAIL = "/142/107/cropped.jpg"
LPW = "https://www.lankapropertyweb.com"


def log(msg: str) -> None:
    print(msg, flush=True)


def pack(item: dict) -> dict:
    """Shorten the long, repetitive URLs to keep the data file small.
    The website (site/app.js) expands them the same way `unpack` does."""
    out = dict(item)
    url, img = out["url"] or "", out["img"] or ""
    if url.startswith(IK_AD):
        slug = url[len(IK_AD):]
        out["url"] = "~" + slug
        if img.startswith(IK_IMG + slug + "/") and img.endswith(IK_IMG_TAIL):
            out["img"] = "~" + img[len(IK_IMG + slug) + 1:-len(IK_IMG_TAIL)]
    elif url.startswith(LPW):
        out["url"] = "^" + url[len(LPW):]
        if img.startswith(LPW):
            out["img"] = "^" + img[len(LPW):]
    return out


def unpack(item: dict) -> dict:
    url, img = item["url"] or "", item["img"] or ""
    if url.startswith("~"):
        slug = url[1:]
        item["url"] = IK_AD + slug
        if img.startswith("~"):
            item["img"] = IK_IMG + slug + "/" + img[1:] + IK_IMG_TAIL
    elif url.startswith("^"):
        item["url"] = LPW + url[1:]
        if img.startswith("^"):
            item["img"] = LPW + img[1:]
    return item


def load_previous() -> tuple[dict[str, dict], dict]:
    if not DATA_FILE.exists():
        return {}, {}
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    cols = raw["columns"]
    return {r[0]: unpack(dict(zip(cols, r))) for r in raw["rows"]}, raw.get("meta", {})


def merge(previous: dict[str, dict], scraped: list[dict], today: str) -> dict[str, dict]:
    merged = dict(previous)
    for item in scraped:
        old = previous.get(item["id"])
        if old is None:
            item.update(first=today, last=today, prev=None, changed=None)
        else:
            item.update(first=old["first"], last=today, prev=old.get("prev"), changed=old.get("changed"))
            if item["price"] and old.get("price") and item["price"] != old["price"] \
                    and item["unit"] == old.get("unit"):
                item.update(prev=old["price"], changed=today)
            # A page that briefly drops a detail should not erase what we knew.
            for key in ("beds", "baths", "perches", "sqft", "img", "price", "total"):
                if item.get(key) in (None, "") and old.get(key) not in (None, ""):
                    item[key] = old[key]
        item["dup"] = None
        merged[item["id"]] = item
    return merged


def expire(listings: dict[str, dict], today: date, days: int, sources_ok: set[str]) -> int:
    """Drop listings not seen for `days` days. A source that failed today is
    left alone so an outage never wipes its listings."""
    cutoff = (today - timedelta(days=days)).isoformat()
    gone = [k for k, v in listings.items() if v["last"] < cutoff and v["src"] in sources_ok]
    for k in gone:
        del listings[k]
    return len(gone)


_STOP = {"for", "sale", "rent", "in", "the", "a", "an", "at", "of", "with", "and", "to", "near", "house", "land", "apartment"}


def _tokens(title: str) -> set[str]:
    return {w for w in norm(title).split() if w not in _STOP and len(w) > 1}


def _same_property(a: dict, b: dict) -> bool:
    for key in ("beds", "perches", "sqft"):
        if a[key] and b[key] and abs(a[key] - b[key]) > 0.03 * max(a[key], b[key]):
            return False
    ta, tb = a["_tok"], b["_tok"]
    similarity = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    if similarity >= 0.6:
        return True
    if a["src"] != b["src"]:
        # Different sites word titles differently: rely on matching facts.
        shared = sum(1 for k in ("beds", "perches", "sqft") if a[k] and b[k])
        ca, cb = norm(a["city"]), norm(b["city"])
        same_town = bool(ca and cb and (ca in cb or cb in ca))
        return shared >= 1 and same_town and similarity >= 0.2
    return False


def mark_duplicates(listings: dict[str, dict]) -> int:
    """Group ads that look like the same property (reposts, or the same
    property on both sites). Sets `dup` to a shared group id."""
    buckets = defaultdict(list)
    for item in listings.values():
        item["dup"] = None
        if item["price"]:
            item["_tok"] = _tokens(item["title"])
            buckets[(item["deal"], item["type"], item["district"], item["price"], item["unit"])].append(item)

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for group in buckets.values():
        if len(group) < 2 or len(group) > 400:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if _same_property(a, b):
                    parent[find(a["id"])] = find(b["id"])

    sizes = defaultdict(int)
    for x in list(parent):
        sizes[find(x)] += 1
    grouped = 0
    for x in list(parent):
        root = find(x)
        if sizes[root] > 1:
            listings[x]["dup"] = root
            grouped += 1
    for item in listings.values():
        item.pop("_tok", None)
    return grouped


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape ikman.lk and LankaPropertyWeb")
    ap.add_argument("--max-pages", type=int, help="limit pages per area/section (for quick tests)")
    ap.add_argument("--only", choices=["ikman", "lpw"], help="scrape one source only")
    args = ap.parse_args()

    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    config["districts"] = [re.sub(r"\s+", "-", d.strip().lower()) for d in config["districts"]]
    if args.max_pages:
        config["ikman_max_pages_per_area"] = args.max_pages
        config["lpw_max_pages_per_section"] = args.max_pages

    started = time.time()
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))  # Sri Lanka time
    today = now.date().isoformat()
    previous, old_meta = load_previous()
    log(f"Run for {today}. {len(previous)} listings carried over from the last run.")

    ik_fetch = Fetcher(config["seconds_between_requests"])
    lpw_fetch = Fetcher(config["seconds_between_requests"])
    locations = ikman.load_locations(ik_fetch)

    scraped, sources_ok, counts = [], set(), {}
    for name, module, fetcher in (("ikman", ikman, ik_fetch), ("lpw", lpw, lpw_fetch)):
        if args.only and args.only != name:
            continue
        log(f"Scraping {name} ...")
        try:
            items = module.scrape(config, locations, fetcher, log)
        except Exception as exc:  # one site failing must not lose the other
            log(f"  ! {name} failed: {exc!r}")
            items = []
        counts[name] = len(items)
        log(f"  {name}: {len(items)} listings ({fetcher.requests_made} requests, {fetcher.failures} failed)")
        had_before = sum(1 for v in previous.values() if v["src"] == name)
        if items and len(items) >= 0.3 * had_before:
            sources_ok.add(name)
        elif had_before:
            log(f"  ! {name} returned far fewer listings than last time ({had_before}); keeping old ones")
        scraped.extend(items)

    if not scraped and not previous:
        log("Nothing scraped and no earlier data - not writing a file.")
        return 1

    listings = merge(previous, scraped, today)
    removed = expire(listings, now.date(), config["expire_after_days"], sources_ok)
    grouped = mark_duplicates(listings)

    new_today = sum(1 for v in listings.values() if v["first"] == today)
    changed_today = sum(1 for v in listings.values() if v.get("changed") == today)
    rows = sorted(listings.values(), key=lambda v: (v["first"], v["id"]), reverse=True)
    meta = {
        "updated": now.isoformat(timespec="minutes"),
        "today": today,
        # Day the database was first built: everything is "first seen" that
        # day, so the website does not flag those as new.
        "baseline": old_meta.get("baseline") or today,
        "districts": sorted({v["district"] for v in rows if v["district"]}),
        "total": len(rows),
        "new_today": new_today if previous else 0,
        "price_changes_today": changed_today,
        "sources_ok": sorted(sources_ok),
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "columns": COLUMNS, "rows": [[p.get(c) for c in COLUMNS] for p in map(pack, rows)]}
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(DATA_FILE)

    log(f"Done in {time.time() - started:.0f}s: {len(rows)} listings "
        f"({new_today} new, {changed_today} price changes, {removed} expired, "
        f"{grouped} in duplicate groups). Wrote {DATA_FILE.relative_to(ROOT)} "
        f"({DATA_FILE.stat().st_size / 1e6:.1f} MB)")
    return 0 if sources_ok or args.only else 1


if __name__ == "__main__":
    sys.exit(main())

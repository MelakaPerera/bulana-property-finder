"""Shared helpers: polite HTTP fetching and text/price/size parsing."""
from __future__ import annotations

import re
import threading
import time

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

PERCHES_PER_ACRE = 160.0


class Fetcher:
    """One per site. Thread-safe, spaces requests out, retries on failure."""

    def __init__(self, min_interval: float = 0.4, timeout: int = 40):
        self.min_interval = min_interval
        self.timeout = timeout
        self.requests_made = 0
        self.failures = 0
        self.last_error = ""
        self._lock = threading.Lock()
        self._next_at = 0.0
        self._local = threading.local()

    def _session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
            self._local.session = s
        return s

    def _wait_turn(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_at)
            self._next_at = start + self.min_interval
        if start > now:
            time.sleep(start - now)

    def get(self, url: str, retries: int = 3) -> str | None:
        """Return page text, or None if the page is missing or keeps failing."""
        for attempt in range(retries):
            self._wait_turn()
            try:
                r = self._session().get(url, timeout=self.timeout)
                self.requests_made += 1
                if r.status_code == 200:
                    return r.content.decode("utf-8", "replace")
                if r.status_code in (404, 410):
                    return None
                # 403 / 429 / 5xx: back off and retry
                self.last_error = f"HTTP {r.status_code} for {url} (server: {r.headers.get('server', '?')})"
            except requests.RequestException as exc:
                self.last_error = f"{type(exc).__name__} for {url}"
            time.sleep(3 * (attempt + 1) ** 2)
        self.failures += 1
        return None


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def norm(text: str | None) -> str:
    """Lowercase, letters/digits only, single spaces. For matching names."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(bn|mn|b|m|k)?\b", re.I)
_MULT = {"k": 1e3, "m": 1e6, "mn": 1e6, "b": 1e9, "bn": 1e9}


def parse_price(text: str | None) -> tuple[int | None, str]:
    """'Rs. 1.8M Per Perch' -> (1800000, 'perch'). Unit is one of
    total, perch, acre, sqft, month, day, year."""
    t = clean(text).lower()
    if not t:
        return None, "total"
    body = re.sub(r"^\s*(rs\.?|lkr)\s*", "", t)
    m = _NUM.search(body)
    if not m:
        return None, "total"
    value = float(m.group(1).replace(",", "")) * _MULT.get((m.group(2) or "").lower(), 1)
    rest = body[m.end():]
    unit = "total"
    for key, name in (
        ("perch", "perch"), ("acre", "acre"), ("sq", "sqft"),
        ("month", "month"), ("day", "day"), ("night", "day"),
        ("year", "year"), ("annum", "year"),
    ):
        if key in rest:
            unit = name
            break
    if value <= 0:
        return None, unit
    return int(round(value)), unit


def parse_land_size(text: str | None) -> float | None:
    """'12.5 perches' / '14 P' / '2 acres' / '1.5 A' -> size in perches."""
    t = clean(text).lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(perch(?:es)?|acres?|p|a)\b", t)
    if not m:
        return None
    value = float(m.group(1))
    if m.group(2).startswith("a"):
        value *= PERCHES_PER_ACRE
    return round(value, 2) if value > 0 else None


def parse_int(text: str | None) -> int | None:
    m = re.search(r"\d+", (text or "").replace(",", ""))
    return int(m.group()) if m else None


def total_price(price: int | None, unit: str, perches: float | None) -> int | None:
    """Whole-property price where it can be worked out (sale listings)."""
    if price is None:
        return None
    if unit == "total":
        return price
    if unit == "perch" and perches:
        return int(round(price * perches))
    if unit == "acre" and perches:
        return int(round(price * perches / PERCHES_PER_ACRE))
    return None

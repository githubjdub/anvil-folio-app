"""Read Finviz ex-dates and cash amounts for dividend cross-checking.

Finviz's public dividendsData does NOT contain payment dates.  Nothing in
this adapter is a payment-date source or substitutes an ex-date for one.
"""
from datetime import date
from http.client import IncompleteRead
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request

_MAX_BYTES = 2_000_000
_MAX_EVENTS = 10_000
_SYMBOL = re.compile(r"[A-Z0-9.-]{1,12}\Z")
_DATA_KEY = re.compile(r'"dividendsData"\s*:')


def _base(symbol):
    return {
        "status": "unavailable",
        "source": "Finviz",
        "source_url": "https://finviz.com/quote.ashx?" + urllib.parse.urlencode({"t": symbol, "ty": "dv"}),
        "rows": [],
        "note": "Finviz's public history contains ex-dates and cash amounts, not payment dates.",
    }


def parse_finviz_reference(document, symbol):
    """Return exact amounts from embedded JSON, without scraping rounded cells."""
    result = _base(symbol)
    matches = list(_DATA_KEY.finditer(document))
    # Do not silently choose between duplicate or ambiguous embedded datasets.
    if len(matches) != 1:
        return result
    try:
        raw, _ = json.JSONDecoder().raw_decode(document[matches[0].end():].lstrip())
    except (ValueError, json.JSONDecodeError):
        return result
    if not isinstance(raw, list) or len(raw) > _MAX_EVENTS:
        return result
    parsed = {}
    for row in raw:
        if not isinstance(row, dict):
            return result
        ticker = row.get("Ticker")
        if not isinstance(ticker, str) or ticker.upper().replace(".", "-") != symbol.replace(".", "-"):
            return result
        try:
            ex_date = date.fromisoformat(row["Exdate"]).isoformat()
            ordinary, special = row["Ordinary"], row["Special"]
            # JSON bools, strings, nulls and negative amounts are not dividend data.
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in (ordinary, special)):
                return result
            amount = ordinary + special
        except (KeyError, TypeError, ValueError):
            return result
        if not math.isfinite(amount):
            return result
        if amount == 0:
            continue
        if ex_date in parsed and parsed[ex_date] != amount:
            return result
        parsed[ex_date] = amount
    result["status"] = "available"
    result["rows"] = [{"ex_date": key, "amount": value} for key, value in sorted(parsed.items())]
    return result


def fetch_finviz_reference(symbol):
    """Fetch a bounded public dividend history; no credentials or user input."""
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise ValueError("Invalid symbol for dividend reference lookup.")
    result = _base(symbol)
    request = urllib.request.Request(result["source_url"], headers={
        "User-Agent": "Mozilla/5.0 (compatible; Portfoliora dividend reference)",
        "Accept": "text/html",
    })
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = response.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            return result
        document = payload.decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError, IncompleteRead, UnicodeDecodeError):
        return result
    return parse_finviz_reference(document, symbol)

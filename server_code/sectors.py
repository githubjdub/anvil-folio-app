import anvil.secrets
"""Current Finviz classifications for the market value of open positions."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from html.parser import HTMLParser
import math
from threading import Lock
from time import monotonic
import urllib.error
import urllib.parse
import urllib.request


# Finviz's public sector taxonomy; individual symbols are always looked up.
_SECTORS = {
  "basicmaterials": "Basic Materials",
  "communicationservices": "Communication Services",
  "consumercyclical": "Consumer Cyclical",
  "consumerdefensive": "Consumer Defensive",
  "energy": "Energy",
  "financial": "Financial",
  "healthcare": "Healthcare",
  "industrials": "Industrials",
  "realestate": "Real Estate",
  "technology": "Technology",
  "utilities": "Utilities",
}
_UNKNOWN = "Unclassified"
_FUNDS = "Funds / ETFs"
_CACHE = OrderedDict()
_CACHE_LOCK = Lock()
_CACHE_LIMIT = 512


class _ClassificationLinks(HTMLParser):
  """Read Finviz's sector/industry filter links without depending on CSS."""

  def __init__(self):
    super().__init__(convert_charrefs=True)
    self.sectors = set()
    self.is_fund = False

  def handle_starttag(self, tag, attrs):
    if tag != "a":
      return
    href = dict(attrs).get("href") or ""
    try:
      parsed = urllib.parse.urlsplit(href)
    except ValueError:
      return
    if parsed.netloc and parsed.hostname not in ("finviz.com", "www.finviz.com"):
      return
    # Finviz now separates the stock and ETF screeners. Keep the legacy
    # routes for cached pages, without treating other page links as metadata.
    if parsed.path.strip("/") not in (
      "screener", "screener.ashx", "screener/stocks", "screener/etf",
    ):
      return
    for filters in urllib.parse.parse_qs(parsed.query).get("f", []):
      for item in filters.split(","):
        if item.startswith("sec_") and item[4:] in _SECTORS:
          self.sectors.add(_SECTORS[item[4:]])
        if item in ("ind_exchangetradedfund", "etf_sectype_exchangetradedfund"):
          self.is_fund = True


def _sector_from_html(html):
  parser = _ClassificationLinks()
  parser.feed(html)
  parser.close()
  if parser.is_fund:
    return _FUNDS
  # Ambiguous or changed markup must not silently assign an arbitrary sector.
  return next(iter(parser.sectors)) if len(parser.sectors) == 1 else _UNKNOWN


def _lookup_sector(symbol, instrument_type):
  if str(instrument_type or "").upper() in ("ETF", "MUTUALFUND"):
    return _FUNDS
  now = monotonic()
  with _CACHE_LOCK:
    cached = _CACHE.get(symbol)
    if cached is not None:
      expires, sector = cached
      if expires > now:
        _CACHE.move_to_end(symbol)
        return sector
      del _CACHE[symbol]
  url = "https://finviz.com/quote.ashx?" + urllib.parse.urlencode({"t": symbol})
  request = urllib.request.Request(url, headers={
    "User-Agent": "Mozilla/5.0 (compatible; Portfoliora/1.0)",
  })
  try:
    with urllib.request.urlopen(request, timeout=8) as response:
      html = response.read(1500000).decode("utf-8", "replace")
    sector = _sector_from_html(html)
  except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
    # Sector metadata is optional; unavailable data remains visible as unknown.
    sector = _UNKNOWN
  with _CACHE_LOCK:
    _CACHE[symbol] = (monotonic() + (300 if sector == _UNKNOWN else 86400), sector)
    _CACHE.move_to_end(symbol)
    while len(_CACHE) > _CACHE_LIMIT:
      _CACHE.popitem(last=False)
  return sector


def classify_securities(symbols, histories):
  """Look up current classifications for analyzed symbols, including sold ones."""
  with ThreadPoolExecutor(max_workers=4) as pool:
    return dict(zip(symbols, pool.map(
      lambda symbol: _lookup_sector(symbol, histories[symbol].get("instrument_type")),
      symbols,
    )))


def build_sector_allocation(open_holdings, as_of, histories, classifications=None):
  """Group only held securities; sale proceeds and dividend cash are excluded."""
  if classifications is None:
    classifications = classify_securities(
      [holding["symbol"] for holding in open_holdings], histories
    )
  holdings = []
  grouped = {}
  for holding in open_holdings:
    sector = classifications[holding["symbol"]]
    holdings.append(dict(holding, sector=sector))
    group = grouped.setdefault(sector, {"sector": sector, "market_value": 0.0, "tickers": []})
    group["market_value"] += holding["market_value"]
    group["tickers"].append(holding["symbol"])
  total_value = math.fsum(holding["market_value"] for holding in holdings)
  if not math.isfinite(total_value):
    raise ValueError("The total market value must be finite.")
  slices = sorted(grouped.values(), key=lambda group: (-group["market_value"], group["sector"]))
  for group in slices:
    group["weight"] = group["market_value"] / total_value if total_value else 0.0
    group["tickers"].sort()
  return {
    "as_of": as_of, "classification_as_of": date.today().isoformat(),
    "classification_source": "Finviz", "total_value": total_value,
    "slices": slices, "holdings": holdings,
    "unclassified_tickers": sorted(holding["symbol"] for holding in holdings if holding["sector"] == _UNKNOWN),
  }

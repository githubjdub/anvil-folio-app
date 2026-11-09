"""Bounded Finviz quote-page classification fallback, without price lookups."""

from html import unescape
from html.parser import HTMLParser
from http.client import IncompleteRead
import re
import urllib.error
import urllib.parse
import urllib.request


SECTOR_LABELS = {
  "basicmaterials": "Basic Materials", "communicationservices": "Communication Services",
  "consumercyclical": "Consumer Cyclical", "consumerdefensive": "Consumer Defensive",
  "energy": "Energy", "financial": "Financial", "healthcare": "Healthcare",
  "industrials": "Industrials", "realestate": "Real Estate",
  "technology": "Technology", "utilities": "Utilities",
}
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.-]{0,11}\Z")
_MAX_BYTES = 2_000_000


def source_url(symbol):
  return "https://finviz.com/quote.ashx?" + urllib.parse.urlencode({"t": symbol})


class _QuoteParser(HTMLParser):
  def __init__(self):
    super().__init__(convert_charrefs=True)
    self.title = []
    self.in_title = False
    self.tickers = set()
    self.sectors = set()

  def handle_starttag(self, tag, attrs):
    attributes = dict(attrs)
    if tag == "title":
      self.in_title = True
    if tag == "h1" and attributes.get("data-ticker"):
      self.tickers.add(attributes["data-ticker"].upper().replace(".", "-"))
    if tag != "a":
      return
    target = urllib.parse.urlsplit(unescape(attributes.get("href") or ""))
    if target.netloc and target.netloc not in ("finviz.com", "www.finviz.com"):
      return
    if target.path not in ("/screener/stocks", "/screener.ashx", "screener.ashx"):
      return
    for filters in urllib.parse.parse_qs(target.query).get("f", []):
      for value in filters.split(","):
        if value.startswith("sec_") and value[4:] in SECTOR_LABELS:
          self.sectors.add(SECTOR_LABELS[value[4:]])

  def handle_endtag(self, tag):
    if tag == "title":
      self.in_title = False

  def handle_data(self, data):
    if self.in_title:
      self.title.append(data)


def parse_finviz_sector(document, symbol):
  """Require the requested ticker and exactly one canonical sector filter."""
  parser = _QuoteParser()
  parser.feed(document)
  canonical = symbol.upper().replace(".", "-")
  title_symbol = re.match(r"\s*([A-Za-z0-9.-]+)\s+[-–|:]", "".join(parser.title))
  if parser.tickers:
    if parser.tickers != {canonical}:
      return None
  elif not title_symbol or title_symbol.group(1).upper().replace(".", "-") != canonical:
    return None
  return next(iter(parser.sectors)) if len(parser.sectors) == 1 else None


def fetch_finviz_sector(symbol):
  """Return None for provider denial, unavailable quotes, or changed markup."""
  if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
    raise ValueError("Invalid symbol for Finviz sector lookup.")
  request = urllib.request.Request(source_url(symbol), headers={
    "User-Agent": "Mozilla/5.0 (compatible; Folio sector classification)",
    "Accept": "text/html",
  })
  try:
    with urllib.request.urlopen(request, timeout=8) as response:
      payload = response.read(_MAX_BYTES + 1)
    if len(payload) > _MAX_BYTES:
      return None
    document = payload.decode("utf-8")
  except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
          ConnectionError, IncompleteRead, UnicodeDecodeError):
    return None
  return parse_finviz_sector(document, symbol)

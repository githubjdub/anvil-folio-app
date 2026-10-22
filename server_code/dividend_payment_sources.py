"""Public dividend payment history fallback; never infer a missing payment date."""

from datetime import datetime
from html.parser import HTMLParser
from http.client import IncompleteRead
import math
import re
import urllib.error
import urllib.request


_MAX_BYTES = 2_000_000
_MAX_ROWS = 1_000
_SOURCE = "Stock Analysis"
_UNAVAILABLE = "Stock Analysis payment history could not be retrieved automatically."
_UNVERIFIED = "Stock Analysis did not provide verifiable USD payment history for this listing."


class _DividendTableParser(HTMLParser):
    """Read header-labelled cells without executing a site's JavaScript."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonical = None
        self.tables = []
        self.text = []
        self._table = None
        self._row = None
        self._cell = None
        self._depth = 0
        self._skip_text = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and "canonical" in (attrs.get("rel") or "").split():
            self.canonical = attrs.get("href")
        if tag in ("script", "style"):
            self._skip_text += 1
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._table = []
        elif tag == "tr" and self._depth == 1:
            self._row = []
        elif tag in ("td", "th") and self._depth == 1 and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if not self._skip_text:
            self.text.append(data)
        if self._cell is not None and self._row is not None and self._depth == 1:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip_text = max(0, self._skip_text - 1)
        if tag in ("td", "th") and self._cell is not None and self._row is not None and self._depth == 1:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._depth == 1:
            if self._table is not None and len(self._table) <= _MAX_ROWS:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            if self._depth == 1 and self._table is not None:
                self.tables.append(self._table)
                self._table = self._row = self._cell = None
            self._depth -= 1


def source_url(symbol, instrument_type):
    """Only construct provider URLs for the app's already-validated US symbols."""
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,11}", symbol):
        raise ValueError("A validated US ticker is required for dividend lookup.")
    namespace = "etf" if str(instrument_type).upper() == "ETF" else "stocks"
    return "https://stockanalysis.com/{}/{}/dividend/".format(namespace, symbol.lower())


def _date(text):
    try:
        return datetime.strptime(text, "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def parse_rows(html, symbol, instrument_type):
    """Return normalized cash rows only from the matching USD listing's table.

    Component rows are retained individually. The caller must match their sum to
    the historical cash amount and require one unambiguous payment date.
    """
    url = source_url(symbol, instrument_type)
    namespace = "etf" if str(instrument_type).upper() == "ETF" else "stocks"
    if not isinstance(html, str) or len(html) > _MAX_BYTES:
        return [], _UNVERIFIED
    parser = _DividendTableParser()
    parser.feed(html)
    parser.close()
    if parser.canonical != url:
        return [], _UNVERIFIED
    # Match the page's primary listing metadata, not a related-stock link.
    identity = re.search(r'info:\{type:"([^"\n]+)",subtype:"[^"\n]+",symbol:"([^"\n]+)",ticker:"([^"\n]+)"', html)
    if not identity or identity.groups() != (namespace, symbol.lower(), symbol):
        return [], _UNVERIFIED
    visible = " ".join(" ".join(parser.text).split())
    listing = r"\b(?:NYSE|NASDAQ|NYSEARCA|NYSEAMERICAN|BATS|CBOE):\s*" + re.escape(symbol) + r"\s*·[^·]*·\s*USD\b"
    if not re.search(listing, visible):
        return [], _UNVERIFIED
    currency = re.search(r'curr:\{([^}]+)\}', html)
    if currency:
        dividend_currency = re.search(r'\bdividend:"([^"\n]+)"', currency.group(1))
        if not dividend_currency or dividend_currency.group(1) != "USD":
            return [], _UNVERIFIED
    elif namespace != "etf":
        return [], _UNVERIFIED
    required = {"exdividenddate": "ex_date", "exdivdate": "ex_date",
                "cashamount": "amount", "amount": "amount",
                "paydate": "payment_date", "paymentdate": "payment_date"}
    matches = []
    for table in parser.tables:
        if not table:
            continue
        columns = {}
        for index, heading in enumerate(table[0]):
            key = re.sub(r"[^a-z]", "", heading.lower())
            if key in required:
                field = required[key]
                if field in columns:
                    return [], _UNVERIFIED
                columns[field] = index
        if set(columns) == {"ex_date", "amount", "payment_date"}:
            matches.append((table, columns))
    # Multiple competing dividend tables or silently truncated history are ambiguous.
    if len(matches) != 1 or len(matches[0][0]) > _MAX_ROWS:
        return [], _UNVERIFIED
    table, columns = matches[0]
    rows = []
    for row in table[1:]:
        if len(row) != len(table[0]):
            continue
        ex_date = _date(row[columns["ex_date"]])
        payment_date = _date(row[columns["payment_date"]])
        amount_text = row[columns["amount"]]
        if not ex_date or not payment_date or not re.fullmatch(r"\$(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", amount_text):
            continue
        amount = float(amount_text[1:].replace(",", ""))
        if not math.isfinite(amount) or amount <= 0:
            continue
        rows.append({"ex_date": ex_date, "payment_date": payment_date,
                     "amount": amount, "currency": "USD", "type": "Cash",
                     "source": _SOURCE, "source_url": url})
    return rows, (None if rows else "Stock Analysis has no matched payment-date rows available for this listing.")


def fetch_rows(symbol, instrument_type):
    """Fetch public payment rows; expected provider failures leave dates unresolved."""
    url = source_url(symbol, instrument_type)
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; Portfoliora/1.0)",
        "Accept": "text/html",
    })
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            if response.geturl() != url:
                return [], _UNVERIFIED
            payload = response.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            return [], _UNVERIFIED
        html = payload.decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError, IncompleteRead, UnicodeDecodeError):
        return [], _UNAVAILABLE
    return parse_rows(html, symbol, instrument_type)

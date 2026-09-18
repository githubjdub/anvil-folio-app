import html
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import anvil.server


_USER_AGENT = "Mozilla/5.0 (compatible; Portfoliora/1.0)"
_GOOGLE_TIMEOUT = 15
_YAHOO_TIMEOUT = 15


def _fetch_text(url, timeout):
  request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
  with urllib.request.urlopen(request, timeout=timeout) as response:
    return response.read().decode("utf-8", "replace")


def _normalise_ticker(raw_ticker):
  raw = str(raw_ticker or "").strip().upper()
  parts = raw.split(":", 1)
  symbol = parts[0].strip()
  exchange = parts[1].strip() if len(parts) == 2 else "NASDAQ"

  if not re.match(r"^[A-Z0-9.-]{1,12}$", symbol):
    return None, None
  if not re.match(r"^[A-Z0-9.-]{1,12}$", exchange):
    return None, None
  return symbol, exchange


def _as_date(value):
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  return date.fromisoformat(str(value))


def _number_from_text(value):
  cleaned = str(value).replace(",", "")
  cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
  return float(cleaned) if cleaned else None


def _google_finance_quote(symbol, exchange):
  encoded_symbol = urllib.parse.quote(symbol, safe=".-")
  encoded_exchange = urllib.parse.quote(exchange, safe=".-")
  url = (
    "https://www.google.com/finance/quote/%s:%s?hl=en&gl=US"
    % (encoded_symbol, encoded_exchange)
  )
  page = _fetch_text(url, _GOOGLE_TIMEOUT)

  quote_pattern = (
    r'\["%s","%s"\],"([^"]+)",0,"([^"]+)",\[([^\]]+)\]'
    % (re.escape(symbol), re.escape(exchange))
  )
  quote_match = re.search(quote_pattern, page)
  if not quote_match:
    return {
      "ok": False,
      "message": "Google Finance could not find %s:%s." % (symbol, exchange),
    }

  values = [part.strip() for part in quote_match.group(3).split(",")]
  try:
    current_price = float(values[0])
  except (IndexError, TypeError, ValueError):
    return {
      "ok": False,
      "message": "Google Finance returned no live price for %s:%s." % (symbol, exchange),
    }

  company_name = html.unescape(quote_match.group(1))
  currency = quote_match.group(2)
  dividend_match = re.search(
    r'<div class="[^"]*">Dividend</div><div class="[^"]*">([^<]+)</div>',
    page,
  )
  quarterly_match = re.search(
    r'<div class="[^"]*">Quarterly dividend</div><div class="[^"]*">([^<]+)</div>',
    page,
  )
  ex_date_match = re.search(
    r'<div class="[^"]*">Ex-dividend date</div><div class="[^"]*">([^<]+)</div>',
    page,
  )

  return {
    "ok": True,
    "company_name": company_name,
    "current_price": current_price,
    "currency": currency,
    "dividend_yield_pct": _number_from_text(dividend_match.group(1)) if dividend_match else None,
    "quarterly_dividend": _number_from_text(quarterly_match.group(1)) if quarterly_match else None,
    "ex_dividend_date": html.unescape(ex_date_match.group(1)).strip() if ex_date_match else None,
    "source_url": url,
  }


def _yahoo_history(symbol, bought_date, as_of):
  yahoo_symbol = symbol.replace(".", "-")
  period_start = datetime.combine(
    bought_date - timedelta(days=10), datetime.min.time(), tzinfo=timezone.utc
  )
  period_end = datetime.combine(
    as_of + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc
  )
  query = urllib.parse.urlencode({
    "period1": int(period_start.timestamp()),
    "period2": int(period_end.timestamp()),
    "interval": "1d",
    "events": "div,splits",
  })
  url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?%s" % (
    urllib.parse.quote(yahoo_symbol, safe="-"),
    query,
  )
  payload = json.loads(_fetch_text(url, _YAHOO_TIMEOUT))
  results = payload.get("chart", {}).get("result") or []
  if not results:
    return {
      "ok": False,
      "message": "No historical price data was found for %s on %s." % (
        symbol, bought_date.isoformat()
      ),
    }

  result = results[0]
  timestamps = result.get("timestamp") or []
  closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
  dated_closes = []
  for timestamp, close in zip(timestamps, closes):
    if close is not None:
      close_date = datetime.fromtimestamp(int(timestamp), timezone.utc).date()
      dated_closes.append((close_date, float(close)))

  if not dated_closes:
    return {
      "ok": False,
      "message": "No historical price data was found for %s." % symbol,
    }

  on_or_before = [item for item in dated_closes if item[0] <= bought_date]
  entry_date, entry_price = (on_or_before[-1] if on_or_before else dated_closes[0])

  dividend_events = (result.get("events") or {}).get("dividends") or {}
  dividends_per_share = 0.0
  for timestamp, event in dividend_events.items():
    event_date = datetime.fromtimestamp(int(timestamp), timezone.utc).date()
    if bought_date <= event_date <= as_of:
      dividends_per_share += float(event.get("amount", 0))

  return {
    "ok": True,
    "entry_date_used": entry_date.isoformat(),
    "entry_price": entry_price,
    "dividends_per_share": dividends_per_share,
  }


@anvil.server.callable
def lookup_market_data(ticker, trade_date, as_of_date=None):
  """Return Google Finance live data plus historical transaction data."""
  symbol, exchange = _normalise_ticker(ticker)
  if not symbol:
    return {"ok": False, "message": "Enter a ticker such as AAPL or AAPL:NASDAQ."}

  try:
    bought_date = _as_date(trade_date)
    as_of = _as_date(as_of_date) if as_of_date else date.today()
  except (TypeError, ValueError):
    return {"ok": False, "message": "Enter a valid transaction date for %s." % symbol}

  if bought_date > as_of:
    return {"ok": False, "message": "%s cannot be dated after the analysis date." % symbol}

  google_quote = _google_finance_quote(symbol, exchange)
  if not google_quote["ok"]:
    return google_quote

  historical = _yahoo_history(symbol, bought_date, as_of)
  if not historical["ok"]:
    return historical

  return {
    "ok": True,
    "ticker": symbol,
    "exchange": exchange,
    "company_name": google_quote["company_name"],
    "currency": google_quote["currency"],
    "current_price": google_quote["current_price"],
    "entry_price": historical["entry_price"],
    "entry_date_used": historical["entry_date_used"],
    "dividends_per_share": historical["dividends_per_share"],
    "quarterly_dividend": google_quote["quarterly_dividend"],
    "dividend_yield_pct": google_quote["dividend_yield_pct"],
    "ex_dividend_date": google_quote["ex_dividend_date"],
    "source_url": google_quote["source_url"],
    "price_source": "Google Finance",
    "history_source": "Yahoo Finance chart",
    "as_of_date": as_of.isoformat(),
  }

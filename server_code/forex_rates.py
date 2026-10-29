"""Dated ECB reference observations, retrieved through Frankfurter's public API."""

import json
import math
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

import anvil.server


_CURRENCIES = ("EUR", "USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
_CACHE = None
_CACHE_UNTIL = 0
_SOURCE = "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html"


def _read_reference_json(url):
  request = urllib.request.Request(url, headers={"User-Agent": "Folio/1.0", "Accept": "application/json"})
  with urllib.request.urlopen(request, timeout=15) as response:
    return json.loads(response.read(2000000).decode("utf-8"))


def _normalise_observations(rows, today):
  """Only publish complete same-date baskets; cross rates never mix dates."""
  if not isinstance(rows, list):
    raise ValueError("The reference provider returned an unexpected response.")
  daily = {}
  for row in rows:
    if not isinstance(row, dict):
      raise ValueError("The reference provider returned an invalid observation.")
    if not isinstance(row.get("date"), str):
      raise ValueError("The reference provider returned an invalid observation date.")
    day = date.fromisoformat(row["date"])
    quote = row.get("quote")
    rate = row.get("rate")
    if (row.get("base") != "EUR" or quote not in _CURRENCIES[1:]
        or isinstance(rate, bool) or not isinstance(rate, (int, float))
        or not math.isfinite(rate) or rate <= 0 or day > today):
      raise ValueError("The reference provider returned an invalid exchange rate.")
    daily.setdefault(day.isoformat(), {"EUR": 1.0})[quote] = float(rate)
  history = [{"date": day, "rates": daily[day]} for day in sorted(daily)
             if all(currency in daily[day] for currency in _CURRENCIES)]
  if not history:
    raise ValueError("No complete reference-rate observations are available.")
  return {"ok": True, "history": history, "as_of": history[-1]["date"],
          "age_days": (today - date.fromisoformat(history[-1]["date"])).days,
          "source": "ECB via Frankfurter", "source_url": _SOURCE}


@anvil.server.callable
def get_forex_reference_rates():
  """Public market information only; no user balances or portfolio data leave the app."""
  global _CACHE, _CACHE_UNTIL
  now = time.time()
  if _CACHE is not None and now < _CACHE_UNTIL:
    return _CACHE
  today = date.today()
  start = today - timedelta(days=95)
  url = ("https://api.frankfurter.dev/v2/providers/ecb/rates"
         "?base=eur&quotes=usd,gbp,jpy,chf,cad,aud,nzd&from=%s&to=%s"
         % (start.isoformat(), today.isoformat()))
  try:
    data = _read_reference_json(url)
    result = _normalise_observations(data, today)
  except (urllib.error.URLError, TimeoutError):
    return {"ok": False, "message": "Reference rates are temporarily unavailable. Try Refresh rates again."}
  except (ValueError, UnicodeDecodeError):
    return {"ok": False, "message": "The reference provider has no usable rate history right now. Try again later."}
  _CACHE = result
  _CACHE_UNTIL = now + 3600
  return result

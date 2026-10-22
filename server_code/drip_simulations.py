"""Prepare and run historical dividend-reinvestment comparisons."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import IncompleteRead
import json
import math
import urllib.error
import urllib.parse
import urllib.request

import anvil.server

from .portfolio_analytics import AnalysisInputError, _fetch_history, _validate
from .drip_engine import DividendDateError, candidate_events, run_engine, simulation_window
from .dividend_payment_sources import fetch_rows as _stockanalysis_rows
from .finviz_dividend_reference import fetch_finviz_reference


def _nasdaq_rows(symbol, instrument_type):
  asset_class = "etf" if instrument_type == "ETF" else "stocks"
  url = "https://api.nasdaq.com/api/quote/%s/dividends?assetclass=%s" % (
    urllib.parse.quote(symbol, safe="-"), asset_class)
  request = urllib.request.Request(url, headers={
    "User-Agent": "Mozilla/5.0 (compatible; Portfoliora/1.0)",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/",
  })
  try:
    with urllib.request.urlopen(request, timeout=8) as response:
      raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
      return [], "Payment-date history was too large to verify. Automatic fallback sources will be checked."
    payload = json.loads(raw)
  except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError, IncompleteRead, json.JSONDecodeError, UnicodeDecodeError):
    return [], "Payment-date history is temporarily unavailable. Automatic fallback sources will be checked."
  if not isinstance(payload, dict):
    return [], "Payment-date history was not readable. Automatic fallback sources will be checked."
  data = payload.get("data")
  dividends = data.get("dividends") if isinstance(data, dict) else None
  rows = dividends.get("rows") if isinstance(dividends, dict) else None
  if not isinstance(rows, list):
    return [], "Nasdaq has no payment-date history for this listing. Automatic fallback sources will be checked."
  return rows[:10000], None


def _nasdaq_records(rows):
  records = []
  for row in rows:
    if not isinstance(row, dict) or row.get("currency") != "USD" or row.get("type") != "Cash":
      continue
    try:
      ex_date = datetime.strptime(str(row.get("exOrEffDate")), "%m/%d/%Y").date()
      payment = datetime.strptime(str(row.get("paymentDate")), "%m/%d/%Y").date()
      raw_amount = str(row.get("amount", "")).strip()
      if not raw_amount.startswith("$"):
        continue
      amount = float(raw_amount[1:].replace(",", ""))
    except (ValueError, TypeError):
      continue
    records.append({"ex_date": ex_date.isoformat(), "payment_date": payment.isoformat(),
                    "amount": amount, "currency": "USD", "type": "Cash"})
  return records


def _match_records(events, rows, source, source_url):
  """Match only explicit pay dates with the same ex-date, currency and amount."""
  grouped = {}
  for row in rows:
    if not isinstance(row, dict) or row.get("currency") != "USD" or row.get("type") != "Cash":
      continue
    try:
      ex_date = datetime.strptime(row["ex_date"], "%Y-%m-%d").date().isoformat()
      payment = datetime.strptime(row["payment_date"], "%Y-%m-%d").date().isoformat()
      amount = float(row["amount"])
    except (ValueError, TypeError, KeyError):
      continue
    if not math.isfinite(amount) or amount <= 0:
      continue
    grouped.setdefault(ex_date, []).append((payment, amount))
  for event in events:
    if event.get("payment_date") or event.get("unsupported"):
      continue
    matches = grouped.get(event["ex_date"], [])
    # Duplicate indistinguishable records cannot be distinguished from two
    # equal components; do not double-count them to manufacture a match.
    if len(set(matches)) != len(matches):
      continue
    payments = {payment for payment, amount in matches}
    total_amount = sum(amount for payment, amount in matches)
    if len(payments) != 1 or not math.isclose(total_amount, event["amount"], rel_tol=1e-5, abs_tol=0.000001):
      continue
    payment = next(iter(payments))
    if payment < event["ex_date"]:
      event["unsupported"] = True
      event["note"] = "This distribution requires special due-bill handling and is not supported."
    else:
      event.update(payment_date=payment, source=source, source_url=source_url,
                   note="Payment date matched automatically to the ex-date, USD currency and cash amount.")


def _lookup_payment_dates(events, histories):
  symbols = sorted({event["symbol"] for event in events})
  if not symbols:
    return
  by_symbol = {symbol: [event for event in events if event["symbol"] == symbol] for symbol in symbols}
  def lookup(job):
    provider, symbol = job
    if provider == "Finviz":
      return fetch_finviz_reference(symbol)
    return _nasdaq_rows(symbol, histories[symbol].get("instrument_type"))
  # Public data requests run together. No portfolio, quantities or account
  # identifiers are sent to the providers, only the security symbols.
  jobs = [(provider, symbol) for symbol in symbols for provider in ("Finviz", "Nasdaq")]
  with ThreadPoolExecutor(max_workers=min(12, len(jobs))) as executor:
    fetched = list(executor.map(lookup, jobs))
  references = {}
  for (provider, symbol), result in zip(jobs, fetched):
    if provider == "Finviz":
      references[symbol] = result
    else:
      rows, note = result
      url = "https://www.nasdaq.com/market-activity/%s/%s/dividend-history" % (
        "etf" if histories[symbol].get("instrument_type") == "ETF" else "stocks", symbol.lower())
      _match_records(by_symbol[symbol], _nasdaq_records(rows), "Nasdaq", url)
  missing_symbols = [symbol for symbol in symbols if any(
    not event.get("payment_date") and not event.get("unsupported") for event in by_symbol[symbol])]
  if missing_symbols:
    with ThreadPoolExecutor(max_workers=min(8, len(missing_symbols))) as executor:
      fallback = list(executor.map(lambda symbol: _stockanalysis_rows(symbol, histories[symbol].get("instrument_type")), missing_symbols))
    for symbol, (rows, note) in zip(missing_symbols, fallback):
      source_url = rows[0]["source_url"] if rows else None
      _match_records(by_symbol[symbol], rows, "Stock Analysis", source_url)
      for event in by_symbol[symbol]:
        if not event.get("payment_date") and not event.get("unsupported"):
          event["note"] = note or "No matching payment date was found in the automatic sources. Refresh to retry, or optionally supply a broker-confirmed date."
  for symbol in symbols:
    reference = references[symbol]
    amounts = {row["ex_date"]: row["amount"] for row in reference["rows"]}
    for event in by_symbol[symbol]:
      amount = amounts.get(event["ex_date"])
      event["finviz_checked"] = reference["status"] == "available"
      event["finviz_matched"] = amount is not None and math.isclose(amount, event["amount"], rel_tol=1e-5, abs_tol=0.000001)
      if event["finviz_matched"]:
        event["note"] += " Ex-date and cash amount also match Finviz."
      # Finviz can display split-adjusted historical amounts. A missing or
      # different reference amount does not invalidate a direct pay-date match.


def _prepare(transactions, as_of_date):
  clean, as_of, _ = _validate(transactions, as_of_date, 0)
  start = min(trade["trade_date"] for trade in clean)
  if (as_of - start).days > 3660:
    raise AnalysisInputError("DRIP simulations currently support up to 10 years of transactions.")
  symbols = sorted({trade["symbol"] for trade in clean})
  now = datetime.now(timezone.utc)
  fetch_symbols = symbols + ["^GSPC"]
  with ThreadPoolExecutor(max_workers=min(8, len(fetch_symbols))) as executor:
    fetched = list(executor.map(lambda symbol: _fetch_history(symbol, start, now), fetch_symbols))
  histories = dict(zip(symbols, fetched[:-1]))
  reference_dates = set(fetched[-1]["close"])
  start, end = simulation_window(clean, histories, as_of, reference_dates)
  events = candidate_events(clean, histories, start, end)
  _lookup_payment_dates(events, histories)
  return clean, histories, events, start, end, as_of, reference_dates


def _compare_prepared(prepared, payment_dates=None):
  clean, histories, events, start, end, as_of, reference_dates = prepared
  try:
    result = run_engine(clean, histories, events, start, end, payment_dates, reference_dates)
    result["metadata"]["requested_end_date"] = as_of.isoformat()
    return result
  except DividendDateError as exc:
    return {"ok": False, "message": str(exc), "payment_event_id": exc.event_id}
  except AnalysisInputError as exc:
    return {"ok": False, "message": str(exc)}


@anvil.server.callable
def prepare_drip_simulation(transactions, as_of_date):
  """Look up dates and compare in one call, retaining sources for optional review."""
  try:
    prepared = _prepare(transactions, as_of_date)
  except AnalysisInputError as exc:
    return {"ok": False, "message": str(exc)}
  clean, histories, events, start, end, as_of, reference_dates = prepared
  return {"ok": True, "events": events, "comparison": _compare_prepared(prepared),
    "metadata": {"start_date": start.isoformat(), "end_date": end.isoformat(),
    "requested_end_date": as_of.isoformat(), "currency": "USD", "symbol_count": len(histories),
    "missing_payment_dates": sum(event["payment_date"] is None for event in events),
    "source": "Yahoo Finance ex-dates and amounts; Finviz cross-checks; Nasdaq and Stock Analysis payment dates"}}


@anvil.server.callable
def run_drip_simulation(transactions, as_of_date, payment_dates=None):
  """Revalidate and replay with optional user corrections to automatic dates."""
  try:
    prepared = _prepare(transactions, as_of_date)
  except AnalysisInputError as exc:
    return {"ok": False, "message": str(exc)}
  result = _compare_prepared(prepared, payment_dates)
  result["payment_events"] = prepared[2]
  return result

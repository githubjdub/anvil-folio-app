"""Prepare and run historical dividend-reinvestment comparisons."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import urllib.error
import urllib.parse
import urllib.request

import anvil.server

from .portfolio_analytics import AnalysisInputError, _fetch_history, _validate
from .drip_engine import DividendDateError, candidate_events, run_engine, simulation_window


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
    with urllib.request.urlopen(request, timeout=12) as response:
      raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
      return [], "Payment-date history was too large to verify. Enter dates from your broker or issuer."
    payload = json.loads(raw)
  except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
    return [], "Payment-date history is temporarily unavailable. Enter dates from your broker or issuer."
  if not isinstance(payload, dict):
    return [], "Payment-date history was not readable. Enter dates from your broker or issuer."
  data = payload.get("data")
  dividends = data.get("dividends") if isinstance(data, dict) else None
  rows = dividends.get("rows") if isinstance(dividends, dict) else None
  if not isinstance(rows, list):
    return [], "Nasdaq has no payment-date history for this listing. Enter dates from your broker or issuer."
  return rows[:10000], None


def _match_payment_dates(events, rows, unavailable_note=None):
  """Exact date/currency and amount agreement; ambiguous records stay manual."""
  grouped = {}
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
    if not math.isfinite(amount) or amount <= 0:
      continue
    grouped.setdefault(ex_date.isoformat(), []).append((payment, amount))
  for event in events:
    matches = grouped.get(event["ex_date"], [])
    payments = {payment for payment, amount in matches}
    total_amount = sum(amount for payment, amount in matches)
    # Multiple components are only safe when all are paid together and their
    # aggregate equals Yahoo's aggregate as-traded per-share dividend.
    if len(payments) == 1 and math.isclose(total_amount, event["amount"], rel_tol=1e-5, abs_tol=0.000001):
      payment = next(iter(payments))
      if payment.isoformat() >= event["ex_date"]:
        event.update(payment_date=payment.isoformat(), source="Nasdaq", note="Payment date matched to the ex-date, USD currency and cash amount.")
      else:
        event["unsupported"] = True
        event["note"] = "This distribution requires special due-bill handling and is not supported."
    elif unavailable_note:
      event["note"] = unavailable_note
    elif matches:
      event["note"] = "The providers' cash amounts or payment schedules differ. Verify the payment date with your broker or issuer."


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
  event_symbols = sorted({event["symbol"] for event in events})
  if event_symbols:
    with ThreadPoolExecutor(max_workers=min(8, len(event_symbols))) as executor:
      lookup = list(executor.map(lambda symbol: _nasdaq_rows(symbol, histories[symbol].get("instrument_type")), event_symbols))
    for symbol, (rows, note) in zip(event_symbols, lookup):
      _match_payment_dates([event for event in events if event["symbol"] == symbol], rows, note)
  return clean, histories, events, start, end, as_of, reference_dates


@anvil.server.callable
def prepare_drip_simulation(transactions, as_of_date):
  """Find eligible cash dividends and verified payment dates for review."""
  try:
    clean, histories, events, start, end, as_of, reference_dates = _prepare(transactions, as_of_date)
  except AnalysisInputError as exc:
    return {"ok": False, "message": str(exc)}
  return {"ok": True, "events": events, "metadata": {"start_date": start.isoformat(),
    "end_date": end.isoformat(), "requested_end_date": as_of.isoformat(), "currency": "USD",
    "symbol_count": len(histories), "missing_payment_dates": sum(event["payment_date"] is None for event in events),
    "source": "Yahoo Finance cash amounts and ex-dates; Nasdaq payment dates when matched"}}


@anvil.server.callable
def run_drip_simulation(transactions, as_of_date, payment_dates=None):
  """Revalidate every input and replay both paths using freshly loaded history."""
  try:
    clean, histories, events, start, end, as_of, reference_dates = _prepare(transactions, as_of_date)
    result = run_engine(clean, histories, events, start, end, payment_dates, reference_dates)
    result["metadata"]["requested_end_date"] = as_of.isoformat()
    return result
  except DividendDateError as exc:
    return {"ok": False, "message": str(exc), "payment_event_id": exc.event_id}
  except AnalysisInputError as exc:
    return {"ok": False, "message": str(exc)}

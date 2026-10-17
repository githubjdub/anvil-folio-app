import anvil.secrets
"""Hypothetical long-only paths reconstructed from consecutive 13F snapshots.

Quarterly net changes are executed with hindsight at each quarter's daily low
or high. This is an optimistic scenario, never a reported hedge-fund return.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import re
import urllib.error
import urllib.parse

from .portfolio_analytics import (
  AnalysisInputError, _as_date, _event_date, _fetch_text, _finite_number,
  _history_from_result,
)


_METHOD = (
  "Hypothetical disclosed long-equity scenario, not actual fund performance. "
  "The first quarterly report is seeded at its last trading-session close. "
  "Only net share changes between consecutive quarterly reports are inferred. "
  "Net buys execute at the next quarter's lowest daily Low, and net sells at "
  "its highest daily High, with earliest-date ties. Quarter extrema are "
  "compared in the reporting quarter's ending share units so splits cannot "
  "create false low or high prices. These prices use hindsight "
  "and cannot be known in real time. Splits adjust quantities; ex-date "
  "dividends belong to previous holders and remain uninvested cash. "
  "Buys are beginning-of-session contributions and sale proceeds are "
  "end-of-session withdrawals for time-weighted returns, because fund cash "
  "flows are not disclosed. Short positions, leverage, fees, other assets, "
  "intraperiod round trips, and actual trade dates are unavailable. "
  "After the latest report, disclosed shares are held unchanged except for "
  "splits. Zero-capital sessions do not provide observed daily risk returns."
)


class FundSimulationError(ValueError):
  """A missing disclosure or market-data limitation that can be shown inline."""


def _quarter_number(value):
  match = re.fullmatch(r"(\d{4})\s*Q([1-4])", str(value))
  if not match:
    raise FundSimulationError("A saved filing has an invalid quarter.")
  return int(match[1]) * 4 + int(match[2]) - 1


def _quarter_end(number):
  year, quarter = divmod(number, 4)
  month = (quarter + 1) * 3
  return date(year + 1, 1, 1) - timedelta(days=1) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)


def _number(value, label, positive=False):
  try:
    number = _finite_number(value, label, positive)
  except AnalysisInputError as exc:
    raise FundSimulationError(str(exc)) from exc
  if number < 0:
    raise FundSimulationError("%s cannot be negative." % label)
  return number


def _eligible_security(item):
  put_call = str(item.get("put_call") or item.get("putCall") or "").strip()
  share_type = str(item.get("share_type") or item.get("sshPrnamtType") or "SH").strip().upper()
  return not put_call and share_type == "SH"


def _eligible(item):
  symbol = str(item.get("symbol") or "").strip().upper().replace(".", "-")
  return (symbol if re.fullmatch(r"[A-Z0-9-]{1,12}", symbol)
          and _eligible_security(item) else None)


def _clean_snapshots(snapshots, end_date):
  if not isinstance(snapshots, list) or not snapshots or len(snapshots) > 40:
    raise FundSimulationError("Sync at least one quarterly holdings report first (up to 40 quarters).")
  clean = []
  for item in snapshots:
    if not isinstance(item, dict) or not isinstance(item.get("holdings"), list):
      raise FundSimulationError("A saved report has invalid holdings data. Refresh its filings.")
    quarter = _quarter_number(item.get("quarter"))
    try:
      period_end = _as_date(item.get("period_end"))
    except (TypeError, ValueError) as exc:
      raise FundSimulationError("A saved report has an invalid period end.") from exc
    if period_end != _quarter_end(quarter):
      raise FundSimulationError("A filing quarter does not match its reporting period end.")
    if period_end <= end_date:
      clean.append({"quarter": item["quarter"], "number": quarter,
                    "period_end": period_end, "holdings": item["holdings"],
                    "source_coverage": item.get("coverage") or {}})
  clean.sort(key=lambda item: item["number"])
  if not clean:
    raise FundSimulationError("The selected end date is before the first saved quarterly report.")
  for previous, current in zip(clean, clean[1:]):
    if current["number"] != previous["number"] + 1:
      raise FundSimulationError("Quarterly reports are missing or duplicated between %s and %s. Sync consecutive filings; no missing-quarter trades were inferred." % (previous["quarter"], current["quarter"]))
  return clean


def _split_factor(history, after, through):
  return math.prod(ratio for day, ratio in history.get("splits", {}).items()
                   if after < day <= through)


def _positions(snapshot, histories):
  positions = {}
  total_value = eligible_value = mapped_value = included_value = 0.0
  total_count = included_count = 0
  excluded = []
  for item in snapshot["holdings"]:
    if not isinstance(item, dict):
      raise FundSimulationError("A holdings report contains an invalid position.")
    value = _number(item.get("value", 0), "Reported holding value")
    total_value += value
    total_count += 1
    if _eligible_security(item):
      eligible_value += value
    symbol = _eligible(item)
    if symbol:
      mapped_value += value
    history = histories.get(symbol) if symbol else None
    if not history or history.get("instrument_type") not in (None, "EQUITY", "ETF"):
      excluded.append({"name": item.get("name") or symbol or item.get("cusip") or "Unmapped holding",
                       "symbol": symbol, "value": value})
      continue
    shares = _number(item.get("shares", 0), "Reported share count")
    if not shares:
      continue
    entry = positions.setdefault(symbol, {"symbol": symbol,
      "name": str(item.get("name") or symbol), "shares": 0.0, "value": 0.0})
    entry["shares"] += shares
    entry["value"] += value
    included_value += value
    included_count += 1
  source_coverage = snapshot["source_coverage"]
  if not isinstance(source_coverage, dict):
    raise FundSimulationError("A report has invalid disclosure coverage metadata.")
  reported_value = _number(source_coverage.get("reported_value", total_value), "Reported 13F value")
  return positions, {"quarter": snapshot["quarter"],
    "period_end": snapshot["period_end"].isoformat(),
    "reported_positions": total_count, "included_positions": included_count,
    "reported_rows": source_coverage.get("reported_rows", total_count),
    "reported_value": reported_value, "included_value": included_value,
    "eligible_value": eligible_value, "mapped_value": mapped_value,
    "value_fraction": included_value / reported_value if reported_value > 0 else None,
    "eligible_value_fraction": included_value / eligible_value if eligible_value > 0 else None,
    "mapped_value_fraction": mapped_value / eligible_value if eligible_value > 0 else None,
    "excluded_symbols": sorted({item["symbol"] for item in excluded if item["symbol"]}),
    "excluded_option_rows": source_coverage.get("excluded_option_rows", 0),
    "excluded_other_rows": source_coverage.get("excluded_other_rows", 0),
    "confidential_omitted": bool(source_coverage.get("confidential_omitted", False)),
    "source_coverage": source_coverage,
    "excluded_positions": excluded}


def fetch_history(symbol, start, now=None):
  """Extend the app's trusted Yahoo split reversal with completed daily OHLC."""
  now = now or datetime.now(timezone.utc)
  query = urllib.parse.urlencode({
    "period1": int(datetime.combine(start - timedelta(days=7), time(), timezone.utc).timestamp()),
    "period2": int((now + timedelta(days=1)).timestamp()),
    "interval": "1d", "events": "div,splits",
  })
  url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?%s" % (
    urllib.parse.quote(symbol, safe="-"), query)
  try:
    payload = json.loads(_fetch_text(url, 20))
  except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
    raise FundSimulationError("Daily price history for %s is temporarily unavailable." % symbol) from exc
  except json.JSONDecodeError as exc:
    raise FundSimulationError("The price provider returned unreadable history for %s." % symbol) from exc
  results = (payload.get("chart") or {}).get("result") or []
  if not results:
    raise FundSimulationError("No daily price history is available for %s." % symbol)
  result = results[0]
  try:
    history = _history_from_result(symbol, result, max(now, datetime.now(timezone.utc)))
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    for field in ("low", "high"):
      values = {}
      for timestamp, value in zip(result.get("timestamp") or [], quote.get(field) or []):
        day = _event_date(timestamp)
        if day in history["close"] and value is not None:
          values[day] = _finite_number(value, "%s historical %s" % (symbol, field), True) * _split_factor(history, day, date.max)
      history[field] = values
  except AnalysisInputError as exc:
    raise FundSimulationError(str(exc)) from exc
  return history


def _build_series(snapshots, end_date, histories, benchmark_symbol):
  snapshots = _clean_snapshots(snapshots, end_date)
  start_period = snapshots[0]["period_end"]
  warnings = []
  coverage = []
  positions = []
  for snapshot in snapshots:
    position, covered = _positions(snapshot, histories)
    positions.append(position)
    coverage.append(covered)
  if not positions[0]:
    raise FundSimulationError("The first saved report has no mapped long-equity positions with usable US price history.")
  if not all(positions):
    warnings.append("At least one report has no included long equities; zero-capital sessions are excluded from daily risk observations.")
  for item in coverage:
    if item["included_positions"] < item["reported_positions"]:
      warnings.append("%s: %d of %d saved holdings are included (%s of reported 13F value). Options, unmapped securities, and unavailable histories are excluded." % (
        item["quarter"], item["included_positions"], item["reported_positions"],
        "%.1f%%" % (100 * item["value_fraction"]) if item["value_fraction"] is not None else "unknown share"))
    if item["excluded_option_rows"] or item["excluded_other_rows"]:
      warnings.append("%s excludes %s option rows and %s other non-equity rows disclosed in the source filing." % (
        item["quarter"], item["excluded_option_rows"], item["excluded_other_rows"]))
    if item["confidential_omitted"]:
      warnings.append("%s reports confidential holdings omitted from the public filing. Coverage measures only the publicly disclosed holdings." % item["quarter"])
  benchmark = histories.get(benchmark_symbol) or {}
  benchmark_close = benchmark.get("close", {})
  if not benchmark_close:
    raise FundSimulationError("S&P 500 daily history is unavailable. Try again later.")
  start = max((day for day in benchmark_close if day <= start_period), default=None)
  if start is None or (start_period - start).days > 7:
    raise FundSimulationError("S&P 500 history does not cover the initial reporting quarter.")
  end = min(end_date, max(benchmark_close))
  for symbol in positions[-1]:
    available = [day for day in histories[symbol].get("close", {}) if day <= end]
    if not available:
      raise FundSimulationError("%s has no close near the selected endpoint." % symbol)
    end = min(end, max(available))
  if end < snapshots[-1]["period_end"]:
    # A weekend quarter end uses its immediately preceding trading close.
    latest_session = max((day for day in benchmark_close if day <= snapshots[-1]["period_end"]), default=None)
    if latest_session is None or end < latest_session:
      raise FundSimulationError("Completed prices do not yet cover the latest saved reporting quarter.")
  if end < end_date:
    warnings.append("The comparison ends on %s, the latest shared completed price session. Intraday quotes are excluded." % end.isoformat())
  sessions = sorted(day for day in benchmark_close if start <= day <= end)
  calendar = histories.get("^GSPC", {}).get("close", {})
  if calendar and any(day not in benchmark_close for day in calendar if start <= day <= end):
    raise FundSimulationError("The S&P 500 benchmark has missing daily sessions. Try again later.")
  if not sessions:
    raise FundSimulationError("No completed trading sessions exist for this comparison.")
  plans = []
  for previous, current, prior_positions, target_positions in zip(snapshots, snapshots[1:], positions, positions[1:]):
    quarter_start = previous["period_end"] + timedelta(days=1)
    for symbol in sorted(set(prior_positions) | set(target_positions)):
      history = histories[symbol]
      previous_shares = prior_positions.get(symbol, {}).get("shares", 0.0)
      previous_at_end = previous_shares * _split_factor(history, previous["period_end"], current["period_end"])
      target_shares = target_positions.get(symbol, {}).get("shares", 0.0)
      delta = target_shares - previous_at_end
      if abs(delta) <= max(abs(previous_at_end), abs(target_shares), 1) * 1e-10:
        continue
      field = "low" if delta > 0 else "high"
      values = history.get(field, {})
      candidates = [(values[day] / _split_factor(history, day, current["period_end"]), day) for day in sessions
                    if quarter_start <= day <= current["period_end"] and day in values]
      expected = [day for day in sessions if quarter_start <= day <= current["period_end"]
                  and day in history.get("close", {})]
      if not candidates or any(day not in values for day in expected):
        raise FundSimulationError("%s has incomplete daily %s data in %s. No favorable trade price was estimated." % (symbol, field, current["quarter"]))
      _, day = min(candidates, key=lambda item: (item[0] if delta > 0 else -item[0], item[1]))
      price = values[day]
      shares = abs(delta) / _split_factor(history, day, current["period_end"])
      plans.append({"quarter": current["quarter"], "symbol": symbol,
        "name": (target_positions.get(symbol) or prior_positions[symbol])["name"],
        "action": "Buy" if delta > 0 else "Sell", "date": day.isoformat(),
        "shares": shares, "price": price, "value": shares * price,
        "shares_quarter_end": abs(delta),
        "assumption": "Quarter's lowest daily Low (hindsight)" if delta > 0 else "Quarter's highest daily High (hindsight)"})
  plans.sort(key=lambda item: (item["date"], item["action"] != "Buy", item["symbol"]))
  by_day = {}
  for trade in plans:
    by_day.setdefault(_as_date(trade["date"]), []).append(trade)
  holdings = {symbol: item["shares"] / _split_factor(histories[symbol], start, start_period)
              for symbol, item in positions[0].items()}
  dividend_cash = previous_nav = 0.0
  index = 100.0
  series = []
  endpoint_targets = {max(day for day in sessions if day <= snapshot["period_end"]): target
                      for snapshot, target in zip(snapshots[1:], positions[1:])}
  for day in sessions:
    if series:
      for symbol, shares in list(holdings.items()):
        shares *= histories[symbol].get("splits", {}).get(day, 1)
        holdings[symbol] = shares
        dividend_cash += shares * histories[symbol].get("dividends", {}).get(day, 0.0)
    contributions = withdrawals = 0.0
    for trade in by_day.get(day, []):
      symbol, shares = trade["symbol"], trade["shares"]
      held = holdings.get(symbol, 0.0)
      if trade["action"] == "Buy":
        holdings[symbol] = held + shares
        contributions += trade["value"]
      else:
        if held - shares < -max(abs(held), abs(shares), 1) * 1e-10:
          raise FundSimulationError("The inferred %s sale would require shorting after a corporate action. This report sequence cannot be reconstructed safely." % symbol)
        holdings[symbol] = max(0.0, held - shares)
        withdrawals += trade["value"]
    market_value = 0.0
    for symbol, shares in holdings.items():
      if shares > 1e-10:
        close = histories[symbol].get("close", {}).get(day)
        if close is None:
          raise FundSimulationError("%s has a missing close on %s while held. No daily return was filled in." % (symbol, day.isoformat()))
        market_value += shares * _number(close, "%s daily close" % symbol, True)
    nav = _number(market_value + dividend_cash, "Hypothetical portfolio value")
    denominator = previous_nav + contributions
    observed = bool(series) and previous_nav > 0
    if series and denominator > 0:
      index *= (nav + withdrawals) / denominator
    if not math.isfinite(index) or index <= 0:
      raise FundSimulationError("The disclosed scenario produced a nonpositive or nonfinite return index.")
    series.append({"date": day.isoformat(), "index": index,
                   "return_observed": observed, "session_complete": True})
    previous_nav = nav
    if day in endpoint_targets:
      target = endpoint_targets[day]
      for symbol in set(holdings) | set(target):
        expected_shares = target.get(symbol, {}).get("shares", 0.0)
        # If the report ends on a non-trading day, reverse any later split.
        report_end = next(item["period_end"] for item in snapshots[1:] if max(session for session in sessions if session <= item["period_end"]) == day)
        expected_shares /= _split_factor(histories[symbol], day, report_end)
        if abs(holdings.get(symbol, 0.0) - expected_shares) > max(abs(expected_shares), 1) * 1e-8:
          raise FundSimulationError("%s shares do not reconcile with the quarterly report after splits. Refresh its corporate-action data." % symbol)
  benchmark_series = [{"date": day.isoformat(), "index": 100 * benchmark_close[day] / benchmark_close[start],
                       "session_complete": True} for day in sessions]
  return {"ok": True, "series": series, "trades": plans,
    "coverage": coverage, "warnings": warnings, "start_date": start.isoformat(),
    "end_date": end.isoformat(), "benchmark": benchmark_series,
    "benchmark_symbol": benchmark_symbol,
    "benchmark_label": "S&P 500 total return (dividends reinvested)" if benchmark_symbol == "^SP500TR" else "S&P 500 price return (dividends excluded)",
    "method": _METHOD, "dividends": dividend_cash,
    "latest_report": snapshots[-1]["quarter"]}


def build_series(snapshots, end_date, histories, benchmark_symbol="^SP500TR"):
  """Pure simulation entry point: fixture histories use date keys and raw OHLC."""
  try:
    try:
      end_date = _as_date(end_date)
    except (TypeError, ValueError) as exc:
      raise FundSimulationError("Choose a valid comparison end date.") from exc
    if benchmark_symbol not in ("^SP500TR", "^GSPC"):
      raise FundSimulationError("Choose an S&P 500 total-return or price-return benchmark.")
    return _build_series(snapshots, end_date, histories, benchmark_symbol)
  except FundSimulationError as exc:
    return {"ok": False, "message": str(exc), "warnings": [], "coverage": []}


def get_series(snapshots, end_date):
  """Fetch Yahoo daily histories, then reconstruct disclosed net changes."""
  try:
    try:
      end_date = _as_date(end_date)
    except (TypeError, ValueError) as exc:
      raise FundSimulationError("Choose a valid comparison end date.") from exc
    clean = _clean_snapshots(snapshots, end_date)
    mapped = set()
    for snapshot in clean:
      for item in snapshot["holdings"]:
        symbol = _eligible(item) if isinstance(item, dict) else None
        if symbol is not None:
          mapped.add(symbol)
    symbols = sorted(mapped)
    if not symbols:
      raise FundSimulationError("No mapped US equity symbols are available in the saved reports.")
    if len(symbols) > 100:
      raise FundSimulationError("This comparison contains more than 100 mapped symbols. Select a fund with a smaller disclosed equity portfolio.")
    start = clean[0]["period_end"]
    now = datetime.now(timezone.utc)
    histories, failures = {}, []
    def fetch_one(symbol):
      try:
        return symbol, fetch_history(symbol, start, now), None
      except FundSimulationError as exc:
        return symbol, None, str(exc)
    with ThreadPoolExecutor(max_workers=6) as pool:
      for symbol, history, failure in pool.map(fetch_one, symbols + ["^GSPC", "^SP500TR"]):
        if history is not None:
          histories[symbol] = history
        else:
          failures.append((symbol, failure))
    if "^GSPC" not in histories:
      raise FundSimulationError("S&P 500 daily price history is unavailable. Try again later.")
    benchmark_symbol = "^SP500TR" if "^SP500TR" in histories else "^GSPC"
    if benchmark_symbol == "^SP500TR":
      price_days = histories["^GSPC"]["close"]
      total_days = histories["^SP500TR"]["close"]
      initial_session = max((day for day in price_days if day <= start), default=None)
      check_end = min(end_date, max(total_days)) if total_days else end_date
      if (initial_session is None or not total_days
          or any(day not in total_days for day in price_days
                 if initial_session <= day <= check_end)):
        benchmark_symbol = "^GSPC"
    result = _build_series(snapshots, end_date, histories, benchmark_symbol)
    if benchmark_symbol == "^GSPC":
      result["warnings"].append("S&P 500 total-return history was unavailable or incomplete. The comparison explicitly uses the S&P 500 price index, excluding benchmark dividends.")
    for symbol, failure in failures:
      if not symbol.startswith("^"):
        result["warnings"].append("%s is excluded from every report because price history is unavailable: %s" % (symbol, failure))
    return result
  except FundSimulationError as exc:
    return {"ok": False, "message": str(exc), "warnings": [], "coverage": []}

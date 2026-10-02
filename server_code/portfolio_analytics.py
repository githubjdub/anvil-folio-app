"""Daily, cash-flow-adjusted portfolio returns and matching S&P 500 history."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import anvil.server
from .sectors import build_sector_allocation


_EXCHANGES = {"NASDAQ", "NYSE", "NYSEARCA", "AMEX", "BATS", "NYSEAMERICAN"}
_YAHOO_EXCHANGES = {"NMS", "NGM", "NCM", "NYQ", "PCX", "ASE", "BTS", "BATS", "CBOE"}
_NEW_YORK = ZoneInfo("America/New_York")


class AnalysisInputError(ValueError):
  """A validation or market-data issue the user can act on."""


def _as_date(value):
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  return date.fromisoformat(str(value))


def _normalise_ticker(value):
  parts = str(value or "").strip().upper().split(":", 1)
  symbol = parts[0].strip()
  exchange = parts[1].strip() if len(parts) == 2 else "NASDAQ"
  if not re.fullmatch(r"[A-Z0-9.-]{1,12}", symbol):
    return None, None
  return symbol, exchange


def _fetch_text(url, timeout):
  request = urllib.request.Request(url, headers={
    "User-Agent": "Mozilla/5.0 (compatible; Portfoliora/1.0)",
  })
  with urllib.request.urlopen(request, timeout=timeout) as response:
    return response.read().decode("utf-8", "replace")


def _finite_number(value, label, positive=False):
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise AnalysisInputError("%s must be a finite number." % label)
  try:
    number = float(value)
  except (ValueError, OverflowError):
    raise AnalysisInputError("%s must be a finite number." % label)
  if not math.isfinite(number) or (positive and number <= 0):
    raise AnalysisInputError("%s must be %s." % (
      label, "greater than zero" if positive else "a finite number"
    ))
  return number


def _validate(transactions, as_of_date, risk_free_rate):
  try:
    as_of = _as_date(as_of_date)
  except (ValueError, TypeError):
    raise AnalysisInputError("Choose a valid analysis date.")
  if as_of > date.today():
    raise AnalysisInputError("The analysis date cannot be in the future.")
  rate = _finite_number(risk_free_rate, "Risk-free rate")
  if rate <= -1:
    raise AnalysisInputError("The annual risk-free rate must be greater than -100%.")
  if not isinstance(transactions, list) or not 1 <= len(transactions) <= 1000:
    raise AnalysisInputError("Enter between 1 and 1,000 transactions.")
  clean = []
  for row, transaction in enumerate(transactions, 1):
    if not isinstance(transaction, dict):
      raise AnalysisInputError("Row %d: enter a valid transaction." % row)
    symbol, exchange = _normalise_ticker(transaction.get("ticker"))
    if not symbol or exchange not in _EXCHANGES:
      raise AnalysisInputError("Row %d: use a USD stock or ETF listed on a supported US exchange." % row)
    try:
      trade_date = _as_date(transaction.get("trade_date"))
    except (ValueError, TypeError):
      raise AnalysisInputError("Row %d: choose a valid transaction date." % row)
    if trade_date > as_of:
      raise AnalysisInputError("Row %d: the transaction is after the analysis date." % row)
    if trade_date < date(1990, 1, 1):
      raise AnalysisInputError("Historical comparisons support transactions from 1990 onward.")
    action = transaction.get("action")
    if action not in ("Buy", "Sell"):
      raise AnalysisInputError("Row %d: choose Buy or Sell." % row)
    shares = _finite_number(transaction.get("shares"), "Row %d share count" % row, True)
    price = transaction.get("trade_price")
    if price is not None:
      price = _finite_number(price, "Row %d price per share" % row, True)
    clean.append({"symbol": symbol.replace(".", "-"), "ticker": transaction["ticker"],
                  "trade_date": trade_date, "action": action, "shares": shares,
                  "trade_price": price, "row": row})
  if len({transaction["symbol"] for transaction in clean}) > 25:
    raise AnalysisInputError("Analyze at most 25 different symbols in one portfolio.")
  return clean, as_of, rate


def _event_date(timestamp):
  return datetime.fromtimestamp(int(timestamp), timezone.utc).astimezone(_NEW_YORK).date()


def _history_from_result(symbol, result, now):
  meta = result.get("meta") or {}
  if not symbol.startswith("^"):
    if meta.get("currency") != "USD" or meta.get("exchangeName") not in _YAHOO_EXCHANGES:
      raise AnalysisInputError("%s is not a supported USD listing on a US exchange." % symbol)
  events = result.get("events") or {}
  splits = {}
  for timestamp, event in (events.get("splits") or {}).items():
    numerator = _finite_number(event.get("numerator"), "%s split numerator" % symbol, True)
    denominator = _finite_number(event.get("denominator"), "%s split denominator" % symbol, True)
    splits[_event_date(event.get("date", timestamp))] = numerator / denominator

  def later_split_factor(day):
    return math.prod(ratio for split_day, ratio in splits.items() if split_day > day)

  regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
  closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
  prices = {}
  today_local = now.astimezone(_NEW_YORK).date()
  for timestamp, close in zip(result.get("timestamp") or [], closes):
    day = _event_date(timestamp)
    if day > today_local:
      continue
    if day == today_local:
      session_end = datetime.combine(day, time(16), _NEW_YORK).timestamp()
      if regular.get("start") and _event_date(regular["start"]) == day:
        session_end = regular.get("end") or session_end
      if now.timestamp() < session_end:
        continue
    if close is not None:
      prices[day] = _finite_number(close, "%s historical close" % symbol, True) * later_split_factor(day)
  if not prices:
    raise AnalysisInputError("No completed daily price history is available for %s." % symbol)
  dividends = {}
  for timestamp, event in (events.get("dividends") or {}).items():
    day = _event_date(event.get("date", timestamp))
    amount = _finite_number(event.get("amount"), "%s dividend" % symbol)
    if amount < 0:
      raise AnalysisInputError("%s returned an invalid negative dividend." % symbol)
    dividends[day] = dividends.get(day, 0) + amount * later_split_factor(day)
  return {"close": prices, "splits": splits, "dividends": dividends,
          "company_name": meta.get("longName") or meta.get("shortName") or symbol,
          "instrument_type": meta.get("instrumentType")}


def _fetch_history(symbol, start, now):
  # Fetch through today even for a historical cutoff: Yahoo adjusts older closes
  # and dividends for later splits. Those adjustments must first be reversed.
  query = urllib.parse.urlencode({
    "period1": int(datetime.combine(start - timedelta(days=7), time(), timezone.utc).timestamp()),
    "period2": int((now + timedelta(days=1)).timestamp()),
    "interval": "1d", "events": "div,splits",
  })
  url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?%s" % (
    urllib.parse.quote(symbol, safe="-"), query
  )
  try:
    payload = json.loads(_fetch_text(url, 20))
  except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
    raise AnalysisInputError("Historical data for %s is temporarily unavailable. Please try again." % symbol)
  except json.JSONDecodeError:
    raise AnalysisInputError("The history provider returned an unreadable response for %s. Please try again." % symbol)
  results = (payload.get("chart") or {}).get("result") or []
  if not results:
    raise AnalysisInputError("No daily history was found for %s. Check its symbol and listing." % symbol)
  return _history_from_result(symbol, results[0], now)


def _build_analysis(transactions, as_of, rate, histories):
  first_trade = min(transaction["trade_date"] for transaction in transactions)
  total_history = histories["^SP500TR"]["close"]
  price_history = histories["^GSPC"]["close"]
  # Index feeds can publish their most recent completed session at different
  # times. Compare through their shared latest date, retaining internal-gap checks.
  benchmark_end = min(as_of, max(total_history), max(price_history))
  sessions = sorted(day for day in set(total_history) | set(price_history)
                    if first_trade <= day <= benchmark_end)
  if not sessions:
    raise AnalysisInputError("No completed US trading sessions exist in the selected period.")
  for day in sessions:
    if day not in total_history or day not in price_history:
      raise AnalysisInputError("S&P 500 history is missing for %s. Please try again later." % day.isoformat())
  start, end = sessions[0], sessions[-1]
  if (start - first_trade).days > 7:
    raise AnalysisInputError("S&P 500 history does not cover the beginning of this portfolio.")
  by_day = {}
  row_summaries = [{} for transaction in transactions]
  moved_dates = 0
  for transaction in sorted(transactions, key=lambda item: (item["trade_date"], item["action"] != "Buy")):
    day = next((day for day in sessions if day >= transaction["trade_date"]), None)
    if day is None:
      raise AnalysisInputError("Row %d has no completed trading session yet. Analyze it after the market closes." % transaction["row"])
    moved_dates += day != transaction["trade_date"]
    symbol = transaction["symbol"]
    history = histories[symbol]
    if day not in history["close"]:
      raise AnalysisInputError("%s has no daily close on %s; check the transaction date and listing history." % (symbol, day.isoformat()))
    entry_price = transaction["trade_price"]
    if entry_price is None:
      entry_price = history["close"][day]
    transaction = dict(transaction, entry_price=entry_price)
    by_day.setdefault(day, []).append(transaction)
    row_summaries[transaction["row"] - 1] = {
      "entry_price": entry_price, "entry_date_used": day.isoformat(),
      "company_name": history["company_name"], "currency": "USD",
      "current_price": history["close"].get(end),
      "price_source": "Yahoo Finance daily close", "history_source": "Yahoo Finance daily history",
    }

  holdings = {}
  dividend_cash = buy_cost = sale_proceeds = previous_nav = 0.0
  market_value = 0.0
  portfolio_index = 100.0
  series = []
  for day in sessions:
    # Splits change pre-existing quantities before dividends and today's trades.
    # Ex-date dividends belong to yesterday's holders, including today's sellers.
    for symbol, shares in list(holdings.items()):
      history = histories[symbol]
      shares *= history["splits"].get(day, 1)
      holdings[symbol] = shares
      dividend_cash += shares * history["dividends"].get(day, 0)
    contributions = withdrawals = 0.0
    for transaction in by_day.get(day, []):
      symbol, shares = transaction["symbol"], transaction["shares"]
      value = shares * transaction["entry_price"]
      _finite_number(value, "Row %d transaction value" % transaction["row"], True)
      held = holdings.get(symbol, 0)
      if transaction["action"] == "Buy":
        holdings[symbol] = held + shares
        contributions += value
        buy_cost += value
      else:
        remaining = held - shares
        tolerance = max(abs(held), abs(shares)) * 1e-12
        if remaining < -tolerance:
          raise AnalysisInputError("Row %d sells more %s shares than held on %s." % (
            transaction["row"], symbol, day.isoformat()
          ))
        holdings[symbol] = 0.0 if abs(remaining) <= tolerance else remaining
        withdrawals += value
        sale_proceeds += value
    market_value = 0.0
    for symbol, shares in holdings.items():
      if shares > 0:
        close = histories[symbol]["close"].get(day)
        if close is None:
          raise AnalysisInputError("%s has a missing daily close on %s while shares are held. No return was estimated." % (
            symbol, day.isoformat()
          ))
        market_value += shares * close
    nav = market_value + dividend_cash
    denominator = previous_nav + contributions
    return_observed = bool(series) and denominator > 0
    if return_observed:
      portfolio_index *= (nav + withdrawals) / denominator
    _finite_number(portfolio_index, "Portfolio return")
    series.append({"date": day.isoformat(), "portfolio_index": portfolio_index,
                   "return_observed": return_observed,
                   "benchmark_index": 100 * total_history[day] / total_history[start],
                   "market_index": 100 * price_history[day] / price_history[start]})
    previous_nav = nav

  total_return = portfolio_index / 100 - 1
  days = (end - start).days
  uninvested_sessions = sum(not point["return_observed"] for point in series[1:])
  annualized_return = None
  if days > 0 and portfolio_index > 0 and not uninvested_sessions:
    exponent = math.log(portfolio_index / 100) * 365.25 / days
    if exponent < 700:
      annualized_return = math.expm1(exponent)
  profit = market_value + sale_proceeds + dividend_cash - buy_cost
  open_holdings = []
  for symbol, shares in sorted(holdings.items()):
    if shares > 0:
      price = histories[symbol]["close"][end]
      value = _finite_number(shares * price, "%s market value" % symbol, True)
      open_holdings.append({"symbol": symbol, "company_name": histories[symbol]["company_name"],
                            "shares": shares, "price": price, "market_value": value})
  return {
    "ok": True, "series": series, "rows": row_summaries, "open_holdings": open_holdings,
    "metrics": {"buy_cost": buy_cost, "sale_proceeds": sale_proceeds,
                "market_value": market_value, "dividends": dividend_cash,
                "profit": profit, "simple_return": profit / buy_cost if buy_cost else 0,
                "total_return": total_return, "annualized_return": annualized_return,
                "days": days, "holding_count": sum(shares > 0 for shares in holdings.values())},
    "metadata": {"start_date": start.isoformat(), "end_date": end.isoformat(),
                 "source": "Yahoo Finance daily history", "currency": "USD",
                 "benchmark": "S&P 500 total return (^SP500TR)",
                 "market_benchmark": "S&P 500 price index (^GSPC)",
                 "risk_free_rate": rate, "moved_transaction_dates": moved_dates,
                 "minimum_risk_observations": 20,
                 "observed_return_sessions": sum(point["return_observed"] for point in series),
                 "uninvested_sessions": uninvested_sessions,
                 "method": "Daily time-weighted returns; buys are beginning-of-day contributions and sales are end-of-day withdrawals. Dividends remain cash. First completed close is the zero-return baseline; non-trading dates use the next completed session. Splits adjust quantities. Prices and dividends use historical USD data. Zero-capital sessions stay flat on the chart, are excluded from risk observations, and suppress annualized return when present."},
  }


@anvil.server.callable
def analyze_portfolio_history(transactions, as_of_date, risk_free_rate):
  """Analyze completed daily sessions; expected data/input issues are explicit."""
  try:
    transactions, as_of, rate = _validate(transactions, as_of_date, risk_free_rate)
    start = min(transaction["trade_date"] for transaction in transactions)
    symbols = sorted({transaction["symbol"] for transaction in transactions} | {"^SP500TR", "^GSPC"})
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=4) as pool:
      histories = dict(zip(symbols, pool.map(lambda symbol: _fetch_history(symbol, start, now), symbols)))
    analysis = _build_analysis(transactions, as_of, rate, histories)
    analysis["sector_allocation"] = build_sector_allocation(
      analysis.pop("open_holdings"), analysis["metadata"]["end_date"], histories
    )
    return analysis
  except AnalysisInputError as error:
    return {"ok": False, "message": str(error)}

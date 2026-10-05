"""Daily portfolio returns, selectable benchmarks, and cached selection paths."""

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
from .sectors import build_sector_allocation, classify_securities


_EXCHANGES = {"NASDAQ", "NYSE", "NYSEARCA", "AMEX", "BATS", "NYSEAMERICAN"}
_YAHOO_EXCHANGES = {"NMS", "NGM", "NCM", "NYQ", "PCX", "ASE", "BTS", "BATS", "CBOE"}
_NEW_YORK = ZoneInfo("America/New_York")

# Benchmark identities follow the issuers' index/fund descriptions. Prices are
# daily Yahoo history; ETF total returns use its dividend-adjusted close series.
_OPTIONAL_BENCHMARKS = (
  ("nasdaq", "^IXIC", "Nasdaq Composite", "https://indexes.nasdaq.com/Index/Overview/COMP", False),
  ("spy", "SPY", "SPY · S&P 500 ETF", "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy", True),
  ("qqq", "QQQ", "QQQ · Nasdaq-100 ETF", "https://www.invesco.com/qqq-etf/en/home.html", True),
  ("dia", "DIA", "DIA · Dow Jones ETF", "https://www.ssga.com/us/en/institutional/etfs/state-street-spdr-dow-jones-industrial-average-etf-trust-dia", True),
  ("iwm", "IWM", "IWM · Russell 2000 ETF", "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf", True),
  ("vti", "VTI", "VTI · Total US market ETF", "https://investor.vanguard.com/investment-products/etfs/profile/vti", True),
)


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


def _regular_quote(symbol, result, now):
  """Keep a timestamped regular-session mark separate from completed history."""
  meta = result.get("meta") or {}
  regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
  values = [meta.get("regularMarketTime"), regular.get("start"), regular.get("end")]
  if any(isinstance(value, bool) or not isinstance(value, (int, float))
         or not math.isfinite(value) for value in values):
    return None
  quoted, opening, closing = (float(value) for value in values if isinstance(value, (int, float)))
  today = now.astimezone(_NEW_YORK).date()
  if (_event_date(quoted) != today or _event_date(opening) != today
      or not opening <= quoted <= min(now.timestamp(), closing)):
    return None
  price = meta.get("regularMarketPrice")
  if price is None:
    return None
  price = _finite_number(price, "%s latest regular-session price" % symbol, True)
  quote = {"date": today, "price": price,
           "quoted_at": datetime.fromtimestamp(quoted, timezone.utc)}
  indicators = result.get("indicators") or {}
  daily = (indicators.get("quote") or [{}])[0]
  adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
  for index, timestamp in enumerate(result.get("timestamp") or []):
    if _event_date(timestamp) != today:
      continue
    opens, closes = daily.get("open") or [], daily.get("close") or []
    if index < len(opens) and opens[index] is not None:
      quote["open"] = _finite_number(opens[index], "%s session open" % symbol, True)
    if index < len(adjusted) and index < len(closes) and adjusted[index] is not None and closes[index] is not None:
      quote["adjusted_price"] = price * (
        _finite_number(adjusted[index], "%s adjusted session price" % symbol, True)
        / _finite_number(closes[index], "%s session price" % symbol, True)
      )
  return quote


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
  quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
  closes = quote.get("close") or []
  opens = quote.get("open") or []
  adjusted = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
  prices = {}
  opening_prices = {}
  adjusted_prices = {}
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
  for timestamp, opening in zip(result.get("timestamp") or [], opens):
    day = _event_date(timestamp)
    if day in prices and opening is not None:
      opening_prices[day] = _finite_number(opening, "%s historical open" % symbol, True) * later_split_factor(day)
  for timestamp, close in zip(result.get("timestamp") or [], adjusted):
    day = _event_date(timestamp)
    if day in prices and close is not None:
      adjusted_prices[day] = _finite_number(close, "%s adjusted close" % symbol, True)
  latest_quote = _regular_quote(symbol, result, now)
  if not prices and latest_quote is None:
    raise AnalysisInputError("No completed daily price history is available for %s." % symbol)
  dividends = {}
  for timestamp, event in (events.get("dividends") or {}).items():
    day = _event_date(event.get("date", timestamp))
    amount = _finite_number(event.get("amount"), "%s dividend" % symbol)
    if amount < 0:
      raise AnalysisInputError("%s returned an invalid negative dividend." % symbol)
    dividends[day] = dividends.get(day, 0) + amount * later_split_factor(day)
  return {"close": prices, "open": opening_prices, "splits": splits, "dividends": dividends,
          "adjusted_close": adjusted_prices,
          "company_name": meta.get("longName") or meta.get("shortName") or symbol,
          "instrument_type": meta.get("instrumentType"), "latest_quote": latest_quote}


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
  # A valid trade can arrive while the request is in flight, especially when
  # several symbols are fetched in batches. Validate against the receipt time.
  received_at = max(now, datetime.now(timezone.utc))
  return _history_from_result(symbol, results[0], received_at)


def _history_for_session(history, day):
  quote = history.get("latest_quote")
  if not quote or quote["date"] != day:
    return history
  result = dict(history)
  result["close"] = dict(history["close"])
  result["close"][day] = quote["price"]
  result["open"] = dict(history.get("open", {}))
  if quote.get("open") is not None:
    result["open"][day] = quote["open"]
  result["adjusted_close"] = dict(history.get("adjusted_close", {}))
  if quote.get("adjusted_price") is not None:
    result["adjusted_close"][day] = quote["adjusted_price"]
  return result


def _current_session(transactions, as_of, histories, now):
  today = now.astimezone(_NEW_YORK).date()
  if as_of < today:
    return histories, None, None
  last_completed = min(max(histories["^SP500TR"]["close"]),
                       max(histories["^GSPC"]["close"]))
  symbols = {item["symbol"] for item in transactions}
  needed = set()
  for symbol in symbols:
    history = histories[symbol]
    shares = math.fsum(
      item["shares"] * (1 if item["action"] == "Buy" else -1)
      * math.prod(ratio for day, ratio in history["splits"].items()
                  if item["trade_date"] < day <= today)
      for item in transactions if item["symbol"] == symbol
    )
    tolerance = max(item["shares"] for item in transactions if item["symbol"] == symbol) * 1e-12
    if shares > tolerance or any(item["symbol"] == symbol and item["trade_date"] > last_completed
                            for item in transactions):
      needed.add(symbol)
  if (last_completed >= today and all(today in histories[symbol]["close"] for symbol in needed)):
    return histories, None, None
  # Quotes prove that today's regular session has opened. Previous-day quotes
  # cannot turn a pre-market, weekend or holiday date into a trading session.
  current = [history.get("latest_quote") for history in histories.values()
             if history.get("latest_quote") and history["latest_quote"]["date"] == today]
  if not current:
    return histories, None, None
  missing = [symbol for symbol in sorted(needed)
             if not histories[symbol].get("latest_quote")
             or histories[symbol]["latest_quote"]["date"] != today]
  if missing:
    raise AnalysisInputError("Current-session prices for %s are not available yet. Sync again when the quotes update." % ", ".join(missing))
  staged = {symbol: _history_for_session(history, today) for symbol, history in histories.items()}
  quotes = [histories[symbol]["latest_quote"] for symbol in needed] or current
  return staged, today, min(quote["quoted_at"] for quote in quotes)


def _build_analysis(transactions, as_of, rate, histories, now=None):
  now = now or datetime.now(timezone.utc)
  histories, provisional_day, quoted_at = _current_session(transactions, as_of, histories, now)
  first_trade = min(transaction["trade_date"] for transaction in transactions)
  total_history = histories["^SP500TR"]["close"]
  price_history = histories["^GSPC"]["close"]
  # Index feeds can publish their most recent completed session at different
  # times. Compare through their shared latest date, retaining internal-gap checks.
  benchmark_end = min(as_of, max(total_history), max(price_history))
  sessions = sorted(day for day in set(total_history) | set(price_history)
                    if first_trade <= day <= benchmark_end)
  if provisional_day is not None and first_trade <= provisional_day <= as_of and provisional_day not in sessions:
    sessions.append(provisional_day)
  if not sessions:
    raise AnalysisInputError("No completed US trading sessions exist in the selected period.")
  for day in sessions:
    if day != provisional_day and (day not in total_history or day not in price_history):
      raise AnalysisInputError("S&P 500 history is missing for %s. Please try again later." % day.isoformat())
  start, end = sessions[0], sessions[-1]
  if (start - first_trade).days > 7:
    raise AnalysisInputError("S&P 500 history does not cover the beginning of this portfolio.")
  by_day = {}
  row_summaries = [{} for transaction in transactions]
  securities = {symbol: {
    "symbol": symbol, "company_name": histories[symbol]["company_name"],
    "sector": "Unclassified", "buy_cost": 0.0, "sale_proceeds": 0.0,
    "dividends": 0.0, "transaction_count": 0, "start_date": None, "days": [],
  } for symbol in sorted({transaction["symbol"] for transaction in transactions})}
  moved_dates = 0
  for transaction in sorted(transactions, key=lambda item: (item["trade_date"], item["action"] != "Buy")):
    day = next((day for day in sessions if day >= transaction["trade_date"]), None)
    if day is None:
      raise AnalysisInputError("Row %d is waiting for a US trading session and a current-session quote. Sync after the market opens." % transaction["row"])
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
    security = securities[symbol]
    security["transaction_count"] += 1
    if security["start_date"] is None:
      security["start_date"] = day.isoformat()
    row_summaries[transaction["row"] - 1] = {
      "entry_price": entry_price, "entry_date_used": day.isoformat(),
      "company_name": history["company_name"], "currency": "USD",
      "current_price": history["close"].get(end),
      "price_source": "Yahoo Finance latest regular-session quote" if day == provisional_day else "Yahoo Finance daily close",
      "current_price_source": "Yahoo Finance latest regular-session quote" if end == provisional_day else "Yahoo Finance daily close",
      "quoted_at": history.get("latest_quote", {}).get("quoted_at").isoformat() if end == provisional_day and history.get("latest_quote") else None,
      "history_source": "Yahoo Finance daily history",
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
      dividend = shares * history["dividends"].get(day, 0)
      dividend_cash += dividend
      securities[symbol]["dividends"] += dividend
    contributions = withdrawals = 0.0
    security_flows = {symbol: [0.0, 0.0] for symbol in securities}
    for transaction in by_day.get(day, []):
      symbol, shares = transaction["symbol"], transaction["shares"]
      value = shares * transaction["entry_price"]
      _finite_number(value, "Row %d transaction value" % transaction["row"], True)
      held = holdings.get(symbol, 0)
      if transaction["action"] == "Buy":
        holdings[symbol] = held + shares
        contributions += value
        buy_cost += value
        securities[symbol]["buy_cost"] += value
        security_flows[symbol][0] += value
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
        securities[symbol]["sale_proceeds"] += value
        security_flows[symbol][1] += value
    market_value = 0.0
    security_market_values = {}
    for symbol, shares in holdings.items():
      if shares > 0:
        close = histories[symbol]["close"].get(day)
        if close is None:
          raise AnalysisInputError("%s has a missing daily close on %s while shares are held. No return was estimated." % (
            symbol, day.isoformat()
          ))
        value = shares * close
        market_value += value
        security_market_values[symbol] = value
    for symbol, security in securities.items():
      contribution, withdrawal = security_flows[symbol]
      shares = holdings.get(symbol, 0.0)
      position_value = security_market_values.get(symbol, 0.0)
      security["days"].append({
        "nav": position_value + security["dividends"],
        "contributions": contribution, "withdrawals": withdrawal,
        # Allocation snapshots measure invested positions, excluding dividend
        # cash; quantities reflect splits and all of this session's trades.
        "market_value": position_value, "shares": shares,
        "price": histories[symbol]["close"].get(day) if shares > 0 else None,
      })
    nav = market_value + dividend_cash
    denominator = previous_nav + contributions
    return_observed = bool(series) and denominator > 0
    if return_observed:
      portfolio_index *= (nav + withdrawals) / denominator
    _finite_number(portfolio_index, "Portfolio return")
    series.append({"date": day.isoformat(), "portfolio_index": portfolio_index,
                   "return_observed": return_observed,
                   "session_complete": day != provisional_day,
                   "benchmark_index": 100 * total_history[day] / total_history[start] if day in total_history and start in total_history else None,
                   "market_index": 100 * price_history[day] / price_history[start] if day in price_history and start in price_history else None})
    previous_nav = nav

  total_return = portfolio_index / 100 - 1
  days = (end - start).days
  uninvested_sessions = sum(not point["return_observed"] for point in series[1:] if point["session_complete"])
  annualized_return = None
  if days > 0 and portfolio_index > 0 and not uninvested_sessions and provisional_day is None:
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
  for symbol, security in securities.items():
    shares = holdings.get(symbol, 0.0)
    price = histories[symbol]["close"].get(end)
    security.update({"shares": shares, "price": price,
                     "market_value": shares * price if shares > 0 else 0.0})
  return {
    "ok": True, "series": series, "rows": row_summaries, "open_holdings": open_holdings,
    "selection_data": {"dates": [day.isoformat() for day in sessions],
                       "securities": list(securities.values())},
    "metrics": {"buy_cost": buy_cost, "sale_proceeds": sale_proceeds,
                "market_value": market_value, "dividends": dividend_cash,
                "profit": profit, "simple_return": profit / buy_cost if buy_cost else 0,
                "total_return": total_return, "annualized_return": annualized_return,
                "days": days, "holding_count": sum(shares > 0 for shares in holdings.values())},
    "metadata": {"start_date": start.isoformat(), "end_date": end.isoformat(),
                 "source": "Yahoo Finance daily history + latest regular-session quotes" if provisional_day else "Yahoo Finance daily history", "currency": "USD",
                 "provisional_date": provisional_day.isoformat() if provisional_day else None,
                 "quoted_at": quoted_at.isoformat() if quoted_at else None,
                 "completed_end_date": next((point["date"] for point in reversed(series) if point["session_complete"]), None),
                 "benchmark": "S&P 500 total return (^SP500TR)",
                 "market_benchmark": "S&P 500 price index (^GSPC)",
                 "risk_free_rate": rate, "moved_transaction_dates": moved_dates,
                 "minimum_risk_observations": 20,
                 "observed_return_sessions": sum(point["return_observed"] for point in series if point["session_complete"]),
                 "uninvested_sessions": uninvested_sessions,
                 "method": "Time-weighted returns; buys are beginning-of-session contributions and sales are end-of-session withdrawals. Dividends remain cash. The first price mark is the zero-return baseline; non-trading dates use the next available session. Splits adjust quantities. Current-session quotes are provisional and excluded from daily risk estimates. Annualized return is withheld for a provisional endpoint or zero-capital gaps."},
  }


def _benchmark_samples(history, sessions, total_return, provisional_day=None):
  """Normalize the available contiguous daily history to its first close."""
  prices = history.get("adjusted_close", {}) if total_return else history["close"]
  available = [day for day in sessions if day in prices]
  if not available:
    return [], "No %s history is available for this portfolio period." % (
      "dividend-adjusted" if total_return else "daily price"
    )
  start, end = available[0], available[-1]
  expected = [day for day in sessions if start <= day <= end]
  if available != expected:
    return [], "Daily benchmark history has missing sessions. Please sync again later."
  first_price = prices[start]
  series = []
  split_factor = 1.0
  for day in available:
    if not total_return and day > start:
      split_factor *= history["splits"].get(day, 1)
    index = 100 * prices[day] / first_price * split_factor
    series.append({"date": day.isoformat(), "index": _finite_number(index, "Benchmark return", True),
                   "session_complete": day != provisional_day})
  message = ""
  if start != sessions[0] or end != sessions[-1]:
    message = "Daily history is available from %s to %s; comparisons use matching dates." % (
      start.isoformat(), end.isoformat()
    )
  return series, message


def _build_benchmarks(analysis, histories, failures):
  """Keep optional feed problems isolated from the portfolio's own history."""
  points = analysis["series"]
  sessions = [_as_date(point["date"]) for point in points]
  provisional_day = _as_date(analysis["metadata"]["provisional_date"]) if analysis["metadata"].get("provisional_date") else None
  if provisional_day:
    histories = {symbol: _history_for_session(history, provisional_day) for symbol, history in histories.items()}
  benchmarks = {
    "sp500_total": {
      "name": "S&P 500 index · total return (dividends reinvested)",
      "total_return": True, "symbol": "^SP500TR",
      "series": [],
      "source_url": "https://www.spglobal.com/spdji/en/indices/equity/sp-500/",
    },
    "sp500_price": {
      "name": "S&P 500 index · price return (excludes dividends)",
      "total_return": False, "symbol": "^GSPC",
      "series": [],
      "source_url": "https://www.spglobal.com/spdji/en/indices/equity/sp-500/",
    },
  }
  for key, symbol in (("sp500_total", "^SP500TR"), ("sp500_price", "^GSPC")):
    benchmarks[key]["series"], message = _benchmark_samples(histories[symbol], sessions, False, provisional_day)
    if message:
      benchmarks[key]["message"] = message
  for key, symbol, name, source_url, has_total in _OPTIONAL_BENCHMARKS:
    for total_return in ([True, False] if has_total else [False]):
      suffix = "total" if total_return else "price"
      entry = {"name": name + (" · total return (dividends reinvested)" if total_return else
                               " · price return (excludes dividends)"),
               "symbol": symbol, "total_return": total_return, "source_url": source_url,
               "series": []}
      if symbol in failures:
        entry["message"] = failures[symbol]
      else:
        entry["series"], message = _benchmark_samples(histories[symbol], sessions, total_return, provisional_day)
        if message:
          entry["message"] = message
      benchmarks[key + "_" + suffix] = entry
  return benchmarks


@anvil.server.callable
def analyze_portfolio_history(transactions, as_of_date, risk_free_rate):
  """Analyze daily history and the latest available current-session prices."""
  try:
    transactions, as_of, rate = _validate(transactions, as_of_date, risk_free_rate)
    start = min(transaction["trade_date"] for transaction in transactions)
    required = {transaction["symbol"] for transaction in transactions} | {"^SP500TR", "^GSPC"}
    symbols = sorted(required | {entry[1] for entry in _OPTIONAL_BENCHMARKS})
    now = datetime.now(timezone.utc)
    def fetch(symbol):
      try:
        return _fetch_history(symbol, start, now), None
      except AnalysisInputError as error:
        if symbol in required:
          raise
        return None, str(error)
    with ThreadPoolExecutor(max_workers=4) as pool:
      fetched = dict(zip(symbols, pool.map(fetch, symbols)))
    histories = {symbol: result[0] for symbol, result in fetched.items() if result[0] is not None}
    failures = {symbol: result[1] for symbol, result in fetched.items() if result[1] is not None}
    analysis = _build_analysis(transactions, as_of, rate, histories, now=now)
    classifications = classify_securities(
      [security["symbol"] for security in analysis["selection_data"]["securities"]],
      histories,
    )
    analysis["sector_allocation"] = build_sector_allocation(
      analysis.pop("open_holdings"), analysis["metadata"]["end_date"], histories,
      classifications=classifications,
    )
    for security in analysis["selection_data"]["securities"]:
      security["sector"] = classifications.get(security["symbol"], "Unclassified")
    analysis["benchmarks"] = _build_benchmarks(analysis, histories, failures)
    return analysis
  except AnalysisInputError as error:
    return {"ok": False, "message": str(error)}

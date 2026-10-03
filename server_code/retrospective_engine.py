"""Deterministic daily strategy simulation without future-price signals.

Inputs use nominal, as-traded prices, cash dividends per contemporaneous share,
and explicit split ratios. Signal windows are adjusted only for splits already
effective on the signal day. Conditional orders fill at the next session open.
There is one entry and at most one fractional exit per security.
"""

from bisect import bisect_left
from datetime import date, datetime, timedelta
import math


class RetrospectiveInputError(ValueError):
  """A missing-data or strategy configuration issue the user can correct."""


def _date(value) -> date:
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  try:
    return date.fromisoformat(str(value))
  except (TypeError, ValueError):
    raise RetrospectiveInputError("Choose valid start and end dates.") from None


def _number(value, label, positive=False):
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise RetrospectiveInputError("%s must be a finite number." % label)
  try:
    number = float(value)
  except (ValueError, OverflowError):
    raise RetrospectiveInputError("%s must be a finite number." % label) from None
  if not math.isfinite(number) or (positive and number <= 0):
    raise RetrospectiveInputError("%s must be %s." % (
      label, "greater than zero" if positive else "a finite number"
    ))
  return number


def _clean_history(ticker, source, cutoff) -> dict:
  if not isinstance(source, dict):
    raise RetrospectiveInputError("%s has no readable historical data." % ticker)
  clean: dict = {"company_name": source.get("company_name") or ticker}
  for name in ("close", "open", "splits", "dividends"):
    values = source.get(name) or {}
    if not isinstance(values, dict):
      raise RetrospectiveInputError("%s has invalid %s history." % (ticker, name))
    clean[name] = {}
    for raw_day, value in values.items():
      day = _date(raw_day)
      if day > cutoff:
        continue
      number = _number(value, "%s %s on %s" % (ticker, name, day), name != "dividends")
      if name == "dividends" and number < 0:
        raise RetrospectiveInputError("%s has a negative dividend on %s." % (ticker, day))
      clean[name][day] = number
  if not clean["close"]:
    raise RetrospectiveInputError("%s has no completed closing prices." % ticker)
  return clean


def _settings(strategy):
  if not isinstance(strategy, dict):
    raise RetrospectiveInputError("Choose a supported strategy.")
  entry = strategy.get("entry_condition", "low")
  exit_rule = strategy.get("exit_condition", "high")
  if entry not in ("low", "high", "buy_hold") or exit_rule not in ("low", "high", "none"):
    raise RetrospectiveInputError("Choose rolling low/high entry and exit conditions or buy and hold.")
  lookback = strategy.get("lookback_days", 365)
  if isinstance(lookback, bool) or not isinstance(lookback, int) or not 1 <= lookback <= 3650:
    raise RetrospectiveInputError("The lookback must be between 1 and 3,650 calendar days.")
  fraction = _number(strategy.get("sell_fraction", 0.5), "Sale fraction", True)
  if fraction > 1:
    raise RetrospectiveInputError("Sell between more than zero and 100% of the position.")
  proximity = {}
  for prefix in ("entry", "exit"):
    value = _number(strategy.get(prefix + "_proximity_pct", 0), prefix.capitalize() + " proximity")
    if not 0 <= value < 100:
      raise RetrospectiveInputError("%s proximity must be between 0%% and less than 100%%." % prefix.capitalize())
    proximity[prefix] = value / 100
  include_dividends = strategy.get("include_dividends", True)
  if not isinstance(include_dividends, bool):
    raise RetrospectiveInputError("Choose whether cash dividends should be included.")
  return {"entry": entry, "exit": exit_rule, "lookback": lookback,
          "sell_fraction": fraction, "proximity": proximity,
          "include_dividends": include_dividends}


def _state(ticker, history, allocation):
  return {"ticker": ticker, "company_name": history["company_name"],
          "allocation": allocation, "cash": allocation, "shares": 0.0,
          "dividends": 0.0, "buy_cost": 0.0, "sale_proceeds": 0.0,
          "entry_signal_date": None, "entry_date": None,
          "exit_signal_date": None, "exit_date": None,
          "pending": None, "eligible": True, "status_detail": "",
          "buy_hold_cash": allocation, "buy_hold_shares": 0.0,
          "buy_hold_dividends": 0.0}


def _rolling_prices(history):
  """Return price units per initial share, using only splits through each day."""
  days = sorted(history["close"])
  split_days = sorted(history["splits"])
  factor, split_index = 1.0, 0
  prices, factors = [], {}
  for day in days:
    while split_index < len(split_days) and split_days[split_index] <= day:
      factor *= history["splits"][split_days[split_index]]
      split_index += 1
    value = history["close"][day] * factor
    if not math.isfinite(value):
      raise RetrospectiveInputError("Historical split adjustments exceed the supported numeric range.")
    prices.append(value)
    factors[day] = factor
  return days, prices, factors


def _condition(rule, proximity, day, history, window, lookback):
  days, adjusted, factors = window
  lower = bisect_left(days, day - timedelta(days=lookback))
  upper = bisect_left(days, day)
  if lower == upper:
    return None
  previous = adjusted[lower:upper]
  boundary = min(previous) if rule == "low" else max(previous)
  threshold = boundary * (1 + proximity if rule == "low" else 1 - proximity)
  current = history["close"][day] * factors[day]
  met = current <= threshold if rule == "low" else current >= threshold
  if not met:
    return None
  return {"signal_date": day.isoformat(), "signal_close": history["close"][day],
          "threshold": threshold / factors[day],
          "reason": "Completed close reached the prior %d-calendar-day %s%s." % (
            lookback, rule, " proximity threshold" if proximity else ""
          )}


def _record_trade(state, action, day, price, shares, order, trades):
  value = shares * price
  trade = {"ticker": state["ticker"], "company_name": state["company_name"],
           "action": action, "signal_date": order["signal_date"],
           "execution_date": day.isoformat(), "date": day.isoformat(),
           "shares": shares, "price": price, "value": value,
           "signal_close": order.get("signal_close"),
           "threshold": order.get("threshold"), "reason": order["reason"]}
  trades.append(trade)
  if action == "Buy":
    state["cash"] -= value
    # Entry spends exactly its reserved allocation; eliminate roundoff residue.
    if abs(state["cash"]) <= state["allocation"] * 1e-12:
      state["cash"] = 0.0
    state["shares"] += shares
    state["buy_cost"] += value
    state["entry_date"] = day.isoformat()
  else:
    state["cash"] += value
    state["shares"] -= shares
    state["sale_proceeds"] += value
    state["exit_date"] = day.isoformat()
    if abs(state["shares"]) <= shares * 1e-12:
      state["shares"] = 0.0
  state["pending"] = None


def _security_output(state, history, last_day):
  last_price = history["close"].get(last_day)
  market_value = state["shares"] * last_price if state["shares"] else 0.0
  equity = state["cash"] + market_value
  if not state["eligible"]:
    status = "Insufficient prior history"
  elif state["entry_date"] is None:
    status = "Entry awaiting next open" if state["pending"] else "No entry signal"
  elif state["exit_date"] is not None:
    status = "Partially sold" if state["shares"] else "Fully sold"
  else:
    status = "Exit awaiting next open" if state["pending"] else "Holding"
  return {key: state[key] for key in (
    "ticker", "company_name", "allocation", "cash", "shares", "dividends",
    "buy_cost", "sale_proceeds", "entry_signal_date", "entry_date",
    "exit_signal_date", "exit_date", "status_detail"
  )} | {"status": status, "last_price": last_price, "market_value": market_value,
        "ending_equity": equity, "profit": equity - state["allocation"],
        "total_return": equity / state["allocation"] - 1,
        "buy_hold_ending_equity": state["buy_hold_cash"] + (
          state["buy_hold_shares"] * last_price if state["buy_hold_shares"] else 0.0),
        "buy_hold_dividends": state["buy_hold_dividends"]}


def run_backtest(histories, start_date, end_date, capital, strategy, benchmark=None):
  """Simulate equal cash sleeves on completed daily sessions.

  A low/high signal compares the close with *prior* rolling closes; its own
  close is excluded from the window. Orders execute at the next supplied
  session open, without testing that open against the earlier threshold again.
  IPOs lacking a full lookback retain their allocation as idle cash. Missing
  in-period data raises rather than fabricating trades or forward-filled values.
  """
  start, end = _date(start_date), _date(end_date)
  if start > end:
    raise RetrospectiveInputError("The start date must be on or before the end date.")
  initial = _number(capital, "Starting capital", True)
  settings = _settings(strategy)
  if not isinstance(histories, dict) or not 1 <= len(histories) <= 25:
    raise RetrospectiveInputError("Choose between 1 and 25 securities.")
  clean = {ticker: _clean_history(ticker, source, end) for ticker, source in sorted(histories.items())}
  benchmark_history = _clean_history("Benchmark", benchmark, end) if benchmark is not None else None
  session_source = set(benchmark_history["close"]) if benchmark_history else {
    day for history in clean.values() for day in history["close"]
  }
  sessions = sorted(day for day in session_source if start <= day <= end)
  if not sessions:
    raise RetrospectiveInputError("No completed daily sessions exist in the selected period.")
  first_day, last_day = sessions[0], sessions[-1]
  lookback_start = first_day - timedelta(days=settings["lookback"])
  prior_sessions = sorted(day for day in session_source if lookback_start <= day < first_day)
  allocation = initial / len(clean)
  states = {ticker: _state(ticker, history, allocation) for ticker, history in clean.items()}
  windows = {ticker: _rolling_prices(history) for ticker, history in clean.items()}
  warnings = []
  needs_warmup = settings["entry"] != "buy_hold" or settings["exit"] != "none"
  for ticker, history in clean.items():
    if needs_warmup and (min(history["close"]) > lookback_start or
                         not any(lookback_start <= day < first_day for day in history["close"]) or
                         any(day not in history["close"] for day in prior_sessions)):
      states[ticker]["eligible"] = False
      states[ticker]["status_detail"] = "A full %d-calendar-day price lookback before %s is unavailable; its allocation stays in cash." % (
        settings["lookback"], first_day.isoformat()
      )
      warnings.append(ticker + ": " + states[ticker]["status_detail"])
      continue
    for day in sessions:
      for name in ("close", "open"):
        if day not in history[name]:
          raise RetrospectiveInputError("%s has no completed %s on %s; choose a period with complete listing history." % (ticker, name, day.isoformat()))
  if benchmark_history:
    for day in sessions:
      if day not in benchmark_history["open"]:
        raise RetrospectiveInputError("The benchmark has no completed open on %s." % day.isoformat())
    benchmark_state = {"shares": initial / benchmark_history["open"][first_day], "cash": 0.0}
  else:
    benchmark_state = None
  trades, series = [], []
  peak, max_drawdown = initial, 0.0
  for day in sessions:
    for ticker, history in clean.items():
      state = states[ticker]
      if not state["eligible"]:
        continue
      ratio = history["splits"].get(day, 1.0)
      state["shares"] *= ratio
      state["buy_hold_shares"] *= ratio
      # Yesterday's holders receive ex-date entitlement before open fills.
      amount = history["dividends"].get(day, 0.0) if settings["include_dividends"] else 0.0
      dividend = state["shares"] * amount
      state["cash"] += dividend
      state["dividends"] += dividend
      baseline_dividend = state["buy_hold_shares"] * amount
      state["buy_hold_cash"] += baseline_dividend
      state["buy_hold_dividends"] += baseline_dividend
      if day == first_day:
        state["buy_hold_shares"] = allocation / history["open"][day]
        state["buy_hold_cash"] = 0.0
        if settings["entry"] == "buy_hold":
          order = {"signal_date": day.isoformat(), "reason": "Predetermined purchase at the first test session open."}
          state["entry_signal_date"] = day.isoformat()
          _record_trade(state, "Buy", day, history["open"][day], allocation / history["open"][day], order, trades)
      order = state["pending"]
      if order:
        price = history["open"][day]
        if order["action"] == "Buy":
          # Equal initial allocations are reserved, never borrowed from sleeves.
          shares = state["allocation"] / price
        else:
          shares = state["shares"] * settings["sell_fraction"]
        _record_trade(state, order["action"], day, price, shares, order, trades)
      if state["entry_date"] is None and state["pending"] is None:
        signal = _condition(settings["entry"], settings["proximity"]["entry"], day,
                            history, windows[ticker], settings["lookback"])
        if signal:
          state["entry_signal_date"] = day.isoformat()
          state["pending"] = dict(signal, action="Buy", ticker=ticker)
      elif state["shares"] > 0 and state["exit_date"] is None and state["pending"] is None and settings["exit"] != "none":
        signal = _condition(settings["exit"], settings["proximity"]["exit"], day,
                            history, windows[ticker], settings["lookback"])
        if signal:
          state["exit_signal_date"] = day.isoformat()
          state["pending"] = dict(signal, action="Sell", ticker=ticker)
    market_value = sum(state["shares"] * clean[ticker]["close"][day]
                       for ticker, state in states.items() if state["shares"])
    cash = sum(state["cash"] for state in states.values())
    equity = cash + market_value
    buy_hold_equity = sum(state["buy_hold_cash"] + (
                            state["buy_hold_shares"] * clean[ticker]["close"][day] if state["buy_hold_shares"] else 0.0)
                          for ticker, state in states.items())
    if not all(math.isfinite(value) for value in (market_value, cash, equity, buy_hold_equity)):
      raise RetrospectiveInputError("Simulated values exceed the supported numeric range.")
    peak = max(peak, equity)
    max_drawdown = min(max_drawdown, equity / peak - 1)
    receivables = sum(state["dividends"] for state in states.values())
    point = {"date": day.isoformat(), "equity": equity, "cash": cash,
             "trade_cash": cash - receivables, "dividend_receivables": receivables,
             "market_value": market_value, "return": equity / initial - 1,
             "buy_hold_equity": buy_hold_equity, "buy_hold_return": buy_hold_equity / initial - 1}
    if benchmark_state is not None and benchmark_history is not None:
      if day != first_day:
        benchmark_state["shares"] *= benchmark_history["splits"].get(day, 1.0)
        if settings["include_dividends"]:
          benchmark_state["cash"] += benchmark_state["shares"] * benchmark_history["dividends"].get(day, 0.0)
      benchmark_equity = benchmark_state["cash"] + benchmark_state["shares"] * benchmark_history["close"][day]
      point.update(benchmark_equity=benchmark_equity, benchmark_return=benchmark_equity / initial - 1)
    series.append(point)
  pending = [dict(state["pending"]) for state in states.values() if state["pending"]]
  if pending:
    warnings.append("%d signal%s on the final session await a later open and were not filled beyond the test period." % (
      len(pending), "" if len(pending) == 1 else "s"
    ))
  if not trades and not pending:
    warnings.append("No entry condition was met. The strategy's starting capital remains in cash.")
  last = series[-1]
  metrics = {"capital": initial, "ending_equity": last["equity"],
             "profit": last["equity"] - initial, "total_return": last["return"],
             "cash": last["cash"], "market_value": last["market_value"],
             "trade_cash": last["trade_cash"], "dividend_receivables": last["dividend_receivables"],
             "dividends": sum(state["dividends"] for state in states.values()),
             "buy_cost": sum(state["buy_cost"] for state in states.values()),
             "sale_proceeds": sum(state["sale_proceeds"] for state in states.values()),
             "max_drawdown": max_drawdown, "trade_count": len(trades),
             "invested_securities": sum(state["entry_date"] is not None for state in states.values()),
             "pending_orders": len(pending), "buy_hold_ending_equity": last["buy_hold_equity"],
             "buy_hold_return": last["buy_hold_return"]}
  return {"status": "completed", "metadata": {
    "requested_start": start.isoformat(), "requested_end": end.isoformat(),
    "start_date": first_day.isoformat(), "end_date": last_day.isoformat(),
    "lookback_days": settings["lookback"], "execution": "next_session_open",
    "include_dividends": settings["include_dividends"], "sell_fraction": settings["sell_fraction"],
    "pending_signals": pending, "currency": "USD", "fees": 0,
    "dividend_method": "Dividend receivables accrue on the ex-date before that day's open trades. Payment dates are unavailable; dividends are never reused or reinvested.",
    "signal_method": "Completed closes compared with prior rolling calendar-window closes; no future yearly extrema.",
    "allocation_method": "Equal reserved cash per security; one entry and one fractional sale, without reallocating cash.",
    "buy_hold_method": "Buy the same eligible securities at the first test open and hold; splits and cash dividends use the same rules."
  }, "metrics": metrics, "series": series, "trades": trades,
    "securities": [_security_output(state, clean[ticker], last_day) for ticker, state in states.items()],
    "warnings": warnings}

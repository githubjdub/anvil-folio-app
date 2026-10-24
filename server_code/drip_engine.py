"""Historical DRIP accounting with explicit ex-dividend and payment dates.

Inputs are validated, as-traded prices/amounts from portfolio_analytics. This
module performs no network calls or persistence. Logged trades have identical
quantities in all paths; sale proceeds stay in cash in all paths.
"""
from datetime import date, datetime
import math

from .portfolio_analytics import AnalysisInputError

_EPSILON = 1e-9


class DividendDateError(AnalysisInputError):
  """A specific dividend needs a corrected or supplied payment date."""
  def __init__(self, message, event_id):
    super().__init__(message)
    self.event_id = event_id


def event_id(symbol, day):
  return "%s|%s" % (symbol, day.isoformat())


def validate_rate(annual_rate_percent):
  """An effective annual comparison rate, entered as a percentage."""
  message = "Enter an annual comparison return from 0% to 100%."
  if isinstance(annual_rate_percent, bool):
    raise AnalysisInputError(message)
  try:
    rate = float(annual_rate_percent)
  except (TypeError, ValueError, OverflowError):
    raise AnalysisInputError(message)
  if not math.isfinite(rate) or not 0 <= rate <= 100:
    raise AnalysisInputError(message)
  return rate


def _trades_by_day(transactions):
  grouped = {}
  for trade in sorted(transactions, key=lambda item: (item["trade_date"], item["row"])):
    grouped.setdefault(trade["trade_date"], []).append(trade)
  return grouped


def _check_sale(trade, owned):
  if trade["shares"] - owned > max(_EPSILON, abs(owned) * 1e-10):
    raise AnalysisInputError("Row %d: %s sells more shares than were held on %s." % (
      trade["row"], trade["symbol"], trade["trade_date"].isoformat()))


def simulation_window(transactions, histories, as_of, reference_dates=None):
  """Use a shared completed endpoint; never fill transaction dates forward."""
  start = min(trade["trade_date"] for trade in transactions)
  if (as_of - start).days > 3660:
    raise AnalysisInputError("DRIP simulations currently support up to 10 years of transactions.")
  symbols = sorted({trade["symbol"] for trade in transactions})
  available = []
  for symbol in symbols:
    dates = [day for day in histories[symbol]["close"] if day <= as_of]
    if not dates:
      raise AnalysisInputError("%s has no completed daily close by the selected date." % symbol)
    available.append(max(dates))
  if reference_dates is not None:
    completed = [day for day in reference_dates if day <= as_of]
    if not completed:
      raise AnalysisInputError("No completed US market session is available by the selected date.")
    end = max(completed)
  else:
    end = min(available)
  if end < start:
    raise AnalysisInputError("This simulation needs a completed market session after your first purchase. Try again after market close.")
  for trade in transactions:
    if trade["trade_date"] > end:
      raise AnalysisInputError("Row %d: DRIP simulations use completed daily closes through %s. Sync after this transaction's market session has closed, or choose an earlier transaction set." % (trade["row"], end.isoformat()))
    if trade["trade_date"] not in histories[trade["symbol"]]["close"]:
      raise AnalysisInputError("Row %d: no daily close is available for %s on %s. Check the transaction date." % (
        trade["row"], trade["symbol"], trade["trade_date"].isoformat()))
  return start, end


def candidate_events(transactions, histories, start, end):
  """Include base entitlements and possible later entitlements on DRIP shares.

Until payment dates are known, a prior earned dividend could have created
fractional shares. The execution pass filters out events with no entitlement.
"""
  symbols = sorted(histories)
  owned = {symbol: 0.0 for symbol in symbols}
  potential = set()
  trades = _trades_by_day(transactions)
  days = set(trades)
  for history in histories.values():
    days.update(day for day in history["splits"] if start <= day <= end)
    days.update(day for day in history["dividends"] if start <= day <= end)
  events = []
  for day in sorted(days):
    for symbol in symbols:
      history = histories[symbol]
      owned[symbol] *= history["splits"].get(day, 1)
      amount = history["dividends"].get(day, 0)
      if amount > 0 and (owned[symbol] > _EPSILON or symbol in potential):
        events.append({"id": event_id(symbol, day), "symbol": symbol,
                       "ex_date": day.isoformat(), "amount": amount,
                       "payment_date": None, "source": "Not verified",
                       "note": "Automatic sources have not confirmed this payment date."})
        potential.add(symbol)
    for trade in trades.get(day, []):
      symbol = trade["symbol"]
      if trade["action"] == "Buy":
        owned[symbol] += trade["shares"]
      else:
        _check_sale(trade, owned[symbol])
        owned[symbol] = max(0.0, owned[symbol] - trade["shares"])
  return events


def _payment_date(value, event):
  try:
    parsed = (value.date() if isinstance(value, datetime) else value
              if isinstance(value, date) else date.fromisoformat(str(value)))
  except (ValueError, TypeError):
    raise DividendDateError("Enter a valid payment date for %s, ex-dividend %s." % (
      event["symbol"], event["ex_date"]), event["id"])
  if parsed < date.fromisoformat(event["ex_date"]):
    raise DividendDateError("%s: payment dates before the ex-dividend date need special due-bill handling and are not supported in this simulation." % event["symbol"], event["id"])
  return parsed


def run_engine(transactions, histories, events, start, end, payment_dates=None, reference_dates=None, annual_rate_percent=4.0):
  annual_rate_percent = validate_rate(annual_rate_percent)
  if payment_dates is None:
    payment_dates = {}
  if not isinstance(payment_dates, dict) or len(payment_dates) > 10000:
    raise AnalysisInputError("Payment dates must be supplied as a list of dividend date entries.")
  known = {event["id"] for event in events}
  if any(key not in known for key in payment_dates):
    raise AnalysisInputError("The dividend history changed. Reload the dividend dates before running again.")
  symbols = sorted(histories)
  ledgers = {symbol: {"drip_shares": 0.0, "cash_shares": 0.0, "drip_cash": 0.0,
                      "cash_cash": 0.0, "drip_receivable": 0.0, "cash_receivable": 0.0,
                      "drip_dividends": 0.0, "cash_dividends": 0.0,
                      "buy_cost": 0.0, "sale_proceeds": 0.0,
                      "rate_balance": 0.0, "rate_principal": 0.0} for symbol in symbols}
  trades = _trades_by_day(transactions)
  ex_events = {}
  for event in events:
    ex_events.setdefault(date.fromisoformat(event["ex_date"]), []).append(event)
  days = {start, end} | set(trades)
  market_days = set(reference_dates) if reference_dates is not None else set().union(*(set(history["close"]) for history in histories.values()))
  days.update(day for day in market_days if start <= day <= end)
  for history in histories.values():
    days.update(day for day in history["close"] if start <= day <= end)
    days.update(day for day in history["splits"] if start <= day <= end)
  days.update(ex_events)
  # Dates supplied here are validated when entitlement is established. Unknown
  # dates on unearned candidates never block an otherwise valid simulation.
  for event in events:
    value = payment_dates.get(event["id"]) or event.get("payment_date")
    if value:
      try:
        payment = _payment_date(value, event)
      except DividendDateError:
        # Validate again only if this candidate earns a dividend below.
        continue
      if start <= payment <= end:
        days.add(payment)
  paid_on = {}
  reinvest_on = {}
  results = []
  series = []
  last_price = {}
  previous_day = start
  for day in sorted(days):
    # The comparison account earns interest over actual calendar days. Deposits
    # below happen after accrual, so no interest is earned before payment.
    growth = math.exp(math.log1p(annual_rate_percent / 100.0) * (day - previous_day).days / 365.0)
    for account in ledgers.values():
      account["rate_balance"] *= growth
    previous_day = day
    for symbol in symbols:
      ratio = histories[symbol]["splits"].get(day, 1)
      account = ledgers[symbol]
      account["drip_shares"] *= ratio
      account["cash_shares"] *= ratio
      if symbol in last_price:
        last_price[symbol] /= ratio
      if day in histories[symbol]["close"]:
        last_price[symbol] = histories[symbol]["close"][day]
    # Entitlement precedes every same-day logged trade and DRIP purchase.
    for event in ex_events.get(day, []):
      symbol = event["symbol"]
      account = ledgers[symbol]
      drip_dividend = account["drip_shares"] * event["amount"]
      cash_dividend = account["cash_shares"] * event["amount"]
      if max(drip_dividend, cash_dividend) <= _EPSILON:
        continue
      if event.get("unsupported"):
        raise DividendDateError("%s has a distribution with payment before the ex-date. Special due-bill handling is not supported in this simulation." % symbol, event["id"])
      value = payment_dates.get(event["id"]) or event.get("payment_date")
      if not value:
        raise DividendDateError("Automatic sources could not confirm the payment date for %s, ex-dividend %s. Use Refresh & compare to retry. An optional broker-confirmed date can also be entered below; no date will be guessed." % (symbol, event["ex_date"]), event["id"])
      payment = _payment_date(value, event)
      user_date = payment_dates.get(event["id"])
      source = ("Entered by you" if user_date and str(user_date) != str(event.get("payment_date"))
                else event.get("source", "Entered by you"))
      eligible_closes = sorted(day for day in histories[symbol]["close"] if payment <= day <= end)
      reinvest = eligible_closes[0] if eligible_closes else None
      result = {"symbol": symbol, "ex_date": event["ex_date"], "payment_date": payment.isoformat(),
                "reinvest_date": None, "amount": event["amount"],
                "drip_dividend": drip_dividend, "cash_dividend": cash_dividend,
                "rate_deposit": cash_dividend, "rate_deposit_date": None,
                "rate_status": "Pending payment",
                "added_shares": 0.0, "price": None, "status": "Pending payment", "source": source}
      results.append(result)
      for mode, amount in (("drip", drip_dividend), ("cash", cash_dividend)):
        account[mode + "_receivable"] += amount
        account[mode + "_dividends"] += amount
      paid_on.setdefault(payment, []).append(result)
      if reinvest is not None:
        reinvest_on.setdefault(reinvest, []).append(result)
    # Logged buys are new external contributions; sales become idle cash.
    for trade in trades.get(day, []):
      symbol = trade["symbol"]
      account = ledgers[symbol]
      price = trade["trade_price"] if trade["trade_price"] is not None else histories[symbol]["close"][day]
      amount = trade["shares"] * price
      if not math.isfinite(amount):
        raise AnalysisInputError("The transaction amounts are too large to simulate. Check the share counts and prices.")
      if trade["action"] == "Buy":
        account["buy_cost"] += amount
        account["drip_shares"] += trade["shares"]
        account["cash_shares"] += trade["shares"]
      else:
        _check_sale(trade, account["cash_shares"])
        for mode in ("drip", "cash"):
          account[mode + "_shares"] = max(0.0, account[mode + "_shares"] - trade["shares"])
          account[mode + "_cash"] += amount
        account["sale_proceeds"] += amount
    for event in paid_on.get(day, []):
      account = ledgers[event["symbol"]]
      for mode in ("drip", "cash"):
        amount = event[mode + "_dividend"]
        account[mode + "_receivable"] -= amount
        account[mode + "_cash"] += amount
      event["status"] = "Awaiting next completed close"
      account["rate_principal"] += event["cash_dividend"]
      account["rate_balance"] += event["cash_dividend"]
      event["rate_deposit_date"] = day.isoformat()
      event["rate_status"] = "Deposited at the comparison return" if event["cash_dividend"] > 0 else "No original-share dividend to deposit"
    for event in reinvest_on.get(day, []):
      account = ledgers[event["symbol"]]
      if account["drip_shares"] <= _EPSILON:
        event["status"] = "Held as cash: position closed"
      elif event["drip_dividend"] > 0:
        price = histories[event["symbol"]]["close"][day]
        added = event["drip_dividend"] / price
        account["drip_shares"] += added
        account["drip_cash"] -= event["drip_dividend"]
        event.update(reinvest_date=day.isoformat(), added_shares=added, price=price, status="Reinvested")
    for symbol, account in ledgers.items():
      if day in market_days and account["drip_shares"] > _EPSILON and day not in histories[symbol]["close"]:
        raise AnalysisInputError("%s has a missing daily close on %s while shares are held. Try syncing again when complete history is available." % (symbol, day.isoformat()))
    values = {"drip": 0.0, "cash": 0.0}
    for symbol, account in ledgers.items():
      for mode in values:
        value = (account[mode + "_shares"] * last_price.get(symbol, 0.0)
                 + account[mode + "_cash"] + account[mode + "_receivable"])
        values[mode] += value
    rate_balance = sum(account["rate_balance"] for account in ledgers.values())
    rate_principal = sum(account["rate_principal"] for account in ledgers.values())
    rate_interest = rate_balance - rate_principal
    rate_value = values["cash"] + rate_interest
    if not all(math.isfinite(value) for value in list(values.values()) + [rate_balance, rate_principal, rate_interest, rate_value]):
      raise AnalysisInputError("The transaction amounts are too large to simulate. Check the share counts and prices.")
    series.append({"date": day.isoformat(), "drip_value": values["drip"], "cash_value": values["cash"],
                   "rate_value": rate_value, "rate_balance": rate_balance, "rate_interest": rate_interest})
  totals = {key: sum(account[key] for account in ledgers.values()) for key in (
    "drip_cash", "cash_cash", "drip_receivable", "cash_receivable", "drip_dividends", "cash_dividends", "buy_cost", "sale_proceeds", "rate_balance", "rate_principal")}
  if not all(math.isfinite(value) for value in totals.values()):
    raise AnalysisInputError("The transaction amounts are too large to simulate. Check the share counts and prices.")
  totals.update(drip_value=series[-1]["drip_value"], cash_value=series[-1]["cash_value"])
  totals["difference"] = totals["drip_value"] - totals["cash_value"]
  totals["drip_profit"] = totals["drip_value"] - totals["buy_cost"]
  totals["cash_profit"] = totals["cash_value"] - totals["buy_cost"]
  totals["rate_value"] = series[-1]["rate_value"]
  totals["rate_interest"] = series[-1]["rate_interest"]
  totals["rate_profit"] = totals["rate_value"] - totals["buy_cost"]
  totals["drip_vs_rate"] = totals["drip_value"] - totals["rate_value"]
  securities = []
  for symbol, account in ledgers.items():
    item = {"symbol": symbol, **account}
    for mode in ("drip", "cash"):
      item[mode + "_value"] = (account[mode + "_shares"] * last_price.get(symbol, 0.0)
                                + account[mode + "_cash"] + account[mode + "_receivable"])
    item["difference"] = item["drip_value"] - item["cash_value"]
    item["rate_interest"] = account["rate_balance"] - account["rate_principal"]
    item["rate_value"] = item["cash_value"] + item["rate_interest"]
    item["rate_profit"] = item["rate_value"] - account["buy_cost"]
    item["drip_vs_rate"] = item["drip_value"] - item["rate_value"]
    securities.append(item)
  return {"ok": True, "series": series, "totals": totals, "securities": securities,
          "events": results, "metadata": {"start_date": start.isoformat(), "end_date": end.isoformat(),
          "currency": "USD", "price_source": "Yahoo Finance completed daily closes",
          "dividend_source": "Yahoo Finance ex-dates and cash amounts; Nasdaq, Stock Analysis or user-entered payment dates; Finviz ex-date and amount cross-checks",
          "dividend_totals": "Earned dividends, including amounts awaiting payment",
          "annual_rate_percent": annual_rate_percent,
          "rate_compounding_basis": "Actual elapsed calendar days / 365; effective annual return",
          "rate_method": "Original-share dividends enter a hypothetical fixed-rate account on each confirmed payment date, including weekends. Each deposit grows by (1 + annual rate) ** (elapsed days / 365) through the simulation endpoint. Unpaid dividends and logged sale proceeds earn no interest. The rate is a user assumption, not an actual Treasury quote or security return.",
          "method": "Identical logged trades in all three scenarios; sale proceeds stay in cash. Fractional shares reinvest at the payment-date close or next available completed close when a position remains open. Earned unpaid dividends count as receivables. The comparison account compounds paid original-share dividends at the selected effective annual return. Taxes, fees and interest on other cash are excluded."}}

"""FIFO estimates for selected logged transactions; no brokerage or tax filing actions."""
from datetime import date, timedelta
import math

from .portfolio_analytics import AnalysisInputError

_EPSILON = 1e-9


def _finite(value):
  if not math.isfinite(value):
    raise AnalysisInputError("The transaction amounts are too large for income review. Check shares and prices.")
  return value


def _sum(values):
  result = 0.0
  for value in values:
    result = _finite(result + value)
  return result


def _term(acquired, disposed):
  # A holding period must exceed one calendar year; 365 days is not sufficient
  # across every leap year. The acquisition day itself is excluded.
  try:
    anniversary = acquired.replace(year=acquired.year + 1)
  except ValueError:
    anniversary = date(acquired.year + 1, 2, 28)
  return "Long-term" if disposed > anniversary else "Short-term"


def _trade_price(trade, history):
  price = trade.get("trade_price")
  estimated = price is None
  if estimated:
    price = history.get("close", {}).get(trade["trade_date"])
  if price is not None:
    if not math.isfinite(price) or price <= 0:
      raise AnalysisInputError("Row %s: the price must be a positive finite number." % trade["row"])
    price = float(price)
  return price, estimated


def _recent_purchase(symbol, day, buys):
  lower, upper = day - timedelta(days=30), day + timedelta(days=30)
  return any(trade["symbol"] == symbol and lower <= trade["trade_date"] <= upper for trade in buys)


def _mark(history, as_of):
  closes = history.get("close", {})
  days = [day for day in closes if day <= as_of]
  if not days:
    return None, None
  day = max(days)
  price = closes[day]
  if not math.isfinite(price) or price <= 0:
    return None, day.isoformat()
  # A stale quote before a split must be expressed on today's share basis.
  for split_day, ratio in sorted(history.get("splits", {}).items()):
    if day < split_day <= as_of:
      if not math.isfinite(ratio) or ratio <= 0:
        raise AnalysisInputError("A stock split ratio is invalid. Refresh the portfolio history.")
      price = _finite(price / ratio)
  return float(price), day.isoformat()


def _gain_totals(items, field):
  """Complete signed totals; an unknown amount must never become a zero."""
  unknown = sum(item[field] is None for item in items)
  if unknown:
    return {"gains": None, "losses": None, "net": None, "unknown_count": unknown}
  return {"gains": _sum(max(0.0, item[field]) for item in items),
    "losses": _sum(min(0.0, item[field]) for item in items),
    "net": _sum(item[field] for item in items), "unknown_count": 0}


def build_tax_report(clean, histories, as_of):
  """Return open FIFO lots, losing lots and YTD realized estimates.

  Missing entered prices use only that session's market price and are labelled
  estimated. If neither exists, basis/proceeds remain unknown rather than zero.
  ``term_breakdown`` contains short/long rows with signed gross gains, gross
  losses and net amounts, keeping open lots separate from YTD realized sales.
  ``losing_positions`` ranks complete net position losses, whereas ``candidates``
  ranks individual losing lots, including lots inside net-profitable positions.
  These are transient analysis results, not persisted brokerage tax records.
  """
  symbols = sorted({trade["symbol"] for trade in clean})
  holdings = {symbol: [] for symbol in symbols}
  buys = [trade for trade in clean if trade["action"] == "Buy"]
  by_day = {}
  for trade in sorted(clean, key=lambda item: (item["trade_date"], item["row"])):
    if trade["trade_date"] <= as_of:
      by_day.setdefault(trade["trade_date"], []).append(trade)
  days = set(by_day)
  for symbol in symbols:
    days.update(day for day in histories[symbol].get("splits", {}) if day <= as_of)
  realized = []
  for day in sorted(days):
    for symbol in symbols:
      ratio = histories[symbol].get("splits", {}).get(day, 1.0)
      if not math.isfinite(ratio) or ratio <= 0:
        raise AnalysisInputError("%s has an invalid split ratio. Refresh the portfolio history." % symbol)
      if ratio != 1.0:
        for lot in holdings[symbol]:
          lot["shares"] = _finite(lot["shares"] * ratio)
    for trade in by_day.get(day, []):
      symbol = trade["symbol"]
      price, estimated = _trade_price(trade, histories[symbol])
      shares = trade["shares"]
      if not math.isfinite(shares) or shares <= 0:
        raise AnalysisInputError("Row %s: shares must be a positive finite number." % trade["row"])
      if trade["action"] == "Buy":
        holdings[symbol].append({"id": "%s|%s|%s" % (symbol, day.isoformat(), trade["row"]),
          "symbol": symbol, "acquired_date": day.isoformat(), "acquired": day,
          "shares": float(shares), "cost_basis": _finite(shares * price) if price is not None else None,
          "basis_estimated": estimated, "row": trade["row"]})
        continue
      owned = _sum(lot["shares"] for lot in holdings[symbol])
      tolerance = max(_EPSILON, owned * 1e-10)
      if shares - owned > tolerance:
        raise AnalysisInputError("Row %s: %s sells more shares than the logged purchases provide." % (trade["row"], symbol))
      remaining = min(float(shares), owned)
      for lot in holdings[symbol]:
        if remaining <= 0:
          break
        if lot["shares"] <= 0:
          continue
        sold = min(remaining, lot["shares"])
        full_lot = sold == lot["shares"]
        basis = (_finite(lot["cost_basis"] * (sold / lot["shares"]))
                 if lot["cost_basis"] is not None else None)
        proceeds = _finite(sold * price) if price is not None else None
        gain = _finite(proceeds - basis) if proceeds is not None and basis is not None else None
        realized.append({"symbol": symbol, "lot_id": lot["id"], "acquired_date": lot["acquired_date"],
          "sale_date": day.isoformat(), "shares": sold, "cost_basis": basis, "proceeds": proceeds,
          "gain": gain, "term": _term(lot["acquired"], day),
          "basis_estimated": lot["basis_estimated"], "proceeds_estimated": estimated,
          "possible_wash_sale": gain is not None and gain < 0 and _recent_purchase(symbol, day, buys)})
        lot["shares"] = 0.0 if full_lot else lot["shares"] - sold
        if lot["cost_basis"] is not None:
          lot["cost_basis"] = 0.0 if full_lot else _finite(lot["cost_basis"] - basis)
        remaining = max(0.0, remaining - sold)
      holdings[symbol] = [lot for lot in holdings[symbol] if lot["shares"] > 0]

  positions, lots, candidates = {}, [], []
  for symbol in symbols:
    price, price_date = _mark(histories[symbol], as_of)
    open_lots = holdings[symbol]
    shares = _sum(lot["shares"] for lot in open_lots)
    basis = _sum(lot["cost_basis"] for lot in open_lots) if all(lot["cost_basis"] is not None for lot in open_lots) else None
    value = _finite(shares * price) if price is not None else (0.0 if not shares else None)
    positions[symbol] = {"symbol": symbol, "shares": shares, "cost_basis": basis,
      "basis_estimated": any(lot["basis_estimated"] for lot in open_lots), "current_price": price,
      "price_date": price_date, "current_value": value,
      "unrealized_gain": _finite(value - basis) if value is not None and basis is not None else None}
    recent = _recent_purchase(symbol, as_of, buys)
    for lot in open_lots:
      value = _finite(lot["shares"] * price) if price is not None else None
      gain = _finite(value - lot["cost_basis"]) if value is not None and lot["cost_basis"] is not None else None
      item = {key: val for key, val in lot.items() if key != "acquired"}
      item.update(term=_term(lot["acquired"], as_of), current_price=price, price_date=price_date,
                  current_value=value, unrealized_gain=gain,
                  potential_loss=max(0.0, -gain) if gain is not None else None,
                  possible_wash_sale=recent, selected=False,
                  review_note=("A same-security purchase falls within 30 days of this review date. Review wash-sale rules before using the loss."
                    if recent else "No same-security purchase found within the logged 30-day window. Check other accounts and purchases during the next 30 days."))
      lots.append(item)
      if gain is not None and gain < -0.005:
        candidates.append(item)
  candidates.sort(key=lambda lot: (-lot["potential_loss"], lot["symbol"], lot["acquired_date"], lot["row"]))
  ytd = [sale for sale in realized if date.fromisoformat(str(sale["sale_date"])).year == as_of.year]
  def realized_total(term=None):
    items = [sale for sale in ytd if term is None or sale["term"] == term]
    return _sum(sale["gain"] for sale in items) if all(sale["gain"] is not None for sale in items) else None
  term_breakdown = []
  for term in ("Short-term", "Long-term"):
    open_items = [lot for lot in lots if lot["term"] == term]
    sold_items = [sale for sale in ytd if sale["term"] == term]
    unrealized_totals = _gain_totals(open_items, "unrealized_gain")
    realized_totals = _gain_totals(sold_items, "gain")
    term_breakdown.append({"term": term, "lot_count": len(open_items),
      "realized_lot_count": len(sold_items),
      "unrealized_gains": unrealized_totals["gains"],
      "unrealized_losses": unrealized_totals["losses"],
      "unrealized_net": unrealized_totals["net"],
      "realized_gains": realized_totals["gains"],
      "realized_losses": realized_totals["losses"],
      "realized_net": realized_totals["net"],
      "unknown_unrealized_count": unrealized_totals["unknown_count"],
      "unknown_realized_count": realized_totals["unknown_count"],
      "estimated_basis_count": sum(lot["basis_estimated"] and lot["cost_basis"] is not None for lot in open_items)})
  losing_positions = []
  for symbol, position in positions.items():
    symbol_lots = [lot for lot in lots if lot["symbol"] == symbol]
    position.update(lot_count=len(symbol_lots),
      short_term_gain=_gain_totals([lot for lot in symbol_lots if lot["term"] == "Short-term"], "unrealized_gain")["net"],
      long_term_gain=_gain_totals([lot for lot in symbol_lots if lot["term"] == "Long-term"], "unrealized_gain")["net"],
      possible_wash_sale=any(lot["possible_wash_sale"] for lot in symbol_lots),
      potential_loss=max(0.0, -position["unrealized_gain"]) if position["unrealized_gain"] is not None else None,
      unrealized_return=(position["unrealized_gain"] / position["cost_basis"]
        if position["unrealized_gain"] is not None and position["cost_basis"] else None))
    if position["unrealized_gain"] is not None and position["unrealized_gain"] < -0.005:
      losing_positions.append(position)
  losing_positions.sort(key=lambda position: (position["unrealized_gain"], position["symbol"]))
  unrealized_totals = _gain_totals(lots, "unrealized_gain")
  estimated_count = sum(lot["basis_estimated"] and lot["cost_basis"] is not None for lot in lots)
  unknown_count = sum(lot["cost_basis"] is None for lot in lots)
  unknown_price_count = sum(lot["current_price"] is None for lot in lots)
  notes = [
    "US taxable-account screening estimate using FIFO from the selected logged transactions. A losing lot may exist inside a profitable holding; choose the correct lot with your broker.",
    "Realized amounts are year-to-date estimates before wash-sale adjustments. Recent same-security purchases are flagged conservatively; the app cannot determine deductibility or detect substantially identical securities, other accounts, a spouse's trades, or future purchases.",
    "Capital losses generally offset capital gains subject to tax rules. This estimate excludes loss carryforwards, fees, return-of-capital basis changes, corporate-action basis adjustments and broker tax-lot elections. It is not a tax filing calculation.",
    "Sales inside an IRA or another tax-advantaged account do not provide current taxable capital-loss offsets. Purchases in those accounts can affect a taxable-account wash sale.",
  ]
  if estimated_count or any(sale["basis_estimated"] or sale["proceeds_estimated"] for sale in ytd):
    notes.append("Prices omitted from logged trades use the exact trade-date market price where available. Those basis or proceeds estimates must be checked against your broker's records.")
  if unknown_count or any(sale["gain"] is None for sale in ytd):
    notes.append("Some trade prices could not be established. Lots or positions with unknown basis are excluded from loss rankings; affected gain/loss totals are unavailable rather than zero.")
  if any(lot["current_price"] is None for lot in lots):
    notes.append("Some open lots have no market price on or before the analysis date and cannot be screened for unrealized losses.")
  return {"positions": positions, "lots": lots, "candidates": candidates, "realized": realized,
    "term_breakdown": term_breakdown, "losing_positions": losing_positions,
    "summary": {"tax_year": as_of.year, "realized_short_term": realized_total("Short-term"),
      "realized_long_term": realized_total("Long-term"), "realized_net": realized_total(),
      "unrealized_short_term": term_breakdown[0]["unrealized_net"],
      "unrealized_long_term": term_breakdown[1]["unrealized_net"],
      "unrealized_net": unrealized_totals["net"],
      "potential_loss": _sum(lot["potential_loss"] for lot in candidates),
      "candidate_count": len(candidates), "unknown_basis_count": unknown_count,
      "estimated_basis_count": estimated_count, "unknown_price_count": unknown_price_count,
      "losing_position_count": len(losing_positions)}, "notes": notes,
    "sources": [{"title": "IRS: capital gains and holding periods", "url": "https://www.irs.gov/taxtopics/tc409"},
      {"title": "IRS Publication 550: investment income and wash sales", "url": "https://www.irs.gov/publications/p550"}]}

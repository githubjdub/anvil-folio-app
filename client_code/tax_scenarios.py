"""Pure, temporary sale scenarios over an already loaded tax-lot snapshot.

Historical disposals have already been reconstructed by the server with FIFO.
The selected ordering applies only to these hypothetical sales, at the snapshot
date. No transaction, lot, or report passed to this module is modified.
"""
import math


METHODS = [("FIFO · oldest shares first", "FIFO"),
           ("LIFO · newest shares first", "LIFO"),
           ("HIFO · highest cost first", "HIFO")]


class SaleInputError(ValueError):
  pass


def positive_number(value, label):
  if isinstance(value, bool):
    raise SaleInputError("%s must be a positive number." % label)
  try:
    result = float(value)
  except (ValueError, TypeError, OverflowError):
    raise SaleInputError("Enter a positive number for %s." % label.lower())
  if not math.isfinite(result) or result <= 0:
    raise SaleInputError("%s must be greater than zero and finite." % label)
  return result


def _finite(value):
  if not math.isfinite(value):
    raise SaleInputError("This scenario is too large to calculate. Reduce the shares or price.")
  return value


def _total(values):
  total = 0.0
  for value in values:
    total = _finite(total + value)
  return total


def _simulate(tax, sales):
  if not sales:
    raise SaleInputError("Add at least one sale to the scenario.")
  results, allocations, seen = [], [], set()
  for sale in sales:
    symbol = sale.get("symbol")
    if symbol in seen:
      raise SaleInputError("Update the existing %s sale instead of adding it twice." % symbol)
    seen.add(symbol)
    if symbol not in tax["positions"]:
      raise SaleInputError("Choose a position from the current portfolio snapshot.")
    position = tax["positions"][symbol]
    lots = [lot for lot in tax["lots"] if lot["symbol"] == symbol and lot["shares"] > 0]
    owned = _total(lot["shares"] for lot in lots)
    quantity = positive_number(sale.get("shares"), "Shares to sell")
    price = positive_number(sale.get("price"), "Sale price")
    if owned <= 0 or quantity > owned + max(1e-9, owned * 1e-12):
      raise SaleInputError("%s has only %s shares available in this snapshot." % (symbol, format_shares(owned)))
    quantity = min(quantity, owned)
    method = sale.get("method")
    if method not in ("FIFO", "LIFO", "HIFO"):
      raise SaleInputError("Choose FIFO, LIFO or HIFO for %s." % symbol)
    by_date = lambda lot: (lot["acquired_date"], lot["row"], lot["id"])
    if method == "HIFO":
      if any(lot["cost_basis"] is None for lot in lots):
        raise SaleInputError("%s needs a known basis for every open lot to compare highest costs." % symbol)
      lots = sorted(lots, key=lambda lot: (-lot["cost_basis"] / lot["shares"], by_date(lot)))
    else:
      lots = sorted(lots, key=by_date, reverse=method == "LIFO")
    remaining, used = quantity, []
    for lot in lots:
      if remaining <= 0:
        break
      if lot["cost_basis"] is None:
        raise SaleInputError("%s has missing cost basis in a selected lot. Enter its purchase price and refresh the review." % symbol)
      shares = min(remaining, lot["shares"])
      basis = _finite(lot["cost_basis"] * (shares / lot["shares"]))
      proceeds = _finite(shares * price)
      gain = _finite(proceeds - basis)
      used.append({"symbol": symbol, "lot_id": lot["id"], "acquired_date": lot["acquired_date"],
        "shares": shares, "basis_per_share": lot["cost_basis"] / lot["shares"],
        "cost_basis": basis, "proceeds": proceeds, "gain": gain, "term": lot["term"],
        "basis_estimated": lot["basis_estimated"],
        "possible_wash_sale": gain < 0 and lot["possible_wash_sale"]})
      remaining = max(0.0, remaining - shares)
    basis = _total(lot["cost_basis"] for lot in used)
    proceeds = _total(lot["proceeds"] for lot in used)
    results.append({"symbol": symbol, "shares": quantity, "price": price, "method": method,
      "remaining_shares": max(0.0, owned - quantity), "cost_basis": basis, "proceeds": proceeds,
      "gain": _finite(proceeds - basis),
      "short_term": _total(lot["gain"] for lot in used if lot["term"] == "Short-term"),
      "long_term": _total(lot["gain"] for lot in used if lot["term"] == "Long-term"),
      "flagged_loss": _total(-lot["gain"] for lot in used if lot["possible_wash_sale"]),
      "basis_estimated": any(lot["basis_estimated"] for lot in used),
      "price_date": position["price_date"]})
    allocations.extend(used)
  total = {key: _total(sale[key] for sale in results)
           for key in ("cost_basis", "proceeds", "gain", "short_term", "long_term", "flagged_loss")}
  for key, field in (("ytd_short_term", "realized_short_term"),
                     ("ytd_long_term", "realized_long_term"), ("ytd_net", "realized_net")):
    extra = total[{"ytd_short_term": "short_term", "ytd_long_term": "long_term", "ytd_net": "gain"}[key]]
    baseline = tax["summary"][field]
    total[key] = _finite(baseline + extra) if baseline is not None else None
  total["basis_estimated"] = any(sale["basis_estimated"] for sale in results)
  return {"sales": results, "lots": allocations, "totals": total}


def simulate_sales(tax, sales):
  result = _simulate(tax, sales)
  result["holding_periods"] = []
  for term, key in (("Short-term", "short_term"), ("Long-term", "long_term")):
    lots = [lot for lot in result["lots"] if lot["term"] == term]
    result["holding_periods"].append({"term": term,
      "gains": _total(max(0.0, lot["gain"]) for lot in lots),
      "losses": _total(min(0.0, lot["gain"]) for lot in lots),
      "net": result["totals"][key]})
  result["netting"] = net_holding_periods(result["totals"]["short_term"], result["totals"]["long_term"])
  result["ytd_netting"] = net_holding_periods(result["totals"]["ytd_short_term"], result["totals"]["ytd_long_term"])
  comparisons = []
  for label, method in METHODS:
    try:
      totals = _simulate(tax, [dict(sale, method=method) for sale in sales])["totals"]
      comparisons.append(dict(totals, method=method, available=True, note=label.split(" · ")[1]))
    except SaleInputError as exc:
      comparisons.append({"method": method, "available": False, "note": str(exc),
                          "gain": None, "short_term": None, "long_term": None})
  result["comparisons"] = comparisons
  return result


def net_holding_periods(short_term, long_term):
  """Illustrate cross-period netting of complete ordinary capital-asset totals.

  Inputs have already been netted within each holding period. This illustration
  excludes wash-sale adjustments, carryforwards, special asset rules and taxes.
  Unknown YTD totals must remain unknown instead of implying a zero balance.
  """
  if short_term is None or long_term is None:
    return {"available": False, "offset": None, "short_term": None, "long_term": None}
  short_term, long_term = _finite(short_term), _finite(long_term)
  offset = 0.0
  if short_term > 0 and long_term < 0:
    offset = min(short_term, -long_term)
    short_term, long_term = short_term - offset, long_term + offset
  elif short_term < 0 and long_term > 0:
    offset = min(-short_term, long_term)
    short_term, long_term = short_term + offset, long_term - offset
  return {"available": True, "offset": offset, "short_term": short_term, "long_term": long_term}


def netting_description(netting):
  if not netting["available"]:
    return "Netting is unavailable because some year-to-date trade prices are missing."
  remaining = []
  for key, title in (("short_term", "short-term"), ("long_term", "long-term")):
    value = netting[key]
    if abs(value) >= 0.005:
      remaining.append("%s %s %s" % (money(abs(value)), title, "gain" if value > 0 else "loss"))
  result = "Remaining: %s." % (" and ".join(remaining)) if remaining else "No net gain or loss remains."
  if netting["offset"] >= 0.005:
    return "%s of gains and losses offset across holding periods. %s" % (money(netting["offset"]), result)
  return result


def money(value, signed=False):
  if value is None:
    return "Unavailable"
  if abs(value) < 0.005:
    value = 0.0
  return "{}${:,.2f}".format("−" if value < 0 else "+" if signed and value > 0 else "", abs(value))


def value_role(value):
  return ["tax-number", "tax-value-negative" if value is not None and value < -0.005 else
          "tax-value-positive" if value is not None and value > 0.005 else "tax-value-neutral"]


def format_shares(value):
  if 0 < value < 0.000001:
    return "{:.8g}".format(value)
  return "{:,.6f}".format(value).rstrip("0").rstrip(".")

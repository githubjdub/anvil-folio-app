"""Prepare clickable trade markers without altering portfolio return series."""

import math


_US_EXCHANGES = ("NASDAQ", "NYSE", "NYSEARCA", "AMEX", "BATS", "NYSEAMERICAN")
_SINGLE_HOVER = (
  "%{customdata[0]}<br>Quantity: %{customdata[1]}"
  "<br>Price/share: %{customdata[2]}<br>Total value: %{customdata[3]}"
  "<br>Entered date: %{customdata[4]}<br>Plotted session: %{customdata[5]}"
  "<br>Price source: %{customdata[6]}<br>%{customdata[7]}<extra></extra>"
)
_GROUP_HOVER = (
  "%{customdata[0]}<br>Securities: %{customdata[1]}"
  "<br>Total value: %{customdata[3]}<br>Entered dates: %{customdata[4]}"
  "<br>Plotted session: %{customdata[5]}<br>Price sources: %{customdata[6]}"
  "<br>%{customdata[7]}<extra></extra>"
)


def _security_key(transaction):
  parts = str(transaction.get("ticker") or "").strip().upper().split(":", 1)
  symbol = parts[0].strip().replace(".", "-")
  exchange = parts[1].strip() if len(parts) == 2 else "NASDAQ"
  if exchange in _US_EXCHANGES:
    return symbol
  return "%s:%s" % (symbol, exchange) if symbol else ""


def _iso_date(value):
  if value is None:
    return ""
  return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _optional_number(value):
  if value is None or not str(value).strip():
    return None
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return None if math.isnan(number) or math.isinf(number) else number


def _compact_number(value):
  if value is None:
    return "Unavailable"
  text = format(value, ",.8f").rstrip("0").rstrip(".")
  return str(value) if text == "0" and value != 0 else text


def _currency(value):
  return "Unavailable" if value is None else "$%s" % format(value, ",.2f")


def _price_text(price, source):
  if price is None:
    return "Unavailable"
  shown = (format(price, ",.2f")
           if source != "User-entered price" and price >= 1
           else _compact_number(price))
  return "$%s" % shown


def _escape(value):
  # Plotly hover labels support markup; only the fixed templates supply it.
  return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(
    ">", "&gt;"
  ).replace('"', "&quot;").replace("'", "&#39;")


def _price_details(transaction, summary):
  entered = _optional_number(transaction.get("trade_price"))
  if entered is not None:
    return entered, "User-entered price"
  price = _optional_number(summary.get("entry_price"))
  if price is None:
    price = _optional_number(transaction.get("entry_price"))
  source = summary.get("price_source") or "Historical daily close"
  return price, str(source) if price is not None else "Unavailable"


def _transaction_details(transaction, summary, original_index, plotted_date):
  ticker = str(transaction.get("ticker") or "").strip().upper()
  action = transaction["action"]
  shares = _optional_number(transaction.get("shares"))
  price, source = _price_details(transaction, summary)
  value = shares * price if shares is not None and price is not None else None
  if value is not None and (math.isnan(value) or math.isinf(value)):
    value = None
  entered_date = _iso_date(transaction.get("trade_date"))
  price_text = _price_text(price, source)
  quantity = "%s shares" % _compact_number(shares)
  notes = ""
  if entered_date != plotted_date:
    notes = "Entered %s; plotted on the next available session, %s." % (
      entered_date or "date unavailable", plotted_date
    )
  return {
    "transaction_index": original_index,
    "ticker": ticker, "action": action, "shares": shares,
    "entered_date": entered_date, "plotted_date": plotted_date,
    "price": price, "price_source": source, "total_value": value,
    "title": "%s · %s · %s" % (ticker, action, quantity),
    "detail": "Entered %s · %s / share · %s trade value · %s" % (
      entered_date or "date unavailable", price_text, _currency(value), source
    ),
    "notes": notes,
  }


def _hover_data(group):
  transactions = group["transactions"]
  if len(transactions) == 1:
    transaction = transactions[0]
    price = transaction["price"]
    values = [
      "%s · %s" % (transaction["ticker"], transaction["action"]),
      "%s shares" % _compact_number(transaction["shares"]),
      _price_text(price, transaction["price_source"]),
      _currency(transaction["total_value"]),
      transaction["entered_date"] or "Unavailable", group["date"],
      transaction["price_source"], "Click to open this transaction.",
    ]
    return [_escape(value) for value in values], _SINGLE_HOVER
  tickers = sorted(set(transaction["ticker"] for transaction in transactions))
  entered_dates = sorted(set(transaction["entered_date"] or "Unavailable"
                             for transaction in transactions))
  sources = sorted(set(transaction["price_source"] for transaction in transactions))
  amounts = [transaction["total_value"] for transaction in transactions]
  total = sum(amounts) if all(value is not None for value in amounts) else None
  values = [
    "%d %s" % (len(transactions), "purchases" if group["action"] == "Buy" else "sales"),
    ", ".join(tickers), "", _currency(total), ", ".join(entered_dates),
    group["date"], ", ".join(sources), "Click to choose a transaction.",
  ]
  return [_escape(value) for value in values], _GROUP_HOVER


def _trace(action, groups, point_indices, values_by_date):
  chosen = [groups[index] for index in point_indices]
  hover = [_hover_data(group) for group in chosen]
  return {
    "type": "scatter", "name": "Purchases" if action == "Buy" else "Sales",
    "mode": "markers+text", "x": [group["date"] for group in chosen],
    "y": [values_by_date[group["date"]] for group in chosen],
    "customdata": [data for data, template in hover],
    "hovertemplate": [template for data, template in hover],
    "text": [str(len(group["transactions"])) if len(group["transactions"]) > 1 else ""
             for group in chosen],
    "textposition": "bottom center" if action == "Buy" else "top center",
    "textfont": {"size": 11, "color": "#14834b" if action == "Buy" else "#c64845"},
    "marker": {
      "symbol": "arrow-up" if action == "Buy" else "arrow-down", "size": 15,
      "color": "#14834b" if action == "Buy" else "#c64845",
      "line": {"color": "#ffffff", "width": 1},
    },
    "cliponaxis": False,
    "hoverlabel": {"bgcolor": "#ffffff", "font": {"color": "#24302b"}},
  }


def build_trade_markers(transactions, summaries, selected_symbols, dates,
                        performance_values, drawdown_values):
  """Return Buy/Sell traces and their links to original transaction rows.

  ``transactions`` and ``summaries`` must describe the same analyzed rows in
  the same order. When blank draft rows were omitted before analysis, pass
  each transaction's original UI index as ``_transaction_index``. Summaries
  provide the effective trading session, including moved non-trading dates.
  Markers are limited to the selected securities and exact plotted dates.
  """
  if len(transactions) != len(summaries):
    raise ValueError("Trade markers require matching analyzed transactions and summaries.")
  if len(dates) != len(performance_values) or len(dates) != len(drawdown_values):
    raise ValueError("Trade marker dates must match both chart series.")
  selected = set(selected_symbols)
  plotted = set(dates)
  grouped = {}
  for index, transaction in enumerate(transactions):
    action = transaction.get("action")
    if action not in ("Buy", "Sell") or _security_key(transaction) not in selected:
      continue
    summary = summaries[index]
    day = _iso_date(summary.get("entry_date_used"))
    if day not in plotted:
      continue
    original_index = transaction.get("_transaction_index", index)
    detail = _transaction_details(transaction, summary, original_index, day)
    grouped.setdefault((day, action), []).append(detail)
  keys = sorted(grouped, key=lambda key: (key[0], key[1] != "Buy"))
  groups = [{"date": day, "action": action, "transactions": grouped[(day, action)]}
            for day, action in keys]
  point_groups = [
    [index for index, group in enumerate(groups) if group["action"] == action]
    for action in ("Buy", "Sell")
  ]
  performance = dict(zip(dates, performance_values))
  drawdown = dict(zip(dates, drawdown_values))
  return {
    "groups": groups, "point_groups": point_groups,
    "transaction_count": sum(len(group["transactions"]) for group in groups),
    "performance_traces": [_trace(action, groups, indices, performance)
                           for action, indices in zip(("Buy", "Sell"), point_groups)],
    "drawdown_traces": [_trace(action, groups, indices, drawdown)
                        for action, indices in zip(("Buy", "Sell"), point_groups)],
  }

"""Display saved portfolio values without importing history calculators."""


ACTIVITIES = (
  ("new", "New positions"), ("added", "Bought more"),
  ("trimmed", "Sold some"), ("closed", "Closed positions"),
  ("mixed", "Bought and sold"),
)


def empty_summary():
  """A fresh placeholder summary, also used by the full-history calculator."""
  return {
    "value": None, "holding_count": 0, "prior_quarter_return": None,
    "prior_quarter_label": "Prior-quarter return", "long_return": None,
    "long_return_label": "Available-period return", "top20_weight": None,
    "as_of": None, "return_is_provisional": False,
    "return_note": "Sync a portfolio to calculate its time-weighted return.",
    "prior_quarter_note": "A complete quarter of invested history is required.",
  }


def display_share_history(history):
  """Small presentation rows shared by the bars and their accessible details."""
  return [dict(item,
    shares_text="Unavailable" if item["shares"] is None else format(item["shares"], ",.8f").rstrip("0").rstrip("."),
    change_text="—" if item["change"] is None else "%+.1f%%" % (100 * item["change"]),
    label_text=item["label"] + (" · partial" if item["partial"] else ""),
  ) for item in history]


def filter_holdings(rows, query="", activity="all", sort="value", position="open"):
  """Filters affect the table alone; total portfolio market value stays fixed."""
  query = str(query or "").strip().lower()
  filtered = [row for row in rows
              if (not query or query in (row["symbol"] + " " + row["company_name"]
                                         + " " + row["sector"]).lower())
              and (activity == "all" or row["activity"] == activity)
              and (position == "all" or row["is_open"])]
  if sort == "symbol":
    return sorted(filtered, key=lambda row: row["symbol"])
  if sort == "return":
    return sorted(filtered, key=lambda row: (row["return"] is None,
                  -(row["return"] or 0), row["symbol"]))
  if sort == "shares":
    return sorted(filtered, key=lambda row: (-row["shares"], row["symbol"]))
  return sorted(filtered, key=lambda row: (-row["market_value"], row["symbol"]))

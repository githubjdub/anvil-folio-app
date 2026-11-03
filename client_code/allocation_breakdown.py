"""Presentation groups and weight changes for invested-position snapshots."""


def allocation_groups(allocation, security_view=False, previous=None):
  """Copy allocation groups, sorted by value, with optional prior weight changes.

  Changes are percentage points of invested market value, not investment returns.
  An absent prior group has zero weight; an absent or empty prior snapshot has
  no comparison. Labels are ordinary text for component properties/bindings.
  The source allocation and its nested ticker lists are never modified.
  """
  collection = "holdings" if security_view else "slices"
  key = "symbol" if security_view else "sector"
  total = allocation.get("total_value") or 0
  previous_total = (previous or {}).get("total_value") or 0
  previous_weights = {
    str(group[key]): group["market_value"] / previous_total
    for group in (previous or {}).get(collection, [])
  } if previous_total > 0 else None
  groups = []
  for source in allocation.get(collection, []):
    group = dict(source)
    name = str(source[key])
    weight = source["market_value"] / total if total > 0 else 0.0
    if security_view:
      tickers = [str(source.get("company_name") or name),
                 str(source.get("sector") or "Unclassified")]
      count = 1
      description = " · ".join(tickers)
    else:
      tickers = sorted(str(ticker) for ticker in source.get("tickers", []))
      count = len(tickers)
      preview = tickers[:4]
      if count > 4:
        preview.append("+%d more" % (count - 4))
      description = "%d holding%s" % (count, "" if count == 1 else "s")
      if preview:
        description += " · " + " · ".join(preview)
    change = ((weight - previous_weights.get(name, 0.0)) * 100
              if previous_weights is not None else None)
    change_text = ("No prior snapshot" if change is None else
                   "0.0 pp" if abs(change) < 0.05 else "%+.1f pp" % change)
    group.update({
      "sector": name, "label": name, "tickers": tickers,
      "weight": weight, "holding_count": count,
      "tickers_text": description, "change_pp": change,
      "change_text": change_text,
    })
    groups.append(group)
  return sorted(groups, key=lambda group: (-group["market_value"], group["sector"]))

"""Calendar-quarter comparisons and invested-position snapshots from history.

The helpers read the existing analysis payload without changing it. Returns
use its dividend-inclusive time-weighted index; allocations use the separate
daily market value of held shares and exclude accumulated dividend cash.
"""

import math


def _quarter(day):
  """ISO dates sort chronologically and do not require client timezone parsing."""
  year = day[:4]
  quarter = (int(day[5:7]) - 1) // 3 + 1
  return "%s-Q%d" % (year, quarter), "Q%d %s" % (quarter, year)


def _period_return(start, end):
  if (start is None or end is None or not math.isfinite(start)
      or not math.isfinite(end) or start <= 0 or end < 0):
    return None
  return end / start - 1


def quarterly_returns(points, benchmark_by_date):
  """Compare each quarter using identical baseline and ending close dates.

  Points must be chronological and contain ``date`` and ``portfolio_index``.
  ``benchmark_by_date`` maps those ISO dates to benchmark index values. The
  initial displayed close is the first baseline; every later quarter starts
  from the prior quarter's last displayed close. Thus a quarter's first daily
  return is included. ``observations`` counts close-to-close intervals, rather
  than prices. A lone initial close has no measured return. Benchmark returns
  stay unavailable when any displayed comparison date lacks benchmark data.
  First and last groups are flagged as potentially partial window edges.
  """
  if not points:
    return []
  rows = []
  start_index = 0
  while start_index < len(points):
    key, label = _quarter(points[start_index]["date"])
    end_index = start_index
    while (end_index + 1 < len(points)
           and _quarter(points[end_index + 1]["date"])[0] == key):
      end_index += 1
    baseline_index = start_index - 1 if start_index else 0
    baseline = points[baseline_index]
    ending = points[end_index]
    observations = end_index - baseline_index
    portfolio_return = benchmark_return = None
    if observations:
      portfolio_return = _period_return(
        baseline["portfolio_index"], ending["portfolio_index"]
      )
      if all(points[index]["date"] in benchmark_by_date
             for index in range(baseline_index, end_index + 1)):
        benchmark_return = _period_return(
          benchmark_by_date[baseline["date"]],
          benchmark_by_date[ending["date"]],
        )
    rows.append({
      "quarter_key": key, "label": label,
      "portfolio_return": portfolio_return,
      "benchmark_return": benchmark_return,
      "excess_return": (portfolio_return - benchmark_return
                        if portfolio_return is not None
                        and benchmark_return is not None else None),
      "start_date": baseline["date"], "end_date": ending["date"],
      "observations": observations,
      "edge_window": start_index == 0 or end_index == len(points) - 1,
    })
    start_index = end_index + 1
  return rows


def allocation_snapshots(source, selected_symbols):
  """Return earlier quarter-end positions and one latest-position snapshot.

  Earlier quarters use their last available completed trading close. The final
  date is represented once with key ``latest`` and label ``Qn YYYY · latest``.
  Selected paths must align with ``selection_data.dates`` and expose the daily
  ``shares``, ``price`` and dividend-excluding ``market_value`` fields. Legacy
  analyses without those fields return no snapshots and must be synced again.
  Sector labels are the source's current classifications, not historical ones.
  No daily paths are copied: only quarter-end position rows are constructed.
  """
  selection_data = source.get("selection_data") or {}
  dates = selection_data.get("dates") or []
  selected = set(selected_symbols)
  securities = [security for security in selection_data.get("securities", [])
                if security["symbol"] in selected]
  if not dates or not securities:
    return []
  if any(len(security.get("days", [])) != len(dates)
         for security in securities):
    return []
  end_indices = []
  for index, day in enumerate(dates):
    if index + 1 == len(dates) or _quarter(dates[index + 1])[0] != _quarter(day)[0]:
      end_indices.append(index)

  snapshots = []
  for index in end_indices:
    key, label = _quarter(dates[index])
    if index == len(dates) - 1:
      key = "latest"
      label += " · latest"
    holdings = []
    groups = {}
    for security in securities:
      daily = security["days"][index]
      if any(field not in daily for field in ("shares", "price", "market_value")):
        return []
      if daily["shares"] <= 0:
        continue
      if daily["price"] is None:
        return []
      holding = {
        "symbol": security["symbol"],
        "company_name": security.get("company_name") or security["symbol"],
        "sector": security.get("sector") or "Unclassified",
        "shares": daily["shares"], "price": daily["price"],
        "market_value": daily["market_value"],
      }
      holdings.append(holding)
      group = groups.setdefault(holding["sector"], {
        "sector": holding["sector"], "market_value": 0.0, "tickers": [],
      })
      group["market_value"] += holding["market_value"]
      group["tickers"].append(holding["symbol"])
    holdings.sort(key=lambda holding: (-holding["market_value"], holding["symbol"]))
    total_value = sum(holding["market_value"] for holding in holdings)
    slices = sorted(groups.values(), key=lambda group: (-group["market_value"], group["sector"]))
    for group in slices:
      group["weight"] = group["market_value"] / total_value if total_value else 0.0
      group["tickers"].sort()
    snapshots.append({
      "key": key, "label": label, "date": dates[index],
      "holdings": holdings, "slices": slices, "total_value": total_value,
    })
  return snapshots

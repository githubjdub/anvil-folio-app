"""Presentation-only transaction grouping; rows retain their original identity."""


def transaction_year(row):
    return getattr(row.get("trade_date"), "year", None)


def year_summary(rows):
    count = len(rows)
    securities = len({str(row.get("ticker") or "").strip().upper() for row in rows
                      if str(row.get("ticker") or "").strip()})
    buys = sum(1 for row in rows if str(row.get("action") or "Buy").title() == "Buy")
    sells = sum(1 for row in rows if str(row.get("action") or "Buy").title() == "Sell")
    return "%d transaction%s · %d %s · %d buy%s · %d sell%s" % (
        count, "" if count == 1 else "s", securities, "security" if securities == 1 else "securities",
        buys, "" if buys == 1 else "s", sells, "" if sells == 1 else "s",
    )


def group_transactions_by_year(transactions, collapsed_years, current_year):
    """Newest dates first, with undated entries visible above the dated groups.

    The most recent year stays open on first use, even for an older portfolio.
    Explicit collapse choices last for the current workspace.
    """
    groups = {}
    for row in transactions:
        groups.setdefault(transaction_year(row), []).append(row)
    years = sorted((year for year in groups if year is not None), reverse=True)
    newest = years[0] if years else None
    order = ([None] if None in groups else []) + years
    result = []
    for year in order:
        rows = list(groups[year])
        if year is not None:
            rows.sort(key=lambda row: row["trade_date"].isoformat(), reverse=True)
        title = "No date yet" if year is None else str(year)
        if year == current_year:
            title += " · This year"
        elif year == current_year - 1:
            title += " · Last year"
        default_closed = year is not None and year < current_year and year != newest
        collapsed = collapsed_years.setdefault(year, default_closed)
        result.append({"year": year, "title": title, "summary": year_summary(rows),
                       "transactions": rows, "collapsed": collapsed})
    return result

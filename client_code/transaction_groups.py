"""Presentation-only transaction grouping; rows retain their original identity."""

from datetime import date, timedelta


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


def transaction_day(row):
    value = row.get("trade_date")
    return date(value.year, value.month, value.day) if getattr(value, "year", None) is not None else None


def transaction_date_range(rows):
    days = [transaction_day(row) for row in rows]
    days = [day for day in days if day is not None]
    if not days:
        return ""
    first, last = min(days).isoformat(), max(days).isoformat()
    return first if first == last else "%s – %s" % (first, last)


def group_transactions_by_date(transactions, collapsed_groups, today=None):
    """Explorer-style calendar buckets, with recent and unfinished entries open.

    Weeks start on Monday. Earlier buckets take priority over broader month/year
    buckets, so each original transaction appears exactly once. Older sections
    start folded and mount no editors until the user opens them.
    """
    today = today or date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = date(today.year, today.month, 1)
    previous_month = month_start - timedelta(days=1)
    definitions = [
        ("today", "Today", today, True),
        ("yesterday", "Yesterday", today - timedelta(days=1), True),
        ("this_week", "Earlier this week", week_start, True),
        ("last_week", "Last week", week_start - timedelta(days=7), False),
        ("this_month", "Earlier this month", month_start, False),
        ("last_month", "Last month", date(previous_month.year, previous_month.month, 1), False),
        ("this_year", "Earlier this year", date(today.year, 1, 1), False),
        ("last_year", "Last year", date(today.year - 1, 1, 1), False),
        ("older", "A long time ago", None, False),
    ]
    groups = {}
    for row in transactions:
        day = transaction_day(row)
        key = "undated" if day is None else "future" if day > today else None
        if key is None:
            key = next(key for key, _, start, _ in definitions if start is None or day >= start)
        groups.setdefault(key, []).append(row)
    order = [("undated", "No date yet", None, True), ("future", "Future dates", None, True)] + definitions
    result = []
    for key, title, _, recent in order:
        if key not in groups:
            continue
        rows = list(groups[key])
        if key != "undated":
            rows.sort(key=lambda row: transaction_day(row).isoformat(), reverse=True)
        result.append({"key": key, "grouping": "date", "title": title,
                       "summary": year_summary(rows), "date_range": transaction_date_range(rows),
                       "transactions": rows, "recent": recent,
                       "collapsed": collapsed_groups.setdefault(key, not recent)})
    return result


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
        result.append({"year": year, "key": year, "grouping": "year", "title": title,
                       "summary": year_summary(rows), "date_range": transaction_date_range(rows),
                       "transactions": rows, "collapsed": collapsed})
    return result

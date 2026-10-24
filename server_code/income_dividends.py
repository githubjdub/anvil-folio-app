"""Dividend income estimates and payment ledger for logged portfolio holdings.

Historical entitlement is frozen before trading on an ex-date. Projections use
trailing distributions per currently held share, adjusted for subsequent splits;
annual / four is an average quarter, not an announced quarterly payment schedule.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import math
from zoneinfo import ZoneInfo

from .portfolio_analytics import AnalysisInputError
from .drip_simulations import _nasdaq_rows, _nasdaq_records, _match_records
from .dividend_payment_sources import fetch_rows as _stockanalysis_rows
from .finviz_dividend_reference import fetch_finviz_reference

_EPSILON = 1e-9
_NEW_YORK = ZoneInfo("America/New_York")


def _finite(value, label):
    if not math.isfinite(value):
        raise AnalysisInputError("%s is too large to calculate. Check the share counts and prices." % label)
    return value


def _sum(values, label):
    try:
        value = math.fsum(values)
    except OverflowError:
        raise AnalysisInputError("%s is too large to calculate. Check the share counts and prices." % label)
    return _finite(value, label)


def _anniversary(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def _reference_url(symbol):
    return "https://finviz.com/quote.ashx?t=%s&ty=dv" % symbol


def _ledger_events(transactions, histories, as_of):
    """Every earned distribution, including holdings that have since been sold."""
    trades = {}
    first = min(trade["trade_date"] for trade in transactions)
    for trade in transactions:
        if trade["trade_date"] <= as_of:
            trades.setdefault(trade["trade_date"], []).append(trade)
    days = set(trades)
    for history in histories.values():
        for field in ("splits", "dividends"):
            days.update(day for day in history.get(field, {}) if first <= day <= as_of)
    owned = {symbol: 0.0 for symbol in histories}
    events = []
    for day in sorted(days):
        for symbol, history in histories.items():
            owned[symbol] = _finite(owned[symbol] * history.get("splits", {}).get(day, 1), "Split-adjusted shares")
            amount = history.get("dividends", {}).get(day, 0)
            if amount > 0 and owned[symbol] > _EPSILON:
                events.append({"id": "%s|%s" % (symbol, day.isoformat()), "symbol": symbol,
                    "ex_date": day.isoformat(), "amount": amount, "shares": owned[symbol],
                    "earned": _finite(owned[symbol] * amount, "Dividend income"),
                    "payment_date": None, "source": "Yahoo Finance", "source_url": _reference_url(symbol),
                    "note": "The payment date could not be confirmed automatically."})
        for trade in sorted(trades.get(day, []), key=lambda item: item.get("row", 0)):
            symbol = trade["symbol"]
            delta = trade["shares"] * (1 if trade["action"] == "Buy" else -1)
            owned[symbol] = _finite(owned[symbol] + delta, "Share balance")
            if owned[symbol] < -max(_EPSILON, abs(delta) * 1e-10):
                raise AnalysisInputError("%s has a sale exceeding its logged shares." % symbol)
            owned[symbol] = max(0.0, owned[symbol])
    return events


def _public_sources(histories, symbols):
    """Fetch only ticker-level public data; no portfolio amounts leave the app."""
    sources = {symbol: {} for symbol in symbols}
    jobs = [(provider, symbol) for symbol in symbols for provider in ("Finviz", "Nasdaq", "Stock Analysis")]
    def fetch(job):
        provider, symbol = job
        if provider == "Finviz":
            return fetch_finviz_reference(symbol)
        instrument = histories[symbol].get("instrument_type")
        return _nasdaq_rows(symbol, instrument) if provider == "Nasdaq" else _stockanalysis_rows(symbol, instrument)
    if jobs:
        with ThreadPoolExecutor(max_workers=min(12, len(jobs))) as executor:
            for (provider, symbol), result in zip(jobs, executor.map(fetch, jobs)):
                sources[symbol][provider] = result
    return sources


def _nasdaq_url(symbol, history):
    namespace = "etf" if history.get("instrument_type") == "ETF" else "stocks"
    return "https://www.nasdaq.com/market-activity/%s/%s/dividend-history" % (namespace, symbol.lower())


def _parse_nasdaq_upcoming(rows, today):
    """Retain explicitly published ex-dates even if amount/pay date is unknown."""
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("currency") != "USD" or row.get("type") != "Cash":
            continue
        try:
            ex_date = datetime.strptime(str(row.get("exOrEffDate")), "%m/%d/%Y").date()
        except ValueError:
            continue
        if ex_date <= today:
            continue
        amount = None
        raw = str(row.get("amount") or "").strip()
        if raw.startswith("$"):
            try:
                parsed = float(raw[1:].replace(",", ""))
                if math.isfinite(parsed) and parsed > 0:
                    amount = parsed
            except ValueError:
                pass
        try:
            payment = datetime.strptime(str(row.get("paymentDate")), "%m/%d/%Y").date().isoformat()
        except ValueError:
            payment = None
        result.append({"ex_date": ex_date.isoformat(), "amount": amount, "payment_date": payment})
    return result


def _upcoming(sources, histories, positions, today, as_of):
    result = []
    for symbol, position in positions.items():
        if position["shares"] <= _EPSILON:
            continue
        source = sources[symbol]
        finviz = source["Finviz"]
        nasdaq_rows = source["Nasdaq"][0]
        stock_rows = source["Stock Analysis"][0]
        groups = {}
        unsupported_dates = set()
        # Finviz is the first reference. Payment dates must be supplied by a
        # provider that publishes them explicitly, never inferred from ex-dates.
        for provider, rows, url in (
            ("Finviz", finviz["rows"], finviz["source_url"]),
            ("Nasdaq", _parse_nasdaq_upcoming(nasdaq_rows, today), _nasdaq_url(symbol, histories[symbol])),
            ("Stock Analysis", stock_rows, stock_rows[0].get("source_url") if stock_rows else ""),
        ):
            by_date = {}
            for row in rows:
                try:
                    ex_date = date.fromisoformat(row["ex_date"])
                except (ValueError, TypeError, KeyError):
                    continue
                if ex_date <= today:
                    continue
                by_date.setdefault(ex_date.isoformat(), []).append(row)
            for ex_date, components in by_date.items():
                amounts = [row.get("amount") for row in components]
                identities = [(row.get("amount"), row.get("payment_date")) for row in components]
                distinct = len(set(identities)) == len(identities)
                amount = (_sum(amounts, "Upcoming dividend") if distinct and all(
                    type(value) in (int, float) and math.isfinite(value) and value > 0 for value in amounts) else None)
                payments = {row.get("payment_date") for row in components}
                payment = next(iter(payments)) if len(payments) == 1 else None
                if payment and payment < ex_date:
                    unsupported_dates.add(ex_date)
                    continue  # Due-bill events require a different eligibility model.
                groups.setdefault(ex_date, []).append({"amount": amount, "payment_date": payment,
                    "source": provider, "source_url": url})
        for ex_date, observations in groups.items():
            if ex_date in unsupported_dates:
                continue
            known = [row for row in observations if row["amount"] is not None]
            conflict = bool(known) and any(not math.isclose(row["amount"], known[0]["amount"], rel_tol=1e-5, abs_tol=1e-6) for row in known)
            amount = known[0]["amount"] if known and not conflict else None
            explicit = [row for row in observations if row["payment_date"] and (amount is None or row["amount"] is not None and math.isclose(row["amount"], amount, rel_tol=1e-5, abs_tol=1e-6))]
            payment_dates = {row["payment_date"] for row in explicit}
            payment = next(iter(payment_dates)) if len(payment_dates) == 1 else None
            preferred = explicit[0] if payment else observations[0]
            split_factor = _finite(math.prod(ratio for day, ratio in histories[symbol].get("splits", {}).items()
                if as_of < day <= date.fromisoformat(ex_date)), "Upcoming split factor")
            if split_factor <= 0:
                raise AnalysisInputError("%s has an invalid split factor." % symbol)
            eligible_shares = _finite(position["shares"] * split_factor, "Upcoming eligible shares")
            note = "Estimated cash uses snapshot shares adjusted for known splits through the published ex-date, assuming no later trades."
            if amount is None:
                note += " Sources disagree on the amount." if conflict else " The cash amount has not been confirmed."
            result.append({"symbol": symbol, "ex_date": ex_date, "amount": amount,
                "payment_date": payment, "estimated_cash": None if amount is None else _finite(amount * eligible_shares, "Upcoming dividend income"),
                "source": preferred["source"], "source_url": preferred["source_url"], "note": note})
    return sorted(result, key=lambda row: (row["ex_date"], row["symbol"]))


def build_dividend_report(transactions, histories, as_of, positions):
    """Return transient analysis data; no portfolio or account records are changed."""
    events = _ledger_events(transactions, histories, as_of)
    open_positions = {symbol: row for symbol, row in positions.items() if row["shares"] > _EPSILON}
    symbols = sorted(set(open_positions) | {row["symbol"] for row in events})
    sources = _public_sources(histories, symbols)
    for symbol in symbols:
        selected = [row for row in events if row["symbol"] == symbol]
        source = sources[symbol]
        stock_rows = source["Stock Analysis"][0]
        observations = []
        for provider, rows, url in (
            ("Nasdaq", _nasdaq_records(source["Nasdaq"][0]), _nasdaq_url(symbol, histories[symbol])),
            ("Stock Analysis", stock_rows, stock_rows[0].get("source_url") if stock_rows else ""),
        ):
            matched = [dict(row) for row in selected]
            _match_records(matched, rows, provider, url)
            observations.append(matched)
        for index, row in enumerate(selected):
            matches = [provider_rows[index] for provider_rows in observations]
            special = next((match for match in matches if match.get("unsupported")), None)
            confirmed = [match for match in matches if match["payment_date"]]
            if special:
                row.update(unsupported=True, note=special["note"])
            elif len({match["payment_date"] for match in confirmed}) > 1:
                row["note"] = "Automatic sources disagree on the payment date; cash is excluded until it can be confirmed."
            elif confirmed:
                row.update({key: confirmed[0][key] for key in ("payment_date", "source", "source_url", "note")})
        reference = {row["ex_date"]: row["amount"] for row in source["Finviz"]["rows"]}
        for row in selected:
            reference_amount = reference.get(row["ex_date"])
            if isinstance(reference_amount, (int, float)) and not isinstance(reference_amount, bool) and math.isfinite(reference_amount) and math.isclose(reference_amount, row["amount"], rel_tol=1e-5, abs_tol=1e-6):
                row["note"] += " Ex-date and cash amount also match Finviz."
            if row.get("unsupported"):
                # Due-bill distributions do not follow ordinary ex-date ownership
                # rules; neither eligibility nor earned cash has been established.
                row["earned"] = None
            row["status"] = ("Special distribution timing — review required" if row.get("unsupported") else
                "Payment date unconfirmed" if not row["payment_date"] else
                "Paid (modeled)" if row["payment_date"] <= as_of.isoformat() else "Awaiting payment")
    trailing_start = _anniversary(as_of)
    securities = []
    for symbol, position in sorted(open_positions.items()):
        history = histories[symbol]
        adjusted = []
        for ex_date, amount in history.get("dividends", {}).items():
            if trailing_start < ex_date <= as_of:
                split_factor = _finite(math.prod(ratio for day, ratio in history.get("splits", {}).items() if ex_date < day <= as_of), "Split factor")
                if split_factor <= 0:
                    raise AnalysisInputError("%s has an invalid split factor." % symbol)
                adjusted.append(amount / split_factor)
        annual = _finite(position["shares"] * _sum(adjusted, "Trailing dividends"), "Annual dividend estimate")
        basis = position.get("cost_basis")
        closes = [day for day in history.get("close", {}) if day <= as_of]
        limited = not closes or min(closes) > trailing_start + timedelta(days=7)
        securities.append({"symbol": symbol, "shares": position["shares"], "cost_basis": basis,
            "basis_estimated": bool(position.get("basis_estimated")), "history_limited": limited,
            "annual_income": annual, "quarterly_average": annual / 4,
            "yield_on_cost": _finite(100 * annual / basis, "Yield on cost") if basis is not None and basis > 0 else None,
            "source_url": _reference_url(symbol)})
    annual_total = _sum((row["annual_income"] for row in securities), "Annual dividend estimate")
    known_basis = all(row["cost_basis"] is not None for row in securities)
    total_basis = _sum((row["cost_basis"] for row in securities), "Portfolio basis") if known_basis else None
    paid = [row for row in events if row["payment_date"] and row["payment_date"] <= as_of.isoformat() and not row.get("unsupported")]
    year_start = date(as_of.year, 1, 1).isoformat()
    paid_ytd = _sum((row["earned"] for row in paid if row["payment_date"] >= year_start), "Year-to-date dividends")
    pending = _sum((row["earned"] for row in events if row["payment_date"] and row["payment_date"] > as_of.isoformat()), "Pending dividends")
    unresolved = [row for row in events if not row["payment_date"]]
    # Show all quarters covered by the logged history, with an honest partial
    # first/current quarter rather than treating missing history as full coverage.
    first = min(trade["trade_date"] for trade in transactions)
    quarter = date(first.year, 1 + 3 * ((first.month - 1) // 3), 1)
    quarters = []
    while quarter <= as_of:
        next_quarter = date(quarter.year + 1, 1, 1) if quarter.month == 10 else date(quarter.year, quarter.month + 3, 1)
        end = min(as_of, next_quarter - timedelta(days=1))
        amount = _sum((row["earned"] for row in paid if quarter.isoformat() <= row["payment_date"] <= end.isoformat()), "Quarterly dividends")
        quarters.append({"label": "%s Q%s" % (quarter.year, (quarter.month - 1) // 3 + 1), "amount": amount,
            "is_partial": first > quarter or as_of < next_quarter - timedelta(days=1)})
        quarter = next_quarter
    today = datetime.now(_NEW_YORK).date()
    coverage = ["Estimates repeat observed trailing 12-month distributions on current shares; dividends can change, and special payouts may not recur.",
        "Portfolio yield on cost includes the FIFO basis of all open holdings, including non-dividend payers.",
        "Confirmed future payments on already-earned dividends: ${:,.2f}.".format(pending)]
    if any(row["basis_estimated"] for row in securities):
        coverage.append("Some purchase prices are estimated from historical closes.")
    if not known_basis:
        coverage.append("Missing purchase basis prevents a portfolio yield-on-cost estimate.")
    if any(row["history_limited"] for row in securities):
        coverage.append("Some holdings have less than 12 months of price history; observed dividends are not annualized.")
    if unresolved:
        coverage.append("Unconfirmed payment dates are excluded from received-cash totals.")
    return {"totals": {"annual_estimate": annual_total, "quarterly_average": annual_total / 4,
        "yield_on_cost": _finite(100 * annual_total / total_basis, "Portfolio yield on cost") if total_basis is not None and total_basis > 0 else None,
        "paid_ytd": paid_ytd, "pending_income": pending, "unresolved_count": len(unresolved),
        "earned_ttm": _sum((row["earned"] for row in events if row["earned"] is not None and row["ex_date"] > trailing_start.isoformat()), "Trailing earned dividends")},
        "securities": securities, "quarterly_history": quarters,
        "ledger": sorted(events, key=lambda row: (row["ex_date"], row["symbol"]), reverse=True),
        "upcoming": _upcoming(sources, histories, open_positions, today, as_of),
        "metadata": {"as_of": as_of.isoformat(), "upcoming_as_of": today.isoformat(), "currency": "USD",
            "coverage_note": " ".join(coverage), "projection_method": "Current shares × trailing 12-month split-adjusted cash distributions; average quarter = annual / 4",
            "source": "Yahoo Finance dividend amounts; Finviz ex-date reference; Nasdaq and Stock Analysis payment dates"}}

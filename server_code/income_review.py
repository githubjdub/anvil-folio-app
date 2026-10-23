"""Dividend and FIFO loss review from the user's selected transaction snapshot."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import anvil.server
from .portfolio_analytics import AnalysisInputError, _validate, _fetch_history, _history_for_session
from .income_tax_lots import build_tax_report
from .income_dividends import build_dividend_report


@anvil.server.callable
def get_income_review(transactions, as_of_date):
  try:
    clean, as_of, _ = _validate(transactions, as_of_date, 0)
    first_trade = min(trade["trade_date"] for trade in clean)
    if (as_of - first_trade).days > 3660:
      raise AnalysisInputError("Income review currently supports up to 10 years of logged transactions.")
    start = min(first_trade, as_of - timedelta(days=370))
    symbols = sorted({trade["symbol"] for trade in clean})
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as executor:
      fetched = list(executor.map(lambda symbol: _fetch_history(symbol, start, now), symbols))
    histories = {symbol: _history_for_session(history, as_of) for symbol, history in zip(symbols, fetched)}
    tax = build_tax_report(clean, histories, as_of)
    dividends = build_dividend_report(clean, histories, as_of, tax["positions"])
    return {"ok":True, "tax":tax, "dividends":dividends,
            "metadata":{"as_of":as_of.isoformat(),"currency":"USD","symbol_count":len(symbols),
                        "calculated_at":now.isoformat()}}
  except AnalysisInputError as exc:
    return {"ok":False,"message":str(exc)}

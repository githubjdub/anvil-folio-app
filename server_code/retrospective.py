import anvil.secrets
"""Validated prompt previews and daily historical strategy simulations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import re

import anvil.server
from .portfolio_analytics import AnalysisInputError, _as_date, _fetch_history, _normalise_ticker, _finite_number, _EXCHANGES
from .retrospective_prompts import parse_retrospective_prompt
from .retrospective_engine import run_backtest, RetrospectiveInputError
from .sectors import _lookup_sector, _SECTORS


SECTORS = set(_SECTORS.values()) | {"Funds / ETFs", "Unclassified"}


@anvil.server.callable
def preview_retrospective_prompt(prompt, sector=None):
  """Interpret supported prose into inspectable rules; never execute prose."""
  if sector is not None and (not isinstance(sector, str) or sector not in SECTORS):
    return {"ok": False, "message": "Choose a sector from the list."}
  return parse_retrospective_prompt(prompt, selected_sector=sector)


def _validate_request(tickers, rules, start_date, end_date, capital, sector):
  if not isinstance(tickers, str) or len(tickers) > 500:
    raise AnalysisInputError("Enter up to 10 US-listed ticker symbols.")
  symbols = []
  for raw in re.split(r"[,;\s]+", tickers.strip()):
    if not raw:
      continue
    symbol, exchange = _normalise_ticker(raw)
    if symbol is None or exchange not in _EXCHANGES:
      raise AnalysisInputError("Use US-listed USD tickers, such as AAPL, MSFT or SPY.")
    symbol = symbol.replace(".", "-")
    if symbol not in symbols:
      symbols.append(symbol)
  if not 1 <= len(symbols) <= 10:
    raise AnalysisInputError("Enter between 1 and 10 different ticker symbols.")
  try:
    start, end = _as_date(start_date), _as_date(end_date)
  except (ValueError, TypeError):
    raise AnalysisInputError("Choose valid start and end dates.")
  if start > end or start < date(1990, 1, 1) or end > date.today():
    raise AnalysisInputError("Use dates from 1990 through today, with start on or before end.")
  if (end - start).days > 5 * 366:
    raise AnalysisInputError("Test up to five years at a time.")
  capital = _finite_number(capital, "Starting capital", True)
  if capital > 100000000:
    raise AnalysisInputError("Starting capital must be greater than zero and at most $100,000,000.")
  if sector is not None and (not isinstance(sector, str) or sector not in SECTORS):
    raise AnalysisInputError("Choose a sector from the list.")
  if not isinstance(rules, dict):
    raise AnalysisInputError("Preview your prompt or set strategy rules first.")
  entry, exit_kind = rules.get("entry_kind"), rules.get("exit_kind")
  if entry not in ("low", "high", "buy_hold") or exit_kind not in ("low", "high", "none"):
    raise AnalysisInputError("Choose a supported entry and exit rule.")
  lookback = rules.get("lookback_days")
  if isinstance(lookback, bool) or not isinstance(lookback, int) or not 20 <= lookback <= 730:
    raise AnalysisInputError("Use a rolling lookback between 20 and 730 calendar days.")
  fraction = _finite_number(rules.get("exit_fraction"), "Sale fraction", True)
  if fraction > 1:
    raise AnalysisInputError("Sell more than 0% and at most 100% of the position at the first exit signal.")
  dividends = rules.get("include_dividends")
  if not isinstance(dividends, bool):
    raise AnalysisInputError("Choose whether to include dividends.")
  strategy = {"entry_condition": entry, "exit_condition": exit_kind,
              "lookback_days": lookback, "sell_fraction": fraction,
              "entry_proximity_pct": 0, "exit_proximity_pct": 0,
              "include_dividends": dividends}
  return symbols, start, end, float(capital), strategy


@anvil.server.callable
def run_retrospective_analysis(tickers, rules, start_date, end_date, capital, sector=None):
  """Run an equal-allocation simulation, isolated from real transactions."""
  try:
    symbols, start, end, capital, strategy = _validate_request(
      tickers, rules, start_date, end_date, capital, sector
    )
    warmup = start - timedelta(days=strategy["lookback_days"] + 14)
    now = datetime.now(timezone.utc)
    fetch_symbols = list(dict.fromkeys(symbols + ["SPY"]))
    with ThreadPoolExecutor(max_workers=4) as pool:
      histories = dict(zip(fetch_symbols, pool.map(lambda symbol: _fetch_history(symbol, warmup, now), fetch_symbols)))
    with ThreadPoolExecutor(max_workers=4) as pool:
      labels = dict(zip(symbols, pool.map(lambda symbol: _lookup_sector(symbol, histories[symbol].get("instrument_type")), symbols)))
    chosen = {symbol: histories[symbol] for symbol in symbols if sector is None or labels[symbol] == sector}
    if not chosen:
      raise AnalysisInputError("None of these tickers currently match %s. Edit your ticker list or choose All sectors." % sector)
    result = run_backtest(chosen, start, end, capital, strategy, benchmark=histories["SPY"])
    result["metrics"]["benchmark_return"] = result["series"][-1]["benchmark_return"]
    for security in result["securities"]:
      security["sector"] = labels[security["ticker"]]
    result["ok"] = True
    result["metadata"].update({"source": "Yahoo Finance completed daily history",
                                "sector": sector, "requested_tickers": symbols,
                                "tested_tickers": list(chosen), "benchmark": "SPY · S&P 500 ETF",
                                "sector_classified_as_of": date.today().isoformat()})
    excluded = [symbol for symbol in symbols if symbol not in chosen]
    if excluded:
      result["warnings"].append("Sector filter excluded: %s." % ", ".join(excluded))
    if sector is not None:
      result["warnings"].append("Sector membership uses current Finviz labels for the tickers you supplied. This is not a historical full-sector universe; current selections can introduce survivorship bias.")
    result["warnings"].append("Hypothetical daily simulation: no intraday or exact-second fills. One entry and at most one exit per security; fractional shares, zero fees/taxes/slippage, and no dividend reinvestment.")
    return result
  except (AnalysisInputError, RetrospectiveInputError) as error:
    return {"ok": False, "message": str(error)}

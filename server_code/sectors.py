"""App-owned sector defaults and private account choices; no website lookups."""

from datetime import date, datetime
import math
import re

import anvil.server
import anvil.users
from anvil.tables import app_tables, in_transaction


# Preserve the app's existing categories and chart colours. This maintained
# starter catalog does not claim to cover every listed security.
_SECTORS = {
  "basicmaterials": "Basic Materials", "communicationservices": "Communication Services",
  "consumercyclical": "Consumer Cyclical", "consumerdefensive": "Consumer Defensive",
  "energy": "Energy", "financial": "Financial", "healthcare": "Healthcare",
  "industrials": "Industrials", "realestate": "Real Estate",
  "technology": "Technology", "utilities": "Utilities",
}
_UNKNOWN = "Unclassified"
_FUNDS = "Funds / ETFs"
SECTOR_OPTIONS = sorted(_SECTORS.values()) + [_FUNDS, _UNKNOWN]
_CATALOG_GROUPS = {
  "Technology": "AAPL MSFT NVDA AVGO AMD INTC ORCL ADBE CRM NOW CSCO IBM QCOM TXN AMAT MU INTU LRCX KLAC SNPS CDNS PANW CRWD FTNT PLTR SHOP ANET DELL HPQ SMCI NXPI ARM ADSK ACN",
  "Financial": "JPM BAC WFC C USB PNC TFC GS MS SCHW BLK BRK-A BRK-B V MA AXP PYPL COF SPGI ICE MET PRU AIG PGR TRV CB MCO CME AON MMC COIN HOOD XYZ SQ",
  "Healthcare": "JNJ UNH LLY ABBV MRK PFE AMGN GILD BMY CVS TMO ABT MDT ZTS DHR ISRG REGN VRTX BSX GEHC",
  "Consumer Cyclical": "AMZN TSLA MCD SBUX NKE HD LOW TJX TGT BKNG CMG YUM F GM EBAY RIVN RACE LULU DHI LEN ORLY AZO ABNB",
  "Consumer Defensive": "WMT COST PG KO PEP PM MO CL KMB MDLZ GIS KHC KR SYY STZ MNST EL DG DLTR ADM TSN HSY",
  "Communication Services": "GOOG GOOGL META NFLX DIS CMCSA T VZ TMUS CHTR WBD EA TTWO PINS SNAP RDDT SPOT",
  "Industrials": "CAT DE HON GE GEV BA RTX LMT NOC GD UPS FDX UNP CSX NSC WM RSG ETN EMR MMM ITW ADP UBER DAL UAL LUV",
  "Energy": "XOM CVX COP EOG SLB OXY MPC VLO PSX KMI WMB OKE TRGP LNG HAL BKR DVN FANG",
  "Basic Materials": "LIN APD SHW FCX NEM NUE DOW DD ECL PPG MLM VMC ALB MOS CF",
  "Real Estate": "PLD AMT EQIX DLR O SPG WELL PSA CCI VICI AVB EQR INVH EXR",
  "Utilities": "NEE DUK SO AEP EXC SRE XEL D ED PEG WEC ETR CEG VST PCG AES PPL",
}
_CATALOG = {symbol: sector for sector, symbols in _CATALOG_GROUPS.items() for symbol in symbols.split()}
_FUND_SYMBOLS = set("SPY IVV VOO VTI ITOT SCHB QQQ QQQM DIA IWM IJR IJH VT VXUS VEA VWO SCHD VIG DGRO VYM JEPI JEPQ BND AGG TLT IEF SHY SGOV BIL LQD HYG TIP MUB GLD IAU SLV USO XLF XLK XLE XLV XLI XLU XLY XLP XLB XLRE XLC VGT VHT VNQ VDE VFH SMH SOXX ARKK ARKG ARKF ARKW ARKQ VO VB VUG VTV SCHG SCHX SPLG SPYM RSP SPYD SPHD NOBL DGRW SPYI QQQI TQQQ SQQQ SOXL SOXS UPRO SPXU IBIT FBTC BITO BITX".split())


def _symbol(value):
  return str(value or "").strip().upper().split(":", 1)[0].replace(".", "-")


def _preferences():
  user = anvil.users.get_user()
  if user is None or user["enabled"] is not True:
    return {}
  return {row["symbol"]: row["sector"] for row in app_tables.security_sectors.search(owner=user)}


def _is_fund(symbol, instrument_type):
  return symbol in _FUND_SYMBOLS or str(instrument_type or "").upper() in ("ETF", "MUTUALFUND")


def _lookup_sector(symbol, instrument_type, preferences=None):
  symbol = _symbol(symbol)
  if _is_fund(symbol, instrument_type):
    return _FUNDS
  chosen = (preferences or {}).get(symbol)
  return chosen if chosen in SECTOR_OPTIONS else _CATALOG.get(symbol, _UNKNOWN)


def classify_securities(symbols, histories):
  """Resolve a batch locally, loading account preferences once per batch."""
  preferences = _preferences()
  return {symbol: _lookup_sector(symbol, histories.get(symbol, {}).get("instrument_type"), preferences)
          for symbol in symbols}


@anvil.server.callable(require_user=True)
@in_transaction
def set_security_sector(symbol, sector):
  """Set or clear only the signed-in account's classification for a ticker."""
  user = anvil.users.get_user()
  if user is None or user["enabled"] is not True:
    raise anvil.users.AuthenticationFailed("Sign in to save sector choices.")
  if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,11}", symbol):
    return {"ok": False, "message": "Choose a valid security ticker."}
  symbol = _symbol(symbol)
  if sector is not None and (not isinstance(sector, str) or sector not in SECTOR_OPTIONS):
    return {"ok": False, "message": "Choose a sector from the list."}
  if symbol in _FUND_SYMBOLS and sector not in (None, _FUNDS):
    return {"ok": False, "message": "Funds and ETFs stay in their own group; their underlying sectors are not estimated."}
  row = app_tables.security_sectors.get(owner=user, symbol=symbol)
  if sector is None:
    if row is not None:
      row.delete()
  elif row is None:
    app_tables.security_sectors.add_row(owner=user, symbol=symbol, sector=sector, updated_at=datetime.now())
  else:
    row.update(sector=sector, updated_at=datetime.now())
  default = _lookup_sector(symbol, None)
  return {"ok": True, "symbol": symbol, "sector": sector or default,
          "sector_override": sector, "default_sector": default}


def refresh_analysis_classifications(analysis):
  """Apply current local choices to a new or saved analysis without repricing."""
  preferences = _preferences()
  securities = analysis["selection_data"]["securities"]
  classifications = {}
  legacy_source = (analysis.get("sector_allocation") or {}).get("classification_source")
  for security in securities:
    symbol = security["symbol"]
    # Older saved analyses did not retain the provider's instrument type.
    instrument_type = security.get("instrument_type")
    if instrument_type is None and legacy_source == "Finviz" and security.get("sector") == _FUNDS:
      instrument_type = "ETF"
    security["instrument_type"] = instrument_type
    security["default_sector"] = _lookup_sector(symbol, instrument_type)
    security["sector_locked"] = _is_fund(symbol, instrument_type)
    security["sector_override"] = preferences.get(symbol)
    security["sector"] = _lookup_sector(symbol, instrument_type, preferences)
    classifications[symbol] = security["sector"]
  old = analysis.get("sector_allocation") or {}
  holdings = analysis.pop("open_holdings", old.get("holdings", []))
  analysis["sector_allocation"] = build_sector_allocation(
    holdings, old.get("as_of") or analysis["metadata"]["end_date"], {}, classifications)
  analysis["sector_options"] = list(SECTOR_OPTIONS)
  return analysis


def build_sector_allocation(open_holdings, as_of, histories, classifications=None):
  """Group held market values, excluding sale proceeds and dividend cash."""
  if classifications is None:
    classifications = classify_securities([holding["symbol"] for holding in open_holdings], histories)
  holdings = []
  grouped = {}
  for holding in open_holdings:
    sector = classifications.get(holding["symbol"], _UNKNOWN)
    holdings.append(dict(holding, sector=sector))
    group = grouped.setdefault(sector, {"sector": sector, "market_value": 0.0, "tickers": []})
    group["market_value"] += holding["market_value"]
    group["tickers"].append(holding["symbol"])
  total_value = math.fsum(holding["market_value"] for holding in holdings)
  if not math.isfinite(total_value):
    raise ValueError("The total market value must be finite.")
  slices = sorted(grouped.values(), key=lambda group: (-group["market_value"], group["sector"]))
  for group in slices:
    group["weight"] = group["market_value"] / total_value if total_value else 0.0
    group["tickers"].sort()
  return {"as_of": as_of, "classification_as_of": date.today().isoformat(),
          "classification_source": "Folio catalog and account choices", "total_value": total_value,
          "slices": slices, "holdings": holdings,
          "unclassified_tickers": sorted(holding["symbol"] for holding in holdings if holding["sector"] == _UNKNOWN)}

"""Resumable public-filing updates, with one external request per step.

Only trusted catalog URLs reach providers. Work is persisted by hedge_funds,
not by a background-task worker, so a later visitor can resume a closed page.
"""
from datetime import date, datetime, timedelta, timezone
import json
import re
from typing import Any

from . import hedge_fund_data, hedge_fund_engine

_data: Any = hedge_fund_data
_engine: Any = hedge_fund_engine
_TIMEOUT = 6
_HISTORY_FIELDS = ("close", "low", "high", "splits", "dividends")


def new_progress(fund_key):
  entry = _data._FUNDS[fund_key]
  return {"phase": "discover", "queue": [
    {"cik": cik, "priority": priority, "url": "https://data.sec.gov/submissions/CIK%s.json" % cik.zfill(10), "older": False}
    for priority, cik in enumerate((entry[3],) + entry[4])],
    "records": [], "file_index": 0, "known_mapping": {},
    "end_date": date.today().isoformat(), "message": "Checking this manager's latest SEC submissions…"}


def _record(value):
  return dict(value, period_end=date.fromisoformat(value["period_end"]), filed_at=date.fromisoformat(value["filed_at"]))


def _select_records(fund_key, records):
  transition = _data._CURRENT_FROM.get(fund_key)
  if transition is not None:
    records = [record for record in records if (date.fromisoformat(record["period_end"]) >= transition) == (record["priority"] == 0)]
  periods = sorted({record["period_end"] for record in records if record["form"] == "13F-HR"}, reverse=True)[:5]
  selected = []
  for period in periods:
    matches = [record for record in records if record["period_end"] == period]
    priority = min(record["priority"] for record in matches if record["form"] == "13F-HR")
    selected.extend(record for record in matches if record["priority"] == priority)
  if not selected:
    raise _data._SourceError("No public quarterly holdings reports were available for this manager.")
  if len(selected) > 40:
    raise _data._SourceError("This history has too many amendments for a bounded update. Saved holdings remain available.")
  return sorted(selected, key=lambda item: (item["period_end"], item["filed_at"], item["accession"]))


def _discover(fund_key, work):
  task = work["queue"].pop(0)
  document = _data._json_request(task["url"], timeout=_TIMEOUT)
  if not isinstance(document, dict):
    raise _data._SourceError("SEC returned an invalid submissions document.")
  records = _data._filing_records(document)
  for record in records:
    record.update(cik=task["cik"], priority=task["priority"], filer_name=document.get("name") or _data._FUNDS[fund_key][1])
    work["records"].append({key: value.isoformat() if isinstance(value, date) else value for key, value in record.items()})
  if not task["older"]:
    # Older pages are also separate requests; never loop over network work here.
    found = {item["period_end"] for item in records if item["form"] == "13F-HR"}
    if len(found) < 5:
      for older in document.get("filings", {}).get("files", [])[:2]:
        name = older.get("name")
        if isinstance(name, str) and re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
          work["queue"].append(dict(task, url="https://data.sec.gov/submissions/" + name, older=True))
  if not work["queue"]:
    work["records"] = _select_records(fund_key, work["records"])
    work["phase"] = "filing"
    work["message"] = "SEC submissions checked. Importing missing quarterly reports…"
  return None


def _known_mapping(fund_key, work):
  mapping = work["known_mapping"]
  for row in _data.app_tables.hedge_fund_snapshots.search(fund_key=fund_key):
    for item in row["holdings"] or []:
      if item.get("symbol"):
        mapping[item["cusip"]] = item["symbol"]
  return mapping


def _filing(fund_key, work):
  records = work["records"]
  while work["file_index"] < len(records):
    record = records[work["file_index"]]
    if _data.app_tables.hedge_fund_snapshots.get(fund_key=fund_key, accession=record["accession"]) is None:
      break
    work["file_index"] += 1
  if work["file_index"] == len(records):
    work.pop("records", None)
    work.pop("known_mapping", None)
    work["phase"] = "prepare_model"
    work["message"] = "Quarterly holdings saved. Preparing the comparison model…"
    return None
  record = records[work["file_index"]]
  url = "https://www.sec.gov/Archives/edgar/data/%s/%s/%s.txt" % (record["cik"], record["accession"].replace("-", ""), record["accession"])
  holdings, coverage = _data._parse_filing(_data._request(url, "sec", timeout=_TIMEOUT), _record(record))
  # Explicit bound on persistent intermediate data, even for very broad managers.
  if len(holdings) > 5000:
    raise _data._SourceError("This filing exceeds the 5,000-position importer limit. Existing holdings remain available.")
  work["current"] = {"record": record, "holdings": holdings, "coverage": coverage}
  _known_mapping(fund_key, work)
  work["phase"] = "mapping"
  work["message"] = "Quarterly report downloaded. Resolving its disclosed securities…"
  return None


def _mapping(fund_key, work):
  current = work["current"]
  mapping = work["known_mapping"]
  needed = sorted({item["cusip"] for item in current["holdings"] if item["cusip"] not in mapping})
  if needed:
    batch = needed[:5]
    payload = [{"idType": "ID_CUSIP", "idValue": cusip, "exchCode": "US", "includeUnlistedEquities": True} for cusip in batch]
    result = _data._json_request("https://api.openfigi.com/v3/mapping", "figi", json.dumps(payload).encode(), timeout=_TIMEOUT)
    if not isinstance(result, list) or len(result) != len(batch):
      raise _data._SourceError("OpenFIGI returned an incomplete mapping response.")
    for cusip, item in zip(batch, result):
      mapping[cusip] = _data._symbol_from_mapping(item)
    work["message"] = "Resolving disclosed securities · %s remaining in this report…" % max(0, len(needed) - len(batch))
  else:
    for item in current["holdings"]:
      item["symbol"] = mapping.get(item["cusip"])
    _data._store_snapshot(fund_key, _record(current["record"]), current["holdings"], _data._coverage(current["holdings"], current["coverage"]))
    work.pop("current")
    work["file_index"] += 1
    work["phase"] = "filing"
    work["message"] = "Report saved · checking the next quarter…"
  return None


def _prepare_model(fund_key, work):
  snapshots = _data.get_snapshots(fund_key)
  clean = _engine._clean_snapshots(snapshots, date.fromisoformat(work["end_date"]))
  symbols = sorted({_engine._eligible(item) for snapshot in clean for item in snapshot["holdings"] if _engine._eligible(item)})
  if not symbols:
    raise _engine.FundSimulationError("Quarterly holdings are saved, but no mapped US equity symbols are available for a comparison model.")
  if len(symbols) > 100:
    raise _engine.FundSimulationError("Quarterly holdings are saved. This manager has more than 100 mapped symbols, beyond the comparison model limit.")
  work.update(phase="prices", symbols=symbols + ["^GSPC", "^SP500TR"], price_index=0,
              start=clean[0]["period_end"].isoformat(), histories={}, failures=[],
              message="Holdings saved. Loading one security's completed prices at a time…")
  return None


def _prices(work):
  index = work["price_index"]
  symbol = work["symbols"][index]
  try:
    history = _engine.fetch_history(symbol, date.fromisoformat(work["start"]), timeout=_TIMEOUT)
    compact = {field: {day.isoformat(): value for day, value in history.get(field, {}).items()}
               for field in _HISTORY_FIELDS}
    compact["instrument_type"] = history.get("instrument_type")
    work["histories"][symbol] = compact
  except _engine.FundSimulationError as error:
    work["failures"].append([symbol, str(error)])
  work["price_index"] += 1
  work["message"] = "Loading completed prices · %s of %s securities checked…" % (work["price_index"], len(work["symbols"]))
  if work["price_index"] == len(work["symbols"]):
    work["phase"] = "build"
    work["message"] = "All price requests finished. Reconstructing the saved holdings model…"
  return None


def advance(fund_key, work):
  """Mutate one bounded step; return a final model only when ready."""
  phase = work["phase"]
  if phase == "discover":
    return _discover(fund_key, work)
  if phase == "filing":
    return _filing(fund_key, work)
  if phase == "mapping":
    return _mapping(fund_key, work)
  if phase == "prepare_model":
    return _prepare_model(fund_key, work)
  if phase == "prices":
    return _prices(work)
  if phase == "build":
    histories = {symbol: dict(history, **{field: {date.fromisoformat(day): value for day, value in history.get(field, {}).items()}
                 for field in _HISTORY_FIELDS}) for symbol, history in work["histories"].items()}
    model = _engine.complete_series(_data.get_snapshots(fund_key), date.fromisoformat(work["end_date"]), histories, work["failures"])
    if not model["ok"]:
      raise _engine.FundSimulationError(model["message"])
    return model
  raise RuntimeError("A saved filing update has an invalid phase.")

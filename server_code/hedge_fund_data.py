import anvil.secrets
"""Public 13F source documents and persistent quarterly holdings.

HedgeFollow is a profile reference, not a scraped/licensed data feed. EDGAR is
the holdings source; OpenFIGI resolves exact CUSIPs to US equity instruments.
Source documents are immutable and amendments are composed when read. These
helpers are deliberately not client-callable: orchestration controls updates.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import json
import math
import re
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

import anvil.tables
from anvil.tables import app_tables


_USER_AGENT = "Portfoliora 13F research contact jonathanjonathanwing@gmail.com"
_CATALOG = (
  ("Berkshire+Hathaway", "Berkshire Hathaway", "Berkshire investment team", "1067983", ()),
  ("Pershing+Square+Capital+Management", "Pershing Square", "Bill Ackman", "2026053", ("1336528",)),
  ("Baupost+Group", "Baupost Group", "Seth Klarman", "1061768", ()),
  ("Appaloosa", "Appaloosa", "David Tepper", "1656456", ()),
  ("Third+Point", "Third Point", "Daniel Loeb", "1040273", ()),
  ("Duquesne+Family+Office", "Duquesne Family Office", "Stanley Druckenmiller", "1536411", ()),
  ("Greenlight+Capital", "Greenlight Capital / DME Capital", "David Einhorn", "1489933", ()),
  ("Coatue+Management", "Coatue Management", "Philippe Laffont", "1135730", ()),
  ("Viking+Global+Investors", "Viking Global Investors", "Andreas Halvorsen", "1103804", ()),
  ("Lone+Pine+Capital", "Lone Pine Capital", "Lone Pine investment team", "1061165", ()),
  ("TCI+Fund+Management", "TCI Fund Management", "Chris Hohn", "1647251", ()),
)
_FUNDS = {entry[0]: entry for entry in _CATALOG}
# Pershing's Q2 2026 notice says its holdings now report under the parent.
# Earlier parent filings were a separate portfolio, not this manager's history.
_CURRENT_FROM = {"Pershing+Square+Capital+Management": date(2026, 6, 30)}
_MAX_BYTES = 16 * 1024 * 1024
_REFRESH_HOURS = 6
_SOURCE_LOCKS = {"sec": threading.Lock(), "figi": threading.Lock()}
_SOURCE_LAST = {"sec": 0.0, "figi": 0.0}
_FUND_LOCKS = {key: threading.Lock() for key in _FUNDS}
_SYMBOL_CACHE = {}
_NEGATIVE_UNTIL = {}


class _SourceError(Exception):
  """An unavailable or malformed external source, not a storage failure."""


def _now():
  return datetime.now(timezone.utc)


def _utc(value):
  return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _table(name):
  try:
    return getattr(app_tables, name)
  except AttributeError as error:
    if str(error) != "No such app table: '%s'" % name:
      raise
    return None


def _metadata(entry) -> dict[str, Any]:
  key, name, manager, cik, aliases = entry
  return {"fund_key": key, "name": name, "manager": manager, "cik": cik,
          "source_url": "https://www.sec.gov/edgar/browse/?CIK=%s&owner=exclude" % cik,
          "hedgefollow_url": "https://hedgefollow.com/funds/" + key}


@anvil.tables.in_transaction
def _seed_catalog():
  table = _table("hedge_funds")
  if table is None:
    return
  for entry in _CATALOG:
    data = _metadata(entry)
    row = table.get(fund_key=entry[0])
    if row is None:
      table.add_row(**data, tracked=False)
    else:
      row.update(**data)


def get_catalog(refresh=False):
  """Return trusted starter managers, not a claimed HedgeFollow ranking."""
  if refresh:
    _seed_catalog()
  table = _table("hedge_funds")
  saved = {} if table is None else {row["fund_key"]: row for row in table.search()}
  items = []
  for entry in _CATALOG:
    item = _metadata(entry)
    row = saved.get(entry[0])
    item.update({key: row[key] if row is not None else None
                 for key in ("updated_at", "last_checked_at", "last_error")})
    item["tracked"] = row is not None and row["tracked"] is True
    item["provider"] = "SEC EDGAR"
    items.append(item)
  return items


def _request(url, provider, data=None, timeout=20):
  # Every URL is constructed internally from verified catalog IDs. Never fetch
  # an arbitrary URL supplied by a browser. Pace even concurrent fund jobs.
  gap = 2.5 if provider == "figi" else 0.2
  lock = _SOURCE_LOCKS[provider]
  started = time.monotonic()
  if not lock.acquire(timeout=timeout):
    raise _SourceError("The public data source is busy; retry later.")
  try:
    delay = gap - (time.monotonic() - _SOURCE_LAST[provider])
    remaining = timeout - (time.monotonic() - started)
    if delay >= remaining:
      raise _SourceError("The public data source is busy; retry later.")
    if delay > 0:
      time.sleep(delay)
    headers = {"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"}
    if data is not None:
      headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers)
    try:
      with urlopen(request, timeout=max(0.1, timeout - (time.monotonic() - started))) as response:
        body = response.read(_MAX_BYTES + 1)
    except HTTPError as error:
      label = "OpenFIGI" if provider == "figi" else "SEC EDGAR"
      raise _SourceError("%s returned HTTP %d; retry later." % (label, error.code)) from error
    except (URLError, TimeoutError, ConnectionError) as error:
      label = "OpenFIGI" if provider == "figi" else "SEC EDGAR"
      raise _SourceError("%s could not be reached; retry later." % label) from error
  finally:
    _SOURCE_LAST[provider] = time.monotonic()
    lock.release()
  if len(body) > _MAX_BYTES:
    raise _SourceError("The source filing exceeds this importer’s size limit.")
  return body


def _json_request(url, provider="sec", data=None, timeout=20):
  try:
    return json.loads(_request(url, provider, data, timeout=timeout))
  except (json.JSONDecodeError, UnicodeDecodeError) as error:
    raise _SourceError("The provider returned an unreadable JSON document.") from error


def _filing_records(document):
  if not isinstance(document, dict):
    raise _SourceError("SEC returned an invalid submissions document.")
  records = document.get("filings", {}).get("recent", document)
  fields = ("form", "reportDate", "filingDate", "accessionNumber")
  if not isinstance(records, dict) or any(not isinstance(records.get(key), list) for key in fields):
    raise _SourceError("SEC submissions metadata did not contain filing arrays.")
  if len({len(records[key]) for key in fields}) != 1:
    raise _SourceError("SEC submissions filing arrays were inconsistent.")
  result = []
  for index, form in enumerate(records["form"]):
    if form not in ("13F-HR", "13F-HR/A"):
      continue
    period = records["reportDate"][index]
    filed = records["filingDate"][index]
    accession = records["accessionNumber"][index]
    if not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
      raise _SourceError("SEC returned an invalid filing accession.")
    try:
      period_date, filed_date = date.fromisoformat(period), date.fromisoformat(filed)
    except (TypeError, ValueError) as error:
      raise _SourceError("SEC returned an invalid report or filing date.") from error
    if period_date.month not in (3, 6, 9, 12) or period_date.day != monthrange(period_date.year, period_date.month)[1]:
      continue
    result.append({"form": form, "period_end": period_date, "filed_at": filed_date,
                   "accession": accession})
  return result


def _list_filings(entry, quarters):
  combined = []
  # The Pershing notice explicitly identifies its public parent as the current
  # reporting manager. Older predecessor reports fill only absent quarters.
  for priority, cik in enumerate((entry[3],) + entry[4]):
    document = _json_request("https://data.sec.gov/submissions/CIK%s.json" % cik.zfill(10))
    if not isinstance(document, dict):
      raise _SourceError("SEC returned an invalid submissions document.")
    records = _filing_records(document)
    files = document.get("filings", {}).get("files", [])
    for older in files[:2]:
      if len({record["period_end"] for record in records if record["form"] == "13F-HR"}) >= quarters:
        break
      name = older.get("name")
      if not isinstance(name, str) or not re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
        continue
      records.extend(_filing_records(_json_request("https://data.sec.gov/submissions/" + name)))
    transition = _CURRENT_FROM.get(entry[0])
    if transition is not None:
      records = [record for record in records
                 if (record["period_end"] >= transition) == (priority == 0)]
    for record in records:
      record.update(cik=cik, priority=priority, filer_name=document.get("name", entry[1]))
      combined.append(record)
  periods = sorted({record["period_end"] for record in combined if record["form"] == "13F-HR"}, reverse=True)[:quarters]
  selected = []
  for period in periods:
    matches = [record for record in combined if record["period_end"] == period]
    priority = min(record["priority"] for record in matches if record["form"] == "13F-HR")
    selected.extend(record for record in matches if record["priority"] == priority)
  # A complete amendment chain is required; never truncate it to look complete.
  if len(selected) > 40:
    raise _SourceError("This period has too many amendments for one update. Choose a shorter history.")
  return sorted(selected, key=lambda item: (item["period_end"], item["filed_at"], item["accession"]))


def _local(tag):
  return tag.rsplit("}", 1)[-1].lower()


def _text(root, name, default=""):
  return next((node.text.strip() for node in root.iter()
               if _local(node.tag) == name.lower() and node.text), default)


def _number(value):
  try:
    result = float(value.replace(",", ""))
  except (ValueError, AttributeError) as error:
    raise _SourceError("The information table contained an invalid numeric amount.") from error
  if not math.isfinite(result) or result < 0 or result > 1e18:
    raise _SourceError("The information table contained an invalid numeric amount.")
  return result


def _parse_filing(body, record):
  try:
    content = body.decode("utf-8-sig") if isinstance(body, bytes) else body
  except UnicodeDecodeError as error:
    raise _SourceError("The SEC filing was not readable XML.") from error
  roots = []
  for block in re.findall(r"<XML>\s*(.*?)\s*</XML>", content, re.I | re.S):
    if "<!DOCTYPE" in block.upper() or "<!ENTITY" in block.upper():
      raise _SourceError("The filing contains unsupported XML declarations.")
    try:
      roots.append(ET.fromstring(block))
    except ET.ParseError as error:
      raise _SourceError("The SEC filing contained malformed XML.") from error
  primary = next((root for root in roots if _local(root.tag) == "edgarsubmission"), None)
  information = [node for root in roots for node in root.iter() if _local(node.tag) == "infotable"]
  if primary is None:
    raise _SourceError("No 13F cover XML was present in the SEC filing.")
  report_period = _text(primary, "reportCalendarOrQuarter") or _text(primary, "periodOfReport")
  try:
    parsed_period = datetime.strptime(report_period, "%m-%d-%Y").date()
  except ValueError as error:
    raise _SourceError("The 13F cover report date was unreadable.") from error
  if parsed_period != record["period_end"]:
    raise _SourceError("The 13F cover date disagreed with the SEC filing metadata.")
  total_entries = _number(_text(primary, "tableEntryTotal", "0"))
  if len(information) != total_entries:
    raise _SourceError("The 13F information table was incomplete.")
  multiplier = 1000 if record["filed_at"] < date(2023, 1, 3) else 1
  holdings, reported_value, excluded_options, excluded_other = {}, 0.0, 0, 0
  for node in information:
    value = _number(_text(node, "value")) * multiplier
    shares = _number(_text(node, "sshPrnamt"))
    reported_value += value
    if _text(node, "putCall"):
      excluded_options += 1
      continue
    title = _text(node, "titleOfClass")
    if (_text(node, "sshPrnamtType").upper() != "SH"
        or re.search(r"\b(WT|WTS|WARRANT|WARRANTS|NOTE|NOTES|BOND|BONDS|UNIT|UNITS|PFD|PREFERRED)\b", title.upper())):
      excluded_other += 1
      continue
    cusip = _text(node, "cusip").upper()
    if not re.fullmatch(r"[A-Z0-9*@#]{9}", cusip):
      raise _SourceError("The information table contained an invalid CUSIP.")
    item = holdings.setdefault(cusip, {"cusip": cusip, "symbol": None,
      "name": _text(node, "nameOfIssuer"), "class_title": title, "shares": 0.0, "value": 0.0})
    item["shares"] += shares
    item["value"] += value
  coverage = {"provider": "SEC EDGAR", "reported_rows": len(information),
    "reported_value": reported_value, "long_equity_positions": len(holdings),
    "excluded_option_rows": excluded_options, "excluded_other_rows": excluded_other,
    "confidential_omitted": _text(primary, "isConfidentialOmitted").lower() in ("true", "y"),
    "is_amendment": record["form"] == "13F-HR/A",
    "amendment_type": _text(primary, "amendmentType").upper(), "filer_name": record.get("filer_name")}
  if coverage["is_amendment"] and coverage["amendment_type"] not in ("RESTATEMENT", "NEW HOLDINGS"):
    raise _SourceError("The 13F amendment type is not supported.")
  return list(holdings.values()), coverage


def _symbol_from_mapping(result):
  if not isinstance(result, dict) or not isinstance(result.get("data", []), list):
    raise _SourceError("OpenFIGI returned an invalid mapping result.")
  symbols = set()
  for item in result.get("data", []):
    if not isinstance(item, dict):
      raise _SourceError("OpenFIGI returned an invalid instrument.")
    if item.get("marketSector") != "Equity" or item.get("exchCode") != "US":
      continue
    if item.get("securityType2") not in ("Common Stock", "Depositary Receipt", "ETF", "Mutual Fund", "REIT"):
      continue
    ticker = item.get("ticker")
    if isinstance(ticker, str):
      ticker = ticker.strip().upper().replace("/", "-").replace(".", "-")
      if re.fullmatch(r"[A-Z][A-Z0-9-]{0,14}", ticker):
        symbols.add(ticker)
  return next(iter(symbols)) if len(symbols) == 1 else None


def _resolve_symbols(holdings):
  now = _now()
  for cusip in list(_SYMBOL_CACHE):
    if _SYMBOL_CACHE[cusip] is None and _NEGATIVE_UNTIL.get(cusip, now) <= now:
      del _SYMBOL_CACHE[cusip]
      _NEGATIVE_UNTIL.pop(cusip, None)
  table = _table("hedge_fund_snapshots")
  if table is not None:
    for row in table.search():
      for item in row["holdings"] or []:
        if item.get("symbol"):
          _SYMBOL_CACHE[item["cusip"]] = item["symbol"]
  needed = sorted({item["cusip"] for item in holdings if item["cusip"] not in _SYMBOL_CACHE})
  # Conservative public OpenFIGI limit: five jobs/request, 25 requests/minute.
  for offset in range(0, len(needed), 5):
    batch = needed[offset:offset + 5]
    payload = [{"idType": "ID_CUSIP", "idValue": cusip, "exchCode": "US",
                "includeUnlistedEquities": True} for cusip in batch]
    result = _json_request("https://api.openfigi.com/v3/mapping", "figi", json.dumps(payload).encode())
    if not isinstance(result, list) or len(result) != len(batch):
      raise _SourceError("OpenFIGI returned an incomplete mapping response.")
    for cusip, mapping in zip(batch, result):
      _SYMBOL_CACHE[cusip] = _symbol_from_mapping(mapping)
      if _SYMBOL_CACHE[cusip] is None:
        _NEGATIVE_UNTIL[cusip] = _now() + timedelta(hours=_REFRESH_HOURS)
  for item in holdings:
    item["symbol"] = _SYMBOL_CACHE.get(item["cusip"])


def _coverage(holdings, coverage):
  result = dict(coverage)
  total = sum(item["value"] for item in holdings)
  mapped = [item for item in holdings if item.get("symbol")]
  mapped_value = sum(item["value"] for item in mapped)
  result.update(long_equity_positions=len(holdings), long_equity_value=total,
                mapped_positions=len(mapped), mapped_value=mapped_value,
                unmapped_positions=len(holdings) - len(mapped),
                mapped_value_pct=100 * mapped_value / total if total > 0 else 0)
  return result


@anvil.tables.in_transaction
def _store_snapshot(fund_key, record, holdings, coverage):
  table = app_tables.hedge_fund_snapshots
  existing = table.get(fund_key=fund_key, accession=record["accession"])
  if existing is not None:
    return False
  period = record["period_end"]
  source = "https://www.sec.gov/Archives/edgar/data/%s/%s/%s-index.html" % (
    record["cik"], record["accession"].replace("-", ""), record["accession"])
  table.add_row(fund_key=fund_key, quarter="%d Q%d" % (period.year, (period.month + 2) // 3),
                period_end=period, filed_at=record["filed_at"], accession=record["accession"],
                source_url=source, fetched_at=_now(), holdings=holdings, coverage=coverage)
  return True


def get_snapshots(fund_key):
  """Chronological effective quarters, merging additional holdings amendments."""
  if not isinstance(fund_key, str) or fund_key not in _FUNDS:
    return []
  table = _table("hedge_fund_snapshots")
  if table is None:
    return []
  records = [{key: row[key] for key in ("fund_key", "quarter", "period_end", "filed_at",
    "accession", "source_url", "fetched_at", "holdings", "coverage")}
    for row in table.search(fund_key=fund_key)]
  records.sort(key=lambda item: (item["period_end"], item["filed_at"], item["accession"]))
  quarters = {}
  for record in records:
    period = record["period_end"]
    coverage = record["coverage"] or {}
    additional = coverage.get("is_amendment") and coverage.get("amendment_type") == "NEW HOLDINGS"
    if additional and period not in quarters:
      # An amendment alone is not a complete portfolio. Retain it in storage
      # while withholding an incomplete effective quarter from the simulator.
      continue
    if not additional:
      quarters[period] = dict(record)
      quarters[period]["holdings"] = [dict(item) for item in record["holdings"]]
      quarters[period]["source_filings"] = []
    else:
      current = quarters[period]
      positions = {item["cusip"]: item for item in current["holdings"]}
      for item in record["holdings"]:
        if item["cusip"] in positions:
          # Additional entries can report shared discretion for the same CUSIP.
          positions[item["cusip"]]["shares"] += item["shares"]
          positions[item["cusip"]]["value"] += item["value"]
        else:
          positions[item["cusip"]] = dict(item)
      current["holdings"] = list(positions.values())
      previous_coverage = dict(current["coverage"])
      for name in ("reported_rows", "reported_value", "excluded_option_rows", "excluded_other_rows"):
        previous_coverage[name] = previous_coverage.get(name, 0) + coverage.get(name, 0)
      current["coverage"] = previous_coverage
      current["filed_at"] = record["filed_at"]
      current["fetched_at"] = max(current["fetched_at"], record["fetched_at"])
    current = quarters[period]
    current["source_filings"].append({name: record[name] for name in ("accession", "source_url", "filed_at")})
    current["coverage"] = _coverage(current["holdings"], current["coverage"])
    current["holdings"].sort(key=lambda item: (-item["value"], item["cusip"]))
  return [quarters[period] for period in sorted(quarters)]


def refresh_fund(fund_key, max_filings=5, force=False):
  """Fetch bounded quarters and amendments; preserve earlier stored history."""
  if not isinstance(fund_key, str) or fund_key not in _FUNDS:
    return {"ok": False, "message": "Choose a supported fund."}
  if isinstance(max_filings, bool) or not isinstance(max_filings, int) or not 1 <= max_filings <= 13:
    return {"ok": False, "message": "Choose between one and thirteen quarterly reports."}
  if not isinstance(force, bool):
    return {"ok": False, "message": "Choose a valid update mode."}
  if _table("hedge_funds") is None or _table("hedge_fund_snapshots") is None:
    return {"ok": False, "message": "Apply the Hedge Funds and Hedge Fund Snapshots Data Table schema, then update filings."}
  with _FUND_LOCKS[fund_key]:
    _seed_catalog()
    row: Any = app_tables.hedge_funds.get(fund_key=fund_key)
    if row is None:
      raise RuntimeError("The fund catalog was removed during an update.")
    existing = get_snapshots(fund_key)
    checked = row["last_checked_at"]
    if (not force and checked is not None and _now() - _utc(checked) < timedelta(hours=_REFRESH_HOURS)
        and len(existing) >= max_filings):
      return {"ok": True, "cached": True, "added": 0, "quarters": len(existing),
              "message": "Using stored filings checked within the last six hours."}
    row.update(last_checked_at=_now(), tracked=True)
    added = 0
    try:
      records = _list_filings(_FUNDS[fund_key], max_filings)
      if not records:
        raise _SourceError("No public quarterly holdings reports were available for this manager.")
      for record in records:
        if app_tables.hedge_fund_snapshots.get(fund_key=fund_key, accession=record["accession"]) is not None:
          continue
        url = "https://www.sec.gov/Archives/edgar/data/%s/%s/%s.txt" % (
          record["cik"], record["accession"].replace("-", ""), record["accession"])
        holdings, coverage = _parse_filing(_request(url, "sec"), record)
        _resolve_symbols(holdings)
        added += int(_store_snapshot(fund_key, record, holdings, _coverage(holdings, coverage)))
    except _SourceError as error:
      row.update(last_error=str(error))
      return {"ok": False, "message": str(error), "added": added,
              "quarters": len(get_snapshots(fund_key)), "provider": "SEC EDGAR"}
    snapshots = get_snapshots(fund_key)
    row.update(updated_at=_now(), last_error=None)
    return {"ok": True, "cached": False, "added": added, "quarters": len(snapshots),
            "latest_quarter": snapshots[-1]["quarter"] if snapshots else None,
            "provider": "SEC EDGAR", "message": "Quarterly filings are up to date."}

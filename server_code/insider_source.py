"""Bounded, read-only SEC Form 4 source for insider activity notifications.

Only Table I purchase/sale codes P/S are alerts. Awards, exercises, gifts,
derivatives and amendments are excluded. These are issuer disclosures: the
security title is retained because an issuer may have multiple share classes.

SEC references:
https://www.sec.gov/search-filings/edgar-application-programming-interfaces
https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
https://www.sec.gov/files/form4.pdf (General Instruction 8)
"""

from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import json
import math
import re
import threading
import time
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from . import hedge_fund_data


class InsiderSourceError(Exception):
    """Expected unavailable, incomplete or invalid SEC source data."""


_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_CACHE_MAX = 256
_CACHE_BYTES_MAX = 16 * 1024 * 1024
_MAX_FILINGS = 40
_MAX_FILINGS_PER_DAY = 160
_MAX_ARCHIVES = 2
_MAX_LOOKBACK_DAYS = 366
_EDGAR_TIMEZONE = ZoneInfo("America/New_York")


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _normal_symbol(value):
    if not isinstance(value, str):
        raise InsiderSourceError("Enter a US-listed ticker symbol.")
    value = value.strip().upper().replace(".", "-")
    if not re.fullmatch(r"[A-Z][A-Z0-9-]{0,14}", value):
        raise InsiderSourceError("Enter a US-listed ticker symbol, such as AAPL or BRK-B.")
    return value


def _read(url, ttl):
    # Share the app's declared SEC contact, timeout, byte limit and rate lock.
    # hedge_fund_data spaces SEC requests by 0.2 s across both importers.
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
        if cached is not None and time.monotonic() - cached[0] < ttl:
            _CACHE.move_to_end(url)
            return cached[1], cached[2]
    started_at = datetime.now(timezone.utc)
    try:
        body = hedge_fund_data._request(url, "sec")
    except hedge_fund_data._SourceError as error:
        raise InsiderSourceError(str(error)) from error
    with _CACHE_LOCK:
        _CACHE[url] = (time.monotonic(), body, started_at)
        _CACHE.move_to_end(url)
        while len(_CACHE) > _CACHE_MAX or sum(len(item[1]) for item in _CACHE.values()) > _CACHE_BYTES_MAX:
            _CACHE.popitem(last=False)
    return body, started_at


def _json(url, ttl):
    body, checked_at = _read(url, ttl)
    try:
        return json.loads(body), checked_at
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InsiderSourceError("SEC returned an unreadable JSON document; retry later.") from error


def resolve_insider_symbol(symbol):
    """Resolve an SEC ticker to issuer CIK; suitable for validating a new rule."""
    symbol = _normal_symbol(symbol)
    document, _ = _json("https://www.sec.gov/files/company_tickers.json", 21600)
    if not isinstance(document, dict):
        raise InsiderSourceError("SEC returned an invalid ticker directory; retry later.")
    for entry in document.values():
        if not isinstance(entry, dict):
            raise InsiderSourceError("SEC returned an invalid ticker directory; retry later.")
        ticker = entry.get("ticker")
        if isinstance(ticker, str) and ticker.upper().replace(".", "-") == symbol:
            cik = str(entry.get("cik_str", ""))
            if not re.fullmatch(r"\d{1,10}", cik) or int(cik) == 0:
                raise InsiderSourceError("SEC returned an invalid issuer identifier.")
            return {"symbol": symbol, "cik": str(int(cik)), "name": entry.get("title") or symbol}
    raise InsiderSourceError("SEC has no issuer mapping for %s. Choose a supported US-listed ticker." % symbol)


def _records(document):
    if not isinstance(document, dict):
        raise InsiderSourceError("SEC returned an invalid submissions document.")
    filings = document.get("filings")
    if filings is not None and not isinstance(filings, dict):
        raise InsiderSourceError("SEC returned invalid filing metadata.")
    rows = filings.get("recent", {}) if filings is not None else document
    fields = ("form", "filingDate", "acceptanceDateTime", "accessionNumber", "primaryDocument")
    if not isinstance(rows, dict) or any(not isinstance(rows.get(key), list) for key in fields):
        raise InsiderSourceError("SEC submissions metadata did not contain filing arrays.")
    if len({len(rows[key]) for key in fields}) != 1:
        raise InsiderSourceError("SEC submissions filing arrays were inconsistent.")
    result = []
    for index, form in enumerate(rows["form"]):
        if form not in ("4", "4/A"):
            continue
        accession = rows["accessionNumber"][index]
        primary = rows["primaryDocument"][index]
        if not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise InsiderSourceError("SEC returned an invalid filing accession.")
        if not isinstance(primary, str) or not re.fullmatch(r"(?:xsl[A-Za-z0-9]+/)?[A-Za-z0-9_.-]+\.xml", primary):
            raise InsiderSourceError("A Form 4 did not reference a supported XML document.")
        try:
            filed_at = datetime.fromisoformat(rows["acceptanceDateTime"][index].replace("Z", "+00:00"))
            filing_date = date.fromisoformat(rows["filingDate"][index])
        except (TypeError, ValueError, AttributeError) as error:
            raise InsiderSourceError("SEC returned an invalid filing date.") from error
        if filed_at.tzinfo is None:
            raise InsiderSourceError("SEC returned a filing timestamp without a timezone.")
        result.append({"form": form, "accession": accession, "filed_at": _utc(filed_at),
                       "filing_date": filing_date,
                       "primary": primary.rsplit("/", 1)[-1]})
    return result


def _accepted_at(record, issuer):
    # The submissions API's acceptanceDateTime has historically mixed local
    # and UTC values despite its Z suffix. The immutable filing's SEC header
    # records its actual acceptance clock in Eastern time. Do not apply a
    # fixed UTC offset: EDGAR operates across daylight-saving transitions.
    accession = record["accession"]
    url = "https://www.sec.gov/Archives/edgar/data/%s/%s/%s-index-headers.html" % (
        issuer["cik"], accession.replace("-", ""), accession)
    body, _ = _read(url, 86400)
    try:
        content = html.unescape(body.decode("utf-8-sig") if isinstance(body, bytes) else body)
    except UnicodeDecodeError as error:
        raise InsiderSourceError("A Form 4 filing header was not readable.") from error
    stamp = re.search(r"<ACCEPTANCE-DATETIME>\s*(\d{14})(?!\d)", content)
    number = re.search(r"ACCESSION NUMBER:\s*(\d{10}-\d{2}-\d{6})", content)
    if stamp is None or number is None or number.group(1) != accession:
        raise InsiderSourceError("A Form 4 filing header did not identify its acceptance time.")
    try:
        accepted = datetime.strptime(stamp.group(1), "%Y%m%d%H%M%S").replace(tzinfo=_EDGAR_TIMEZONE)
    except ValueError as error:
        raise InsiderSourceError("A Form 4 filing header has an invalid acceptance time.") from error
    if accepted.date() > record["filing_date"]:
        raise InsiderSourceError("A Form 4 acceptance time disagrees with its filing date.")
    return _utc(accepted)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _child(node, name):
    return next((child for child in node if _local(child.tag) == name), None)


def _text(node, path):
    for name in path.split("/"):
        node = _child(node, name)
        if node is None:
            return ""
    return (node.text or "").strip()


def _number(text, optional=False):
    if not text and optional:
        return None
    try:
        value = float(text.replace(",", ""))
    except (ValueError, AttributeError) as error:
        raise InsiderSourceError("A Form 4 contains an invalid transaction amount.") from error
    if not math.isfinite(value) or value < 0 or value > 1e18:
        raise InsiderSourceError("A Form 4 contains an invalid transaction amount.")
    return value


def _parse_filing(body, record, issuer):
    """Parse one immutable filing. IDs are stable across polls and ticker aliases."""
    try:
        content = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    except UnicodeDecodeError as error:
        raise InsiderSourceError("A Form 4 was not readable XML.") from error
    if "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper():
        raise InsiderSourceError("A Form 4 contains unsupported XML declarations.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise InsiderSourceError("A Form 4 contains malformed XML.") from error
    if _local(root.tag) != "ownershipDocument":
        raise InsiderSourceError("The SEC document is not an ownership filing.")
    cik = _text(root, "issuer/issuerCik")
    if not cik.isdigit() or int(cik) != int(issuer["cik"]):
        raise InsiderSourceError("The Form 4 issuer does not match the selected security.")
    form = _text(root, "documentType")
    if form not in ("4", "4/A") or form != record["form"]:
        raise InsiderSourceError("The Form 4 type disagrees with the SEC filing index.")
    if form == "4/A":
        # Corrections can repeat or replace an earlier trade. They need amendment
        # reconciliation, so never turn them into another purchase/sale alert.
        return []
    names = [_text(owner, "reportingOwnerId/rptOwnerName") for owner in root
             if _local(owner.tag) == "reportingOwner"]
    names = list(dict.fromkeys(name for name in names if name))
    if not names:
        raise InsiderSourceError("The Form 4 did not identify its reporting insider.")
    table = _child(root, "nonDerivativeTable")
    if table is None:
        return []
    events = []
    for index, row in enumerate(table):
        if _local(row.tag) != "nonDerivativeTransaction":
            continue
        code = _text(row, "transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        direction = _text(row, "transactionAmounts/transactionAcquiredDisposedCode/value")
        if direction != ("A" if code == "P" else "D"):
            raise InsiderSourceError("A Form 4 has inconsistent transaction codes.")
        try:
            transacted = date.fromisoformat(_text(row, "transactionDate/value"))
        except ValueError as error:
            raise InsiderSourceError("A Form 4 contains an invalid transaction date.") from error
        shares = _number(_text(row, "transactionAmounts/transactionShares/value"))
        price = _number(_text(row, "transactionAmounts/transactionPricePerShare/value"), optional=True)
        title = _text(row, "securityTitle/value")
        if not title:
            raise InsiderSourceError("A Form 4 did not identify the security traded.")
        accession = record["accession"]
        identity = "%s:nonDerivative:%s" % (accession, index)
        events.append({
            "id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "symbol": issuer["symbol"], "insider_name": "; ".join(names),
            "action": "buy" if code == "P" else "sell", "transaction_date": transacted,
            "filed_at": record["filed_at"], "shares": shares, "price": price,
            "value": shares * price if price is not None else None,
            "filing_url": "https://www.sec.gov/Archives/edgar/data/%s/%s/%s-index.html" % (
                issuer["cik"], accession.replace("-", ""), accession),
            "security_title": title, "is_amendment": False,
        })
    return events


def fetch_insider_transactions(symbol, since_datetime):
    """Return new P/S disclosures and a safe inclusive polling cursor.

    The caller must deduplicate by event id, persist checked_at, and overlap
    polls to account for SEC dissemination delays. Batches close complete
    Eastern filing days, targeting 40 filings (up to 160 for one dense day),
    so identical timestamps cannot skip filings or trap a cursor in a loop.
    checked_at stops at the last complete day while a backlog remains.
    When has_more is true, continue from next_since without applying overlap
    until the backlog is exhausted, then resume ordinary overlapping polls.
    Amendments are counted in amendments_skipped, never emitted as new trades.
    No messages are sent and no app data is written by this source module.
    """
    if not isinstance(since_datetime, datetime):
        raise InsiderSourceError("The notification start time must be a datetime.")
    since = _utc(since_datetime)
    now = datetime.now(timezone.utc)
    if since < now - timedelta(days=_MAX_LOOKBACK_DAYS):
        raise InsiderSourceError("Insider alerts support up to 366 days of recent SEC filings.")
    if since > now:
        raise InsiderSourceError("The notification start time cannot be in the future.")
    issuer = resolve_insider_symbol(symbol)
    document, checked_at = _json("https://data.sec.gov/submissions/CIK%s.json" % issuer["cik"].zfill(10), 60)
    records = _records(document)
    files = document.get("filings", {}).get("files", [])
    if not isinstance(files, list):
        raise InsiderSourceError("SEC returned invalid filing archive metadata.")
    needed = []
    first_day = since.astimezone(_EDGAR_TIMEZONE).date()
    for archive in files:
        if not isinstance(archive, dict):
            raise InsiderSourceError("SEC returned invalid filing archive metadata.")
        try:
            end = date.fromisoformat(archive.get("filingTo", ""))
        except (TypeError, ValueError) as error:
            raise InsiderSourceError("SEC returned an invalid filing archive date.") from error
        if end >= first_day:
            name = archive.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
                raise InsiderSourceError("SEC returned an invalid filing archive name.")
            needed.append(name)
    if len(needed) > _MAX_ARCHIVES:
        raise InsiderSourceError("This issuer has too many historical filings; choose a more recent alert start time.")
    for name in needed:
        archive, _ = _json("https://data.sec.gov/submissions/" + name, 3600)
        records.extend(_records(archive))
    last_day = checked_at.astimezone(_EDGAR_TIMEZONE).date()
    records = {row["accession"]: row for row in records if first_day <= row["filing_date"] <= last_day}
    days = {}
    for record in records.values():
        days.setdefault(record["filing_date"], []).append(record)
    selected = []
    for filing_day in sorted(days):
        group = sorted(days[filing_day], key=lambda row: row["accession"])
        if selected and len(selected) + len(group) > _MAX_FILINGS:
            break
        if len(group) > _MAX_FILINGS_PER_DAY:
            raise InsiderSourceError("This issuer has over 160 ownership filings in one day; alert processing needs review.")
        selected.extend(group)
    has_more = len(selected) < len(records)
    if has_more:
        next_day = selected[-1]["filing_date"] + timedelta(days=1)
        checked_at = _utc(datetime.combine(next_day, datetime.min.time(), _EDGAR_TIMEZONE)) - timedelta(microseconds=1)
    events = []
    amendments = 0
    for record in selected:
        record["filed_at"] = _accepted_at(record, issuer)
        if not since <= record["filed_at"] <= checked_at:
            continue
        if record["form"] == "4/A":
            amendments += 1
            continue
        url = "https://www.sec.gov/Archives/edgar/data/%s/%s/%s" % (
            issuer["cik"], record["accession"].replace("-", ""), record["primary"])
        body, _ = _read(url, 86400)
        events.extend(_parse_filing(body, record, issuer))
    checked_at = max(since, checked_at)
    return {"events": sorted(events, key=lambda event: (event["filed_at"], event["id"])),
            "checked_at": checked_at, "amendments_skipped": amendments,
            "has_more": has_more,
            "next_since": checked_at + timedelta(microseconds=1) if has_more else None}

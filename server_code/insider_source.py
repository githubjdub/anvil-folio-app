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
import json
import math
import re
import threading
import time
from xml.etree import ElementTree as ET

from . import hedge_fund_data


class InsiderSourceError(Exception):
    """Expected unavailable, incomplete or invalid SEC source data."""


_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_CACHE_MAX = 256
_CACHE_BYTES_MAX = 16 * 1024 * 1024
_MAX_FILINGS = 40
_MAX_ARCHIVES = 2
_MAX_LOOKBACK_DAYS = 366


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
            date.fromisoformat(rows["filingDate"][index])
        except (TypeError, ValueError, AttributeError) as error:
            raise InsiderSourceError("SEC returned an invalid filing date.") from error
        if filed_at.tzinfo is None:
            raise InsiderSourceError("SEC returned a filing timestamp without a timezone.")
        result.append({"form": form, "accession": accession, "filed_at": _utc(filed_at),
                       "primary": primary.rsplit("/", 1)[-1]})
    return result


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
    polls to account for SEC dissemination delays. Up to 40 filings are read
    per call; checked_at stops at the batch boundary while a backlog remains.
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
    for archive in files:
        if not isinstance(archive, dict):
            raise InsiderSourceError("SEC returned invalid filing archive metadata.")
        try:
            end = date.fromisoformat(archive.get("filingTo", ""))
        except (TypeError, ValueError) as error:
            raise InsiderSourceError("SEC returned an invalid filing archive date.") from error
        if end >= since.date():
            name = archive.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
                raise InsiderSourceError("SEC returned an invalid filing archive name.")
            needed.append(name)
    if len(needed) > _MAX_ARCHIVES:
        raise InsiderSourceError("This issuer has too many historical filings; choose a more recent alert start time.")
    for name in needed:
        archive, _ = _json("https://data.sec.gov/submissions/" + name, 3600)
        records.extend(_records(archive))
    records = {row["accession"]: row for row in records if since <= row["filed_at"] <= checked_at}
    ordered = sorted(records.values(), key=lambda row: (row["filed_at"], row["accession"]))
    selected = ordered[:_MAX_FILINGS]
    if len(ordered) > _MAX_FILINGS:
        boundary = selected[-1]["filed_at"]
        # Include all filings at a boundary timestamp or refuse the incomplete
        # batch. A timestamp-only cursor must never silently skip a sibling.
        same_time = [row for row in ordered[_MAX_FILINGS:] if row["filed_at"] == boundary]
        if same_time:
            selected = [row for row in selected if row["filed_at"] < boundary]
            if not selected:
                raise InsiderSourceError("SEC has too many filings at the same timestamp for this alert batch.")
        checked_at = selected[-1]["filed_at"]
    events = []
    amendments = 0
    for record in selected:
        if record["form"] == "4/A":
            amendments += 1
            continue
        url = "https://www.sec.gov/Archives/edgar/data/%s/%s/%s" % (
            issuer["cik"], record["accession"].replace("-", ""), record["primary"])
        body, _ = _read(url, 86400)
        events.extend(_parse_filing(body, record, issuer))
    has_more = len(selected) < len(ordered)
    checked_at = max(since, checked_at)
    return {"events": events, "checked_at": checked_at, "amendments_skipped": amendments,
            "has_more": has_more,
            "next_since": checked_at + timedelta(microseconds=1) if has_more else None}

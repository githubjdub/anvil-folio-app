"""Parse browser-read brokerage text into reviewable drafts; never persist it."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
import math
import re
from statistics import median

import anvil.server


_MAX_BLOCKS = 5000
_MAX_TEXT_LENGTH = 300
_MAX_COORDINATE = 30000
_MAX_ROWS = 100
_MAX_DOCUMENT_PAGES = 20
_SNAPSHOT_WARNING = (
  "Holdings images show current quantities, not complete trade history. Choose a "
  "snapshot date that matches those quantities; past dates may give incorrect "
  "returns after stock splits. Missing purchase dates are left blank."
)
_REVIEW_WARNING = "Check every ticker, quantity and cost against your image before importing."
_CASH = re.compile(
  r"^\s*(?:total|subtotal|grand total|balance)\b|\b(?:cash|buying power|account total|total value|portfolio total|settled funds|"
  r"cash equivalents|money market|available balance|pending activity)\b", re.I
)
_NON_USD = re.compile(r"(?:[€£¥]|\b(?:EUR|GBP|CAD|AUD|JPY|CHF|HKD|CNY|INR)\b)", re.I)
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,6}(?:[.-][A-Z0-9]{1,3})?$")
_NOT_SYMBOLS = {
  "BUY", "SELL", "TOTAL", "CASH", "USD", "VALUE", "PRICE", "SHARES", "QTY",
  "SYMBOL", "TICKER", "DATE", "COST", "STOCK", "STOCKS", "EQUITY", "EQUITIES",
  "ETFS", "ETF", "OPTIONS", "BONDS", "BOND", "GAIN", "LOSS", "MARKET", "DAY",
  "TODAY", "ACCOUNT", "SUMMARY", "POSITIONS", "HOLDINGS", "QUANTITY", "ACTION",
  "AVG", "AVERAGE", "CURRENT", "RETURN", "RETURNS", "NASDAQ", "NYSE", "AMEX", "ARCA",
}
# Statement sections and footers must not inherit a preceding holdings table's
# columns. These filters apply only to documents, preserving image imports.
_PDF_PAGE_FOOTER = re.compile(
  r"^(?:(?:statement\s+)?page\s*:?\s*\d+\b|\d+\s*(?:of|/)\s*\d+\s*$)", re.I
)
_PDF_BOILERPLATE = re.compile(
  r"^(?:statement\s+(?:period|date)|account\s+(?:number|no\.?\b|#)|"
  r"member\s+(?:finra|sipc)|(?:finra|sipc|fdic)\b|"
  r"(?:for\s+)?(?:questions|assistance)\b|contact\s+(?:us|information)\b)", re.I
)
_PDF_OTHER_SECTION = re.compile(
  r"^(?:(?:account|portfolio)\s+(?:summary|overview)|"
  r"cash\s+(?:activity|balances?|transactions)|"
  r"(?:transaction|activity)\s+(?:summary|history|details)|"
  r"(?:income|dividends?|interest|fees?|deposits?|withdrawals?|transfers?)"
  r"(?:\s+(?:summary|activity|history|details))?|"
  r"(?:realized|unrealized)\s+gain(?:s|/loss)?\s+(?:summary|details)|"
  r"tax\s+(?:information|summary)|disclosures?|"
  r"important\s+(?:information|disclosures)|terms\s+and\s+conditions)\s*:?\s*$", re.I
)
_PDF_POSITION_SECTION = re.compile(
  r"^(?:(?:(?:your|account|portfolio|investment)\s+)?(?:holdings|positions)|"
  r"(?:holdings|positions)\s+(?:detail|details)|equities|stocks|etfs|securities)\s*:?\s*$", re.I
)
_ACCOUNT_CURRENCY = re.compile(
  r"\b(?:account|base|reporting)\s+currency\s*(?::|=|is)?\s*"
  r"(USD|EUR|GBP|CAD|AUD|JPY|CHF|HKD|CNY|INR)\b", re.I
)
_EXPLICIT_USD = re.compile(r"\bUSD\b|\bUS\$", re.I)


def _account_currency(text):
  """Read an explicit account caption; generic currency mentions are ambiguous."""
  match = _ACCOUNT_CURRENCY.search(text.replace(" · ", " "))
  return match.group(1).upper() if match else None


def _apply_account_currency(row, currency, text):
  if currency and currency != "USD" and not _EXPLICIT_USD.search(text):
    row["include"] = False
    row["trade_price"] = ""
    row["notes"] += (
      " Account currency %s detected; this analyzer uses USD. Review the security and currency."
      % currency
    )
  return row


def _failure(message):
  return {"ok": False, "message": message, "rows": [], "warnings": []}


def _number(text):
  """Only accept an unambiguous complete number, not the digits inside prose."""
  text = str(text).strip().replace("\u2212", "-")
  text = re.sub(r"^(?:USD\s*|US\$\s*|\$\s*)", "", text, flags=re.I)
  text = re.sub(r"\s*(?:USD|shares?|units?)$", "", text, flags=re.I)
  negative = text.startswith("(") and text.endswith(")")
  if negative:
    text = text[1:-1]
  # Decimal comma and shorthand (1.5K) are intentionally left for review.
  if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
    return ""
  value = text.replace(",", "")
  if len(value) > 24:
    return ""
  try:
    number = Decimal(value)
  except InvalidOperation:
    return ""
  if not number.is_finite():
    return ""
  return "-" + value.lstrip("+") if negative else value.lstrip("+")


def _symbol(text, labelled=False):
  text = str(text).strip()
  parts = text.split()
  if len(parts) != 1:
    return ""
  candidate = parts[0].strip("():") if parts else ""
  if ":" in candidate:
    exchange, candidate = candidate.split(":", 1)
    if exchange.upper() not in ("NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"):
      return ""
  upper = candidate.upper()
  if upper in _NOT_SYMBOLS or not _SYMBOL.fullmatch(upper):
    return ""
  if candidate != upper:
    return ""
  return upper


def _header(text):
  key = re.sub(r"[^a-z]", "", text.lower())
  key = re.sub(r"(?:usd|eur|gbp|cad|aud|jpy|chf|hkd|cny|inr)$", "", key)
  if key in ("symbol", "ticker", "security", "securitysymbol", "asset", "investment"):
    return "ticker"
  if key in ("shares", "quantity", "qty", "units", "sharequantity", "totalshares"):
    return "shares"
  if key in ("action", "type", "transaction", "transactiontype", "activity"):
    return "action"
  if key in ("date", "tradedate", "transactiondate", "purchasedate", "dateacquired", "acquireddate"):
    return "trade_date"
  if key in (
    "averagecost", "avgcost", "averagecostpershare", "avgcostpershare", "costpershare",
    "purchaseprice", "buyprice", "pricepaid", "averageprice", "avgprice", "unitcost",
    "averagepurchaseprice", "averagebuyprice", "avgcostbasis", "averagecostbasis",
  ):
    return "trade_price"
  if key in ("costbasis", "totalcost", "totalcostbasis", "bookcost", "bookvalue", "purchasecost"):
    return "total_cost"
  if key in ("price", "pricepershare", "executionprice", "tradeprice", "fillprice"):
    return "execution_price"
  if key in ("currency", "ccy"):
    return "currency"
  return None


def _date(text):
  text = str(text).strip()
  for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"):
    try:
      value = datetime.strptime(text, pattern).date()
    except ValueError:
      continue
    return value if 1900 <= value.year <= date.today().year + 1 else None
  return None


def _normalise_blocks(result, offset=0):
  blocks = []
  for entry in result or []:
    points, text, confidence = entry[:3]
    text = str(text).strip()
    if not text:
      continue
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) + offset for point in points]
    blocks.append({
      "text": text, "score": float(confidence), "left": min(xs), "right": max(xs),
      "top": min(ys), "bottom": max(ys),
      "x": (min(xs) + max(xs)) / 2, "y": (min(ys) + max(ys)) / 2,
      "height": max(ys) - min(ys),
      "points": list(zip(xs, ys)),
      "angle": math.atan2(ys[1] - ys[0], xs[1] - xs[0]),
    })
  return blocks


def _deskew_blocks(blocks):
  """Align table rows in mildly tilted photos using the detected text baselines."""
  angles = [block["angle"] for block in blocks if "angle" in block and len(block["text"]) >= 3]
  if not angles:
    return blocks
  angle = median(angles)
  if not math.radians(.75) <= abs(angle) <= math.radians(15):
    return blocks
  cosine, sine = math.cos(angle), math.sin(angle)
  upright = []
  for block in blocks:
    if "points" not in block:
      upright.append(block)
      continue
    points = [(x * cosine + y * sine, -x * sine + y * cosine) for x, y in block["points"]]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    upright.append(dict(block, left=min(xs), right=max(xs), top=min(ys), bottom=max(ys),
                        x=(min(xs) + max(xs)) / 2, y=(min(ys) + max(ys)) / 2,
                        height=max(ys) - min(ys)))
  return upright


def _lines(blocks):
  lines = []
  for block in sorted(blocks, key=lambda value: (value["y"], value["left"])):
    if lines and abs(block["y"] - lines[-1]["y"]) <= max(7, min(block["height"], lines[-1]["height"]) * .6):
      line = lines[-1]
      line["blocks"].append(block)
      line["y"] = sum(item["y"] for item in line["blocks"]) / len(line["blocks"])
      line["height"] = max(line["height"], block["height"])
    else:
      lines.append({"y": block["y"], "height": block["height"], "blocks": [block]})
  for line in lines:
    line["blocks"].sort(key=lambda value: value["left"])
    line["text"] = " · ".join(item["text"] for item in line["blocks"])
  return lines


def _draft(ticker="", shares="", price="", action="Buy", trade_date=None, row_kind="holding", notes=None, include=True):
  notes = list(notes or [])
  if not ticker:
    notes.append("Ticker not read; enter it before importing.")
  if not shares:
    notes.append("Quantity not read; enter the number of shares.")
  if row_kind == "holding":
    trade_date = None
    notes.append("Current holding: choose a snapshot date for this quantity.")
    if price:
      notes.append("This cost is an average for the holding, not an individual trade price.")
  elif trade_date is None:
    notes.append("Trade date not read; choose the actual transaction date.")
  if not price:
    notes.append("Purchase price not read; current market prices were ignored.")
  if shares and Decimal(shares) <= 0:
    include = False
    notes.append("Zero or short quantity excluded; only positive share counts are supported.")
  return {
    "ticker": ticker, "shares": shares, "trade_date": trade_date, "trade_price": price,
    "action": action, "notes": " ".join(notes), "row_kind": row_kind, "include": include,
  }


def _parse_table(lines, document=False, account_currency=None):
  rows = []
  columns = None
  is_transaction = False
  used_lines = set()
  other_section = False
  for index, line in enumerate(lines):
    if document:
      text = line["text"].replace(" · ", " ").strip()
      currency_caption = _account_currency(text)
      if currency_caption:
        account_currency = currency_caption
        used_lines.add(index)
        continue
      if _PDF_PAGE_FOOTER.search(text) or _PDF_BOILERPLATE.search(text):
        used_lines.add(index)
        continue
      if _PDF_OTHER_SECTION.fullmatch(text):
        columns = None
        other_section = True
        used_lines.add(index)
        continue
      if _PDF_POSITION_SECTION.fullmatch(text):
        columns = None
        other_section = False
        used_lines.add(index)
        continue
    recognized = [(block, _header(block["text"])) for block in line["blocks"]]
    recognized = [(block, field) for block, field in recognized if field]
    fields = {field for _, field in recognized}
    if len(fields) >= 2 and ("shares" in fields or "ticker" in fields):
      # Include every header box as a column boundary, including current prices/value.
      columns = [(block["x"], _header(block["text"])) for block in line["blocks"]]
      is_transaction = "action" in fields
      other_section = False
      used_lines.add(index)
      continue
    if other_section:
      used_lines.add(index)
      continue
    if columns is None:
      continue
    if _CASH.search(line["text"]):
      used_lines.add(index)
      continue
    cells = {}
    for block in line["blocks"]:
      center, field = min(columns, key=lambda column: abs(column[0] - block["x"]))
      if field:
        cells.setdefault(field, []).append(block)
    ticker_blocks = cells.get("ticker", [])
    ticker_text = " ".join(block["text"] for block in ticker_blocks)
    ticker = _symbol(ticker_text, labelled=True)
    if not ticker and ticker_blocks:
      ticker = _symbol(ticker_blocks[0]["text"], labelled=True)
    quantity_blocks = cells.get("shares", [])
    shares = _number(" ".join(block["text"] for block in quantity_blocks))
    if not ticker_text and not shares:
      continue
    # A table description can be retained without guessing its ticker, but a
    # header/footer with no numeric quantity is not a position.
    if not ticker and not shares:
      continue
    action_text = " ".join(block["text"] for block in cells.get("action", [])).lower()
    action_match = re.search(r"\b(buy|bought|purchase|sell|sold|sale)\b", action_text)
    row_kind = "transaction" if is_transaction and action_match else "holding"
    if is_transaction and action_text and not action_match and re.search(
      r"\b(?:dividend|interest|deposit|withdrawal|transfer|fee|fees|distribution)\b", action_text
    ):
      used_lines.add(index)
      continue  # Dividends, deposits, transfers and fees are not share trades.
    action = "Sell" if action_match and action_match.group() in ("sell", "sold", "sale") else "Buy"
    notes = []
    if is_transaction and action_text and not action_match and action_text not in ("stock", "stocks", "etf", "equity", "fund", "mutual fund"):
      notes.append("Type/action read as %s; kept as a holding for review." % action_text[:80])
    if not ticker and ticker_text:
      notes.append("Security read as %s." % ticker_text[:100])
    date_text = " ".join(block["text"] for block in cells.get("trade_date", []))
    trade_date = _date(date_text) if row_kind == "transaction" else None
    cost_field = (
      "execution_price" if row_kind == "transaction" and cells.get("execution_price")
      else "trade_price" if cells.get("trade_price") else None
    )
    cost_blocks = cells.get(cost_field, []) if cost_field else []
    price = _number(" ".join(block["text"] for block in cost_blocks))
    if price and Decimal(price) <= 0:
      price = ""
    if not price and shares and Decimal(shares) > 0:
      total = _number(" ".join(block["text"] for block in cells.get("total_cost", [])))
      if total and Decimal(total) > 0:
        with localcontext() as context:
          context.prec = 64
          price = format((Decimal(total) / Decimal(shares)).quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".")
        notes.append("Average purchase cost derived from total cost basis ÷ shares.")
    include = True
    if _NON_USD.search(line["text"]):
      include = False
      price = ""
      notes.append("Non-USD currency detected; this analyzer uses USD. Review the security and currency.")
    if re.search(r"\b(?:shorts?|options?|puts?|calls?|contracts?)\b", line["text"], re.I):
      include = False
      notes.append("Short/option position excluded; this analyzer supports ordinary shares.")
    relevant = ticker_blocks + quantity_blocks + cost_blocks
    if any(block["score"] < .8 for block in relevant):
      notes.append("Some characters were read with low confidence; check the image carefully.")
    row = _draft(ticker, shares, price, action, trade_date, row_kind, notes, include)
    if document:
      _apply_account_currency(row, account_currency, line["text"])
    rows.append(row)
    used_lines.add(index)
  return rows, used_lines


def _parse_cards(lines, used_lines, document=False, account_currency=None):
  """Support broker cards with explicit 'shares'/'average cost' labels."""
  rows = []
  candidates = []
  account_currencies = {}
  for index, line in enumerate(lines):
    if document:
      account_currency = _account_currency(line["text"]) or account_currency
      account_currencies[index] = account_currency
    if index in used_lines or _CASH.search(line["text"]):
      continue
    for block in line["blocks"]:
      if _symbol(block["text"]):
        candidates.append((index, block, _symbol(block["text"])))
        break
  for position, (index, block, ticker) in enumerate(candidates):
    end = candidates[position + 1][0] if position + 1 < len(candidates) else min(len(lines), index + 8)
    if document:
      for following in range(index + 1, end):
        if _account_currency(lines[following]["text"]):
          end = following
          break
    nearby = [line for line in lines[index:end] if line["y"] - block["y"] <= 220]
    text = " \n ".join(line["text"].replace(" · ", " ") for line in nearby)
    if _CASH.search(text):
      continue
    qty_match = re.search(r"(?:\b(?:shares?|quantity|qty|units?)\s*[:=]?\s*([+-]?[\d,.]+)|([+-]?[\d,.]+)\s*shares?\b)", text, re.I)
    cost_match = re.search(r"\b(?:average cost|avg\.? cost|cost per share|purchase price|price paid|average price)\s*[:=]?\s*((?:USD\s*|\$\s*)?[\d,.]+)", text, re.I)
    if not qty_match and not cost_match and not re.search(r"(?:\$\s*\d|[€£¥]\s*\d)", text):
      continue
    shares = _number(qty_match.group(1) or qty_match.group(2)) if qty_match else ""
    price = _number(cost_match.group(1)) if cost_match else ""
    # A ticker alongside only market value is still useful as a partial draft.
    notes = ["No table headers found; only explicitly labelled share counts and purchase costs were filled."]
    include = not bool(_NON_USD.search(text))
    if not include:
      notes.append("Non-USD currency detected; this analyzer uses USD.")
      price = ""
    if re.search(r"\b(?:shorts?|options?|puts?|calls?|contracts?)\b", text, re.I):
      include = False
      notes.append("Short/option position excluded; this analyzer supports ordinary shares.")
    if block["score"] < .8:
      notes.append("Ticker has low reading confidence; check it against the image.")
    row = _draft(ticker, shares, price, notes=notes, include=include)
    if document:
      _apply_account_currency(row, account_currencies[index], text)
    rows.append(row)
  return rows


def _parse_blocks(blocks, document=False, account_currency=None):
  blocks = _deskew_blocks(blocks)
  lines = _lines(blocks)
  rows, used_lines = _parse_table(lines, document=document, account_currency=account_currency)
  rows.extend(_parse_cards(lines, used_lines, document=document, account_currency=account_currency))
  caption = " ".join(block["text"] for block in blocks)
  non_usd_heading = any(
    _header(block["text"]) in ("trade_price", "total_cost", "execution_price")
    and _NON_USD.search(block["text"]) for block in blocks
  )
  non_usd_caption = re.search(
    (r"\b(?:" + (r"account\s+currency\s*:?|" if not document else "")
     + r"(?:all\s+)?(?:amounts|prices|values|costs)\s+(?:are\s+)?(?:shown\s+)?in)\s*(?:EUR|GBP|CAD|AUD|JPY|CHF|HKD|CNY|INR)\b"),
    caption, re.I,
  )
  if non_usd_heading or non_usd_caption:
    for row in rows:
      row["include"] = False
      row["trade_price"] = ""
      row["notes"] += " Non-USD %s currency detected; this analyzer uses USD." % (
        "document" if document else "image",
      )
  return rows[:_MAX_ROWS], len(rows) > _MAX_ROWS


def _validate_ocr_blocks(ocr_blocks):
  if not isinstance(ocr_blocks, list) or len(ocr_blocks) > _MAX_BLOCKS:
    return None, "This image contains too much text. Crop it to the holdings and try again."
  clean = []
  for entry in ocr_blocks:
    if not isinstance(entry, (list, tuple)) or len(entry) != 3:
      return None, "The image reader returned invalid text. Read the image again."
    points, text, confidence = entry
    if not isinstance(text, str) or len(text) > _MAX_TEXT_LENGTH:
      return None, "A text region is too long. Crop the image to your positions and try again."
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1 or not math.isfinite(confidence):
      return None, "The image reader returned invalid confidence values. Read the image again."
    if not isinstance(points, (list, tuple)) or len(points) != 4:
      return None, "The image reader returned invalid text locations. Read the image again."
    clean_points = []
    for point in points:
      if not isinstance(point, (list, tuple)) or len(point) != 2:
        return None, "The image reader returned invalid text locations. Read the image again."
      if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= _MAX_COORDINATE or not math.isfinite(value) for value in point):
        return None, "The image reader returned invalid text locations. Read the image again."
      clean_points.append([float(value) for value in point])
    if text.strip():
      clean.append([clean_points, text.strip(), float(confidence)])
  return clean, None


@anvil.server.callable
def parse_holdings_image_text(ocr_blocks):
  """Parse bounded, untrusted OCR output into transient drafts for client review."""
  clean_blocks, error = _validate_ocr_blocks(ocr_blocks)
  if error:
    return _failure(error)
  blocks = _normalise_blocks(clean_blocks)
  if not blocks:
    return _failure("No readable text was found. Try a sharper, straight-on screenshot with the column headings visible.")
  rows, truncated = _parse_blocks(blocks)
  if not rows:
    return _failure("No positions could be identified. Include ticker symbols and quantity column headings in a clearer screenshot.")
  warnings = [_REVIEW_WARNING]
  if any(row["row_kind"] == "holding" for row in rows):
    warnings.append(_SNAPSHOT_WARNING)
  if any(not row["include"] for row in rows):
    warnings.append("Unsupported or non-USD positions were left unchecked for review.")
  if truncated:
    warnings.append("Only the first 100 positions were included. Split a larger image into smaller uploads.")
  return {
    "ok": True, "message": "Read %d position%s. Review the drafts before adding them." % (len(rows), "" if len(rows) == 1 else "s"),
    "rows": rows, "warnings": warnings,
  }


@anvil.server.callable
def parse_holdings_document_text(ocr_pages):
  """Parse bounded PDF text page by page into reviewable, unpersisted drafts."""
  if not isinstance(ocr_pages, list) or not 1 <= len(ocr_pages) <= _MAX_DOCUMENT_PAGES:
    return _failure("Read a PDF with 1 to 20 pages at a time.")
  clean_pages = []
  seen_pages = set()
  block_count = 0
  for entry in ocr_pages:
    if not isinstance(entry, dict):
      return _failure("The PDF reader returned an invalid page. Read the file again.")
    page = entry.get("page")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page in seen_pages:
      return _failure("The PDF reader returned invalid or repeated page numbers. Read the file again.")
    seen_pages.add(page)
    page_blocks = entry.get("blocks")
    if not isinstance(page_blocks, list):
      return _failure("The PDF reader returned invalid text. Read the file again.")
    block_count += len(page_blocks)
    if block_count > _MAX_BLOCKS:
      return _failure("This PDF contains too much text. Export just the holdings pages and try again.")
    clean_blocks, error = _validate_ocr_blocks(page_blocks)
    if error:
      return _failure(error.replace("image", "PDF").replace("Image", "PDF"))
    clean_pages.append((page, clean_blocks))
  rows = []
  truncated = False
  account_currency = None
  for page, clean_blocks in sorted(clean_pages):
    page_blocks = _normalise_blocks(clean_blocks)
    page_rows, page_truncated = _parse_blocks(page_blocks, document=True, account_currency=account_currency)
    for line in _lines(_deskew_blocks(page_blocks)):
      account_currency = _account_currency(line["text"]) or account_currency
    truncated = truncated or page_truncated or len(rows) + len(page_rows) > _MAX_ROWS
    for row in page_rows[:max(0, _MAX_ROWS - len(rows))]:
      row["source_page"] = page
      row["notes"] = (row["notes"] + " PDF page %d." % page).strip()
      rows.append(row)
  if not rows:
    return _failure("No positions could be identified in this PDF. Include the holdings pages with ticker symbols and quantity headings, or import a clear screenshot.")
  warnings = ["Check every ticker, quantity and cost against your PDF before importing."]
  if any(row["row_kind"] == "holding" for row in rows):
    warnings.append(_SNAPSHOT_WARNING.replace("images", "documents"))
  if any(not row["include"] for row in rows):
    warnings.append("Unsupported or non-USD positions were left unchecked for review.")
  # A repeated snapshot may be a duplicated statement view or another account.
  # Keep both for the user to decide; separate trade executions remain valid.
  first_pages = {}
  repeated = set()
  for row in rows:
    if row["row_kind"] != "holding" or not row["ticker"] or not row["shares"]:
      continue
    key = (row["ticker"], Decimal(row["shares"]),
           Decimal(row["trade_price"]) if row["trade_price"] else None)
    if key in first_pages and first_pages[key] != row["source_page"]:
      repeated.add(key)
    first_pages.setdefault(key, row["source_page"])
  if repeated:
    warnings.append("Some identical holdings appear on different PDF pages. Check for repeated statement views or separate accounts before importing; both rows are still selected.")
    for row in rows:
      if row["row_kind"] == "holding" and row["ticker"] and row["shares"]:
        key = (row["ticker"], Decimal(row["shares"]),
               Decimal(row["trade_price"]) if row["trade_price"] else None)
        if key in repeated:
          row["notes"] += " An identical holding appears on another PDF page; check whether both should be imported."
  if truncated:
    warnings.append("Only the first 100 positions were included. Export fewer holdings pages to import the rest.")
  return {
    "ok": True, "message": "Read %d position%s from the PDF. Review the drafts before adding them." % (len(rows), "" if len(rows) == 1 else "s"),
    "rows": rows, "warnings": warnings,
  }

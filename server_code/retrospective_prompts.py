"""Bounded, deterministic prose-to-rules preview for the daily backtester.

This module recognises a deliberately small strategy vocabulary. It never runs
user text and refuses additional conditions rather than dropping them.
"""

from datetime import date
import re


_SECTOR_ALIASES = {
  "basic materials": "Basic Materials", "materials": "Basic Materials",
  "communication services": "Communication Services", "communications": "Communication Services",
  "telecommunications": "Communication Services", "telecom": "Communication Services",
  "consumer cyclical": "Consumer Cyclical", "consumer discretionary": "Consumer Cyclical",
  "consumer defensive": "Consumer Defensive", "consumer staples": "Consumer Defensive",
  "energy": "Energy", "financial services": "Financial", "financials": "Financial",
  "financial": "Financial", "finance": "Financial", "health care": "Healthcare",
  "healthcare": "Healthcare", "industrials": "Industrials", "industrial": "Industrials",
  "real estate": "Real Estate", "technology": "Technology", "information technology": "Technology",
  "tech": "Technology", "utilities": "Utilities",
  "funds / etfs": "Funds / ETFs", "funds/etfs": "Funds / ETFs",
  "funds and etfs": "Funds / ETFs", "unclassified": "Unclassified",
}
_SECTOR_NAMES = set(_SECTOR_ALIASES.values())
_BUY = re.compile(r"\b(?:buy|bought|buying|purchase|purchased|purchasing|enter|entry|invest|invested)\b")
_SELL = re.compile(r"\b(?:sell|sold|selling|exit|exiting)\b")
_HORIZON = r"(?:(?:\d+|one|two|three|a|an)\s*-?\s*(?:years?|yrs?|months?|mos?|weeks?|wks?|days?)|yearly|annual)"
_EXTREME = re.compile(r"(?:(" + _HORIZON + r")\s+(?:rolling\s+|trailing\s+)?)?\b(lows?|highs?)\b")
_HOLD = re.compile(r"\b(?:buy\s+(?:and\s+)?hold|buy\s+and\s+hold|bought\s+and\s+held|buy[- ]and[- ]hold)\b")
_UNSUPPORTED = (
  (r"\b(?:rsi|macd|sma|ema|bollinger|moving average|momentum|volume|earnings|fundamentals|pe ratio|p/e|valuation)\b", "Technical indicators, volume, and fundamental filters are not supported by this daily low/high strategy editor."),
  (r"\b(?:dividend[- ]paying|dividend stocks?|dividend yield|high[- ]yield|low[- ]yield|profitable|profitability|growing dividends)\b|\b(?:dividends?|profits?|returns?|gains?)\s+(?:at\s+|is\s+|are\s+|the\s+|yearly\s+|annual\s+)*(?:lows?|highs?)\b", "Low/high signals refer to share prices. Dividend yield, profitability, and return filters are not supported; dividend inclusion only controls cash payments in the calculation."),
  (r"\b(?:short|shorting|shorted|options?|puts?|calls?|leverage|leveraged|margin|futures|crypto|cryptocurrency)\b", "Use long stock or ETF positions; shorting, derivatives, and leverage are not supported."),
  (r"\b(?:intraday|minute|minutes|hour|hours|tick|ticks|second|seconds|stop[- ]loss|stoploss|limit order|limit orders)\b", "This test uses completed daily closes and the next session's open. Intraday, exact-second, stop, and limit-order fills are not supported."),
  (r"\b(?:rebalance|rebalancing|repeatedly|repeat|reinvest|reinvesting|reinvestment|compound|compounding)\b", "Use one entry and at most one sale per security. Rebalancing, repeated trades, and dividend reinvestment are not supported; included dividends remain cash."),
  (r"\b(?:absolute|ultimate|best possible|worst possible|lowest of the|highest of the|lowest price during|highest price during)\b", "Choose a trailing low or high known on that date. The eventual lowest or highest price of the entire test period would use future information."),
  (r"\b(?:every time|each time|on every|buy again|sell again|multiple entries|multiple exits)\b", "This editor tests one entry and at most one sale per security; repeated entries or exits are not supported."),
)
_FILLER = set("""
  a an and or but the this that these those of to for from in during over within past last
  previous prior trailing rolling yearly annual year years yr yrs month months mo mos
  week weeks wk wks day days one all any selected sector sectors stock stocks
  security securities etf etfs fund funds position positions holding holdings shares portfolio my your its
  their our each per current existing can could please help give me us tell want i we you
  what how do does is are was were be been being would will should look looks looking like
  work worked run test simulate simulated simulation backtest backtesting retrospective
  analysis analyze analyse calculate calculating performance profit profits gain gains
  return returns roi total overall generated prompt then after before once first at when
  whenever hits hit reaches reach makes make made new low lows high highs buy bought buying purchase
  purchased purchasing enter entry invest invested sell sold selling exit exiting half
  quarter full entire whole remaining rest everything hold held holding including include
  included with plus dividend dividends cash excluding exclude excluded without no price
  prices as if on ago period horizon timeframe date dates through until start end beginning
  percent percentage immediately moment met parameters conditions real time actual
  only result results get had have has worth amount values value cost dollars
""".split())
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}
_ALLOWED_LOOKBACKS = {30, 90, 180, 365, 730}
_PERIOD = re.compile(r"\b(?:past|last|previous|during|over|for)\s+(?:the\s+)?(?:(?:past|last|previous)\s+)?(\d+|one|two|three|a|an)?\s*(years?|yrs?|months?|mos?|weeks?|wks?|days?)\b")
_PERCENTAGE = re.compile(r"(?<![\w.])([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:%|percent\b)")


def _failure(message):
  return {"ok": False, "message": message}


def _normalise(text):
  text = text.lower().replace("\u2013", "-").replace("\u2014", "-")
  text = text.replace("\u2019", "'").replace("&", " and ").replace("+", " and ")
  text = re.sub(r"\b1\s*y\b", "1 yr", text)
  # The hyphen between a number and its unit is accepted in ordinary prose.
  return re.sub(r"\s+", " ", text).strip()


def _horizon_days(horizon):
  if not horizon or horizon in ("annual", "yearly"):
    return 365
  match = re.fullmatch(r"(\d+|one|two|three|a|an)\s*-?\s*([a-z]+)", horizon)
  if not match:
    return None
  count = _WORDS.get(match[1], int(match[1]) if match[1].isdigit() else 0)
  unit = match[2]
  if unit.startswith("year") or unit.startswith("yr"):
    days = count * 365
  elif unit.startswith("month") or unit.startswith("mo"):
    days = {1: 30, 3: 90, 6: 180, 12: 365, 24: 730}.get(count)
  elif unit.startswith("week") or unit.startswith("wk"):
    days = 365 if count == 52 else 730 if count == 104 else count * 7
  else:
    days = count
  return days if days in _ALLOWED_LOOKBACKS else None


def _rule_extreme(segment):
  matches = list(_EXTREME.finditer(segment))
  if len(matches) != 1:
    return None, None
  match = matches[0]
  return match[2].rstrip("s"), _horizon_days(match[1])


def _period_matches(text):
  # A previous-year low describes the signal window, not the test period.
  return [match for match in _PERIOD.finditer(text)
          if not re.match(r"\s+(?:(?:rolling|trailing)\s+)?(?:lows?|highs?)\b", text[match.end():])]


def _period(text):
  matches = _period_matches(text)
  if not matches:
    return 12, None
  periods = set()
  for match in matches:
    if match[2].startswith(("week", "wk", "day")):
      return None, "Describe the test period in months or years, or set exact custom dates in the editor. Daily and weekly test periods are not inferred from the prompt."
    number = match[1] or "one"
    count = int(number) if number.isdigit() else _WORDS[number]
    periods.add(count * 12 if match[2].startswith(("year", "yr")) else count)
  if len(periods) != 1 or next(iter(periods)) not in (1, 3, 6, 12, 24, 36, 60):
    return None, "Choose one test period: past 1, 3, 6, 12, 24, 36, or 60 months, or set custom dates in the editor."
  return next(iter(periods)), None


def _sector(text, selected_sector):
  if selected_sector is not None and (not isinstance(selected_sector, str) or selected_sector not in _SECTOR_NAMES):
    return None, text, "Choose a recognised sector in the editor."
  detected = set()
  remainder = text
  for alias in sorted(_SECTOR_ALIASES, key=len, reverse=True):
    pattern = r"\b" + re.escape(alias) + r"\b"
    if re.search(pattern, remainder):
      detected.add(_SECTOR_ALIASES[alias])
      remainder = re.sub(pattern, " ", remainder)
  if len(detected) > 1:
    return None, remainder, "Choose one sector per test, or choose all selected securities in the editor."
  all_sectors = bool(re.search(r"\b(?:all|any)\s+sectors?\b", text))
  this_sector = bool(re.search(r"\b(?:this|that|selected|current)\s+sector\b", text))
  if all_sectors and detected:
    return None, remainder, "Choose a named sector or all sectors, rather than both."
  if detected:
    return next(iter(detected)), remainder, None
  if all_sectors:
    return None, remainder, None
  if this_sector and not selected_sector:
    return None, remainder, "For 'this sector', choose a sector in the editor first, or name it in the prompt."
  return selected_sector or None, remainder, None


def _dividend_mode(text):
  """Recognise positive and negative dividend wording in either word order."""
  noun = r"(?:(?:cash|the)\s+)*dividends?"
  exclude = bool(re.search(r"\b(?:without|excluding|exclude|excluded|no)\s+" + noun + r"\b|\b" + noun + r"\s+(?:excluded|omitted|off)\b|\bprice[- ]only\b", text))
  include = bool(re.search(r"\b(?:including|include|included|with|plus)\s+" + noun + r"\b|\b" + noun + r"\s+(?:included|on)\b", text))
  return not exclude, include, exclude


def _sale_fraction(segment):
  percentages = _PERCENTAGE.findall(segment)
  fractions = set()
  for number in percentages:
    value = float(number) / 100
    if not 0 < value <= 1:
      return None, "Sale percentage must be greater than 0% and at most 100%."
    fractions.add(value)
  if re.search(r"\bhalf\b", segment):
    fractions.add(.5)
  if re.search(r"\bquarter\b", segment):
    fractions.add(.25)
  if re.search(r"\b(?:sell|sold|selling|exit|exiting)\s+(?:the\s+)?(?:all|full|entire|whole|everything)\b|\band\s+(?:all|everything)\b", segment):
    fractions.add(1.0)
  if len(fractions) > 1:
    return None, "Choose a single sale size, such as half, 25%, or all."
  if re.search(r"\b(?:third|thirds|some|part|portion|few)\b", segment):
    return None, "Specify sale size as a percentage, half, a quarter, or all."
  return next(iter(fractions)) if fractions else 1.0, None


def parse_retrospective_prompt(prompt, selected_sector=None):
  """Return recognised rules for review, or an actionable unsupported-rule error.

  Sector scope is limited to securities chosen in the UI. No ticker universe is
  discovered or implied by this parser. Optional ISO dates override test_months
  when the caller applies the preview to its date controls.
  """
  if not isinstance(prompt, str) or not prompt.strip():
    return _failure("Enter a prompt, such as 'Buy at a 52-week low and sell half at a yearly high, including dividends, over the past year.'")
  if len(prompt) > 1500:
    return _failure("Keep the strategy prompt to 1,500 characters or fewer.")
  text = _normalise(prompt)
  # These negations describe supported cash-dividend and no-lookahead behaviour.
  checked = re.sub(r"\b(?:without|no|not)\s+(?:dividend\s+)?reinvest(?:ed|ing|ment)?\b", " ", text)
  checked = re.sub(r"\bnot\s+(?:in\s+retrospect|retrospectively|using\s+future\s+(?:data|information|knowledge))\b", " ", checked)
  for pattern, message in _UNSUPPORTED:
    if re.search(pattern, checked):
      return _failure(message)
  if re.search(r"\b(?:not|never|unless|except)\b", checked):
    return _failure("Negated or conditional exceptions are not supported. State one positive buy rule and, optionally, one positive sell rule.")
  alternatives = re.sub(r"\b(?:stocks?|securities)\s+or\s+(?:etfs?|funds?)\b", " ", text)
  if re.search(r"\bor\b", alternatives):
    return _failure("Choose a single entry and optional sale rule. Alternative 'or' conditions are not supported.")
  if re.search(r"\b(?:same[- ]day|same[- ]session|signal[- ]day)\s+(?:fill|fills|trade|trades|open|close|execution)\b", text):
    return _failure("Signals use a completed daily close and execute at the next session's open; same-session fills would use information not yet available.")
  if "$" in text:
    return _failure("Set the starting capital in the editor; the prompt describes entry and sale rules.")

  date_strings = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
  custom_dates = None
  if date_strings:
    if len(date_strings) != 2:
      return _failure("Give both custom dates as YYYY-MM-DD, or choose them in the editor.")
    try:
      start, end = (date.fromisoformat(value) for value in date_strings)
    except ValueError:
      return _failure("Use valid custom dates in YYYY-MM-DD format.")
    if start >= end:
      return _failure("The custom start date must be before the end date.")
    custom_dates = (start.isoformat(), end.isoformat())
  months, period_error = _period(text)
  if period_error:
    return _failure(period_error)
  if custom_dates and _period_matches(text):
    return _failure("Choose an explicit date range or a relative past period, rather than both.")
  sector, remainder, sector_error = _sector(text, selected_sector)
  if sector_error:
    return _failure(sector_error)
  buy_matches = list(_BUY.finditer(text))
  sell_matches = list(_SELL.finditer(text))
  if not buy_matches:
    return _failure("Include a buy rule: buy and hold, buy at a trailing low, or buy at a trailing high.")
  if len(sell_matches) > 1 or len(buy_matches) > 1:
    return _failure("Describe one entry and at most one sale per security.")
  if sell_matches and sell_matches[0].start() < buy_matches[0].start():
    return _failure("Write the buy rule first, then the optional sell rule.")
  if re.search(r"\bif\b", text[buy_matches[0].end():]):
    return _failure("Additional 'if' conditions are not supported. Describe the trailing low/high directly, and use the editor for sector and dividend settings.")
  if re.search(r"\bbefore\b", text):
    return _failure("Before-event or advance-timing conditions are not supported. This test acts after a completed daily low/high signal, at the next session's open.")
  for after in re.finditer(r"\bafter\b", text):
    if not re.match(r"\s+(?:(?:a|an|the|its)\s+)?(?:(?:price|close)\s+)?(?:(?:hits|reaches|makes)\s+)?(?:(?:a|an|the|new)\s+)?(?:" + _HORIZON + r"\s+)?(?:lows?|highs?)\b", text[after.end():]):
      return _failure("Only a trailing low/high can time an entry or sale. Additional after-event or minimum-holding-period conditions are not supported.")
  split = sell_matches[0].start() if sell_matches else len(text)
  buy_segment, sell_segment = text[:split], text[split:]
  if _PERCENTAGE.search(buy_segment) or re.search(r"\b(?:half|quarter|third|some)\b", buy_segment):
    return _failure("Entry uses the full equal-weight allocation for each selected security. Specify sale percentages after the sell rule.")
  if _HOLD.search(buy_segment):
    entry_kind, entry_days = "buy_hold", 365
    if list(_EXTREME.finditer(buy_segment)):
      return _failure("Choose buy and hold or a trailing low/high entry, rather than both.")
  else:
    entry_kind, entry_days = _rule_extreme(buy_segment)
    if entry_kind is None:
      return _failure("Use one entry condition: buy at a trailing low, buy at a trailing high, or buy and hold.")
    if entry_days is None:
      return _failure("Supported low/high windows are 30, 90, 180, 365, or 730 calendar days (52 weeks means 365 days).")
  exit_kind, fraction = "none", 1.0
  if sell_matches:
    exit_kind, exit_days = _rule_extreme(sell_segment)
    if exit_kind is None:
      return _failure("Use one sale condition: sell at a trailing high or trailing low; omit the sale rule to keep the holding.")
    if exit_days is None:
      return _failure("Supported low/high windows are 30, 90, 180, 365, or 730 calendar days (52 weeks means 365 days).")
    if entry_kind == "buy_hold":
      entry_days = exit_days
    elif exit_days != entry_days:
      return _failure("Entry and sale must use the same trailing window. Edit the prompt to use a matching low/high horizon.")
    fraction, fraction_error = _sale_fraction(sell_segment)
    if fraction_error:
      return _failure(fraction_error)
    assert fraction is not None
  # Reject meaningful unrecognised terms. This prevents, for example, silently
  # treating 'buy at a low if the company is profitable' as an unconditional buy.
  residual = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", remainder)
  residual = _PERCENTAGE.sub(" ", residual)
  residual = _EXTREME.sub(" ", residual)
  residual = _PERIOD.sub(" ", residual)
  residual = re.sub(r"\b(?:without|no|not)\s+(?:dividend\s+)?reinvest(?:ed|ing|ment)?\b", " ", residual)
  residual = re.sub(r"\bnot\s+(?:in\s+retrospect|retrospectively|using\s+future\s+(?:data|information|knowledge))\b", " ", residual)
  residual = re.sub(r"\bdividends?\s+(?:included|excluded|omitted|off|on)\b", " ", residual)
  unknown = sorted(set(re.findall(r"[a-z][a-z']*|\d+(?:\.\d+)?", residual)) - _FILLER)
  if unknown:
    return _failure("Unrecognised prompt terms: %s. Use the low/high or buy-and-hold rules and choose securities, capital, and dates in the editor." % ", ".join(unknown[:6]))
  include_dividends, explicit_include, exclude_dividends = _dividend_mode(text)
  if exclude_dividends and explicit_include:
    return _failure("Choose whether to include or exclude dividends, rather than both.")
  rules = {
    "entry_kind": entry_kind, "exit_kind": exit_kind, "lookback_days": entry_days,
    "exit_fraction": fraction, "include_dividends": include_dividends,
    "test_months": months, "sector": sector,
  }
  if custom_dates:
    rules.update(start_date=custom_dates[0], end_date=custom_dates[1])
  entry_description = "buy at the first test session's open" if entry_kind == "buy_hold" else "buy after a completed daily close reaches the prior %d-calendar-day %s" % (entry_days, entry_kind)
  exit_description = "keep the remaining holding through the test end"
  if exit_kind != "none":
    exit_description = "sell %g%% once after a completed daily close reaches the prior %d-calendar-day %s, then hold the remainder" % (fraction * 100, entry_days, exit_kind)
  warnings = [
    "Daily data only: qualifying closes are filled at the next session's open, which can differ from the signal price. Intraday touches and exact-second fills are not modelled.",
    "Each low/high is measured against earlier closes in the rolling calendar-day window, excluding the signal day. The eventual low or high of the full test period is never used.",
    "One entry and at most one sale per selected security; uninvested allocations and sale proceeds remain cash.",
  ]
  if not explicit_include and not exclude_dividends:
    warnings.append("Dividends are included by default as cash without reinvestment; change the checkbox to exclude them.")
  if sell_matches and not (_PERCENTAGE.search(sell_segment) or re.search(r"\b(?:half|quarter|all|full|entire|whole|everything)\b", sell_segment)):
    warnings.append("No sale percentage was specified, so the preview sells 100%; edit the sale size before running.")
  period_description = "%s through %s" % custom_dates if custom_dates else "the past %d months" % months
  scope = sector + " securities selected in the editor" if sector else "securities selected in the editor"
  description = "For %s over %s, %s; %s. %s" % (
    scope, period_description, entry_description, exit_description,
    "Cash dividends are included without reinvestment." if include_dividends else "Dividends are excluded.",
  )
  return {"ok": True, "rules": rules, "description": description, "warnings": warnings}

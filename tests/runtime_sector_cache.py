"""Run in the disposable app database: anvil repl - < this file."""

from datetime import datetime, timedelta, timezone
import copy
import uuid

import anvil.users
from anvil.tables import app_tables
import finviz_sectors
import sectors


original_user = anvil.users.get_user
original_fetch = sectors.fetch_finviz_sector
tag = uuid.uuid4().hex[:8].upper()
symbols = ["Z" + tag + suffix for suffix in ("A", "B", "C")]
users = []
calls = []


def fake_fetch(symbol):
  calls.append(symbol)
  return "Real Estate" if symbol == symbols[0] else None


def forbidden_fetch(symbol):
  raise AssertionError("Cache-only load fetched Finviz")


try:
  for index in range(2):
    users.append(app_tables.users.add_row(email="sector-%s-%s@example.invalid" % (tag, index),
      enabled=True, signed_up=datetime.now(timezone.utc),
      username="sector_" + tag.lower() + str(index), username_key="sector_" + tag.lower() + str(index)))
  user = users[0]
  anvil.users.get_user = lambda: user
  sectors.fetch_finviz_sector = fake_fetch
  first, negative, leased = symbols
  labels = sectors.classify_securities([first, first, negative, "AAPL", "SPY"], {}, fetch_missing=True)
  assert labels[first] == "Real Estate" and labels[negative] == "Unclassified"
  assert labels["AAPL"] == "Technology" and labels["SPY"] == "Funds / ETFs"
  assert sorted(calls) == sorted([first, negative]), calls
  sectors.classify_securities([first, negative], {}, fetch_missing=True)
  assert len(calls) == 2, calls
  print("PASS: deduplicated symbol fetch, local catalog/fund bypass, persistent positive and negative cache")

  sectors.fetch_finviz_sector = forbidden_fetch
  payload = {"metadata": {"end_date": "2026-10-30"},
    "selection_data": {"securities": [{"symbol": first, "sector": "Unclassified"}]},
    "open_holdings": [{"symbol": first, "market_value": 100, "shares": 1, "price": 100}]}
  refreshed = sectors.refresh_analysis_classifications(copy.deepcopy(payload))
  assert refreshed["selection_data"]["securities"][0]["sector"] == "Real Estate"
  assert refreshed["sector_allocation"]["slices"][0]["sector"] == "Real Estate"
  assert sectors.classify_securities([first], {first: {"instrument_type": "ETF"}})[first] == "Funds / ETFs"
  print("PASS: saved unclassified analysis receives cached sector with zero network; provider ETF stays grouped")

  assert sectors.set_security_sector(first, "Industrials")["ok"]
  assert sectors.classify_securities([first], {})[first] == "Industrials"
  assert sectors.set_security_sector(first, "Unclassified")["ok"]
  assert sectors.classify_securities([first], {}, fetch_missing=True)[first] == "Unclassified"
  assert sectors.refresh_analysis_classifications(copy.deepcopy(payload))["selection_data"]["securities"][0]["default_sector"] == "Real Estate"
  user = users[1]
  assert sectors.classify_securities([first], {})[first] == "Real Estate"
  user = users[0]
  assert sectors.set_security_sector(first, None)["sector"] == "Real Estate"
  print("PASS: private overrides including explicit Unclassified win; shared cache does not leak other user's choices")

  now = datetime.now(timezone.utc)
  assert sectors._claim_lookup(leased, now) == (True, "Unclassified")
  assert sectors._claim_lookup(leased, now) == (False, "Unclassified")
  row = app_tables.security_sectors.get(owner=None, symbol=first)
  row.update(updated_at=now - timedelta(days=31))
  sectors.fetch_finviz_sector = lambda symbol: None
  assert sectors.classify_securities([first], {}, fetch_missing=True)[first] == "Real Estate"
  row = app_tables.security_sectors.get(owner=None, symbol=first)
  assert sectors._cache_fresh(row, now)
  assert not sectors._cache_fresh(row, now + timedelta(hours=6, minutes=1))
  print("PASS: concurrent request lease and stale positive retained on outage with six-hour retry")

  # Exercise the actual running server's network/parser, without modifying cache.
  for symbol, expected in (("MRP", "Real Estate"), ("SIRI", "Communication Services"), ("EG", "Financial")):
    result = finviz_sectors.fetch_finviz_sector(symbol)
    assert result is None or result == expected, (symbol, result)
    print("LIVE Finviz %s: %s" % (symbol, result or "provider unavailable; expected fallback path"))
finally:
  sectors.fetch_finviz_sector = original_fetch
  anvil.users.get_user = original_user
  for symbol in symbols:
    for row in list(app_tables.security_sectors.search(symbol=symbol)):
      row.delete()
  for user in users:
    user.delete()
  print("Fixture rows removed.")

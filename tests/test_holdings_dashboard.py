"""Cash-flow and historical snapshot examples independent of the Anvil runtime."""

import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client_code"))
from holdings_dashboard import build_dashboard, filter_holdings


def point(value, shares, bought=0, sold=0, cost=100):
  return dict(market_value=value, shares=shares, contributions=bought,
              withdrawals=sold, price=value / shares if shares else None,
              buy_cost=cost)


def source(days):
  dates = ["2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"][:len(days)]
  return dict(selection_data=dict(dates=dates, securities=[dict(
    symbol="TEST", company_name="Test <Company>", sector="Technology",
    days=days, **days[-1])]),
    series=[dict(date=day, session_complete=True) for day in dates])


class HoldingsDashboardTests(unittest.TestCase):
  def test_split_preserves_return_and_does_not_create_trade_activity(self):
    dashboard = build_dashboard(source([
      point(100, 1), point(100, 2), point(110, 2), point(110, 2)]))
    row = dashboard["rows"][0]
    self.assertAlmostEqual(row["return"], .1)
    self.assertEqual(row["activity"], "unchanged")
    self.assertEqual(row["shares"], 2)

  def test_buying_more_does_not_create_a_price_gain(self):
    row = build_dashboard(source([
      point(100, 1), point(200, 2, bought=100), point(220, 2), point(220, 2)]))["rows"][0]
    self.assertAlmostEqual(row["return"], .1)
    self.assertEqual(row["activity"], "added")

  def test_partial_and_complete_sales_are_withdrawals(self):
    dashboard = build_dashboard(source([
      point(100, 2), point(55, 1, sold=55), point(0, 0, sold=60), point(0, 0)]))
    self.assertAlmostEqual(dashboard["rows"][0]["return"], .2)
    self.assertEqual(dashboard["rows"][0]["activity"], "closed")
    self.assertEqual(dashboard["total"], 0)

  def test_new_position_uses_cash_contribution(self):
    row = build_dashboard(source([
      point(0, 0, cost=0), point(100, 1, bought=100), point(110, 1), point(110, 1)]))["rows"][0]
    self.assertAlmostEqual(row["return"], .1)
    self.assertEqual(row["activity"], "new")
    self.assertEqual(row["return_start"], "2026-07-01")

  def test_round_trip_remains_visible_in_activity(self):
    dashboard = build_dashboard(source([
      point(0, 0, cost=0), point(100, 1, bought=100), point(0, 0, sold=110), point(0, 0)]))
    self.assertEqual(dashboard["rows"][0]["activity"], "mixed")
    self.assertAlmostEqual(dashboard["rows"][0]["return"], .1)
    self.assertEqual(dashboard["activity_groups"][-1]["symbols"], ["TEST"])

  def test_legacy_latest_positions_have_no_invented_returns(self):
    payload = source([point(100, 1), point(110, 1)])
    payload["selection_data"]["securities"][0]["days"] = [{"nav": 100}, {"nav": 110}]
    dashboard = build_dashboard(payload)
    self.assertTrue(dashboard["legacy"])
    self.assertIsNone(dashboard["rows"][0]["return"])
    self.assertEqual(dashboard["rows"][0]["market_value"], 110)

  def test_legacy_historical_positions_do_not_use_latest_data(self):
    payload = source([point(100, 1), point(110, 1)])
    payload["selection_data"]["securities"][0]["days"] = [{"nav": 100}, {"nav": 110}]
    dashboard = build_dashboard(payload, quarter_key="2026-Q2")
    self.assertTrue(dashboard["legacy"])
    self.assertFalse(dashboard["rows"])

  def test_historical_positions_and_weights_use_the_selected_date(self):
    dashboard = build_dashboard(source([
      point(100, 1), point(200, 2, bought=100)]), quarter_key="2026-Q2")
    row = dashboard["rows"][0]
    self.assertEqual(row["shares"], 1)
    self.assertEqual(row["market_value"], 100)
    self.assertEqual(row["weight"], 1)
    self.assertIsNone(row["return"])

  def test_empty_selection_and_source_immutability(self):
    payload = source([point(100, 1), point(110, 1)])
    original = copy.deepcopy(payload)
    self.assertFalse(build_dashboard(payload, selected_symbols=[])["rows"])
    build_dashboard(payload)
    self.assertEqual(payload, original)

  def test_search_handles_plain_user_text_and_missing_returns_sort_last(self):
    dashboard = build_dashboard(source([point(100, 1), point(110, 1)]))
    self.assertEqual(len(filter_holdings(dashboard["rows"], query="<company>")), 1)
    missing = dict(dashboard["rows"][0], symbol="MISSING", **{"return": None})
    losing = dict(dashboard["rows"][0], symbol="LOSING", **{"return": -.1})
    self.assertEqual(filter_holdings([missing, losing], sort="return")[0]["symbol"], "LOSING")


if __name__ == "__main__":
  unittest.main()

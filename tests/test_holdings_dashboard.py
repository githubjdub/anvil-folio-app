"""Cash-flow and historical snapshot examples independent of the Anvil runtime."""

import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client_code"))
from holdings_dashboard import build_dashboard, filter_holdings, quarter_periods, quarterly_share_history, display_share_history, remaining_fifo_basis


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


def trade(day, action, shares, price=None):
  return dict(ticker="TEST:NYSE", trade_date=day, action=action, shares=shares, trade_price=price)


class HoldingsHistoryAndBasisTests(unittest.TestCase):
  def test_unchanged_days_preserve_basis_but_still_validate_daily_quantities(self):
    dates = ['2026-01-%02d' % day for day in range(1, 21)]
    days = [dict(shares=10) for _ in dates]
    trades = {dates[0]: [dict(action='Buy', shares=10, price=15, estimated=False,
                             entered_date=dates[0], row=0)]}
    self.assertEqual(remaining_fifo_basis(days, dates, 19, trades), (150, False))
    for invalid in (None, True, float('nan'), float('inf'), -1, 'invalid'):
      with self.subTest(invalid=invalid):
        changed = [dict(day) for day in days]
        changed[10]['shares'] = invalid
        self.assertEqual(remaining_fifo_basis(changed, dates, 19, trades), (None, False))
    days[-1]['shares'] = 20  # Last-day split still reconciles the endpoint.
    self.assertEqual(remaining_fifo_basis(days, dates, 19, trades), (150, False))

  def fixture(self, dates, shares, trades, prices=None):
    days = [point(quantity * 10, quantity) for quantity in shares]
    payload = dict(selection_data=dict(dates=dates, securities=[dict(
      symbol="TEST", company_name="Test", sector="Technology", days=days, **days[-1])]),
      series=[dict(date=day, session_complete=True) for day in dates],
      rows=[dict(entry_date_used=dates[index], entry_price=(prices or [None] * len(trades))[index])
            for index in range(len(trades))])
    return payload

  def test_fifo_remaining_basis_excludes_already_sold_lots(self):
    dates = ["2026-04-01", "2026-04-02", "2026-04-03"]
    trades = [trade(dates[0], "Buy", 10, 10), trade(dates[1], "Buy", 10, 20), trade(dates[2], "Sell", 15, 30)]
    payload = self.fixture(dates, [10, 20, 5], trades)
    row = build_dashboard(payload, transactions=trades)["rows"][0]
    self.assertEqual(row["cost_basis"], 100)
    self.assertFalse(row["basis_estimated"])

  def test_split_and_same_session_trade_preserve_total_basis(self):
    dates = ["2026-04-01", "2026-04-02", "2026-04-03"]
    trades = [trade(dates[0], "Buy", 10, 10), trade(dates[1], "Buy", 10, 8), trade(dates[2], "Sell", 25, 9)]
    payload = self.fixture(dates, [10, 30, 5], trades)
    row = build_dashboard(payload, transactions=trades)["rows"][0]
    self.assertAlmostEqual(row["cost_basis"], 40)

  def test_reverse_split_uses_pre_split_lot_total(self):
    dates = ["2026-04-01", "2026-04-02"]
    trades = [trade(dates[0], "Buy", 100, 3)]
    payload = self.fixture(dates, [100, 10], trades)
    row = build_dashboard(payload, transactions=trades)["rows"][0]
    self.assertEqual(row["cost_basis"], 300)

  def test_unsorted_weekend_buys_use_original_dates_before_fifo_sale(self):
    dates = ["2026-06-08", "2026-06-09"]
    # Sunday appears first in the editor, but Saturday must create the first lot.
    trades = [trade("2026-06-07", "Buy", 10, 20), trade("2026-06-06", "Buy", 10, 10),
              trade("2026-06-09", "Sell", 10, 30)]
    payload = self.fixture(dates, [20, 10], [])
    payload["rows"] = [dict(entry_date_used="2026-06-08", entry_price=20),
                       dict(entry_date_used="2026-06-08", entry_price=10),
                       dict(entry_date_used="2026-06-09", entry_price=30)]
    self.assertEqual(build_dashboard(payload, transactions=trades)["rows"][0]["cost_basis"], 200)

  def test_synced_close_fallback_is_explicitly_estimated(self):
    dates = ["2026-04-01", "2026-04-02"]
    trades = [trade(dates[0], "Buy", 10)]
    payload = self.fixture(dates, [10, 10], trades, [13])
    row = build_dashboard(payload, transactions=trades)["rows"][0]
    self.assertEqual(row["cost_basis"], 130)
    self.assertTrue(row["basis_estimated"])

  def test_missing_execution_prices_stay_unknown(self):
    dates = ["2026-04-01", "2026-04-02"]
    trades = [trade(dates[0], "Buy", 10)]
    payload = self.fixture(dates, [10, 10], trades)
    row = build_dashboard(payload, transactions=trades)["rows"][0]
    self.assertIsNone(row["cost_basis"])

  def test_unknown_basis_does_not_poison_later_known_repurchase(self):
    dates = ["2026-04-01", "2026-04-02", "2026-04-03"]
    trades = [trade(dates[0], "Buy", 10), trade(dates[1], "Sell", 10, 20), trade(dates[2], "Buy", 5, 15)]
    payload = self.fixture(dates, [10, 0, 5], trades)
    row = build_dashboard(payload, transactions=trades)["rows"][0]
    self.assertEqual(row["cost_basis"], 75)
    self.assertFalse(row["basis_estimated"])

  def test_missing_transaction_history_cannot_invent_basis(self):
    payload = self.fixture(["2026-04-01", "2026-04-02"], [10, 10], [])
    self.assertIsNone(build_dashboard(payload)["rows"][0]["cost_basis"])

  def test_historical_basis_never_reads_later_purchase(self):
    dates = ["2026-06-30", "2026-07-01"]
    trades = [trade(dates[0], "Buy", 10, 10), trade(dates[1], "Buy", 10, 25)]
    payload = self.fixture(dates, [10, 20], trades)
    row = build_dashboard(payload, quarter_key="2026-Q2", transactions=trades)["rows"][0]
    self.assertEqual(row["cost_basis"], 100)
    self.assertEqual(len(row["share_history"]), 1)
    self.assertEqual(row["share_history"][0]["shares"], 10)

  def test_history_caps_at_five_and_compares_previous_hidden_quarter(self):
    dates = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-05-15"]
    history = quarterly_share_history([dict(shares=n) for n in [10, 20, 15, 20, 25, 30]],
                                      dates, quarter_periods(dates), 5)
    self.assertEqual(len(history), 5)
    self.assertEqual(history[0]["label"], "Q2 2025")
    self.assertEqual(history[0]["change"], 1)
    self.assertAlmostEqual(history[-1]["change"], .2)
    self.assertTrue(history[-1]["partial"])
    self.assertTrue(history[-1]["latest"])
    self.assertEqual(history[-1]["height"], 100)
    self.assertIn("partial", display_share_history(history)[-1]["label_text"])

  def test_history_endpoints_have_no_lookahead_and_raw_split_counts(self):
    dates = ["2026-03-31", "2026-06-30", "2026-07-02"]
    history = quarterly_share_history([dict(shares=n) for n in [10, 20, 99]],
                                      dates, quarter_periods(dates), 1)
    self.assertEqual([row["shares"] for row in history], [10, 20])
    self.assertEqual(history[-1]["change"], 1)
    self.assertFalse(history[-1]["partial"])

  def test_complete_weekend_ending_quarter_is_not_partial(self):
    dates = ["2028-09-29"]  # Quarter ends on Saturday September 30.
    history = quarterly_share_history([dict(shares=10)], dates, quarter_periods(dates), 0)
    self.assertFalse(history[0]["partial"])

  def test_source_and_trades_remain_read_only(self):
    dates = ["2026-04-01", "2026-04-02"]
    trades = [trade(dates[0], "Buy", 10, 10)]
    payload = self.fixture(dates, [10, 20], trades)
    original = copy.deepcopy([payload, trades])
    build_dashboard(payload, transactions=trades)
    self.assertEqual(original, [payload, trades])


if __name__ == "__main__":
  unittest.main()

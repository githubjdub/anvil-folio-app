"""Header return semantics; run python -m unittest discover -s tests."""
from datetime import date, timedelta
import copy
import importlib
from pathlib import Path
import sys
import types
import unittest


package = types.ModuleType("folio_client_for_tests")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "client_code")]
sys.modules[package.__name__] = package
summary_module = importlib.import_module(package.__name__ + ".portfolio_summary")
summarize_portfolio = summary_module.summarize_portfolio


def history(start=date(2023, 11, 6), end=date(2026, 11, 6)):
  day = start
  points = []
  while day <= end:
    if day.weekday() < 5:
      points.append({"date": day.isoformat(), "portfolio_index": 100 + len(points),
                     "return_observed": bool(points), "session_complete": True})
    day += timedelta(days=1)
  return {"series": points, "metrics": {"market_value": 300, "holding_count": 2,
            "simple_return": .9, "total_return": points[-1]["portfolio_index"] / 100 - 1},
          "metadata": {"end_date": end.isoformat()},
          "sector_allocation": {"total_value": 300,
             "holdings": [{"symbol": "AAPL", "market_value": 100},
                          {"symbol": "MSFT", "market_value": 200}]}}


class PortfolioSummaryTests(unittest.TestCase):
  def test_value_holdings_and_top20(self):
    result = summarize_portfolio(history())
    self.assertEqual((result["value"], result["holding_count"], result["top20_weight"]), (300, 2, 1))

  def test_real_top20_weight(self):
    data = history()
    data["sector_allocation"] = {"total_value": 25, "holdings": [{"market_value": 1}] * 25}
    self.assertEqual(summarize_portfolio(data)["top20_weight"], .8)

  def test_previous_quarter_includes_first_day_return(self):
    data = history()
    by_date = {point["date"]: point for point in data["series"]}
    result = summarize_portfolio(data)
    self.assertEqual(result["prior_quarter_label"], "Q3 2026 return")
    expected = by_date["2026-09-30"]["portfolio_index"] / by_date["2026-06-30"]["portfolio_index"] - 1
    self.assertAlmostEqual(result["prior_quarter_return"], expected)

  def test_partial_start_quarter_has_no_completed_return(self):
    result = summarize_portfolio(history(start=date(2026, 7, 2)))
    self.assertIsNone(result["prior_quarter_return"])

  def test_zero_capital_gap_excludes_quarter(self):
    data = history()
    next(point for point in data["series"] if point["date"] == "2026-08-03")["return_observed"] = False
    self.assertIsNone(summarize_portfolio(data)["prior_quarter_return"])

  def test_three_year_return_is_cumulative_twr(self):
    data = history()
    result = summarize_portfolio(data)
    self.assertEqual(result["long_return_label"], "3-year return")
    self.assertAlmostEqual(result["long_return"], data["series"][-1]["portfolio_index"] / 100 - 1)
    self.assertNotEqual(result["long_return"], data["metrics"]["simple_return"])

  def test_short_history_fallback_names_actual_period(self):
    data = history(start=date(2025, 11, 6))
    result = summarize_portfolio(data)
    self.assertEqual(result["long_return_label"], "Available-period return")
    self.assertAlmostEqual(result["long_return"], data["metrics"]["total_return"])
    self.assertIn("2025-11-06", result["return_note"])

  def test_live_endpoint_is_not_presented_as_three_completed_years(self):
    data = history()
    data["series"][-1]["session_complete"] = False
    result = summarize_portfolio(data)
    self.assertTrue(result["return_is_provisional"])
    self.assertEqual(result["long_return_label"], "Available-period return · live")
    self.assertIsNotNone(result["prior_quarter_return"])

  def test_completed_quarter_end_can_use_own_quarter(self):
    result = summarize_portfolio(history(end=date(2026, 9, 30)))
    self.assertEqual(result["prior_quarter_label"], "Q3 2026 return")
    self.assertIsNotNone(result["prior_quarter_return"])

  def test_provisional_quarter_end_does_not_use_its_partial_quarter(self):
    data = history(end=date(2026, 9, 30))
    data["series"][-1]["session_complete"] = False
    result = summarize_portfolio(data)
    self.assertEqual(result["prior_quarter_label"], "Q2 2026 return")

  def test_leap_day_anniversary(self):
    result = summarize_portfolio(history(start=date(2021, 2, 26), end=date(2024, 2, 29)))
    self.assertEqual(result["long_return_label"], "3-year return")

  def test_no_input_mutation(self):
    data = history()
    original = copy.deepcopy(data)
    summarize_portfolio(data)
    self.assertEqual(data, original)

  def test_empty_selection_and_no_history(self):
    empty = {"series": [], "metrics": {"market_value": 0, "holding_count": 0},
             "metadata": {"end_date": "2026-11-06"}, "sector_allocation": {"total_value": 0, "holdings": []}}
    result = summarize_portfolio(empty)
    self.assertEqual(result["value"], 0)
    self.assertIsNone(result["long_return"])
    self.assertIsNone(result["top20_weight"])
    self.assertIsNone(summarize_portfolio(None)["value"])


if __name__ == "__main__":
  unittest.main()

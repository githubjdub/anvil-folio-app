"""Quote identity, modern/legacy filters, and expected provider failures."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server_code"))
import finviz_sectors


def quote(symbol="MRP", sector="realestate", path="/screener/stocks"):
  return ('<title>%s - Company Stock Price and Quote</title>'
          '<h1 class="quote-header_ticker-wrapper_ticker" data-ticker="%s">%s</h1>'
          '<a class="quote-header_category" href="%s?v=111&amp;f=sec_%s">Sector</a>'
          % (symbol, symbol, symbol, path, sector))


class FinvizSectorTests(unittest.TestCase):
  def test_current_filters_in_screenshot_symbols(self):
    for symbol, key, label in (("MRP", "realestate", "Real Estate"),
                               ("SIRI", "communicationservices", "Communication Services"),
                               ("EG", "financial", "Financial")):
      self.assertEqual(finviz_sectors.parse_finviz_sector(quote(symbol, key), symbol), label)

  def test_legacy_quote_and_normalized_class_symbol(self):
    document = '<title>BRK.B - Company Stock Price and Quote</title><a href="screener.ashx?f=sec_financial">Financial</a>'
    self.assertEqual(finviz_sectors.parse_finviz_sector(document, "BRK-B"), "Financial")

  def test_different_ticker_or_ambiguous_quote_is_rejected(self):
    self.assertIsNone(finviz_sectors.parse_finviz_sector(quote("EG", "financial"), "MRP"))
    self.assertIsNone(finviz_sectors.parse_finviz_sector(
      quote() + '<h1 data-ticker="SIRI">SIRI</h1>', "MRP"))

  def test_challenge_page_and_unknown_sector_do_not_guess(self):
    self.assertIsNone(finviz_sectors.parse_finviz_sector('<title>Just a moment...</title>', "MRP"))
    self.assertIsNone(finviz_sectors.parse_finviz_sector(quote(sector="madeup"), "MRP"))

  def test_ambiguous_sector_filters_are_rejected(self):
    self.assertIsNone(finviz_sectors.parse_finviz_sector(
      quote() + '<a href="/screener/stocks?f=sec_technology">Technology</a>', "MRP"))

  def test_foreign_filter_link_does_not_classify(self):
    self.assertIsNone(finviz_sectors.parse_finviz_sector(
      quote().replace('/screener/stocks?', 'https://example.invalid/screener/stocks?'), "MRP"))

  def test_http_denial_is_an_expected_missing_classification(self):
    error = HTTPError(finviz_sectors.source_url("MRP"), 429, "Too many requests", {}, None)
    with patch.object(finviz_sectors.urllib.request, "urlopen", side_effect=error):
      self.assertIsNone(finviz_sectors.fetch_finviz_sector("MRP"))

  def test_unexpected_errors_propagate(self):
    with patch.object(finviz_sectors.urllib.request, "urlopen", side_effect=RuntimeError("unexpected")):
      with self.assertRaises(RuntimeError):
        finviz_sectors.fetch_finviz_sector("MRP")

  def test_invalid_symbol_is_not_sent_to_provider(self):
    with self.assertRaises(ValueError):
      finviz_sectors.fetch_finviz_sector("MRP&other=1")


if __name__ == "__main__":
  unittest.main()

from datetime import date, timedelta
import math

import anvil.server
from ._anvil_designer import Form1Template
from anvil import *


class Form1(Form1Template):
  def __init__(self, **properties):
    super().__init__(**properties)

    self.transactions = []
    self._set_dates()
    self._load_sample_transactions()
    self._show_results(False)

  def _set_dates(self):
    self.as_of_date_picker.date = date.today()

  def _new_transaction(self, ticker="", trade_date=None, action="Buy", shares=""):
    return {
      "ticker": ticker,
      "trade_date": trade_date,
      "action": action,
      "shares": shares,
      "entry_price": None,
      "current_price": None,
      "dividends_per_share": None,
      "quote_text": "Not synced",
      "dividend_text": "—",
    }

  def _load_sample_transactions(self):
    today = date.today()
    self.transactions = [
      self._new_transaction("AAPL", today - timedelta(days=365), "Buy", "20"),
      self._new_transaction("MSFT", today - timedelta(days=210), "Buy", "10"),
    ]
    self.holdings_panel.items = self.transactions
    self._set_status("Sample transactions loaded — sync to fetch live market data.")

  def _show_results(self, visible):
    self.results_section.visible = visible
    self.empty_results.visible = not visible

  def _set_status(self, message, is_error=False):
    self.status_label.text = message
    self.status_region.classes["is-hidden"] = not bool(message)
    self.status_region.classes["is-error"] = is_error

  def _parse_number(self, value, label, row_number):
    try:
      parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
      raise ValueError("Row %d: enter a valid %s." % (row_number, label))

    if math.isnan(parsed) or math.isinf(parsed):
      raise ValueError("Row %d: enter a valid %s." % (row_number, label))
    return parsed

  def _read_transactions(self):
    clean_transactions = []
    for row_number, row in enumerate(self.transactions, start=1):
      values = [
        row.get("ticker", ""),
        row.get("trade_date"),
        row.get("shares", ""),
      ]
      if not any(str(value).strip() for value in values if value is not None):
        continue

      ticker = str(row.get("ticker", "")).strip().upper()
      if not ticker:
        raise ValueError("Row %d: add a ticker symbol." % row_number)

      trade_date = row.get("trade_date")
      if trade_date is None:
        raise ValueError("Row %d: choose the date bought or sold." % row_number)

      action = str(row.get("action", "Buy")).strip().title()
      if action not in ("Buy", "Sell"):
        raise ValueError("Row %d: choose Buy or Sell." % row_number)

      shares = self._parse_number(row.get("shares"), "share count", row_number)
      if shares <= 0:
        raise ValueError("Row %d: shares must be greater than zero." % row_number)

      row.update({
        "ticker": ticker,
        "trade_date": trade_date,
        "action": action,
        "shares": shares,
      })
      clean_transactions.append(row)

    if not clean_transactions:
      raise ValueError("Add at least one transaction before running the analysis.")
    return clean_transactions

  def _sync_market_data(self, transactions, as_of_date):
    for row_number, transaction in enumerate(transactions, start=1):
      self._set_status(
        "Looking up %s in Google Finance (%d/%d)…"
        % (transaction["ticker"], row_number, len(transactions))
      )
      result = anvil.server.call(
        "lookup_market_data",
        transaction["ticker"],
        transaction["trade_date"],
        as_of_date,
      )
      if not result.get("ok"):
        raise ValueError("Row %d: %s" % (row_number, result.get("message", "Lookup failed.")))

      transaction.update({
        "entry_price": result["entry_price"],
        "current_price": result["current_price"],
        "dividends_per_share": result["dividends_per_share"],
        "quote_text": "%s %s" % (
          result["currency"], self._format_currency(result["current_price"])
        ),
        "dividend_text": "%s / share" % self._format_currency(
          result["dividends_per_share"]
        ),
        "company_name": result["company_name"],
        "entry_date_used": result["entry_date_used"],
        "price_source": result["price_source"],
        "history_source": result["history_source"],
      })

    self.holdings_panel.items = self.transactions

  def _format_currency(self, value):
    return "$%s" % format(value, ",.2f")

  def _format_percent(self, value):
    return "%+.2f%%" % (value * 100)

  def _format_ratio(self, value):
    return "—" if value is None else "%+.2f" % value

  def _calculate_metrics(self, transactions, as_of_date, risk_free_rate):
    buy_cost = 0.0
    sale_proceeds = 0.0
    dividends = 0.0
    net_shares = {}
    current_prices = {}
    position_returns = []
    first_trade_date = min(transaction["trade_date"] for transaction in transactions)

    for transaction in transactions:
      ticker = transaction["ticker"]
      shares = transaction["shares"]
      entry_price = transaction["entry_price"]
      current_price = transaction["current_price"]
      if entry_price is None or current_price is None:
        raise ValueError("Sync market data before running the analysis.")

      current_prices[ticker] = current_price
      if transaction["action"] == "Buy":
        buy_cost += shares * entry_price
        dividends += shares * transaction["dividends_per_share"]
        net_shares[ticker] = net_shares.get(ticker, 0) + shares
        position_returns.append((
          shares * entry_price,
          (current_price + transaction["dividends_per_share"]) / entry_price - 1,
        ))
      else:
        sale_proceeds += shares * entry_price
        net_shares[ticker] = net_shares.get(ticker, 0) - shares

    for ticker, shares in net_shares.items():
      if shares < 0:
        raise ValueError("More %s shares are sold than bought." % ticker)

    market_value = sum(
      shares * current_prices[ticker] for ticker, shares in net_shares.items()
    )
    profit = market_value + sale_proceeds + dividends - buy_cost
    total_return = profit / buy_cost if buy_cost else 0.0
    days = max((as_of_date - first_trade_date).days, 1)
    years = max(days / 365.25, 1 / 365.25)
    annualized_return = (1 + total_return) ** (1 / years) - 1

    estimated_sharpe = None
    annualized_volatility = 0.0
    if position_returns:
      weighted_mean = sum(weight * value for weight, value in position_returns) / buy_cost
      dispersion = sum(
        (weight / buy_cost) * (value - weighted_mean) ** 2
        for weight, value in position_returns
      )
      annualized_volatility = math.sqrt(dispersion) / math.sqrt(years)
      if annualized_volatility > 0:
        estimated_sharpe = (annualized_return - risk_free_rate) / annualized_volatility

    return {
      "buy_cost": buy_cost,
      "sale_proceeds": sale_proceeds,
      "market_value": market_value,
      "dividends": dividends,
      "total_return": total_return,
      "annualized_return": annualized_return,
      "annualized_volatility": annualized_volatility,
      "estimated_sharpe": estimated_sharpe,
      "days": days,
      "holding_count": len([shares for shares in net_shares.values() if shares > 0]),
    }

  @handle("add_button", "click")
  def add_button_click(self, **event_args):
    self.transactions.append(self._new_transaction())
    self.holdings_panel.items = self.transactions
    self._set_status("New transaction added — enter its ticker, date, action, and shares.")

  @handle("sample_button", "click")
  def sample_button_click(self, **event_args):
    self._load_sample_transactions()
    self._show_results(False)

  @handle("holdings_panel", "x-remove-holding")
  def holdings_panel_remove_holding(self, item, **event_args):
    if item in self.transactions:
      self.transactions.remove(item)
      self.holdings_panel.items = self.transactions
      self._set_status("Transaction removed.")

  @handle("analyze_button", "click")
  def analyze_button_click(self, **event_args):
    try:
      transactions = self._read_transactions()
      as_of_date = self.as_of_date_picker.date
      if as_of_date is None:
        raise ValueError("Choose an analysis date.")
      if any(transaction["trade_date"] > as_of_date for transaction in transactions):
        raise ValueError("Every transaction must be on or before the analysis date.")

      risk_free_rate = self._parse_number(
        self.risk_free_box.text, "risk-free rate", 1
      ) / 100
      self._sync_market_data(transactions, as_of_date)
      metrics = self._calculate_metrics(transactions, as_of_date, risk_free_rate)

      self.invested_label.text = self._format_currency(metrics["buy_cost"])
      self.value_label.text = self._format_currency(metrics["market_value"])
      self.return_label.text = self._format_percent(metrics["total_return"])
      self.annualized_label.text = self._format_percent(metrics["annualized_return"])
      self.dividends_label.text = self._format_currency(metrics["dividends"])
      self.volatility_label.text = self._format_percent(metrics["annualized_volatility"])
      self.sharpe_label.text = self._format_ratio(metrics["estimated_sharpe"])
      self.period_label.text = "%d days · %d open holdings" % (
        metrics["days"], metrics["holding_count"]
      )
      self._set_status("Live prices synced and analysis updated.")
      self._show_results(True)
    except ValueError as error:
      self._set_status(str(error), is_error=True)
      self._show_results(False)

from datetime import date, timedelta
import math

import anvil.server
import anvil.users
from ._anvil_designer import Form1Template
from anvil import *


class Form1(Form1Template):
  def __init__(self, **properties):
    super().__init__(**properties)

    self._user = None
    self._current_portfolio = None
    self._portfolio_dirty = False
    self._busy = False
    self.transactions = []
    self._set_dates()
    self._load_sample_transactions()
    self._show_results(False)
    self._refresh_account()

  def _set_portfolio_status(self, message, is_error=False):
    self.portfolio_status_label.text = message
    self.portfolio_status_region.classes["is-error"] = is_error

  def _update_controls(self):
    for component in (
      self.account_button, self.save_portfolio_button, self.new_portfolio_button,
      self.portfolio_name_box, self.add_button, self.sample_button,
      self.analyze_button, self.as_of_date_picker, self.risk_free_box,
    ):
      setattr(component, "enabled", not self._busy)
    self.saved_portfolios_dropdown.enabled = not self._busy and self._user is not None
    setattr(self.open_portfolio_button, "enabled", (
      not self._busy and self.saved_portfolios_dropdown.selected_value is not None
    ))
    self.save_portfolio_button.text = (
      "Save changes" if self._current_portfolio is not None else "Save portfolio"
    )
    self.holdings_panel.raise_event_on_children("x-set-editable", editable=not self._busy)

  def _set_busy(self, busy):
    self._busy = busy
    self._update_controls()

  def _refresh_portfolios(self):
    portfolios = anvil.server.call("list_portfolios") if self._user is not None else []
    self.saved_portfolios_dropdown.items = [
      (portfolio["name"], portfolio) for portfolio in portfolios
    ]
    self.saved_portfolios_dropdown.placeholder = (
      "Choose a saved portfolio" if portfolios else "No saved portfolios yet"
    )
    current_id = self._current_portfolio.get_id() if self._current_portfolio is not None else None
    self.saved_portfolios_dropdown.selected_value = next(
      (portfolio for portfolio in portfolios if portfolio.get_id() == current_id), None
    )
    self._update_controls()

  def _refresh_account(self):
    self._user = anvil.users.get_user()
    self.account_button.text = "Sign out" if self._user is not None else "Sign in / Create account"
    self.account_label.text = (
      "Signed in as %s · Only you can access your portfolios." % self._user["email"]
      if self._user is not None
      else "Sign in to save your portfolios privately and reopen them on any device."
    )
    self._refresh_portfolios()

  def _ensure_signed_in(self):
    if anvil.users.get_user() is None:
      user = anvil.users.login_with_form(allow_cancel=True)
      if user is None:
        self._set_portfolio_status("Sign-in canceled. Your unsaved portfolio is still here.")
        return False
    self._refresh_account()
    return True

  def _mark_portfolio_dirty(self):
    self._portfolio_dirty = True
    self._set_portfolio_status("Unsaved changes — save your portfolio to keep them.")

  def _confirm_discard(self):
    return not self._portfolio_dirty or confirm(
      "You have unsaved portfolio changes. Discard them and continue?",
      title="Unsaved changes",
      buttons=[("Keep editing", False), ("Discard changes", True)],
    )

  def _new_portfolio(self):
    self._current_portfolio = None
    self.portfolio_name_box.text = ""
    self.transactions = [self._new_transaction()]
    self.holdings_panel.items = self.transactions
    self._set_dates()
    self.risk_free_box.text = "4.50"
    self.saved_portfolios_dropdown.selected_value = None
    self._portfolio_dirty = False
    self._show_results(False)
    self._set_status("Add transactions, then save your portfolio or sync prices.")
    self._set_portfolio_status("New portfolio — enter a name before saving.")
    self._update_controls()

  def _read_analysis_settings(self, transactions):
    as_of_date = self.as_of_date_picker.date
    if as_of_date is None:
      raise ValueError("Choose an analysis date.")
    if any(transaction["trade_date"] > as_of_date for transaction in transactions):
      raise ValueError("Every transaction must be on or before the analysis date.")
    risk_free_rate = self._parse_number(self.risk_free_box.text, "risk-free rate", 1)
    return as_of_date, risk_free_rate

  @handle("account_button", "click")
  def account_button_click(self, **event_args):
    if self._busy:
      return
    if self._user is not None and not self._confirm_discard():
      return
    self._set_busy(True)
    try:
      if self._user is None:
        if self._ensure_signed_in():
          self._set_portfolio_status("Signed in. Open a saved portfolio or save your current portfolio.")
      else:
        anvil.users.logout(invalidate_client_objects=True)
        self._new_portfolio()
        self._refresh_account()
        self._set_portfolio_status("Signed out. Your saved portfolios are kept in your account.")
    finally:
      self._set_busy(False)

  @handle("portfolio_name_box", "change")
  def portfolio_name_box_change(self, **event_args):
    self._mark_portfolio_dirty()

  @handle("as_of_date_picker", "change")
  @handle("risk_free_box", "change")
  @handle("holdings_panel", "x-holding-changed")
  def portfolio_inputs_change(self, **event_args):
    self._mark_portfolio_dirty()
    self._show_results(False)

  @handle("saved_portfolios_dropdown", "change")
  def saved_portfolios_dropdown_change(self, **event_args):
    self._update_controls()

  @handle("new_portfolio_button", "click")
  def new_portfolio_button_click(self, **event_args):
    if not self._busy and self._confirm_discard():
      self._new_portfolio()

  @handle("save_portfolio_button", "click")
  def save_portfolio_button_click(self, **event_args):
    if self._busy:
      return
    try:
      name = str(self.portfolio_name_box.text or "").strip()
      if not 1 <= len(name) <= 80:
        raise ValueError("Give your portfolio a name of 1–80 characters.")
      transactions = self._read_transactions()
      as_of_date, risk_free_rate = self._read_analysis_settings(transactions)
      document = [{
        "ticker": transaction["ticker"],
        "trade_date": transaction["trade_date"].isoformat(),
        "action": transaction["action"],
        "shares": transaction["shares"],
      } for transaction in transactions]
    except ValueError as error:
      self._set_portfolio_status(str(error), is_error=True)
      return

    self._set_busy(True)
    try:
      if not self._ensure_signed_in():
        return
      self._set_portfolio_status("Saving portfolio…")
      portfolio_id = self._current_portfolio.get_id() if self._current_portfolio is not None else None
      result = anvil.server.call(
        "save_portfolio", portfolio_id, name, document, as_of_date, risk_free_rate
      )
      if not result["ok"]:
        self._set_portfolio_status(result["message"], is_error=True)
        return
      self._current_portfolio = result["portfolio"]
      self.portfolio_name_box.text = self._current_portfolio["name"]
      self._portfolio_dirty = False
      self._set_portfolio_status('Saved "%s" to your account.' % self._current_portfolio["name"])
      self._refresh_portfolios()
    finally:
      self._set_busy(False)

  @handle("open_portfolio_button", "click")
  def open_portfolio_button_click(self, **event_args):
    portfolio = self.saved_portfolios_dropdown.selected_value
    if self._busy or portfolio is None or not self._confirm_discard():
      return
    self._set_busy(True)
    try:
      result = anvil.server.call("load_portfolio", portfolio.get_id())
      if not result["ok"]:
        self._set_portfolio_status(result["message"], is_error=True)
        return
      portfolio = result["portfolio"]
      self._current_portfolio = portfolio
      self.portfolio_name_box.text = portfolio["name"]
      self.as_of_date_picker.date = portfolio["as_of_date"]
      self.risk_free_box.text = str(portfolio["risk_free_rate"])
      self.transactions = [self._new_transaction(
        transaction["ticker"],
        date(*[int(part) for part in transaction["trade_date"].split("-")]),
        transaction["action"],
        str(transaction["shares"]),
      ) for transaction in portfolio["transactions"]]
      self.holdings_panel.items = self.transactions
      self._portfolio_dirty = False
      self._show_results(False)
      self._set_portfolio_status('Opened "%s". Changes are saved when you click Save changes.' % portfolio["name"])
      self._set_status("Saved transactions loaded — sync to fetch current market data.")
    finally:
      self._set_busy(False)

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
      raise ValueError("Add at least one transaction before saving or running the analysis.")
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
    self._mark_portfolio_dirty()
    self._show_results(False)
    self._set_status("New transaction added — enter its ticker, date, action, and shares.")

  @handle("sample_button", "click")
  def sample_button_click(self, **event_args):
    if not self._confirm_discard():
      return
    self._current_portfolio = None
    self.portfolio_name_box.text = "Sample portfolio"
    self.saved_portfolios_dropdown.selected_value = None
    self._load_sample_transactions()
    self._mark_portfolio_dirty()
    self._show_results(False)
    self._update_controls()

  @handle("holdings_panel", "x-remove-holding")
  def holdings_panel_remove_holding(self, item, **event_args):
    if item in self.transactions:
      self.transactions.remove(item)
      self.holdings_panel.items = self.transactions
      self._mark_portfolio_dirty()
      self._show_results(False)
      self._set_status("Transaction removed.")

  @handle("analyze_button", "click")
  def analyze_button_click(self, **event_args):
    if self._busy:
      return
    self._set_busy(True)
    try:
      transactions = self._read_transactions()
      as_of_date, risk_free_rate = self._read_analysis_settings(transactions)
      self._sync_market_data(transactions, as_of_date)
      metrics = self._calculate_metrics(transactions, as_of_date, risk_free_rate / 100)

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
    finally:
      self._set_busy(False)

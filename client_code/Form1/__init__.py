from datetime import date, timedelta
import math

import anvil.server
import anvil.users
from ._anvil_designer import Form1Template
from anvil import *
from ..analysis_selection import analyze_selection


SECTOR_COLORS = {
  "Technology": "#2c9b67", "Financial": "#466b9f",
  "Healthcare": "#8463ac", "Consumer Cyclical": "#df8054",
  "Consumer Defensive": "#b09437", "Communication Services": "#2e9da4",
  "Industrials": "#607386", "Energy": "#b55269",
  "Basic Materials": "#7d9446", "Real Estate": "#ad729c",
  "Utilities": "#64a1be", "Funds / ETFs": "#1d3344", "Unclassified": "#a0a6ad",
}

BENCHMARK_CHOICES = [
  ("S&P 500 index", "sp500"), ("Nasdaq Composite index", "nasdaq"),
  ("SPY · S&P 500 ETF", "spy"), ("QQQ · Nasdaq-100 ETF", "qqq"),
  ("DIA · Dow Jones ETF", "dia"), ("IWM · Russell 2000 ETF", "iwm"),
  ("VTI · Total US market ETF", "vti"),
]


class Form1(Form1Template):
  def __init__(self, **properties):
    super().__init__(**properties)

    self._user = None
    self._current_portfolio = None
    self._portfolio_dirty = False
    self._busy = False
    self.transactions = []
    self._history = None
    self._analysis_source = None
    self._selected_symbols = set()
    self._market_status_loading = False
    self.transaction_view_dropdown.items = [("Entry order", "entry"), ("Group by security", "security")]
    self.transaction_view_dropdown.selected_value = "entry"
    self.regroup_button.visible = False
    self.timeframe_dropdown.items = [
      ("1 month", "1M"), ("3 months", "3M"), ("6 months", "6M"),
      ("Year to date", "YTD"), ("1 year", "1Y"), ("3 years", "3Y"),
      ("All history", "ALL"), ("Custom dates", "CUSTOM"),
    ]
    self.timeframe_dropdown.selected_value = "ALL"
    self.benchmark_dropdown.items = BENCHMARK_CHOICES
    self.benchmark_dropdown.selected_value = "sp500"
    self.benchmark_return_dropdown.items = [
      ("Total return · dividends reinvested", "total"),
      ("Price return · excludes dividends", "price"),
    ]
    self.benchmark_return_dropdown.selected_value = "total"
    self.custom_period_region.visible = False
    self._set_dates()
    self._load_sample_transactions()
    self._show_results(False)
    self._refresh_account()

  @handle("market_status_timer", "tick")
  def market_status_timer_tick(self, **event_args):
    if self._market_status_loading:
      return
    self._market_status_loading = True
    try:
      status = anvil.server.call_s("get_us_market_status")
      self.market_status_label.text = status["label"]
      self.market_detail_label.text = status["detail"]
      self.market_status_label.tooltip = "%s\nChecked %s\n%s" % (
        status["scope"], status["checked_at"], status["schedule_source"]
      )
      for state in ("open", "closed", "unavailable"):
        self.market_status_region.classes["is-" + state] = status["state"] == state
    finally:
      self._market_status_loading = False
      self.market_status_timer.interval = 60

  def _security_key(self, transaction):
    parts = str(transaction.get("ticker") or "").strip().upper().split(":", 1)
    symbol = parts[0].strip().replace(".", "-")
    exchange = parts[1].strip() if len(parts) == 2 else "NASDAQ"
    if exchange in ("NASDAQ", "NYSE", "NYSEARCA", "AMEX", "BATS", "NYSEAMERICAN"):
      return symbol
    return "%s:%s" % (symbol, exchange) if symbol else ""

  def _refresh_transactions(self):
    grouped = self.transaction_view_dropdown.selected_value == "security"
    self.regroup_button.visible = grouped
    for row in self.transactions:
      row["_security_group"] = ""
      row["_security_group_detail"] = ""
    visible_rows = list(self.transactions)
    if grouped:
      groups = {}
      for row in self.transactions:
        groups.setdefault(self._security_key(row), []).append(row)
      visible_rows = []
      for key in sorted(groups, key=lambda value: (not bool(value), value)):
        group = groups[key]
        buys = sum(1 for row in group if row.get("action", "Buy") == "Buy")
        sells = len(group) - buys
        group[0]["_security_group"] = key or "Unassigned security"
        group[0]["_security_group_detail"] = "%d transaction%s · %d buy%s · %d sell%s" % (
          len(group), "" if len(group) == 1 else "s", buys, "" if buys == 1 else "s",
          sells, "" if sells == 1 else "s",
        )
        visible_rows.extend(group)
    self.holdings_panel.items = visible_rows
    self.holdings_panel.raise_event_on_children("x-set-editable", editable=not self._busy)
    self.transaction_view_help.text = (
      "Trades are grouped by ticker. Use Regroup after editing a ticker or action."
      if grouped else "Transactions appear in the order you entered them."
    )

  @handle("transaction_view_dropdown", "change")
  @handle("regroup_button", "click")
  def transaction_view_change(self, **event_args):
    if not self._busy:
      self._refresh_transactions()

  @handle("collapse_all_button", "click")
  def collapse_all_button_click(self, **event_args):
    if not self._busy:
      self.holdings_panel.raise_event_on_children("x-set-collapsed", collapsed=True)

  @handle("expand_all_button", "click")
  def expand_all_button_click(self, **event_args):
    if not self._busy:
      self.holdings_panel.raise_event_on_children("x-set-collapsed", collapsed=False)

  def _show_entry_order_for_error(self, message):
    # Validation row numbers refer to the original transaction order.
    if message.startswith("Row ") and self.transaction_view_dropdown.selected_value == "security":
      self.transaction_view_dropdown.selected_value = "entry"
      self._refresh_transactions()

  def _set_portfolio_status(self, message, is_error=False):
    if is_error:
      self._show_entry_order_for_error(message)
    self.portfolio_status_label.text = message
    self.portfolio_status_region.classes["is-error"] = is_error

  def _update_controls(self):
    for component in (
      self.account_button, self.save_portfolio_button, self.new_portfolio_button,
      self.portfolio_name_box, self.add_button, self.sample_button,
      self.analyze_button, self.as_of_date_picker, self.risk_free_box,
      self.timeframe_dropdown, self.benchmark_dropdown,
      self.benchmark_return_dropdown, self.select_all_securities_button,
      self.comparison_start_picker, self.comparison_end_picker,
      self.transaction_view_dropdown, self.regroup_button,
      self.collapse_all_button, self.expand_all_button,
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
    self.security_panel.raise_event_on_children("x-set-editable", editable=not self._busy)
    self.benchmark_return_dropdown.enabled = not self._busy and len(self.benchmark_return_dropdown.items) > 1

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
    self._refresh_transactions()
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
    if risk_free_rate <= -100:
      raise ValueError("The annual risk-free rate must be greater than -100%.")
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
        "trade_price": transaction["trade_price"],
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
        transaction.get("trade_price"),
      ) for transaction in portfolio["transactions"]]
      self._refresh_transactions()
      self._portfolio_dirty = False
      self._show_results(False)
      self._set_portfolio_status('Opened "%s". Changes are saved when you click Save changes.' % portfolio["name"])
      self._set_status("Saved transactions loaded — sync to fetch daily market history.")
    finally:
      self._set_busy(False)

  def _set_dates(self):
    self.as_of_date_picker.date = date.today()

  def _new_transaction(self, ticker="", trade_date=None, action="Buy", shares="", trade_price=None):
    return {
      "ticker": ticker,
      "trade_date": trade_date,
      "action": action,
      "shares": shares,
      "trade_price": trade_price,
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
    self._refresh_transactions()
    self._set_status("Sample transactions loaded — sync to fetch daily market history.")

  def _show_results(self, visible):
    if not visible:
      self._history = None
      self._analysis_source = None
      self._selected_symbols = set()
    self.results_section.visible = visible
    self.empty_results.visible = not visible

  def _set_status(self, message, is_error=False):
    if is_error:
      self._show_entry_order_for_error(message)
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
        row.get("trade_price"),
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

      trade_price = row.get("trade_price")
      if trade_price is None or not str(trade_price).strip():
        trade_price = None
      else:
        trade_price = self._parse_number(trade_price, "price per share", row_number)
        if trade_price <= 0:
          raise ValueError("Row %d: price per share must be greater than zero." % row_number)

      row.update({
        "ticker": ticker,
        "trade_date": trade_date,
        "action": action,
        "shares": shares,
        "trade_price": trade_price,
      })
      clean_transactions.append(row)

    if not clean_transactions:
      raise ValueError("Add at least one transaction before saving or running the analysis.")
    return clean_transactions

  def _format_currency(self, value):
    return "$%s" % format(value, ",.2f")

  def _format_percent(self, value):
    return "%+.2f%%" % (value * 100)

  def _format_ratio(self, value):
    return "—" if value is None else "%+.2f" % value

  def _date_from_iso(self, value):
    return date(*[int(part) for part in value.split("-")])

  def _months_before(self, value, months):
    month_index = value.year * 12 + value.month - 1 - months
    year, month = month_index // 12, month_index % 12 + 1
    next_month = date(year + (1 if month == 12 else 0), month % 12 + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(value.day, last_day))

  def _daily_risk(self, indices, observed):
    if self._history is None:
      return None, None
    daily_returns = [
      indices[index] / indices[index - 1] - 1
      for index in range(1, len(indices)) if observed[index]
    ]
    if len(daily_returns) < 20:
      return None, None
    daily_rate = (1 + self._history["metadata"]["risk_free_rate"]) ** (1 / 252) - 1
    excess_returns = [value - daily_rate for value in daily_returns]
    mean_excess = sum(excess_returns) / len(excess_returns)
    variance = sum((value - mean_excess) ** 2 for value in excess_returns) / (len(excess_returns) - 1)
    daily_volatility = math.sqrt(variance)
    volatility = daily_volatility * math.sqrt(252)
    sharpe = mean_excess / daily_volatility * math.sqrt(252) if daily_volatility > 1e-12 else None
    return volatility, sharpe

  def _chart_layout(self):
    return {
      "margin": {"l": 48, "r": 12, "t": 15, "b": 62},
      "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
      "font": {"family": "Inter, sans-serif", "size": 11, "color": "#6d7881"},
      "hovermode": "x unified",
      "legend": {"orientation": "h", "x": 0, "y": -0.22},
      "xaxis": {"type": "date", "showgrid": False, "fixedrange": True},
      "yaxis": {"tickformat": ".0%", "gridcolor": "#edf0f1", "zerolinecolor": "#c6d2cb", "fixedrange": True},
    }

  def _set_chart(self, plot, dates, portfolio, benchmark, benchmark_name):
    plot.data = [
      {"type": "scatter", "mode": "lines", "name": "Your portfolio", "x": dates,
       "y": portfolio, "line": {"color": "#2c9b67", "width": 2.5},
       "hovertemplate": "%{y:.2%}<extra>Your portfolio</extra>"},
      {"type": "scatter", "mode": "lines", "name": benchmark_name, "x": dates,
       "y": benchmark, "line": {"color": "#bd603f", "width": 2},
       "hovertemplate": "%{y:.2%}<extra>" + benchmark_name + "</extra>"},
    ]
    plot.layout = self._chart_layout()
    plot.config = {"displayModeBar": False, "responsive": True}

  def _drawdowns(self, indices):
    peak = indices[0]
    values = []
    for value in indices:
      peak = max(peak, value)
      values.append(value / peak - 1)
    return values

  def _clear_comparison(self, message):
    self.comparison_period_label.text = message
    for label in (self.window_return_label, self.benchmark_return_label,
                  self.volatility_label, self.sharpe_label,
                  self.benchmark_volatility_label, self.benchmark_sharpe_label):
      label.text = "—"
    self.risk_status_label.text = ""
    self.performance_plot.data = []
    self.drawdown_plot.data = []

  def _update_benchmark_options(self):
    if self._analysis_source is None:
      return
    benchmarks = self._analysis_source["benchmarks"]
    available = [(label, key) for label, key in BENCHMARK_CHOICES
                 if benchmarks.get(key + "_price", {}).get("series")
                 or benchmarks.get(key + "_total", {}).get("series")]
    selected = self.benchmark_dropdown.selected_value
    self.benchmark_dropdown.items = available
    available_keys = [key for label, key in available]
    self.benchmark_dropdown.selected_value = selected if selected in available_keys else "sp500"
    selected = self.benchmark_dropdown.selected_value
    modes = []
    for label, mode in [("Total return · dividends reinvested", "total"),
                        ("Price return · excludes dividends", "price")]:
      if benchmarks.get(selected + "_" + mode, {}).get("series"):
        modes.append((label, mode))
    previous_mode = self.benchmark_return_dropdown.selected_value
    self.benchmark_return_dropdown.items = modes
    self.benchmark_return_dropdown.selected_value = (
      previous_mode if previous_mode in [mode for label, mode in modes]
      else modes[0][1]
    )
    self.benchmark_return_dropdown.enabled = not self._busy and len(modes) > 1

  def _render_comparison(self):
    if self._history is None:
      return
    series = self._history["series"]
    if not series:
      self._clear_comparison("Select at least one security to compare returns.")
      return
    timeframe = self.timeframe_dropdown.selected_value or "ALL"
    self.custom_period_region.visible = timeframe == "CUSTOM"
    end = self._date_from_iso(series[-1]["date"])
    start = self._date_from_iso(series[0]["date"])
    if timeframe == "CUSTOM":
      start = self.comparison_start_picker.date
      end = self.comparison_end_picker.date
      if start is None or end is None or start > end:
        self._clear_comparison("Choose a start date on or before the end date.")
        return
    elif timeframe == "YTD":
      start = date(end.year, 1, 1)
    elif timeframe != "ALL":
      months = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36}[timeframe]
      start = self._months_before(end, months)
    selected = [point for point in series if start.isoformat() <= point["date"] <= end.isoformat()]
    if not selected:
      self._clear_comparison("No portfolio history is available in this period.")
      return
    benchmark_key = "%s_%s" % (self.benchmark_dropdown.selected_value,
                               self.benchmark_return_dropdown.selected_value)
    benchmark_data = self._history["benchmarks"][benchmark_key]
    benchmark_name = benchmark_data["name"]
    benchmark_by_date = {point["date"]: point["index"] for point in benchmark_data["series"]}
    available_dates = sorted(benchmark_by_date)
    common = [point for point in selected
              if available_dates[0] <= point["date"] <= available_dates[-1]]
    if not common:
      self.benchmark_status_label.text = "This benchmark has no history in the selected period. Choose another benchmark or time frame."
      self._clear_comparison("No overlapping portfolio and benchmark dates.")
      return
    if any(point["date"] not in benchmark_by_date for point in common):
      self.benchmark_status_label.text = "This benchmark has missing daily closes in the selected period. Choose another benchmark."
      self._clear_comparison("Incomplete benchmark history; no return was estimated.")
      return
    notice = "Nasdaq Composite is a price index; it excludes dividends." if benchmark_key == "nasdaq_price" else ""
    if len(common) < len(selected):
      notice += " Both lines use their overlapping dates: %s – %s." % (common[0]["date"], common[-1]["date"])
    missing = [label for label, key in BENCHMARK_CHOICES
               if not self._history["benchmarks"].get(key + "_price", {}).get("series")
               and not self._history["benchmarks"].get(key + "_total", {}).get("series")]
    if missing:
      notice += " Unavailable this sync: %s." % ", ".join(missing)
    self.benchmark_status_label.text = notice.strip()
    selected = common
    dates = [point["date"] for point in selected]
    portfolio = [point["portfolio_index"] for point in selected]
    benchmark = [benchmark_by_date[point["date"]] for point in selected]
    portfolio_returns = [value / portfolio[0] - 1 for value in portfolio]
    benchmark_returns = [value / benchmark[0] - 1 for value in benchmark]
    observed = [point["return_observed"] for point in selected]
    observations = sum(1 for available in observed[1:] if available)
    inactive_days = len(selected) - 1 - observations
    self.comparison_period_label.text = "%s – %s · %d daily returns" % (dates[0], dates[-1], observations)
    self.window_return_label.text = self._format_percent(portfolio_returns[-1])
    self.benchmark_return_label.text = self._format_percent(benchmark_returns[-1])
    volatility, sharpe = self._daily_risk(portfolio, observed)
    self.volatility_label.text = "—" if volatility is None else "%.2f%%" % (volatility * 100)
    self.sharpe_label.text = self._format_ratio(sharpe)
    # A benchmark stays invested across every close, including sessions when
    # the selected portfolio had no capital. Use its own observation mask.
    benchmark_observations = len(benchmark) - 1
    benchmark_volatility, benchmark_sharpe = self._daily_risk(benchmark, [True] * len(benchmark))
    self.benchmark_volatility_label.text = "—" if benchmark_volatility is None else "%.2f%%" % (benchmark_volatility * 100)
    self.benchmark_sharpe_label.text = self._format_ratio(benchmark_sharpe)
    if observations < 20:
      message = "At least 20 daily returns are needed for volatility and Sharpe; this period has %d." % observations
    elif sharpe is None:
      message = "No measurable daily variation in this period; Sharpe is undefined."
    elif observations < 252:
      message = "Short sample: risk estimates use less than one trading year."
    else:
      message = "Risk estimates use daily returns from the selected period."
    if inactive_days:
      message += " %d sessions with no invested capital are excluded from portfolio risk metrics; the portfolio line stays flat." % inactive_days
    message += " Benchmark risk uses %d daily returns over the same dates and the same risk-free rate." % benchmark_observations
    if benchmark_observations < 20:
      message += " Benchmark volatility and Sharpe need at least 20 daily returns."
    elif benchmark_sharpe is None:
      message += " The benchmark has no measurable daily variation; its Sharpe is undefined."
    self.risk_status_label.text = message + " Yahoo Finance · 252 trading days/year."
    self._set_chart(self.performance_plot, dates, portfolio_returns, benchmark_returns, benchmark_name)
    self._set_chart(self.drawdown_plot, dates, self._drawdowns(portfolio), self._drawdowns(benchmark), benchmark_name)

  def _render_sectors(self):
    if self._history is None:
      return
    allocation = self._history["sector_allocation"]
    colors = SECTOR_COLORS
    slices = allocation["slices"]
    self.sector_period_label.text = "%s · %s in open positions" % (
      allocation["as_of"], self._format_currency(allocation["total_value"])
    )
    self.sector_empty_label.visible = not bool(slices)
    self.sector_empty_label.text = (
      "Select a security below to see its allocation." if not self._selected_symbols
      else "The selected securities have no open positions at the analysis date. Closed trades and dividends still contribute to returns."
    )
    self.sector_plot.visible = bool(slices)
    self.sector_panel.items = [dict(group,
      color=colors.get(group["sector"], "#a0a6ad"),
      tickers_text=", ".join(group["tickers"]),
      weight_text="%.1f%%" % (group["weight"] * 100),
      value_text=self._format_currency(group["market_value"]),
    ) for group in slices]
    self.sector_plot.data = [{
      "type": "pie", "hole": 0.48, "sort": False, "direction": "clockwise",
      "labels": [group["sector"] for group in slices],
      "values": [group["market_value"] for group in slices],
      "customdata": [", ".join(group["tickers"]) for group in slices],
      "marker": {"colors": [colors.get(group["sector"], "#a0a6ad") for group in slices],
                 "line": {"color": "#ffffff", "width": 2}},
      "textinfo": "percent", "textposition": "inside",
      "hovertemplate": "%{label}: %{percent}<br>$%{value:,.2f}<br>%{customdata}<extra></extra>",
    }] if slices else []
    self.sector_plot.layout = {
      "margin": {"l": 10, "r": 10, "t": 15, "b": 15}, "showlegend": False,
      "paper_bgcolor": "rgba(0,0,0,0)", "font": {"family": "Inter, sans-serif", "size": 12},
    }
    self.sector_plot.config = {"displayModeBar": False, "responsive": True}
    unknown = allocation["unclassified_tickers"]
    self.sector_status_label.text = (
      "Sector unavailable for %s; its value is included under Unclassified." % ", ".join(unknown)
      if unknown else "Classifications checked %s." % allocation["classification_as_of"]
    )

  def _render_security_rows(self):
    if self._analysis_source is None or self._history is None:
      return
    securities = self._analysis_source["selection_data"]["securities"]
    total_value = self._history["metrics"]["market_value"]
    rows = []
    for security in sorted(securities, key=lambda row: (row["sector"], row["symbol"])):
      included = security["symbol"] in self._selected_symbols
      shares = security["shares"]
      shares_text = format(shares, ",.8f").rstrip("0").rstrip(".")
      position = "%s shares" % shares_text if shares > 0 else "Closed position"
      count = security["transaction_count"]
      rows.append(dict(security,
        included=included, color=SECTOR_COLORS.get(security["sector"], "#a0a6ad"),
        detail_text="%s · %s · %d trade%s" % (security["sector"], position, count, "" if count == 1 else "s"),
        value_text=self._format_currency(security["market_value"]),
        weight_text=("%.1f%% of selection" % (security["market_value"] / total_value * 100)
                     if included and total_value else "0.0% of selection" if included else "Excluded"),
      ))
    self.security_panel.items = rows
    self.security_panel.raise_event_on_children("x-set-editable", editable=not self._busy)
    self.selection_status_label.text = (
      "%d of %d securities selected · All metrics and charts use this selection." % (len(self._selected_symbols), len(securities))
      if self._selected_symbols else "No securities selected. Check a security or choose Select all."
    )

  def _render_readout(self):
    if self._history is None:
      return
    metrics = self._history["metrics"]
    for label, key in [(self.invested_label, "buy_cost"), (self.value_label, "market_value"),
                       (self.dividends_label, "dividends"), (self.sale_proceeds_label, "sale_proceeds"),
                       (self.profit_label, "profit")]:
      label.text = self._format_currency(metrics[key])
    self.return_label.text = "—" if metrics["simple_return"] is None else self._format_percent(metrics["simple_return"])
    self.annualized_label.text = "—" if metrics["annualized_return"] is None else self._format_percent(metrics["annualized_return"])
    self.period_label.text = "%s · %d selected open holdings" % (
      self._history["metadata"]["end_date"], metrics["holding_count"]
    )
    self._render_comparison()
    self._render_sectors()
    self._render_security_rows()

  @handle("security_panel", "x-security-selection-changed")
  def security_selection_change(self, symbol, included, **event_args):
    if self._busy or self._analysis_source is None:
      return
    if included:
      self._selected_symbols.add(symbol)
    else:
      self._selected_symbols.discard(symbol)
    self._history = analyze_selection(self._analysis_source, self._selected_symbols)
    self._render_readout()

  @handle("select_all_securities_button", "click")
  def select_all_securities_button_click(self, **event_args):
    if not self._busy and self._analysis_source is not None:
      self._selected_symbols = set(row["symbol"] for row in self._analysis_source["selection_data"]["securities"])
      self._history = analyze_selection(self._analysis_source, self._selected_symbols)
      self._render_readout()

  @handle("timeframe_dropdown", "change")
  @handle("benchmark_dropdown", "change")
  @handle("benchmark_return_dropdown", "change")
  @handle("comparison_start_picker", "change")
  @handle("comparison_end_picker", "change")
  def comparison_inputs_change(self, **event_args):
    if not self._busy:
      self._update_benchmark_options()
      self._render_comparison()

  @handle("add_button", "click")
  def add_button_click(self, **event_args):
    self.transactions.append(self._new_transaction())
    self._refresh_transactions()
    self._mark_portfolio_dirty()
    self._show_results(False)
    self._set_status("New transaction added — enter its ticker, date, action, shares, and optional price.")

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
      self.transactions = [row for row in self.transactions if row is not item]
      self._refresh_transactions()
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
      self._show_results(False)
      self._set_status("Loading daily prices, dividends, sectors, and benchmark history…")
      document = [{
        "ticker": row["ticker"], "trade_date": row["trade_date"],
        "action": row["action"], "shares": row["shares"],
        "trade_price": row["trade_price"],
      } for row in transactions]
      result = anvil.server.call("analyze_portfolio_history", document, as_of_date, risk_free_rate / 100)
      if not result["ok"]:
        self._set_status(result["message"], is_error=True)
        return
      self._analysis_source = result
      self._selected_symbols = set(row["symbol"] for row in result["selection_data"]["securities"])
      self._history = analyze_selection(result, self._selected_symbols)
      for transaction, synced in zip(transactions, result["rows"]):
        transaction.update(synced)
        price = synced["current_price"]
        transaction["quote_text"] = "No current holding" if price is None else "USD %s" % self._format_currency(price)
        transaction["dividend_text"] = "Daily history synced"
      self._refresh_transactions()
      self.comparison_start_picker.date = self._date_from_iso(result["series"][0]["date"])
      self.comparison_end_picker.date = self._date_from_iso(result["series"][-1]["date"])
      self._show_results(True)
      self._update_benchmark_options()
      self._render_readout()
      message = "Daily history synced and analysis updated."
      moved_dates = result["metadata"]["moved_transaction_dates"]
      if moved_dates:
        message += " %d non-trading date(s) use the next completed session." % moved_dates
      self._set_status(message)
    except ValueError as error:
      self._set_status(str(error), is_error=True)
      self._show_results(False)
    finally:
      self._set_busy(False)

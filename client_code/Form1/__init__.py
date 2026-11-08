from datetime import date, timedelta
import math

import anvil.server
import anvil.users
import anvil.js
from ._anvil_designer import Form1Template
from anvil import *
from .. import chart_theme
from ..analysis_selection import analyze_selection, update_sector_classification
from ..chart_trades import build_trade_markers
from ..portfolio_periods import quarterly_returns, allocation_snapshots
from ..allocation_breakdown import allocation_groups
from ..HoldingRow import HoldingRow
from ..Components.TransactionYearGroup import TransactionYearGroup
from ..transaction_groups import group_transactions_by_year
from ..site_tracking import record_page
from ..account_profile import ensure_username, sign_in
from ..workspace_cache import account_workspace, invalidate_workspace


SECTOR_COLORS = chart_theme.SECTOR_COLORS

BENCHMARK_CHOICES = [
  ("S&P 500 index", "sp500"), ("Nasdaq Composite index", "nasdaq"),
  ("SPY · S&P 500 ETF", "spy"), ("QQQ · Nasdaq-100 ETF", "qqq"),
  ("DIA · Dow Jones ETF", "dia"), ("IWM · Russell 2000 ETF", "iwm"),
  ("VTI · Total US market ETF", "vti"),
]


class Form1(Form1Template):
  def __init__(self, **properties):
    self._initializing = True
    super().__init__(**properties)

    self._user = None
    self._username = ""
    self.account_id = None
    self._notification_badge_loading = False
    self._notification_badge_revision = 0
    self._set_notification_unread_count(0)
    self._restore_on_resume = False
    self.membership_region.visible = False
    self._creator_enabled = False
    self._creator_open = False
    self._creator_reference = None
    self.creator_access_region.visible = False
    self.creator_setup_button.visible = False
    self.creator_setup_region.visible = False
    self.creator_tab_button.visible = False
    self.creator_panel.visible = False
    self.share_composer.visible = False
    self._current_portfolio = None
    self._portfolio_dirty = False
    self._busy = False
    self.transactions = []
    self._collapsed_transaction_years = {}
    self._history = None
    self._analysis_source = None
    self._selected_symbols = set()
    self._analyzed_transactions = []
    self._chart_trade_groups = []
    self._chart_point_groups = [[], []]
    self._charts_expanded = False
    self._transactions_collapsed = False
    self._allocation_snapshots = []
    self._allocation_snapshot = None
    self._security_colors = {}
    self.allocation_view_dropdown.items = [("Sectors", "sector"), ("Securities", "security")]
    self.allocation_view_dropdown.selected_value = "sector"
    self.allocation_quarter_dropdown.items = [("Latest", "latest")]
    self.allocation_quarter_dropdown.selected_value = "latest"
    self.allocation_position_dropdown.items = [("All positions", "all"), ("Open positions", "open"), ("Closed positions", "closed")]
    self.allocation_position_dropdown.selected_value = "all"
    self.chart_trade_region.visible = False
    self._market_status_loading = False
    self._readout_page = "overview"
    self._dirty_readout_pages = {"overview", "allocation", "performance"}
    self._plot_sizes = {}
    self._appearance_mode = chart_theme.MODE
    self._image_import_rows = []
    self.image_import_region.visible = False
    self.image_import_review_region.visible = False
    self.holdings_image_preview.visible = False
    self.pdf_import_options.visible = False
    self.image_import_date_picker.date = None
    self.image_import_mode_dropdown.items = [
      ("Add to current transactions", "append"),
      ("Replace current transactions", "replace"),
    ]
    self.image_import_mode_dropdown.selected_value = "append"
    self.transaction_view_dropdown.items = [("Entry order", "entry"), ("Group by security", "security"), ("Group by year", "year")]
    self.transaction_view_dropdown.selected_value = "entry"
    self.regroup_button.visible = False
    self.timeframe_dropdown.items = [
      ("1 month", "1M"), ("3 months", "3M"), ("6 months", "6M"),
      ("Year to date", "YTD"), ("1 year", "1Y"), ("3 years", "3Y"),
      ("All history", "ALL"), ("Custom dates", "CUSTOM"),
    ]
    self.timeframe_dropdown.selected_value = "ALL"
    self.timeframe_dropdown.visible = False
    self.benchmark_dropdown.items = BENCHMARK_CHOICES
    self.benchmark_dropdown.selected_value = "sp500"
    self.benchmark_return_dropdown.items = [
      ("Total return · dividends reinvested", "total"),
      ("Price return · excludes dividends", "price"),
    ]
    self.benchmark_return_dropdown.selected_value = "total"
    self.custom_period_region.visible = False
    self._set_dates()
    self.portfolio_name_box.text = ""
    self._refresh_transactions()
    self._set_status("Add your first security to start building your portfolio.")
    self._show_results(False)
    self._refresh_account()
    if self._user is not None:
      self._set_busy(True)
      try:
        self._restore_last_portfolio()
      finally:
        self._set_busy(False)
    self._initializing = False

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
      for state in ("open", "closed", "pre_market", "after_hours", "unavailable"):
        self.market_status_region.classes["is-" + state] = status["state"] == state
      self.market_status_region.classes["is-extended"] = status["state"] in ("pre_market", "after_hours")
    finally:
      self._market_status_loading = False
      self.market_status_timer.interval = 60

  def _update_readout_context(self):
    self._update_retirement_context()
    self._update_simulations_context()
    if self._history is not None:
      self.period_label.text = "%s · %d selected open holdings%s" % (
        self._history["metadata"]["end_date"], self._history["metrics"]["holding_count"],
        " · provisional prices" if self._history["metadata"].get("provisional_date") else ""
      )
    name = {"overview": "Overview", "allocation": "Portfolio structure",
            "performance": "Performance", "dividends": "Dividends", "passive": "Passive Income",
            "tax": "Tax Harvester", "simulations": "Simulations", "retirement": "Retirement",
            "notifications": "Notifications"}[self._readout_page]
    if self._readout_page == "simulations":
      self.readout_tab_context.text = "Simulations · Compare reinvestment, cash and a fixed-rate account"
    elif self._readout_page in ("dividends", "passive", "tax"):
      self.readout_tab_context.text = {
        "dividends": "Dividends · Upcoming dates and payment history",
        "passive": "Passive Income · Projected payouts and income trends",
        "tax": "Tax Harvester · Capital gains by holding period and largest losses",
      }[self._readout_page]
    elif self._readout_page == "notifications":
      self.readout_tab_context.text = "Notifications · Insider activity, your inbox, and delivery preferences"
    elif self._readout_page == "retirement":
      self.readout_tab_context.text = "Retirement · Explore future savings and spending"
    elif self._analysis_source is not None:
      count = len(self._analysis_source["selection_data"]["securities"])
      self.readout_tab_context.text = "%s · %d of %d securities selected" % (
        name, len(self._selected_symbols), count
      )
    else:
      self.readout_tab_context.text = "%s · Sync history to see your readout" % name

  def _apply_readout_page(self, redraw=False):
    has_analysis = self._history is not None
    portfolio_page = self._readout_page not in ("retirement", "simulations", "dividends", "passive", "tax", "notifications")
    self.results_section.visible = has_analysis and portfolio_page
    self.empty_results.visible = not has_analysis and portfolio_page
    self.hedge_fund_panel.visible = self._readout_page == "performance"
    for name, panel, button in (
      ("overview", self.overview_panel, self.overview_tab_button),
      ("allocation", self.allocation_panel, self.allocation_tab_button),
      ("performance", self.performance_panel, self.performance_tab_button),
      ("retirement", self.retirement_panel, self.retirement_tab_button),
      ("simulations", self.simulations_panel, self.simulations_tab_button),
      ("notifications", self.notifications_panel, self.notifications_tab_button),
    ):
      active = name == self._readout_page
      panel.visible = active
      button.role = "readout-tab-active" if active else "readout-tab"
      setattr(button, "enabled", not self._busy)
    income_page = self._readout_page in ("dividends", "passive", "tax")
    self.income_panel.visible = income_page
    for name, button in (("dividends", self.dividends_tab_button),
                         ("passive", self.passive_tab_button), ("tax", self.tax_tab_button)):
      button.role = "readout-tab-active" if name == self._readout_page else "readout-tab"
      setattr(button, "enabled", not self._busy)
    if income_page:
      self.income_form.set_view(self._readout_page)
    self._update_readout_context()
    if redraw:
      self._render_active_readout()
      self._resize_readout_charts()
      if self._readout_page == "notifications":
        self.notifications_form.ensure_loaded()
      elif self._readout_page == "simulations":
        self.simulations_form.redraw()
      elif self._readout_page == "performance":
        self.hedge_fund_comparison.redraw()
      elif self._readout_page in ("dividends", "passive", "tax"):
        self.income_form.ensure_loaded()
        self.income_form.redraw()
      elif self._readout_page == "retirement":
        self.retirement_form.redraw()

  def _render_active_readout(self):
    self._ensure_readout_rendered(self._readout_page)

  def _ensure_readout_rendered(self, page):
    if self._history is None or page not in self._dirty_readout_pages:
      return
    if page == "overview":
      self._render_overview()
    elif page == "allocation":
      self._render_sectors()
      self._render_security_rows()
    elif page == "performance":
      self._render_comparison()
    self._dirty_readout_pages.discard(page)
    # Setting Plot data/layout already renders it. Remember its current size
    # so returning to the same tab does not immediately rebuild it again.
    for name, plot in self._page_charts(page):
      size = self._plot_size(plot)
      if size is not None:
        self._plot_sizes[name] = size
      else:
        # Offscreen renders (for example, preparing a shared chart) need a
        # fresh size when this tab becomes visible again.
        self._plot_sizes.pop(name, None)

  def _page_charts(self, page):
    if page == "performance":
      return [("performance", self.performance_plot), ("quarterly", self.quarterly_plot),
              ("drawdown", self.drawdown_plot)]
    if page == "allocation":
      return [("sector", self.sector_plot), ("allocation_history", self.allocation_history_plot),
              ("allocation_map", self.allocation_map_plot)]
    return []

  def _plot_size(self, plot):
    if not plot.visible:
      return None
    bounds = anvil.js.get_dom_node(plot).getBoundingClientRect()
    if bounds.width <= 0 or bounds.height <= 0:
      return None
    return (round(bounds.width), round(bounds.height))

  def _resize_readout_charts(self):
    if self._history is None:
      return
    for name, plot in self._page_charts(self._readout_page):
      size = self._plot_size(plot)
      if size is not None and self._plot_sizes.get(name) != size:
        plot.redraw()
        self._plot_sizes[name] = size

  def apply_appearance(self):
    if self._appearance_mode == chart_theme.MODE:
      return
    self._appearance_mode = chart_theme.MODE
    self._dirty_readout_pages.update(("overview", "allocation", "performance"))
    self._render_active_readout()
    if self._readout_page == "simulations":
      self.simulations_form.apply_appearance()
    elif self._readout_page == "performance":
      self.hedge_fund_comparison.apply_appearance()
    elif self._readout_page in ("dividends", "passive", "tax"):
      self.income_form.apply_appearance()
    elif self._readout_page == "retirement":
      self.retirement_form.apply_appearance()
    if self._creator_open:
      self.creator_workspace.apply_appearance()
    if self.share_composer.visible:
      self.share_composer.apply_appearance()

  def _select_readout_page(self, page):
    if (self._busy or page == self._readout_page or
        page not in ("overview", "allocation", "performance", "simulations", "retirement", "dividends", "passive", "tax", "notifications")):
      return
    if page != "performance":
      self._set_charts_expanded(False, resize=False)
    self._readout_page = page
    self._apply_readout_page(redraw=True)
    self.dom_nodes["readout_scroller"].scrollTop = 0
    record_page(page)

  def _set_creator_open(self, opened):
    self._creator_open = bool(opened and self._creator_enabled)
    self.creator_panel.visible = self._creator_open
    self.creator_tab_button.text = "Close creator workspace" if self._creator_open else "Open creator workspace"
    if self._creator_open:
      self.creator_workspace.open_library()

  @handle("creator_tab_button", "click")
  def creator_tab_button_click(self, **event_args):
    if not self._busy and self._creator_enabled:
      self._set_creator_open(not self._creator_open)

  @handle("creator_setup_button", "click")
  def creator_setup_button_click(self, **event_args):
    if self._busy or self._creator_reference is None:
      return
    self.creator_setup_region.visible = not self.creator_setup_region.visible
    if self.creator_setup_region.visible:
      self.creator_reference_box.text = str(self._creator_reference)
      self.creator_reference_box.focus()
      self.creator_reference_box.select()

  @handle("creator_workspace", "x-import-creator-portfolio")
  def import_creator_portfolio(self, portfolio_id, **event_args):
    if self._busy or not self._creator_enabled or not self._confirm_discard():
      return
    self._set_busy(True)
    try:
      result = anvil.server.call("import_creator_portfolio", portfolio_id)
      if not result["ok"]:
        self.creator_workspace.show_status(result["message"], is_error=True)
        return
      portfolio = result["portfolio"]
      self._apply_portfolio(portfolio)
      self._set_creator_open(False)
      self._refresh_portfolios()
      self._set_portfolio_status('Imported "%s" as a saved copy in your account. Sync to analyze it.' % portfolio["name"])
    finally:
      self._set_busy(False)

  def _set_transactions_collapsed(self, collapsed):
    if collapsed == self._transactions_collapsed:
      return
    self._transactions_collapsed = collapsed
    self.transactions_column.visible = not collapsed
    self.workspace_region.classes["transactions-collapsed"] = collapsed
    self.toggle_transactions_button.text = "Show transactions" if collapsed else "Collapse transactions"
    self._apply_readout_page(redraw=True)

  @handle("toggle_transactions_button", "click")
  def toggle_transactions_click(self, **event_args):
    if not self._busy:
      collapsed = not self._transactions_collapsed
      self._set_transactions_collapsed(collapsed)
      if anvil.js.window.innerWidth <= 980:
        # On stacked layouts, keep the newly requested workspace in view.
        target = "readout_workspace" if collapsed else "transactions_workspace"
        self.dom_nodes[target].scrollIntoView({"block": "start"})

  @handle("overview_tab_button", "click")
  def overview_tab_click(self, **event_args):
    self._select_readout_page("overview")

  @handle("allocation_tab_button", "click")
  def allocation_tab_click(self, **event_args):
    self._select_readout_page("allocation")

  @handle("performance_tab_button", "click")
  def performance_tab_click(self, **event_args):
    self._select_readout_page("performance")

  @handle("retirement_tab_button", "click")
  def retirement_tab_click(self, **event_args):
    self._select_readout_page("retirement")

  def _update_retirement_context(self):
    value = None
    as_of = None
    if self._history is not None and self._selected_symbols:
      value = self._history["metrics"]["market_value"]
      as_of = self._history["metadata"]["end_date"]
    self.retirement_form.set_portfolio_context(value, as_of)

  @handle("retirement_form", "x-use-portfolio-balance")
  def retirement_use_portfolio(self, **event_args):
    if not self._busy and self._history is not None and self._selected_symbols:
      self.retirement_form.set_starting_balance(
        self._history["metrics"]["market_value"], self._history["metadata"]["end_date"]
      )

  @handle("retirement_form", "x-retirement-busy")
  def retirement_busy_change(self, busy, **event_args):
    self._set_busy(busy)

  def _update_simulations_context(self):
    transactions = []
    as_of = None
    if self._history is not None and self._selected_symbols:
      # Copy only the simulation inputs, never hand the child live editor rows.
      transactions = [{key: row.get(key) for key in
                       ("ticker", "trade_date", "action", "shares", "trade_price")}
                      for row in self._analyzed_transactions
                      if self._security_key(row) in self._selected_symbols]
      as_of = self._history["metadata"]["end_date"]
    self.simulations_form.set_portfolio_context(transactions, as_of)
    self.income_form.set_portfolio_context(transactions, as_of)

  @handle("dividends_tab_button", "click")
  def dividends_tab_click(self, **event_args):
    self._select_readout_page("dividends")

  @handle("passive_tab_button", "click")
  def passive_tab_click(self, **event_args):
    self._select_readout_page("passive")

  @handle("tax_tab_button", "click")
  def tax_tab_click(self, **event_args):
    self._select_readout_page("tax")

  @handle("notifications_tab_button", "click")
  def notifications_tab_click(self, **event_args):
    self.open_notifications()

  def open_notifications(self):
    self._select_readout_page("notifications")
    if self._readout_page == "notifications":
      self.notifications_form.ensure_loaded()

  @handle("notifications_form", "x-notifications-busy")
  def notifications_busy_change(self, busy, **event_args):
    self._set_busy(busy)

  def _set_notification_unread_count(self, count):
    self._notification_badge_revision += 1
    count = max(0, int(count or 0)) if self.account_id else 0
    self.notification_badge.visible = count > 0
    self.notification_badge_label.text = "99+" if count > 99 else str(count)
    description = "%s unread notification%s" % (count, "" if count == 1 else "s")
    self.notification_badge_accessible_label.text = description if self.account_id else ""
    self.notifications_tab_button.tooltip = description if self.account_id else "Sign in to view your notifications"

  @handle("notifications_form", "x-notifications-unread")
  def notifications_unread_change(self, count, account_id, **event_args):
    if account_id == self.account_id:
      self._set_notification_unread_count(count)

  def _refresh_notification_unread_count(self):
    if not self.account_id or self._notification_badge_loading:
      return
    account_id = self.account_id
    revision = self._notification_badge_revision
    self._notification_badge_loading = True
    try:
      count = anvil.server.call_s("get_notification_unread_count")
      # A newer inbox response or account change takes precedence over a poll.
      if account_id == self.account_id and revision == self._notification_badge_revision:
        self._set_notification_unread_count(count)
    finally:
      self._notification_badge_loading = False

  @handle("notification_badge_timer", "tick")
  def notification_badge_timer_tick(self, **event_args):
    if not self._initializing and not self._busy:
      self._refresh_notification_unread_count()

  @handle("income_form", "x-income-busy")
  def income_busy_change(self, busy, **event_args):
    self._set_busy(busy)

  @handle("simulations_tab_button", "click")
  def simulations_tab_click(self, **event_args):
    self._select_readout_page("simulations")

  @handle("simulations_form", "x-simulations-busy")
  def simulations_busy_change(self, busy, **event_args):
    self._set_busy(busy)

  def _security_key(self, transaction):
    parts = str(transaction.get("ticker") or "").strip().upper().split(":", 1)
    symbol = parts[0].strip().replace(".", "-")
    exchange = parts[1].strip() if len(parts) == 2 else "NASDAQ"
    if exchange in ("NASDAQ", "NYSE", "NYSEARCA", "AMEX", "BATS", "NYSEAMERICAN"):
      return symbol
    return "%s:%s" % (symbol, exchange) if symbol else ""

  def _set_image_import_status(self, message, is_error=False):
    self.image_import_status_label.text = message
    self.image_import_status_region.classes["is-error"] = is_error

  def _import_file_is_pdf(self):
    uploaded = self.holdings_image_loader.file
    return uploaded is not None and (
      str(getattr(uploaded, "content_type", "") or "").lower() == "application/pdf"
      or str(getattr(uploaded, "name", "") or "").lower().endswith(".pdf")
    )

  def _read_pdf_page_range(self):
    pages = []
    for value, label, default in (
      (self.pdf_start_page_box.text, "start", 1),
      (self.pdf_end_page_box.text, "end", None),
    ):
      text = str(value or "").strip()
      if not text:
        pages.append(default)
        continue
      try:
        number = float(text)
      except (TypeError, ValueError):
        raise ValueError("Enter a whole-number %s page." % label)
      if not math.isfinite(number) or number < 1 or number != int(number):
        raise ValueError("Enter a whole-number %s page greater than zero." % label)
      pages.append(int(number))
    first, last = pages
    first = first if first is not None else 1
    if last is not None and (last < first or last - first + 1 > 20):
      raise ValueError("Choose a PDF range of 1–20 pages, with the end on or after the start.")
    return first, last

  def _update_image_import_controls(self):
    count = sum(1 for row in self._image_import_rows if row.get("include", True))
    self.image_import_count_label.text = "%d of %d rows selected · %d current transactions" % (
      count, len(self._image_import_rows), len(self.transactions)
    )
    replacing = self.image_import_mode_dropdown.selected_value == "replace"
    self.apply_image_import_button.text = (
      "Replace with %d selected row%s" if replacing else "Add %d selected row%s"
    ) % (count, "" if count == 1 else "s")
    setattr(self.apply_image_import_button, "enabled", not self._busy and count > 0)
    setattr(self.read_image_button, "enabled", not self._busy and self.holdings_image_loader.file is not None)
    self.image_import_rows_panel.raise_event_on_children("x-set-editable", editable=not self._busy)

  def _clear_image_import(self):
    self._image_import_rows = []
    self.image_import_rows_panel.items = []
    self.image_import_review_region.visible = False
    self.holdings_image_preview.visible = False
    self.holdings_image_preview.source = ""
    self.holdings_image_loader.clear()
    self.pdf_import_options.visible = False
    self.pdf_start_page_box.text = "1"
    self.pdf_end_page_box.text = ""
    self.pdf_password_box.text = ""
    self.image_import_warnings_label.text = ""
    self.image_import_date_picker.date = None
    self.image_import_mode_dropdown.selected_value = "append"
    self._set_image_import_status("")
    self._update_image_import_controls()

  @handle("toggle_image_import_button", "click")
  def toggle_image_import_button_click(self, **event_args):
    if not self._busy:
      self.image_import_region.visible = not self.image_import_region.visible
      self.toggle_image_import_button.text = "Hide import" if self.image_import_region.visible else "Import file"

  @handle("holdings_image_loader", "change")
  def holdings_image_loader_change(self, **event_args):
    if self._busy:
      return
    image = self.holdings_image_loader.file
    is_pdf = self._import_file_is_pdf()
    self._image_import_rows = []
    self.image_import_rows_panel.items = []
    self.image_import_review_region.visible = False
    self.holdings_image_preview.source = image if image is not None and not is_pdf else ""
    self.holdings_image_preview.visible = image is not None and not is_pdf
    self.pdf_import_options.visible = is_pdf
    self.pdf_start_page_box.text = "1"
    self.pdf_end_page_box.text = ""
    self.pdf_password_box.text = ""
    self._set_image_import_status(("PDF ready. Choose your page range, then read and review the positions." if is_pdf else "Image ready. Read it, then review the detected positions.") if image is not None else "")
    self._update_image_import_controls()

  @handle("clear_image_import_button", "click")
  def clear_image_import_button_click(self, **event_args):
    if not self._busy:
      self._clear_image_import()

  @handle("read_image_button", "click")
  def read_image_button_click(self, **event_args):
    image = self.holdings_image_loader.file
    if self._busy or image is None:
      return
    if image.length > 10 * 1024 * 1024:
      self._set_image_import_status("Choose a PDF or image smaller than 10 MB.", is_error=True)
      return
    try:
      first_page, last_page = self._read_pdf_page_range() if self._import_file_is_pdf() else (1, None)
    except ValueError as error:
      self._set_image_import_status(str(error), is_error=True)
      return
    self._set_busy(True)
    self.read_image_button.text = "Reading file…"
    self._set_image_import_status("Reading the file. Scanned PDF pages may take longer. Current transactions stay in place until you apply the reviewed rows.")
    self._image_import_rows = []
    self.image_import_rows_panel.items = []
    self.image_import_review_region.visible = False
    try:
      reader = anvil.js.import_from("./_/theme/holdings-file-reader.js")
      image_url = image.get_url()
      if image_url is None:
        input_node = anvil.js.get_dom_node(self.holdings_image_loader).querySelector("input[type=file]")
        native_files = getattr(input_node, "files", None)
        if native_files is None or len(native_files) == 0:
          self._set_image_import_status("Choose the file again, then retry reading it.", is_error=True)
          return
        image_url = native_files[0]
      extracted = reader.readHoldingsFile(image_url, first_page, last_page, str(self.pdf_password_box.text or ""))
      if not extracted["ok"]:
        self._set_image_import_status(extracted["message"], is_error=True)
        return
      self._set_image_import_status("Text read. Organizing the detected positions for your review…")
      pdf_summary = ""
      if extracted["document_kind"] == "pdf":
        pdf_summary = "PDF pages %s of %d." % (
          ", ".join(str(page) for page in extracted["pages_read"]), extracted["total_pages"]
        )
        result = anvil.server.call("parse_holdings_document_text", extracted["page_blocks"])
      else:
        result = anvil.server.call("parse_holdings_image_text", extracted["blocks"])
      if not result["ok"]:
        self._set_image_import_status((result["message"] + " " + pdf_summary).strip(), is_error=True)
        return
      self._image_import_rows = result["rows"]
      existing_symbols = set(self._security_key(row) for row in self.transactions if row.get("ticker"))
      for row in self._image_import_rows:
        if self._security_key(row) in existing_symbols:
          row["notes"] = (row.get("notes", "") + " This security already has transactions in this portfolio; check for duplicate positions.").strip()
      self.image_import_rows_panel.items = self._image_import_rows
      warnings = list(extracted.get("warnings", [])) + result.get("warnings", [])
      self.image_import_warnings_label.text = "\n".join(warnings)
      self.image_import_warnings_label.visible = bool(warnings)
      self.image_import_review_region.visible = True
      self._set_image_import_status((result["message"] + " " + pdf_summary).strip())
    finally:
      self.read_image_button.text = "Read file"
      self._set_busy(False)

  @handle("image_import_rows_panel", "x-import-row-changed")
  @handle("image_import_mode_dropdown", "change")
  def image_import_review_change(self, **event_args):
    self._update_image_import_controls()

  @handle("apply_image_import_button", "click")
  def apply_image_import_button_click(self, **event_args):
    if self._busy:
      return
    imported = []
    fallback_date = self.image_import_date_picker.date
    try:
      for index, row in enumerate(self._image_import_rows, start=1):
        if not row.get("include", True):
          continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if not 1 <= len(ticker) <= 40:
          raise ValueError("Import row %d: enter a ticker symbol of 1–40 characters." % index)
        trade_date = row.get("trade_date") or fallback_date
        if trade_date is None:
          raise ValueError("Import row %d: choose a snapshot / trade date, or set the fallback date above." % index)
        if self.as_of_date_picker.date is not None and trade_date > self.as_of_date_picker.date:
          raise ValueError("Import row %d: the date must be on or before the analysis date." % index)
        action = row.get("action", "Buy")
        if action not in ("Buy", "Sell"):
          raise ValueError("Import row %d: choose Buy or Sell." % index)
        shares = self._parse_number(row.get("shares"), "share count", index)
        if shares <= 0:
          raise ValueError("Import row %d: shares must be greater than zero." % index)
        raw_price = row.get("trade_price")
        price = None
        if raw_price is not None and str(raw_price).strip():
          price = self._parse_number(raw_price, "price per share", index)
          if price <= 0:
            raise ValueError("Import row %d: price per share must be greater than zero." % index)
        transaction = self._new_transaction(ticker, trade_date, action, str(shares), price)
        transaction["_collapsed"] = True
        imported.append(transaction)
      if not imported:
        raise ValueError("Select at least one detected row to import.")
      replacing = self.image_import_mode_dropdown.selected_value == "replace"
      if len(imported) + (0 if replacing else len(self.transactions)) > 1000:
        raise ValueError("A portfolio can contain up to 1,000 transactions. Import fewer rows or replace the current transactions.")
    except ValueError as error:
      self._set_image_import_status(str(error), is_error=True)
      return
    if replacing:
      self._collapsed_transaction_years.clear()
    else:
      self._commit_transaction_inputs()
    self.transactions = imported if replacing else self.transactions + imported
    self._refresh_transactions()
    self._mark_portfolio_dirty()
    self._show_results(False)
    count = len(imported)
    self._clear_image_import()
    self._set_image_import_status("Imported %d row%s. You can edit the transactions, save your portfolio, or sync history." % (count, "" if count == 1 else "s"))
    self._set_status("Imported positions ready — sync history to calculate the updated readout.")

  def _refresh_transactions(self):
    self.transactions_empty_region.visible = not bool(self.transactions)
    self.add_button.text = "Add A Security!" if not self.transactions else "Add security / transaction"
    view = self.transaction_view_dropdown.selected_value
    grouped = view == "security"
    by_year = view == "year"
    self.regroup_button.visible = grouped or by_year
    self.collapse_prior_years_button.visible = by_year
    self.collapse_all_button.text = "Collapse years" if by_year else "Collapse all"
    self.expand_all_button.text = "Expand years" if by_year else "Expand all"
    for index, row in enumerate(self.transactions):
      row["_transaction_index"] = index
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
    # Callers commit editors before user-initiated rebuilds. Do not commit here:
    # Sync and portfolio replacement already supplied the authoritative rows.
    self.holdings_panel.raise_event_on_children("x-set-editable", editable=False)
    self.transaction_years_panel.raise_event_on_children("x-set-editable", editable=False)
    self.holdings_panel.visible = bool(self.transactions) and not by_year
    self.transaction_years_panel.visible = bool(self.transactions) and by_year
    if by_year:
      self.holdings_panel.items = []
      self.transaction_years_panel.items = group_transactions_by_year(
        self.transactions, self._collapsed_transaction_years, date.today().year
      )
    else:
      self.transaction_years_panel.items = []
      self.holdings_panel.items = visible_rows
    self.holdings_panel.raise_event_on_children("x-set-editable", editable=not self._busy)
    self.transaction_years_panel.raise_event_on_children("x-set-editable", editable=not self._busy)
    self.transaction_view_help.text = (
      "Newest dates first. Click a year to fold it away. Use Regroup after changing dates."
      if by_year else
      "Trades are grouped by ticker. Use Regroup after editing a ticker or action."
      if grouped else "Transactions appear in the order you entered them."
    )
    self._update_image_import_controls()

  def _transaction_year_forms(self):
    return [group for group in self.transaction_years_panel.get_components()
            if isinstance(group, TransactionYearGroup)]

  def _transaction_row_forms(self):
    rows = [form for form in self.holdings_panel.get_components() if isinstance(form, HoldingRow)]
    for group in self._transaction_year_forms():
      rows.extend(group.transaction_rows())
    return rows

  def _commit_transaction_inputs(self):
    for row in self._transaction_row_forms():
      row.commit_inputs()

  @handle("transaction_years_panel", "x-year-collapsed")
  def transaction_year_collapsed(self, year, collapsed, **event_args):
    self._collapsed_transaction_years[year] = collapsed

  @handle("transaction_view_dropdown", "change")
  @handle("regroup_button", "click")
  def transaction_view_change(self, **event_args):
    if not self._busy:
      self._commit_transaction_inputs()
      self._refresh_transactions()

  @handle("collapse_all_button", "click")
  def collapse_all_button_click(self, **event_args):
    if not self._busy:
      self._commit_transaction_inputs()
      panel = self.transaction_years_panel if self.transaction_view_dropdown.selected_value == "year" else self.holdings_panel
      panel.raise_event_on_children("x-set-collapsed", collapsed=True)

  @handle("expand_all_button", "click")
  def expand_all_button_click(self, **event_args):
    if not self._busy:
      panel = self.transaction_years_panel if self.transaction_view_dropdown.selected_value == "year" else self.holdings_panel
      panel.raise_event_on_children("x-set-collapsed", collapsed=False)

  @handle("collapse_prior_years_button", "click")
  def collapse_prior_years_click(self, **event_args):
    if self._busy or self.transaction_view_dropdown.selected_value != "year":
      return
    self._commit_transaction_inputs()
    self._refresh_transactions()
    for group in self._transaction_year_forms():
      year = group.item["year"]
      group.set_collapsed(year is not None and year < date.today().year)

  def _show_entry_order_for_error(self, message):
    if message.startswith("Row ") or message.startswith("Add at least one transaction"):
      self._set_transactions_collapsed(False)
    # Validation row numbers refer to the original transaction order.
    if message.startswith("Row ") and self.transaction_view_dropdown.selected_value != "entry":
      self.transaction_view_dropdown.selected_value = "entry"
      self._refresh_transactions()

  def _set_portfolio_status(self, message, is_error=False):
    if is_error:
      self._show_entry_order_for_error(message)
    self.portfolio_status_label.text = message
    self.portfolio_status_region.classes["is-error"] = is_error

  def _update_controls(self):
    self._update_welcome()
    setattr(self.share_performance_button, "enabled", not self._busy)
    for component in (
      self.account_button, self.save_portfolio_button, self.new_portfolio_button,
      self.refresh_membership_button,
      self.plans_button, self.subscription_button,
      self.portfolio_name_box, self.add_button, self.sample_button,
      self.analyze_button, self.as_of_date_picker, self.risk_free_box,
      self.timeframe_dropdown, self.benchmark_dropdown,
      self.benchmark_return_dropdown, self.select_all_securities_button,
      self.allocation_view_dropdown, self.allocation_quarter_dropdown,
      self.comparison_start_picker, self.comparison_end_picker,
      self.transaction_view_dropdown, self.regroup_button,
      self.collapse_all_button, self.expand_all_button, self.collapse_prior_years_button,
      self.overview_tab_button, self.allocation_tab_button,
      self.performance_tab_button, self.retirement_tab_button,
      self.simulations_tab_button, self.dividends_tab_button, self.passive_tab_button,
      self.tax_tab_button, self.notifications_tab_button, self.toggle_transactions_button,
      self.creator_tab_button, self.creator_setup_button,
      self.toggle_image_import_button, self.holdings_image_loader,
      self.clear_image_import_button, self.image_import_date_picker,
      self.image_import_mode_dropdown,
      self.pdf_start_page_box, self.pdf_end_page_box, self.pdf_password_box,
      self.show_trades_checkbox, self.expand_charts_button,
      self.reset_chart_zoom_button,
      self.close_chart_trades_button,
    ):
      setattr(component, "enabled", not self._busy)
    for timeframe, button in self._timeframe_buttons():
      setattr(button, "enabled", not self._busy)
    setattr(self.view_chart_trades_button, "enabled", not self._busy and bool(self._chart_trade_groups))
    self.saved_portfolios_dropdown.enabled = not self._busy and self._user is not None
    setattr(self.open_portfolio_button, "enabled", (
      not self._busy and self.saved_portfolios_dropdown.selected_value is not None
    ))
    self.save_portfolio_button.text = (
      "Save changes" if self._current_portfolio is not None else "Save portfolio"
    )
    self.holdings_panel.raise_event_on_children("x-set-editable", editable=not self._busy)
    self.transaction_years_panel.raise_event_on_children("x-set-editable", editable=not self._busy)
    self.security_panel.raise_event_on_children("x-set-editable", editable=not self._busy, can_classify=self._user is not None)
    self.simulations_form.raise_event("x-set-editable", editable=not self._busy)
    self.income_form.raise_event("x-set-editable", editable=not self._busy)
    self.notifications_form.set_editable(editable=not self._busy)
    self.retirement_form.raise_event("x-set-editable", editable=not self._busy)
    self.creator_workspace.raise_event("x-set-editable", editable=not self._busy)
    self.hedge_fund_comparison.raise_event("x-set-editable", editable=not self._busy)
    self.benchmark_return_dropdown.enabled = not self._busy and len(self.benchmark_return_dropdown.items) > 1
    self._update_image_import_controls()

  def _set_busy(self, busy):
    self._busy = busy
    self._update_controls()
    if not busy and self._readout_page in ("dividends", "passive", "tax"):
      self.income_form.ensure_loaded()
    elif not busy and self._readout_page == "notifications":
      self.notifications_form.ensure_loaded()

  def _refresh_portfolios(self, portfolios=None):
    if portfolios is None:
      portfolios = anvil.server.call("list_portfolios") if self._user is not None else []
    self.saved_portfolios_dropdown.items = [
      (portfolio["name"], portfolio) for portfolio in portfolios
    ]
    self.saved_portfolios_dropdown.placeholder = (
      "Choose a saved portfolio" if portfolios else "No saved portfolios yet"
    )
    current_id = self._current_portfolio.get_id() if self._current_portfolio is not None else None
    self.saved_portfolios_dropdown.selected_value = next(
      (portfolio for portfolio in portfolios if portfolio["portfolio_id"] == current_id), None
    )
    self._update_controls()

  def _refresh_account(self):
    previous_account_id = self.account_id
    self._user = anvil.users.get_user()
    self.account_id = self._user.get_id() if self._user is not None else None
    if self.account_id != previous_account_id:
      self._set_notification_unread_count(0)
    self.notifications_form.set_account_context(self.account_id)
    self._username = ""
    workspace = None
    if self._user is not None:
      # A saved profile can be newer than the Users row cached in this browser.
      workspace = account_workspace()
      current_user = anvil.users.get_user()
      if workspace is None or current_user is None or current_user.get_id() != self.account_id:
        self._refresh_account()
        return
      profile = workspace["profile"]
      self._username = profile["username"] or ""
    self.subscription_button.visible = self._user is not None
    self.account_button.text = "Sign out" if self._user is not None else "Sign in / Create account"
    self.account_label.text = (
      "Signed in as %s · Saved portfolios are accessible to you and the platform creator." % (self._username or self._user["email"])
      if self._user is not None
      else "Sign in to save and reopen portfolios on any device. Saved portfolios are accessible to you and the platform creator."
    )
    if workspace is not None:
      self._display_membership(workspace["membership"])
    else:
      self._refresh_membership()
    creator = workspace["creator"] if workspace is not None else {"enabled": False, "email": None}
    self._creator_enabled = bool(creator["enabled"])
    self._creator_reference = creator.get("setup_reference")
    self.creator_setup_button.visible = self._creator_reference is not None
    self.creator_setup_region.visible = False
    self.creator_reference_box.text = ""
    self.creator_tab_button.visible = self._creator_enabled
    self.creator_access_region.visible = self._creator_enabled
    self.creator_workspace.configure_access(self._creator_enabled, creator["email"])
    self.hedge_fund_comparison.set_creator_access(self._creator_enabled)
    self._set_creator_open(False)
    self._apply_readout_page()
    self._refresh_portfolios(workspace["portfolios"] if workspace is not None else [])

    self._refresh_notification_unread_count()

  def resume_account(self):
    self.apply_appearance()
    if self._busy:
      return
    was_guest = self.account_id is None
    self._set_busy(True)
    try:
      self._refresh_account()
      if (was_guest or self._restore_on_resume) and self._user is not None and not self._has_unsaved_work():
        self._restore_last_portfolio()
      self._restore_on_resume = False
    finally:
      self._set_busy(False)

  @handle("plans_button", "click")
  def plans_click(self, **event_args):
    if not self._busy:
      set_url_hash({"page": "subscribe"})

  @handle("profile_button", "click")
  def profile_click(self, **event_args):
    if not self._busy:
      set_url_hash({"page": "profile"})

  def open_profile_portfolio(self, portfolio_id):
    if self._busy:
      return False
    if self._current_portfolio is not None and self._current_portfolio.get_id() == portfolio_id:
      return True
    if not self._confirm_discard():
      return False
    self._set_busy(True)
    try:
      result = anvil.server.call("load_portfolio", portfolio_id)
      if not result["ok"]:
        self._set_portfolio_status(result["message"], is_error=True)
        return False
      self._apply_portfolio(result["portfolio"], result.get("analysis"))
      return True
    finally:
      self._set_busy(False)

  @handle("subscription_button", "click")
  def subscription_click(self, **event_args):
    if not self._busy:
      set_url_hash({"page": "subscription"})

  def _display_membership(self, membership):
    self.membership_badge.text = membership["label"]
    self.membership_badge.role = (
      "membership-badge-premium" if membership["is_premium"] else "membership-badge-free"
    )
    self.membership_detail_label.text = membership["detail"]
    self.membership_region.visible = True

  def _refresh_membership(self):
    self.membership_region.visible = False
    if self._user is None:
      self.membership_badge.text = "Free"
      self.membership_badge.role = "membership-badge-free"
      self.membership_detail_label.text = ""
      return
    account_id = self._user.get_id()
    membership = anvil.server.call("get_my_membership")
    if self._user is not None and self._user.get_id() == account_id:
      self._display_membership(membership)

  @handle("refresh_membership_button", "click")
  def refresh_membership_click(self, **event_args):
    if self._busy or self._user is None:
      return
    self._set_busy(True)
    try:
      self._refresh_membership()
      invalidate_workspace()
    finally:
      self._set_busy(False)

  @handle("creator_workspace", "x-membership-changed")
  def creator_membership_changed(self, account_id, membership, **event_args):
    if self._user is not None and self._user.get_id() == account_id:
      self._display_membership(membership)

  def _update_welcome(self):
    signed_in = self._user is not None
    self.guest_hero.visible = not signed_in
    self.welcome_region.visible = signed_in
    self.welcome_heading_label.text = (
      "Welcome back, %s." % self._username if signed_in and self._username else "Welcome back."
    )
    if not signed_in:
      self.welcome_detail_label.text = ""
    else:
      self.welcome_detail_label.text = (
        'Continue with "%s" below.' % self._current_portfolio["name"]
        if self._current_portfolio is not None else
        "Continue your draft below, or open a saved portfolio. We'll remember the portfolio you open or save."
      )

  def _restore_last_portfolio(self):
    result = anvil.server.call("get_last_portfolio")
    if not result["ok"]:
      self._set_portfolio_status(result["message"], is_error=True)
      return
    portfolio = result["portfolio"]
    if portfolio is None:
      if not self._has_unsaved_work():
        self._new_portfolio()
      self._set_portfolio_status("Welcome! Save your first portfolio to return to it next time.")
      return
    if not self._confirm_discard():
      self._set_portfolio_status("Welcome back. Your unsaved work is still here; open a saved portfolio whenever you're ready.")
      return
    self._apply_portfolio(portfolio, result.get("analysis"))
    self._set_portfolio_status('Welcome back — reopened "%s".' % portfolio["name"])

  def _ensure_signed_in(self):
    new_login = False
    if anvil.users.get_user() is None:
      user = sign_in()
      if user is None:
        self._set_portfolio_status("Sign-in canceled. Your unsaved portfolio is still here.")
        return False
      new_login = True
    if not ensure_username():
      self._refresh_account()
      self._set_portfolio_status("Choose a username when you sign in to continue saving your portfolio.")
      return False
    self._refresh_account()
    if new_login:
      record_page(self._readout_page)
    return True

  def _mark_portfolio_dirty(self):
    self._portfolio_dirty = True
    self._set_portfolio_status("Unsaved changes — save your portfolio to keep them.")

  def _has_unsaved_work(self):
    return self._portfolio_dirty or self.holdings_image_loader.file is not None

  def _confirm_discard(self):
    return not self._has_unsaved_work() or confirm(
      "You have unsaved portfolio changes or an import in progress. Discard them and continue?",
      title="Unsaved changes",
      buttons=[("Keep editing", False), ("Discard changes", True)],
    )

  def _new_portfolio(self):
    self._collapsed_transaction_years.clear()
    self._restore_on_resume = False
    self._current_portfolio = None
    self._clear_image_import()
    self.simulations_form.reset_workspace()
    self.income_form.reset_workspace()
    self.retirement_form.reset_workspace()
    self.portfolio_name_box.text = ""
    self.transactions = []
    self._refresh_transactions()
    self._set_dates()
    self.risk_free_box.text = "4.50"
    self.saved_portfolios_dropdown.selected_value = None
    self._portfolio_dirty = False
    self._readout_page = "overview"
    self._show_results(False)
    self._set_transactions_collapsed(False)
    self._set_status("Add A Security! Enter its transaction, then save your portfolio or sync prices.")
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
          # Show the welcome page first. Restore only when Portfolio is opened,
          # leaving any guest draft intact while the user views their account.
          self._restore_on_resume = True
          set_url_hash({"page": "profile"})
      else:
        anvil.users.logout(invalidate_client_objects=True)
        self._user = None
        self._current_portfolio = None
        self._new_portfolio()
        self._refresh_account()
        self._set_portfolio_status("Signed out. Your saved portfolios are kept in your account.")
    finally:
      self._set_busy(False)

  @handle("portfolio_name_box", "change")
  def portfolio_name_box_change(self, **event_args):
    if not self._initializing and not self._busy:
      self._mark_portfolio_dirty()

  @handle("as_of_date_picker", "change")
  @handle("risk_free_box", "change")
  @handle("holdings_panel", "x-holding-changed")
  @handle("transaction_years_panel", "x-holding-changed")
  def portfolio_inputs_change(self, **event_args):
    # Binding/rebuilding editors is not a user edit. In particular, a date
    # change emitted while syncing must not discard the result being rendered.
    if self._initializing or self._busy:
      return
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
      invalidate_workspace()
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
      result = anvil.server.call("load_portfolio", portfolio["portfolio_id"])
      if not result["ok"]:
        self._set_portfolio_status(result["message"], is_error=True)
        return
      portfolio = result["portfolio"]
      self._apply_portfolio(portfolio, result.get("analysis"))
      self._set_portfolio_status('Opened "%s". Changes are saved when you click Save changes.' % portfolio["name"])
    finally:
      self._set_busy(False)

  def _apply_portfolio(self, portfolio, analysis=None):
    self._collapsed_transaction_years.clear()
    self._clear_image_import()
    self.simulations_form.reset_workspace()
    self.income_form.reset_workspace()
    self.retirement_form.reset_workspace()
    self._current_portfolio = portfolio
    self.portfolio_name_box.text = portfolio["name"]
    # Reopening a portfolio starts a fresh analysis through today. Users can
    # still choose a historical date for the current analysis.
    self._set_dates()
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
    self._readout_page = "overview"
    self._show_results(False)
    self._set_transactions_collapsed(False)
    self.saved_portfolios_dropdown.selected_value = next(
      (entry for _, entry in self.saved_portfolios_dropdown.items if entry["portfolio_id"] == portfolio.get_id()), None
    )
    self._update_simulations_context()
    self._set_status("Saved transactions loaded — sync to fetch daily market history.")
    if analysis is not None:
      self.as_of_date_picker.date = portfolio["as_of_date"]
      self._apply_analysis(analysis, self._read_transactions())
      self._set_transactions_collapsed(True)
      self._set_status("Saved analysis restored through %s. Sync history to refresh prices." % analysis["metadata"]["end_date"])
    self._update_controls()

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
    self._collapsed_transaction_years.clear()
    self._clear_image_import()
    today = date.today()
    self.transactions = [
      self._new_transaction("AAPL", today - timedelta(days=365), "Buy", "20"),
      self._new_transaction("MSFT", today - timedelta(days=210), "Buy", "10"),
    ]
    self._refresh_transactions()
    self._set_status("Sample transactions loaded — sync to fetch daily market history.")

  def _show_results(self, visible):
    if not visible:
      self._set_charts_expanded(False, resize=False)
      self._history = None
      self._analysis_source = None
      self._selected_symbols = set()
      self._analyzed_transactions = []
      self._allocation_snapshots = []
      self._allocation_snapshot = None
      self._dirty_readout_pages.update(("overview", "allocation", "performance"))
      self._plot_sizes.clear()
      self.allocation_quarter_dropdown.selected_value = "latest"
      self._clear_chart_trades()
      self.hedge_fund_comparison.reset_context()
    self._apply_readout_page()

  def _sharing_context(self):
    if self._history is None or not self._selected_symbols:
      raise ValueError("Sync your portfolio and select at least one security before sharing performance.")
    # Sharing can start from Overview before Performance has been opened.
    self._ensure_readout_rendered("performance")
    traces = self.performance_plot.data
    dates = traces[0].get("x", []) if traces else []
    if not dates:
      raise ValueError("Choose a valid performance date range and available benchmark before sharing.")
    transactions = [{
      "ticker": row["ticker"], "trade_date": row["trade_date"],
      "action": row["action"], "shares": row["shares"],
      "trade_price": row.get("trade_price"),
    } for row in self._analyzed_transactions]
    benchmark = self.benchmark_dropdown.selected_value
    portfolio_return = traces[0]["y"][-1]
    benchmark_return = traces[1]["y"][-1]
    benchmark_label = next(label for label, key in BENCHMARK_CHOICES if key == benchmark)
    summary_lines = [
      "My portfolio performance",
      "%s – %s" % (dates[0], dates[-1]),
      "Portfolio: %s" % self._format_percent(portfolio_return),
      "%s: %s" % (benchmark_label, self._format_percent(benchmark_return)),
      "Versus benchmark: %+.2f percentage points" % ((portfolio_return - benchmark_return) * 100),
      "Benchmark uses %s returns." % ("total" if self.benchmark_return_dropdown.selected_value == "total" else "price"),
      "Based on user-entered trades; portfolio dividends held as cash.",
    ]
    if not self._history["series"][-1].get("session_complete", True) and str(dates[-1]) == self._history["series"][-1]["date"]:
      summary_lines.append("Provisional regular-session prices as of %s." % self._history["metadata"].get("quoted_at"))
    summary = "\n".join(summary_lines)
    return {
      "transactions": transactions, "as_of_date": self.as_of_date_picker.date,
      "risk_free_rate": float(self.risk_free_box.text) / 100,
      "selected_symbols": sorted(self._selected_symbols), "benchmark": benchmark,
      "benchmark_return": self.benchmark_return_dropdown.selected_value,
      "start_date": str(dates[0]), "end_date": str(dates[-1]),
      "benchmark_label": benchmark_label, "summary": summary,
    }

  @handle("share_performance_button", "click")
  def share_performance_click(self, **event_args):
    if self._busy:
      return
    self._set_charts_expanded(False)
    self.share_composer.visible = True
    self.share_composer.show_for(self._sharing_context, self._ensure_signed_in)

  @handle("share_composer", "x-close-sharing")
  def close_sharing(self, **event_args):
    self.share_composer.visible = False

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
    # Read the editors before disabling or rebuilding them so Sync and Save
    # include the last active field even if its change event is still pending.
    self._commit_transaction_inputs()
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
    return chart_theme.layout({
      "margin": {"l": 48, "r": 18, "t": 38, "b": 80},
      "paper_bgcolor": chart_theme.PANEL, "plot_bgcolor": chart_theme.PANEL,
      "font": {"family": "Inter, sans-serif", "size": 13, "color": chart_theme.MUTED},
      "hovermode": "closest" if self.show_trades_checkbox.checked else "x unified",
      "legend": {"orientation": "h", "x": 0, "y": -0.25},
      "xaxis": {"type": "date", "showgrid": False, "fixedrange": False},
      "yaxis": {"tickformat": ".0%", "gridcolor": chart_theme.GRID, "zerolinecolor": chart_theme.ZERO_LINE, "fixedrange": False},
    })

  def _set_chart(self, plot, dates, portfolio, benchmark, benchmark_name, markers):
    plot.data = [
      {"type": "scatter", "mode": "lines", "name": "Your portfolio", "x": dates,
       "y": portfolio, "line": {"color": chart_theme.POSITIVE, "width": 2.5},
       "hovertemplate": "%{y:.2%}<extra>Your portfolio</extra>"},
      {"type": "scatter", "mode": "lines", "name": next((label.split(" · ")[0] for label, key in BENCHMARK_CHOICES
                      if key == self.benchmark_dropdown.selected_value), "Benchmark"), "x": dates,
       "y": benchmark, "line": {"color": chart_theme.BENCHMARK, "width": 2},
       "hovertemplate": "%{y:.2%}<extra>" + benchmark_name + "</extra>"},
    ] + markers
    plot.layout = self._chart_layout()
    plot.config = {"displayModeBar": True, "displaylogo": False, "responsive": True,
                   "scrollZoom": False,
                   "modeBarButtonsToRemove": ["select2d", "lasso2d"]}

  def _timeframe_buttons(self):
    return [("1M", self.range_1m_button), ("3M", self.range_3m_button),
            ("6M", self.range_6m_button), ("YTD", self.range_ytd_button),
            ("1Y", self.range_1y_button), ("3Y", self.range_3y_button),
            ("ALL", self.range_all_button), ("CUSTOM", self.range_custom_button)]

  def _chart_button_node(self, button):
    node = anvil.js.get_dom_node(button)
    if node.tagName == "BUTTON":
      return node
    # The M3 component wraps its native HTMLButtonElement. The browser stub
    # types querySelector as a generic Element, which omits button focus.
    native_button = getattr(node, "querySelector")("button")
    if native_button is None:
      raise RuntimeError("Chart control has no native button.")
    return native_button

  def _clear_chart_trades(self):
    self._chart_trade_groups = []
    self._chart_point_groups = [[], []]
    self.chart_trade_region.visible = False
    self.chart_trade_panel.items = []
    setattr(self.view_chart_trades_button, "enabled", False)

  def _show_chart_trade_choices(self, transactions, title):
    self.chart_trade_title_label.text = title
    self.chart_trade_panel.items = transactions
    self.chart_trade_region.visible = True
    anvil.js.get_dom_node(self.chart_trade_region).scrollIntoView({"block": "nearest"})

  @handle("performance_plot", "click")
  @handle("drawdown_plot", "click")
  def chart_trade_click(self, points, **event_args):
    if self._busy or self._history is None or not self.show_trades_checkbox.checked:
      return
    for point in points:
      curve = point.get("curve_number")
      number = point.get("point_number")
      if curve not in (2, 3) or not isinstance(number, int):
        continue
      indices = self._chart_point_groups[curve - 2]
      if number < 0 or number >= len(indices):
        continue
      group = self._chart_trade_groups[indices[number]]
      # Include both actions on a date so coincident buy/sell arrows never
      # hide access to an individual transaction.
      transactions = [row for same_day in self._chart_trade_groups
                      if same_day["date"] == group["date"]
                      for row in same_day["transactions"]]
      if len(transactions) == 1:
        self._open_chart_transaction(transactions[0]["transaction_index"])
      else:
        self._show_chart_trade_choices(transactions, "Trades on %s" % group["date"])
      return

  @handle("chart_trade_panel", "x-open-chart-transaction")
  def chart_transaction_choice(self, transaction_index, **event_args):
    if not self._busy:
      self._open_chart_transaction(transaction_index)

  def _open_chart_transaction(self, index):
    if self._history is None or index < 0 or index >= len(self.transactions):
      return
    target = self.transactions[index]
    self._set_charts_expanded(False)
    self._set_transactions_collapsed(False)
    self.chart_trade_region.visible = False
    for row in self.transactions:
      row["_chart_target"] = row is target
    for group in self._transaction_year_forms():
      if any(row is target for row in group.item["transactions"]):
        group.set_collapsed(False)
    for form in self._transaction_row_forms():
      selected = form.item is target
      form.classes["is-chart-target"] = selected
      if selected:
        form.set_collapsed(collapsed=False)
        node = anvil.js.get_dom_node(form)
        node.setAttribute("tabindex", "-1")
        node.focus({"preventScroll": True})
        node.scrollIntoView({"block": "center", "behavior": "smooth"})

  @handle("view_chart_trades_button", "click")
  def view_chart_trades_click(self, **event_args):
    if not self._busy and self._chart_trade_groups:
      transactions = [row for group in self._chart_trade_groups for row in group["transactions"]]
      self._show_chart_trade_choices(transactions, "Transactions in this period (%d)" % len(transactions))

  @handle("close_chart_trades_button", "click")
  def close_chart_trades_click(self, **event_args):
    self.chart_trade_region.visible = False

  def _set_charts_expanded(self, expanded, resize=True):
    if expanded == self._charts_expanded:
      return
    self._charts_expanded = expanded
    self.performance_panel.classes["is-expanded"] = expanded
    node = self.dom_nodes["performance_dialog"]
    node.setAttribute("role", "dialog" if expanded else "region")
    if expanded:
      node.setAttribute("aria-modal", "true")
    else:
      node.removeAttribute("aria-modal")
    self.expand_charts_button.text = "Restore split view" if expanded else "Expand charts"
    self.expand_charts_button.icon = "mi:fullscreen_exit" if expanded else "mi:fullscreen"
    self.performance_plot.height = "480px" if expanded else "380px"
    self.drawdown_plot.height = "360px" if expanded else "300px"
    self.quarterly_plot.height = "390px" if expanded else "330px"
    if resize:
      self._resize_readout_charts()
    if expanded:
      node.scrollTop = 0
      node.focus({"preventScroll": True})

  @handle("expand_charts_button", "click")
  def expand_charts_click(self, **event_args):
    if not self._busy and self._history is not None:
      self._set_charts_expanded(not self._charts_expanded)

  def performance_keydown(self, event):
    if not self._charts_expanded:
      return
    if event.key == "Escape":
      event.preventDefault()
      self._set_charts_expanded(False)
      self._chart_button_node(self.expand_charts_button).focus()
    elif event.key == "Tab":
      # Keep keyboard navigation within the expanded chart dialog.
      nodes = self.dom_nodes["performance_dialog"].querySelectorAll(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], summary, [tabindex='0']"
      )
      node_at = getattr(nodes, "item")
      visible = [node_at(index) for index in range(nodes.length)
                 if node_at(index).getClientRects().length]
      if visible:
        active = anvil.js.window.document.activeElement
        if event.shiftKey and (active == visible[0] or active == self.dom_nodes["performance_dialog"]):
          event.preventDefault()
          visible[-1].focus()
        elif not event.shiftKey and active == visible[-1]:
          event.preventDefault()
          visible[0].focus()

  @handle("reset_chart_zoom_button", "click")
  def reset_chart_zoom_click(self, **event_args):
    if not self._busy:
      self._render_comparison()

  @handle("range_1m_button", "click")
  @handle("range_3m_button", "click")
  @handle("range_6m_button", "click")
  @handle("range_ytd_button", "click")
  @handle("range_1y_button", "click")
  @handle("range_3y_button", "click")
  @handle("range_all_button", "click")
  @handle("range_custom_button", "click")
  def timeframe_shortcut_click(self, sender, **event_args):
    if not self._busy:
      for timeframe, button in self._timeframe_buttons():
        if sender is button:
          self.timeframe_dropdown.selected_value = timeframe
          self._render_comparison()
          return

  def _drawdowns(self, indices):
    peak = indices[0]
    values = []
    for value in indices:
      peak = max(peak, value)
      values.append(value / peak - 1)
    return values

  def _clear_comparison(self, message):
    self._clear_chart_trades()
    self.hedge_fund_comparison.reset_context()
    self.trade_markers_status_label.text = "No transactions to show in this period."
    self.comparison_period_label.text = message
    for label in (self.window_return_label, self.benchmark_return_label,
                  self.window_excess_label, self.window_annualized_label,
                  self.volatility_label, self.sharpe_label,
                  self.benchmark_volatility_label, self.benchmark_sharpe_label):
      label.text = "—"
    self.risk_status_label.text = ""
    self.performance_plot.data = []
    self.drawdown_plot.data = []
    self.quarterly_plot.data = []
    self.quarterly_panel.items = []
    self.quarterly_period_label.text = message

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
    fallback = "sp500" if "sp500" in available_keys else available_keys[0] if available_keys else None
    self.benchmark_dropdown.selected_value = selected if selected in available_keys else fallback
    selected = self.benchmark_dropdown.selected_value
    modes = []
    for label, mode in [("Total return · dividends reinvested", "total"),
                        ("Price return · excludes dividends", "price")]:
      if selected and benchmarks.get(selected + "_" + mode, {}).get("series"):
        modes.append((label, mode))
    previous_mode = self.benchmark_return_dropdown.selected_value
    self.benchmark_return_dropdown.items = modes
    self.benchmark_return_dropdown.selected_value = (
      previous_mode if previous_mode in [mode for label, mode in modes]
      else modes[0][1] if modes else None
    )
    self.benchmark_return_dropdown.enabled = not self._busy and len(modes) > 1

  def _render_comparison(self):
    if self._history is None or self._analysis_source is None:
      return
    series = self._history["series"]
    if not series:
      self._clear_comparison("Select at least one security to compare returns.")
      return
    timeframe = self.timeframe_dropdown.selected_value or "ALL"
    for value, button in self._timeframe_buttons():
      button.role = "chart-range-active" if value == timeframe else "chart-range"
      self._chart_button_node(button).setAttribute("aria-pressed", "true" if value == timeframe else "false")
    self._clear_chart_trades()
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
    benchmark_data = self._history["benchmarks"].get(benchmark_key)
    if not benchmark_data or not benchmark_data.get("series"):
      self.benchmark_status_label.text = "Portfolio prices are synced. A matching benchmark quote is not available yet; sync again when it updates."
      self._clear_comparison("No matching benchmark data is available for this period yet.")
      return
    benchmark_name = benchmark_data["name"]
    benchmark_by_date = {point["date"]: point["index"] for point in benchmark_data["series"]}
    benchmark_complete = {point["date"]: point.get("session_complete", True)
                          for point in benchmark_data["series"]}
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
    self.hedge_fund_comparison.configure_context([{
      "date": point["date"], "index": point["portfolio_index"],
      "return_observed": point["return_observed"],
      "session_complete": point.get("session_complete", True),
    } for point in selected], self._history["metadata"]["risk_free_rate"])
    dates = [point["date"] for point in selected]
    portfolio = [point["portfolio_index"] for point in selected]
    benchmark = [benchmark_by_date[point["date"]] for point in selected]
    portfolio_returns = [value / portfolio[0] - 1 for value in portfolio]
    benchmark_returns = [value / benchmark[0] - 1 for value in benchmark]
    completed = [point.get("session_complete", True) for point in selected]
    observed = [point["return_observed"] and completed[index] for index, point in enumerate(selected)]
    observations = sum(1 for available in observed[1:] if available)
    inactive_days = sum(completed[index] and not selected[index]["return_observed"]
                        for index in range(1, len(selected)))
    provisional = not completed[-1]
    self.comparison_period_label.text = "%s – %s · %d completed daily returns%s" % (
      dates[0], dates[-1], observations, " · provisional endpoint" if provisional else "")
    self.window_return_label.text = self._format_percent(portfolio_returns[-1])
    self.benchmark_return_label.text = self._format_percent(benchmark_returns[-1])
    self.window_excess_label.text = "%.2f pp" % ((portfolio_returns[-1] - benchmark_returns[-1]) * 100)
    days = (self._date_from_iso(dates[-1]) - self._date_from_iso(dates[0])).days
    exponent = math.log(portfolio[-1] / portfolio[0]) * 365.25 / days if days > 0 and portfolio[-1] > 0 and portfolio[0] > 0 else None
    self.window_annualized_label.text = (
      self._format_percent(math.exp(exponent) - 1)
      if exponent is not None and exponent < 700 and not inactive_days and not provisional else "—"
    )
    volatility, sharpe = self._daily_risk(portfolio, observed)
    self.volatility_label.text = "—" if volatility is None else "%.2f%%" % (volatility * 100)
    self.sharpe_label.text = self._format_ratio(sharpe)
    # A benchmark stays invested across every close, including sessions when
    # the selected portfolio had no capital. Use its own observation mask.
    benchmark_observed = [benchmark_complete[day] for day in dates]
    benchmark_observations = sum(benchmark_observed[1:])
    benchmark_volatility, benchmark_sharpe = self._daily_risk(benchmark, benchmark_observed)
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
    if provisional:
      message += " Current-session prices are provisional; the unfinished session is excluded from both risk estimates and annualized return."
    self.risk_status_label.text = message + " 252 trading days/year."
    drawdowns = self._drawdowns(portfolio)
    markers = build_trade_markers(
      self._analyzed_transactions, self._analysis_source["rows"],
      self._selected_symbols, dates, portfolio_returns, drawdowns,
    )
    self._chart_trade_groups = markers["groups"]
    self._chart_point_groups = markers["point_groups"]
    count = markers["transaction_count"]
    setattr(self.view_chart_trades_button, "enabled", bool(count) and not self._busy)
    self.trade_markers_status_label.text = (
      "%d trade%s in this period · Green arrows: buys. Red arrows: sells. Hover for details; click to open. Same-day trades are grouped." % (count, "" if count == 1 else "s")
      if self.show_trades_checkbox.checked and count
      else "No trades in this period for the selected securities." if self.show_trades_checkbox.checked
      else "Trade arrows hidden. View trades still opens transactions in this period."
    )
    self._set_chart(self.performance_plot, dates, portfolio_returns, benchmark_returns, benchmark_name,
                    markers["performance_traces"] if self.show_trades_checkbox.checked else [])
    self._set_chart(self.drawdown_plot, dates, drawdowns, self._drawdowns(benchmark), benchmark_name,
                    markers["drawdown_traces"] if self.show_trades_checkbox.checked else [])
    self._render_quarterly(selected, benchmark_by_date, benchmark_name)

  def _render_quarterly(self, points, benchmark_by_date, benchmark_name):
    rows = quarterly_returns(points, benchmark_by_date)
    short_benchmark_name = next((label.split(" · ")[0] for label, key in BENCHMARK_CHOICES
                                 if key == self.benchmark_dropdown.selected_value), "Benchmark")
    self.quarterly_panel.items = [dict(row,
      detail_text="%s – %s%s%s" % (row["start_date"], row["end_date"], " · window edge" if row["edge_window"] else "", " · provisional" if row.get("provisional") else ""),
      portfolio_text="—" if row["portfolio_return"] is None else self._format_percent(row["portfolio_return"]),
      benchmark_text="—" if row["benchmark_return"] is None else self._format_percent(row["benchmark_return"]),
      excess_text="—" if row["excess_return"] is None else "%.2f pp" % (row["excess_return"] * 100),
    ) for row in reversed(rows)]
    self.quarterly_period_label.text = "Calendar quarters within the displayed period · %s. Window edges may be partial; the latest quarter is provisional when it includes current-session prices." % benchmark_name
    data = [[row["start_date"], row["end_date"], row["observations"]] for row in rows]
    labels = [row["label"] for row in rows]
    self.quarterly_plot.data = [
      {"type": "bar", "name": "Your portfolio", "x": labels,
       "y": [row["portfolio_return"] for row in rows], "customdata": data,
       "marker": {"color": [chart_theme.NEGATIVE if (row["portfolio_return"] or 0) < 0 else chart_theme.POSITIVE for row in rows]},
       "hovertemplate": "%{x}<br>%{y:.2%}<br>%{customdata[0]} – %{customdata[1]}<br>%{customdata[2]} daily intervals<extra>Your portfolio</extra>"},
      {"type": "bar", "name": short_benchmark_name, "x": labels,
       "y": [row["benchmark_return"] for row in rows], "customdata": data,
       "marker": {"color": chart_theme.BENCHMARK},
       "hovertemplate": "%{x}<br>%{y:.2%}<br>%{customdata[0]} – %{customdata[1]}<extra>" + benchmark_name + "</extra>"},
    ]
    self.quarterly_plot.layout = chart_theme.layout({
      "barmode": "group", "bargap": 0.3, "margin": {"l": 48, "r": 12, "t": 40, "b": 55},
      "paper_bgcolor": chart_theme.PANEL, "plot_bgcolor": chart_theme.PANEL,
      "font": {"family": "Inter, sans-serif", "size": 13, "color": chart_theme.MUTED},
      "legend": {"orientation": "h", "y": 1.15},
      "xaxis": {"type": "category", "showgrid": False},
      "yaxis": {"tickformat": ".0%", "gridcolor": chart_theme.GRID, "zerolinecolor": chart_theme.ZERO_LINE},
    })
    self.quarterly_plot.config = {"displayModeBar": False, "responsive": True}

  def _render_sectors(self):
    if self._history is None or self._analysis_source is None:
      return
    self._allocation_snapshots = allocation_snapshots(self._analysis_source, self._selected_symbols)
    previous = self.allocation_quarter_dropdown.selected_value or "latest"
    self.allocation_quarter_dropdown.items = [(snapshot["label"], snapshot["key"]) for snapshot in reversed(self._allocation_snapshots)] or [("Latest", "latest")]
    keys = [snapshot["key"] for snapshot in self._allocation_snapshots]
    self.allocation_quarter_dropdown.selected_value = previous if previous in keys else "latest"
    palette = list(SECTOR_COLORS.values())
    symbols = sorted(row["symbol"] for row in self._analysis_source["selection_data"]["securities"])
    self._security_colors = {symbol: palette[index % len(palette)] for index, symbol in enumerate(symbols)}
    self._render_allocation_snapshot()
    self._render_allocation_history()

  def _render_allocation_snapshot(self):
    if self._history is None:
      return
    chosen = self.allocation_quarter_dropdown.selected_value
    self._allocation_snapshot = next((snapshot for snapshot in self._allocation_snapshots if snapshot["key"] == chosen), None)
    allocation = self._allocation_snapshot or self._history["sector_allocation"]
    dated = allocation.get("date", allocation.get("as_of"))
    slices = allocation["slices"]
    holdings = sorted(allocation["holdings"], key=lambda row: (-row["market_value"], row["symbol"]))
    total = allocation["total_value"]
    security_view = self.allocation_view_dropdown.selected_value == "security"
    snapshot_index = next((index for index, snapshot in enumerate(self._allocation_snapshots)
                           if snapshot["key"] == chosen), None)
    previous = self._allocation_snapshots[snapshot_index - 1] if snapshot_index is not None and snapshot_index > 0 else None
    groups = allocation_groups(allocation, security_view, previous)
    colors = [self._security_colors.get(group["symbol"], chart_theme.MUTED) if security_view else SECTOR_COLORS.get(group["sector"], chart_theme.MUTED) for group in groups]
    provisional = (self._allocation_snapshot or {}).get("provisional", bool(self._history["metadata"].get("provisional_date")))
    self.sector_period_label.text = "%s · %s in open positions%s" % (
      dated, self._format_currency(total), " · provisional prices" if provisional else ""
    )
    self.allocation_value_label.text = self._format_currency(total)
    self.allocation_count_label.text = str(len(holdings))
    self.allocation_largest_label.text = ("%s · %.1f%%" % (holdings[0]["symbol"], holdings[0]["market_value"] / total * 100)) if total else "—"
    self.allocation_top5_label.text = "%.1f%%" % (sum(row["market_value"] for row in holdings[:5]) / total * 100) if total else "—"
    equity_sectors = [group for group in slices if group["sector"] not in ("Funds / ETFs", "Unclassified") and group["market_value"] > 0]
    self.allocation_sector_count_label.text = str(len(equity_sectors))
    self.allocation_largest_sector_label.text = "%s · %.1f%%" % (equity_sectors[0]["sector"], equity_sectors[0]["weight"] * 100) if equity_sectors else "—"
    self.allocation_legend_label.text = "Securities by weight" if security_view else "Sectors by weight"
    self.allocation_change_label.text = (
      "Weight change vs %s · percentage points" % previous["date"]
      if previous and previous["total_value"] > 0 else "Weights use the selected snapshot's market values."
    )
    self.sector_empty_label.visible = not total
    self.sector_empty_label.text = (
      "Select a security below to see its allocation." if not self._selected_symbols
      else "No selected open positions at this snapshot. Closed trades and dividends still contribute to performance."
    )
    self.sector_plot.visible = total > 0
    self.sector_panel.items = [dict(group,
      color=color,
      weight_text="%.1f%%" % (group["weight"] * 100),
      value_text=self._format_currency(group["market_value"]),
    ) for group, color in zip(groups, colors)]
    self.sector_plot.data = [{
      "type": "pie", "hole": 0.72, "sort": False, "direction": "clockwise",
      "labels": [group["sector"] for group in groups],
      "values": [group["market_value"] for group in groups],
      "customdata": [group["tickers_text"] for group in groups],
      "marker": {"colors": colors, "line": {"color": chart_theme.PANEL, "width": 3}},
      "text": ["%.1f%%" % (group["weight"] * 100) if group["weight"] >= 0.04 else "" for group in groups],
      "textinfo": "text", "textposition": "inside",
      "insidetextfont": {"color": chart_theme.PIE_TEXT, "size": 13},
      "hovertemplate": "%{label}: %{percent}<br>$%{value:,.2f}<br>%{customdata}<extra></extra>",
    }] if total > 0 else []
    self.sector_plot.layout = chart_theme.layout({
      "margin": {"l": 8, "r": 8, "t": 15, "b": 15}, "showlegend": False,
      "paper_bgcolor": chart_theme.PANEL, "font": {"family": "Inter, sans-serif", "size": 14, "color": chart_theme.TEXT},
      "annotations": [{"text": str(len(groups)), "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.54, "showarrow": False, "font": {"size": 38, "color": chart_theme.TEXT}},
                      {"text": "holdings" if security_view else "allocation groups", "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.42, "showarrow": False, "font": {"size": 12, "color": chart_theme.MUTED}}],
    })
    self.sector_plot.config = {"displayModeBar": False, "responsive": True}
    unknown = [row["symbol"] for row in holdings if row["sector"] == "Unclassified"]
    self.sector_status_label.text = (
      "Choose a sector in the holdings table for %s. Their values remain included under Unclassified." % ", ".join(unknown)
      if unknown else "Folio classifications · Sector choices can be customized in the holdings table."
    )
    self._render_allocation_map(allocation)

  def _render_allocation_map(self, allocation):
    total = allocation["total_value"]
    self.allocation_map_region.visible = total > 0
    self.allocation_map_plot.visible = total > 0
    nodes = [{"id": "portfolio", "label": "Portfolio", "parent": "", "value": total,
              "detail": "Selected open positions", "color": chart_theme.PANEL}]
    for group in allocation["slices"]:
      if group["market_value"] <= 0:
        continue
      nodes.append({"id": "sector:" + group["sector"], "label": group["sector"], "parent": "portfolio",
                    "value": group["market_value"], "detail": "%d holding(s)" % len(group["tickers"]),
                    "color": SECTOR_COLORS.get(group["sector"], chart_theme.MUTED)})
    for holding in allocation["holdings"]:
      if holding["market_value"] <= 0:
        continue
      nodes.append({"id": "holding:" + holding["symbol"], "label": holding["symbol"],
                    "parent": "sector:" + holding["sector"], "value": holding["market_value"],
                    "detail": holding.get("company_name") or holding["symbol"],
                    "color": SECTOR_COLORS.get(holding["sector"], chart_theme.MUTED)})
    self.allocation_map_plot.data = [{
      "type": "treemap", "ids": [node["id"] for node in nodes],
      "labels": [node["label"] for node in nodes], "parents": [node["parent"] for node in nodes],
      "values": [node["value"] for node in nodes], "branchvalues": "total",
      "customdata": [[node["detail"], node["value"] / total] for node in nodes],
      "texttemplate": "%{label}<br>%{customdata[1]:.1%}",
      "hovertemplate": "%{label}<br>%{customdata[0]}<br>$%{value:,.2f}<br>%{customdata[1]:.1%} of open positions<extra></extra>",
      "marker": {"colors": [node["color"] for node in nodes], "line": {"color": chart_theme.PANEL, "width": 2}},
      "textfont": {"color": chart_theme.PIE_TEXT, "size": 14},
      "pathbar": {"visible": True, "textfont": {"color": chart_theme.TEXT}},
      "tiling": {"pad": 4}, "root": {"color": chart_theme.PANEL},
    }] if total > 0 else []
    self.allocation_map_plot.layout = chart_theme.layout({
      "margin": {"l": 0, "r": 0, "t": 4, "b": 0},
      "uniformtext": {"minsize": 11, "mode": "hide"},
    })
    self.allocation_map_plot.config = {"displayModeBar": False, "responsive": True}

  def _render_allocation_history(self):
    snapshots = self._allocation_snapshots
    security_view = self.allocation_view_dropdown.selected_value == "security"
    self.allocation_history_title_label.text = "Security allocation over time" if security_view else "Sector allocation over time"
    self.allocation_history_plot.visible = bool(snapshots)
    self.allocation_history_status_label.text = (
      "Quarter-end positions plus the latest snapshot. Select a bar to inspect it; the outlined bar is selected. Weights exclude cash."
      if snapshots else "Select securities and sync history to see quarterly position weights."
    )
    grouped = [{group["sector"]: group for group in allocation_groups(snapshot, security_view)} for snapshot in snapshots]
    names = set(name for groups in grouped for name in groups)
    selected = self.allocation_quarter_dropdown.selected_value
    current = next((groups for groups, snapshot in zip(grouped, snapshots) if snapshot["key"] == selected), {})
    names = sorted(names, key=lambda name: (-current.get(name, {}).get("weight", 0), name))
    self.allocation_history_plot.data = [
      {"type": "bar", "name": name, "x": [snapshot["label"] for snapshot in snapshots],
       "y": [groups.get(name, {}).get("weight", 0) if snapshot["total_value"] else None for groups, snapshot in zip(grouped, snapshots)],
       "customdata": [snapshot["date"] for snapshot in snapshots],
       "marker": {"color": self._security_colors.get(name, chart_theme.MUTED) if security_view else SECTOR_COLORS.get(name, chart_theme.MUTED),
                  "line": {"color": chart_theme.TEXT, "width": [2 if snapshot["key"] == selected else 0 for snapshot in snapshots]}},
       "hovertemplate": "%{x}<br>%{customdata}<br>%{y:.1%}<extra>" + name + "</extra>"}
      for name in names
    ]
    self.allocation_history_plot.height = "%dpx" % (360 + 22 * ((len(names) + 2) // 3))
    self.allocation_history_plot.layout = chart_theme.layout({
      "barmode": "stack", "bargap": 0.25, "margin": {"l": 48, "r": 12, "t": 15, "b": 75 + 22 * ((len(names) + 2) // 3)},
      "paper_bgcolor": chart_theme.PANEL, "plot_bgcolor": chart_theme.PANEL,
      "font": {"family": "Inter, sans-serif", "size": 13, "color": chart_theme.MUTED},
      "legend": {"orientation": "h", "y": -0.25, "traceorder": "normal"},
      "xaxis": {"type": "category", "showgrid": False},
      "yaxis": {"tickformat": ".0%", "dtick": 0.25, "range": [0, 1], "gridcolor": chart_theme.GRID, "fixedrange": True},
    })
    self.allocation_history_plot.config = {"displayModeBar": False, "responsive": True}

  @handle("allocation_view_dropdown", "change")
  @handle("allocation_quarter_dropdown", "change")
  def allocation_snapshot_change(self, **event_args):
    if not self._busy and self._history is not None:
      self._render_allocation_snapshot()
      self._render_allocation_history()
      self._render_security_rows()

  @handle("allocation_history_plot", "click")
  def allocation_history_click(self, points, **event_args):
    if not self._busy:
      for point in points:
        index = point.get("point_number")
        if isinstance(index, int) and 0 <= index < len(self._allocation_snapshots):
          self.allocation_quarter_dropdown.selected_value = self._allocation_snapshots[index]["key"]
          self.allocation_snapshot_change()
          return

  def _render_security_rows(self):
    if self._analysis_source is None or self._history is None:
      return
    securities = self._analysis_source["selection_data"]["securities"]
    allocation = self._allocation_snapshot or self._history["sector_allocation"]
    total_value = allocation["total_value"]
    snapshot_date = allocation.get("date", allocation.get("as_of"))
    dates = self._analysis_source["selection_data"]["dates"]
    index = dates.index(snapshot_date) if snapshot_date in dates else None
    rows = []
    for security in securities:
      included = security["symbol"] in self._selected_symbols
      position_day = security["days"][index] if index is not None else {}
      shares = position_day.get("shares", security["shares"])
      value = position_day.get("market_value", security["market_value"])
      # Historical snapshots must use only trades/dividends through that date.
      # An older analysis without daily stats shows blanks until the next sync.
      stats = position_day if index is not None else security
      buy_cost = stats.get("buy_cost")
      profit = stats.get("profit") if buy_cost else None
      profit_return = stats.get("profit_return") if buy_cost else None
      average_buy = stats.get("average_buy_price")
      average_sell = stats.get("average_sell_price")
      shares_text = format(shares, ",.8f").rstrip("0").rstrip(".")
      position = "%s shares" % shares_text if shares > 0 else "No open position at this snapshot"
      if shares <= 0 and buy_cost is not None:
        position = "Closed position" if buy_cost > 0 else "No trades by this snapshot"
      count = security["transaction_count"]
      rows.append(dict(security,
        sector_options=self._analysis_source.get("sector_options", sorted(SECTOR_COLORS)),
        shares_text=shares_text, is_open=shares > 0,
        included=included, snapshot_value=value,
        color=self._security_colors.get(security["symbol"], chart_theme.MUTED) if self.allocation_view_dropdown.selected_value == "security" else SECTOR_COLORS.get(security["sector"], chart_theme.MUTED),
        detail_text="%s · %s · %d trade%s total" % (security["sector"], position, count, "" if count == 1 else "s"),
        value_text=self._format_currency(value),
        profit=profit,
        profit_text=("—" if profit is None else ("+" if profit > 0 else "-" if profit < 0 else "") + self._format_currency(abs(profit))),
        profit_return_text="—" if profit_return is None else self._format_percent(profit_return),
        average_buy_text=self._format_average_price(average_buy),
        average_sell_text=self._format_average_price(average_sell),
        weight_percent=value / total_value * 100 if included and total_value else 0,
        weight_text=("%.1f%% of snapshot" % (value / total_value * 100)
                     if included and total_value else "0.0% of snapshot" if included else "Excluded"),
      ))
    query = str(self.allocation_search_box.text or "").strip().lower()
    position = self.allocation_position_dropdown.selected_value or "all"
    filtered = [row for row in rows
                if (not query or query in ("%s %s %s" % (row["symbol"], row.get("company_name") or "", row["sector"])).lower())
                and (position == "all" or row["is_open"] == (position == "open"))]
    self.security_panel.items = sorted(filtered, key=lambda row: (-row["snapshot_value"], row["symbol"]))
    self.allocation_filter_empty_label.visible = bool(rows) and not filtered
    self.security_panel.raise_event_on_children("x-set-editable", editable=not self._busy, can_classify=self._user is not None)
    self.selection_status_label.text = (
      "%d of %d securities selected · All metrics and charts use this selection." % (len(self._selected_symbols), len(securities))
      if self._selected_symbols else "No securities selected. Check a security or choose Select all."
    )
    if query or position != "all":
      self.selection_status_label.text += " Showing %d of %d holdings; filters do not change your selection." % (len(filtered), len(rows))

  @handle("allocation_search_box", "change")
  @handle("allocation_position_dropdown", "change")
  def allocation_filter_change(self, **event_args):
    if not self._busy:
      self._render_security_rows()

  @handle("security_panel", "x-sector-changed")
  def security_sector_change(self, symbol, sector, **event_args):
    if self._busy or self._analysis_source is None or self._user is None:
      return
    self._set_busy(True)
    try:
      result = anvil.server.call("set_security_sector", symbol, sector)
      if not result["ok"]:
        self._set_status(result["message"], is_error=True)
        self._render_security_rows()
        return
      update_sector_classification(self._analysis_source, symbol, result["sector"], result["sector_override"])
      self._history = analyze_selection(self._analysis_source, self._selected_symbols)
      self._render_readout()
      self._set_status("%s sector saved as %s. Charts updated." % (symbol, result["sector"]))
    finally:
      self._set_busy(False)

  def _format_average_price(self, price):
    if price is None:
      return "—"
    # Preserve useful precision for low-priced securities as well as averages.
    digits = 8 if price < .01 else 4 if price < 1 else 2
    return "$%s" % format(price, ",.%df" % digits)

  def _render_readout(self):
    if self._history is None:
      return
    self._dirty_readout_pages.update(("overview", "allocation", "performance"))
    self._render_active_readout()
    self._update_readout_context()

  def _render_overview(self):
    if self._history is None:
      return
    metrics = self._history["metrics"]
    for label, key in [(self.invested_label, "buy_cost"), (self.value_label, "market_value"),
                       (self.dividends_label, "dividends"), (self.sale_proceeds_label, "sale_proceeds"),
                       (self.profit_label, "profit")]:
      label.text = self._format_currency(metrics[key])
    self.return_label.text = "—" if metrics["simple_return"] is None else self._format_percent(metrics["simple_return"])
    self.annualized_label.text = "—" if metrics["annualized_return"] is None else self._format_percent(metrics["annualized_return"])
    for label, key in ((self.return_label, "simple_return"),
                       (self.annualized_label, "annualized_return"),
                       (self.profit_label, "profit")):
      value = metrics[key]
      label.foreground = (chart_theme.TEXT if value is None or value == 0 else
                          chart_theme.POSITIVE if value > 0 else chart_theme.NEGATIVE)

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
  @handle("show_trades_checkbox", "change")
  def comparison_inputs_change(self, **event_args):
    if not self._busy:
      self._update_benchmark_options()
      self._render_comparison()

  @handle("add_button", "click")
  def add_button_click(self, **event_args):
    if self._busy:
      return
    self._commit_transaction_inputs()
    self._collapsed_transaction_years[None] = False
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
  @handle("transaction_years_panel", "x-remove-holding")
  def holdings_panel_remove_holding(self, item, **event_args):
    if not self._busy and any(row is item for row in self.transactions):
      self._commit_transaction_inputs()
      self.transactions = [row for row in self.transactions if row is not item]
      self._refresh_transactions()
      self._mark_portfolio_dirty()
      self._show_results(False)
      self._set_status("Transaction removed.")

  @handle("analyze_button", "click")
  def analyze_button_click(self, **event_args):
    if self._busy:
      return
    try:
      transactions = self._read_transactions()
      as_of_date, risk_free_rate = self._read_analysis_settings(transactions)
    except ValueError as error:
      self._set_status(str(error), is_error=True)
      self._show_results(False)
      return

    self._set_busy(True)
    try:
      self._show_results(False)
      self._set_status("Loading history, latest session prices, dividends, sectors, and benchmarks…")
      document = [{
        "ticker": row["ticker"], "trade_date": row["trade_date"],
        "action": row["action"], "shares": row["shares"],
        "trade_price": row["trade_price"],
      } for row in transactions]
      portfolio_id = self._current_portfolio.get_id() if self._current_portfolio is not None else None
      result = anvil.server.call("analyze_portfolio_history", document, as_of_date, risk_free_rate / 100, portfolio_id)
      if not result["ok"]:
        self._set_status(result["message"], is_error=True)
        return
      self._apply_analysis(result, transactions)
      if result["metadata"].get("provisional_date"):
        timestamp = result["metadata"]["quoted_at"].replace("T", " ").replace("+00:00", " UTC")
        message = "Latest regular-session prices synced · oldest portfolio quote %s. Today's values are provisional; daily risk uses completed sessions." % timestamp
      else:
        message = "Daily history synced and analysis updated."
      moved_dates = result["metadata"]["moved_transaction_dates"]
      if moved_dates:
        message += " %d non-trading date(s) use the next available session." % moved_dates
      self._set_status(message)
    finally:
      self._set_busy(False)

  def _apply_analysis(self, result, transactions):
    self._analysis_source = result
    self._selected_symbols = set(row["symbol"] for row in result["selection_data"]["securities"])
    self._history = analyze_selection(result, self._selected_symbols)
    self._analyzed_transactions = list(transactions)
    for transaction, synced in zip(transactions, result["rows"]):
      transaction.update(synced)
      price = synced["current_price"]
      transaction["quote_text"] = "No current holding" if price is None else "USD %s" % self._format_currency(price)
      transaction["dividend_text"] = "Latest session price synced" if result["metadata"].get("provisional_date") else "Daily history synced"
    self._refresh_transactions()
    self.comparison_start_picker.date = self._date_from_iso(result["series"][0]["date"])
    self.comparison_end_picker.date = self._date_from_iso(result["series"][-1]["date"])
    self._show_results(True)
    self._update_benchmark_options()
    self._render_readout()

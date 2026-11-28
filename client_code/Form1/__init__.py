from datetime import date, timedelta
import math

import anvil.server
import anvil.users
import anvil.js
from ._anvil_designer import Form1Template
from anvil import *
from .. import chart_theme
from ..portfolio_display import empty_summary
from ..site_tracking import record_page
from ..account_profile import ensure_username, sign_in
from ..workspace_cache import account_workspace, invalidate_workspace, startup_workspace
from ..deferred_work import defer
from .. import startup_performance


SECTOR_COLORS = chart_theme.SECTOR_COLORS
HEDGE_FUND_COMPARISONS_ENABLED = False
HOLDINGS_MAP_ENABLED = False

BENCHMARK_CHOICES = [
  ("S&P 500 index", "sp500"), ("Nasdaq Composite index", "nasdaq"),
  ("SPY · S&P 500 ETF", "spy"), ("QQQ · Nasdaq-100 ETF", "qqq"),
  ("DIA · Dow Jones ETF", "dia"), ("IWM · Russell 2000 ETF", "iwm"),
  ("VTI · Total US market ETF", "vti"),
]


def _create_tool(name):
  if name == "income_form":
    from ..Components.IncomeTracker import IncomeTracker
    return IncomeTracker()
  if name == "tax_estimator_form":
    from ..Components.TaxEstimator import TaxEstimator
    return TaxEstimator()
  if name == "retirement_form":
    from ..Components.Retirement import Retirement
    return Retirement()
  if name == "notifications_form":
    from ..Components.Notifications import Notifications
    return Notifications()
  if name == "creator_workspace":
    from ..Components.CreatorWorkspace import CreatorWorkspace
    return CreatorWorkspace()
  if name == "share_composer":
    from ..Components.ShareComposer import ShareComposer
    return ShareComposer()
  if name == "hedge_fund_comparison":
    from ..Components.HedgeFundComparison import HedgeFundComparison
    return HedgeFundComparison()
  raise ValueError("Unknown tool")


class Form1(Form1Template):
  def __init__(self, initial_portfolio_id=None, defer_load=False, startup_ready=None,
               startup_request=None, **properties):
    startup_performance.mark("portfolio_build_start")
    self._initializing = True
    self._defer_control_updates = False
    super().__init__(**properties)
    startup_performance.mark("portfolio_template_ready")

    self.income_form = None
    self.tax_estimator_form = None
    self.retirement_form = None
    self.notifications_form = None
    self.creator_workspace = None
    self.share_composer = None
    self.hedge_fund_comparison = None

    self._user = None
    self._username = ""
    self._profile_summary = None
    self.account_id = None
    self._notification_badge_loading = False
    self._notification_badge_revision = 0
    self._set_notification_unread_count(0)
    self._restore_on_resume = False
    self.membership_region.visible = False
    self._creator_enabled = False
    self._creator_open = False
    self._creator_reference = None
    self._creator_email = None
    self.creator_access_region.visible = False
    self.creator_setup_button.visible = False
    self.creator_setup_region.visible = False
    self.creator_tab_button.visible = False
    self.creator_panel.visible = False
    self._current_portfolio = None
    self._portfolio_dirty = False
    self._edit_revision = 0
    self._autosave_generation = 0
    self._autosaving = False
    self._refresh_requested = False
    self._refreshing = False
    self._busy = False
    self._background_tool = None
    self._navigation_job = None
    self._navigation_history = None
    self._navigation_scope = None
    self._startup_loading = False
    self._startup_overview = None
    self._startup_scheduled = False
    self._initial_portfolio_id = initial_portfolio_id
    self._startup_ready = startup_ready
    self._startup_request = startup_request
    self.startup_region.visible = False
    self.transactions = []
    self._transaction_rows_dirty = True
    self._collapsed_transaction_years = {}
    self._collapsed_transaction_dates = {}
    self._date_groups_on = None
    self._history = None
    self._comparison_window_source = None
    self._comparison_window_key = None
    self._comparison_window_data = None
    self._performance_weighting = "manager"
    self._equal_weight_source = None
    self._equal_weight_history = None
    self._performance_details_open = False
    self._performance_details_dirty = True
    self._performance_details_context = None
    self._analysis_source = None
    self._last_synced_analysis = None
    self._last_synced_transactions = []
    self._selected_symbols = set()
    self._analyzed_transactions = []
    self._chart_trade_groups = []
    self._chart_point_groups = [[], []]
    self._charts_expanded = False
    self._transactions_collapsed = False
    self._allocation_snapshots = []
    self._allocation_snapshot = None
    self._allocation_data_ready = False
    self._allocation_groups_cache = {}
    self._allocation_history_key = None
    self._allocation_highlight_key = None
    self._allocation_trace_names = []
    self._allocation_row_names = set()
    self._allocation_hover_name = None
    self._allocation_hover_source = None
    self._allocation_hover_styling = False
    self._allocation_security_rows = None
    self._allocation_holdings_open = False
    self.allocation_map_region.visible = HOLDINGS_MAP_ENABLED
    self.allocation_history_plot.config = {"displayModeBar": False, "responsive": True, "scrollZoom": False}
    self._retirement_context_key = None
    self._tax_context_key = None
    self._tax_context_source = None
    self._income_context_key = None
    self._income_context_history = None
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
    self._portfolio_readout_page = "overview"
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
    self.transaction_view_dropdown.items = [("Group by date", "date"), ("Entry order", "entry"), ("Group by security", "security"), ("Group by year", "year")]
    self.transaction_view_dropdown.selected_value = "date"
    self.regroup_button.visible = False
    self.timeframe_dropdown.items = [
      ("1 month", "1M"), ("3 months", "3M"), ("6 months", "6M"),
      ("Year to date", "YTD"), ("1 year", "1Y"), ("3 years", "3Y"), ("5 years", "5Y"),
      ("All history", "ALL"), ("Custom dates", "CUSTOM"),
    ]
    self.timeframe_dropdown.selected_value = "3Y"
    self.timeframe_dropdown.visible = False
    self.benchmark_dropdown.items = [("S&P 500 index", "sp500")]
    self.benchmark_dropdown.selected_value = "sp500"
    self.benchmark_return_dropdown.items = [
      ("Total return · dividends reinvested", "total"),
      ("Price return · excludes dividends", "price"),
    ]
    self.benchmark_return_dropdown.selected_value = "total"
    for plot in (self.performance_plot, self.drawdown_plot, self.quarterly_plot):
      plot.config = {"displayModeBar": False, "displaylogo": False, "responsive": True, "scrollZoom": False}
    self.custom_period_region.visible = False
    self._set_dates()
    self.portfolio_name_box.text = ""
    self._refresh_transactions()
    self._set_status("Add your first security to start building your portfolio.")
    self._show_results(False)
    user = anvil.users.get_user()
    if defer_load and user is not None:
      # The Router's saved-values request can already be in flight. Attach the
      # page before applying it or loading any history and secondary tools.
      self._user = user
      self.account_id = user.get_id()
      self._startup_loading = True
      self.startup_region.visible = True
      self.guest_hero.visible = False
      self.welcome_region.visible = False
      self.workspace_region.visible = False
      self._set_busy(True)
      self._initializing = False
      self._update_controls()
      startup_performance.mark("portfolio_shell_ready")
      return
    self._refresh_account()
    if self._user is not None:
      self._set_busy(True)
      try:
        self._restore_last_portfolio(initial_portfolio_id)
      finally:
        self._set_busy(False)
    self._initializing = False
    self._update_controls()
    startup_performance.mark("portfolio_shell_ready")

  def prepare_navigation(self):
    """Prepare secondary views after the requested page is already visible."""
    if self._startup_loading or self.account_id is None:
      return
    scope = (self.account_id, self._autosave_generation)
    if self._navigation_scope == scope and self._navigation_history is self._history:
      return
    self._navigation_scope, self._navigation_history = scope, self._history
    job = self._navigation_job = object()
    history = self._history

    def current():
      return (job is self._navigation_job and self._history is history
              and self._workspace_matches(scope[0], self._current_portfolio.get_id()
                if self._current_portfolio is not None else None)
              and scope[1] == self._autosave_generation and not self._busy
              and not self._autosaving)

    for page in ("overview", "allocation", "performance"):
      defer(lambda page=page: self._prepare_view(page), current)
    defer(self._ensure_transaction_rows, current)
    for tool in ("retirement", "tax", "notifications", "income"):
      defer(lambda tool=tool: self._prepare_tool(tool), current)

  def _prepare_view(self, page):
    self._ensure_readout_rendered(page)
    if page == "overview":
      self.portfolio_holdings_form.prepare_activity()
    for _, plot in self._page_charts(page):
      plot.prepare()

  def _ensure_tool(self, name):
    form = getattr(self, name)
    if form is not None:
      return form
    form = _create_tool(name)
    setattr(self, name, form)
    events = {
      "income_form": {"x-income-busy": self.income_busy_change},
      "tax_estimator_form": {"x-tax-estimator-busy": self.tax_estimator_busy_change},
      "retirement_form": {"x-retirement-busy": self.retirement_busy_change,
                          "x-use-portfolio-balance": self.retirement_use_portfolio},
      "notifications_form": {"x-notifications-busy": self.notifications_busy_change,
                             "x-notifications-unread": self.notifications_unread_change},
      "creator_workspace": {"x-import-creator-portfolio": self.import_creator_portfolio,
                            "x-membership-changed": self.creator_membership_changed},
      "share_composer": {"x-close-sharing": self.close_sharing},
    }
    for event, handler in events.get(name, {}).items():
      form.set_event_handler(event, handler)
    if name == "notifications_form":
      getattr(form, "set_account_context")(self.account_id)
      getattr(form, "set_active")(self._readout_page == "notifications")
    elif name == "creator_workspace":
      getattr(form, "configure_access")(self._creator_enabled, self._creator_email)
    elif name == "hedge_fund_comparison":
      getattr(form, "set_creator_access")(self._creator_enabled)
    elif name == "share_composer":
      getattr(form, "set_account_context")()
      form.visible = False
    self._update_retirement_context()
    self._update_tax_estimator_context()
    if name != "share_composer":
      form.raise_event("x-set-editable", editable=not self._busy)
    getattr(self, name + "_host").add_component(form)
    return form

  def _reset_tools(self):
    for form in (self.tax_estimator_form, self.income_form, self.retirement_form):
      if form is not None:
        form.reset_workspace()

  def _prepare_tool(self, tool, prepare=None):
    if prepare is None:
      name, method = {"retirement": ("retirement_form", "redraw"),
                      "tax": ("tax_estimator_form", "redraw"),
                      "notifications": ("notifications_form", "preload"),
                      "income": ("income_form", "ensure_loaded")}[tool]
      prepare = getattr(self._ensure_tool(name), method)
    if tool == "tax" and self.tax_estimator_form is not None and self.tax_estimator_form._inputs_dirty:
      return
    self._background_tool = tool
    try:
      prepare()
    finally:
      self._background_tool = None

  def _prefetched_startup_ready(self):
    request, self._startup_request = self._startup_request, None
    if request is None:
      return
    if not request.succeeded:
      self._release_startup()
      return
    self._load_startup(request)

  def _load_startup(self, request=None):
    account_id = self.account_id
    history_pending = False
    # Account metadata and the saved portfolio both refresh controls. Apply
    # their final state once, after all startup state has been populated.
    self._defer_control_updates = True
    try:
      if not self._workspace_matches(account_id, None):
        return
      if request is None:
        startup_performance.mark("values_load_start")
        result = startup_workspace(self._initial_portfolio_id)
        startup_performance.mark("values_load_end")
      else:
        result = request.result
      if result is None or not self._workspace_matches(account_id, None):
        return
      # startup_workspace already populated the shared account metadata cache.
      self._refresh_account(require_profile=False)
      if not self._workspace_matches(account_id, None):
        return
      if not result["ok"]:
        self._set_portfolio_status(result["message"], is_error=True)
      elif result["portfolio"] is None:
        self._new_portfolio()
      else:
        portfolio = result["portfolio"]
        self._apply_portfolio(portfolio, overview=result["overview"])
        self._set_portfolio_status('Welcome back — reopened "%s".' % portfolio["name"])
        if result["overview"] is not None:
          self.startup_region.visible = False
          self.workspace_region.visible = True
          startup_performance.mark("values_ready")
          startup_performance.after_frame("values_frame")
          generation = self._autosave_generation
          defer(lambda: self._load_startup_history(account_id, portfolio.get_id(), generation), priority=True)
          history_pending = True
      if (self._profile_summary or {}).get("investor_pending"):
        defer(lambda: self._load_startup_profile(account_id))
    finally:
      self._defer_control_updates = False
      if history_pending:
        self._update_controls()
      else:
        self._release_startup()
    if not history_pending:
      self._complete_startup()

  def _load_startup_profile(self, account_id):
    user = anvil.users.get_user()
    if user is None or user.get_id() != account_id or self.account_id != account_id:
      return
    workspace = account_workspace()
    user = anvil.users.get_user()
    if (workspace is None or user is None or user.get_id() != account_id
        or self.account_id != account_id):
      return
    self._profile_summary = workspace["profile"]
    self._update_welcome()

  def _load_startup_history(self, account_id, portfolio_id, generation):
    try:
      if generation != self._autosave_generation or not self._workspace_matches(account_id, portfolio_id):
        return
      startup_performance.mark("portfolio_restore_start")
      result = anvil.server.call("get_portfolio_history", portfolio_id)
      if generation != self._autosave_generation or not self._workspace_matches(account_id, portfolio_id):
        return
      if result["ok"]:
        self._apply_portfolio(result["portfolio"], result.get("analysis"), holdings_snapshot=result.get("holdings"))
      else:
        self._new_portfolio()
        self._set_portfolio_status(result["message"], is_error=True)
      startup_performance.mark("portfolio_restore_end")
    finally:
      self._release_startup()
    self._complete_startup()

  def _release_startup(self):
    # A failed or obsolete history read must not leave a partial snapshot
    # available to editing, chart, sharing or planning actions.
    if self._startup_overview is not None:
      self._show_results(False)
      self._set_status("Saved transactions loaded — Refresh to load analysis.")
    self._startup_loading = False
    self.startup_region.visible = False
    self.workspace_region.visible = True
    self._set_busy(False)

  def _complete_startup(self):
    self._apply_readout_page(redraw=True)
    self.prepare_navigation()
    if self._startup_ready is not None:
      self._startup_ready(self)
    startup_performance.complete()

  @handle("", "show")
  def form_show(self, **event_args):
    startup_performance.mark("portfolio_attached")
    startup_performance.after_frame("portfolio_frame")
    if self._startup_loading:
      if not self._startup_scheduled:
        self._startup_scheduled = True
        if self._startup_request is None:
          defer(self._load_startup, priority=True)
        else:
          self._startup_request.when_ready(self._prefetched_startup_ready)
      return
    # Router may prepare a retained workspace while detached. Once visible,
    # resize its existing plots without fetching data or resetting the editor.
    if not self._initializing and not self._busy:
      self._refresh_date_groups_if_needed()
      self._apply_readout_page(redraw=True)
      startup_performance.complete()

  @handle("market_status_timer", "tick")
  def market_status_timer_tick(self, **event_args):
    if (self._market_status_loading
        or (self._startup_loading and self._startup_overview is None)):
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
    self._update_tax_estimator_context()
    readout = self._history or self._startup_overview
    if readout is not None:
      self.period_label.text = "%s · %d selected open holdings%s" % (
        readout["metadata"]["end_date"], readout["metrics"]["holding_count"],
        " · provisional prices" if readout["metadata"].get("provisional_date") else ""
      )
    name = {"overview": "Top Holdings", "transactions": "Transactions", "allocation": "Portfolio structure",
            "performance": "Performance", "dividends": "Dividends", "passive": "Passive Income",
            "tax": "Tax Harvester", "tax_estimator": "Tax Estimator", "retirement": "Retirement",
            "notifications": "Notifications"}[self._readout_page]
    if self._readout_page == "transactions":
      self.readout_tab_context.text = "Transactions · Changes save automatically when signed in. Refresh updates prices through today."
    elif self._readout_page == "tax_estimator":
      self.readout_tab_context.text = "Tax Estimator · A quick estimate from your synced portfolio"
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
    elif self._startup_overview is not None:
      self.readout_tab_context.text = "Saved holdings · Loading chart history…"
    elif self._analysis_source is not None:
      count = len(self._analysis_source["selection_data"]["securities"])
      self.readout_tab_context.text = "%s · %d of %d securities selected" % (
        name, len(self._selected_symbols), count
      )
    else:
      self.readout_tab_context.text = "%s · Sync history to see your readout" % name

  def _apply_readout_page(self, redraw=False):
    has_analysis = self._history is not None or self._startup_overview is not None
    tool = {"retirement": "retirement_form", "tax_estimator": "tax_estimator_form",
            "notifications": "notifications_form", "dividends": "income_form",
            "passive": "income_form", "tax": "income_form"}.get(self._readout_page)
    if tool is not None and not self._startup_loading:
      self._ensure_tool(tool)
    if self.notifications_form is not None:
      self.notifications_form.set_active(self._readout_page == "notifications")
    if self._readout_page != "notifications":
      self._portfolio_readout_page = self._readout_page
    self.portfolio_header_link.attrs["aria-current"] = "false" if self._readout_page == "notifications" else "page"
    editing = self._readout_page == "transactions"
    self.transactions_column.visible = editing
    self.readout_column.visible = not editing
    self._transactions_collapsed = not editing
    if editing and not self._startup_loading:
      self._ensure_transaction_rows()
    self.workspace_region.classes["transactions-collapsed"] = not editing
    self.toggle_transactions_button.text = "View holdings" if editing else "Edit transactions"
    self.transactions_tab_button.role = "readout-tab-active" if editing else "readout-tab"
    self.transactions_tab_button.enabled = not self._busy
    portfolio_page = self._readout_page in ("overview", "allocation", "performance")
    self.results_section.visible = has_analysis and portfolio_page
    self.empty_results.visible = not has_analysis and portfolio_page
    self.hedge_fund_panel.visible = HEDGE_FUND_COMPARISONS_ENABLED and self._readout_page == "performance"
    for name, panel, button in (
      ("overview", self.overview_panel, self.overview_tab_button),
      ("allocation", self.allocation_panel, self.allocation_tab_button),
      ("performance", self.performance_panel, self.performance_tab_button),
      ("retirement", self.retirement_panel, self.retirement_tab_button),
      ("tax_estimator", self.tax_estimator_panel, self.tax_estimator_tab_button),
      ("notifications", self.notifications_panel, self.notifications_tab_button),
    ):
      active = name == self._readout_page
      panel.visible = active
      button.role = ("masthead-tab-active" if active else "masthead-tab") if name == "notifications" else ("readout-tab-active" if active else "readout-tab")
      setattr(button, "enabled", not self._busy)
    income_page = self._readout_page in ("dividends", "passive", "tax")
    self.income_panel.visible = income_page
    for name, button in (("dividends", self.dividends_tab_button),
                         ("passive", self.passive_tab_button), ("tax", self.tax_tab_button)):
      button.role = "readout-tab-active" if name == self._readout_page else "readout-tab"
      setattr(button, "enabled", not self._busy)
    if income_page:
      if self.income_form is not None:
        self.income_form.set_view(self._readout_page)
    self._update_readout_context()
    if redraw:
      self._render_active_readout()
      self._resize_readout_charts()
      if self._readout_page == "notifications":
        if self.notifications_form is not None:
          self.notifications_form.ensure_loaded()
      elif self._readout_page == "overview" and has_analysis:
        self.portfolio_holdings_form.redraw()
      elif self._readout_page == "tax_estimator":
        if self.tax_estimator_form is not None:
          self.tax_estimator_form.redraw()
      elif self._readout_page == "performance" and HEDGE_FUND_COMPARISONS_ENABLED:
        if self.hedge_fund_comparison is not None:
          self.hedge_fund_comparison.redraw()
      elif self._readout_page in ("dividends", "passive", "tax"):
        if self.income_form is not None:
          self.income_form.ensure_loaded()
        if self.income_form is not None:
          self.income_form.redraw()
      elif self._readout_page == "retirement":
        if self.retirement_form is not None:
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
      return [("performance", self.performance_plot)] + (
        [("quarterly", self.quarterly_plot), ("drawdown", self.drawdown_plot)]
        if self._performance_details_open else []
      )
    if page == "allocation":
      return [("allocation_history", self.allocation_history_plot)] + (
        [("allocation_map", self.allocation_map_plot)] if HOLDINGS_MAP_ENABLED else []
      )
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
    self.portfolio_holdings_form.apply_appearance()
    self._render_portfolio_summary()
    self._dirty_readout_pages.update(("overview", "allocation", "performance"))
    self._render_active_readout()
    if self._readout_page == "overview" and self._history is not None:
      self.portfolio_holdings_form.redraw()
    if self._readout_page == "tax_estimator":
      if self.tax_estimator_form is not None:
        self.tax_estimator_form.apply_appearance()
    elif self._readout_page == "performance" and HEDGE_FUND_COMPARISONS_ENABLED:
      if self.hedge_fund_comparison is not None:
        self.hedge_fund_comparison.apply_appearance()
    elif self._readout_page in ("dividends", "passive", "tax"):
      if self.income_form is not None:
        self.income_form.apply_appearance()
    elif self._readout_page == "retirement":
      if self.retirement_form is not None:
        self.retirement_form.apply_appearance()
    if self._creator_open:
      if self.creator_workspace is not None:
        self.creator_workspace.apply_appearance()
    if self.share_composer is not None and self.share_composer.visible:
      self.share_composer.apply_appearance()

  def _select_readout_page(self, page):
    if (self._busy or page == self._readout_page or
        page not in ("overview", "transactions", "allocation", "performance", "tax_estimator", "retirement", "dividends", "passive", "tax", "notifications")):
      return
    if page != "performance":
      self._set_charts_expanded(False, resize=False)
    if page != "allocation":
      self._set_allocation_hover(None)
    self._readout_page = page
    if page == "transactions":
      self._refresh_date_groups_if_needed()
    self._apply_readout_page(redraw=True)
    self.dom_nodes["readout_scroller"].scrollTop = 0
    record_page(page)

  def _set_creator_open(self, opened):
    self._creator_open = bool(opened and self._creator_enabled)
    self.creator_panel.visible = self._creator_open
    self.creator_tab_button.text = "Close creator workspace" if self._creator_open else "Open creator workspace"
    if self._creator_open:
      self._ensure_tool("creator_workspace").open_library()

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

  def import_creator_portfolio(self, portfolio_id, **event_args):
    if self._busy or not self._creator_enabled or not self._confirm_discard():
      return
    self._set_busy(True)
    try:
      result = anvil.server.call("import_creator_portfolio", portfolio_id)
      if not result["ok"]:
        self._ensure_tool("creator_workspace").show_status(result["message"], is_error=True)
        return
      portfolio = result["portfolio"]
      self._apply_portfolio(portfolio)
      self._set_creator_open(False)
      self._refresh_portfolios()
      self._set_portfolio_status('Imported "%s" as a saved copy in your account. Sync to analyze it.' % portfolio["name"])
    finally:
      self._set_busy(False)

  def _set_transactions_collapsed(self, collapsed):
    if not collapsed:
      self._readout_page = "transactions"
    elif self._readout_page == "transactions":
      self._readout_page = "overview"
    self._apply_readout_page(redraw=True)

  @handle("toggle_transactions_button", "click")
  def toggle_transactions_click(self, **event_args):
    if not self._busy:
      self._select_readout_page("overview" if self._readout_page == "transactions" else "transactions")

  @handle("transactions_tab_button", "click")
  @handle("empty_add_security_button", "click")
  def transactions_tab_click(self, **event_args):
    self._select_readout_page("transactions")

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
    source = self._last_synced_analysis
    if source is not None:
      value = source["metrics"]["market_value"]
      as_of = source["metadata"]["end_date"]
    portfolio_id = self._current_portfolio.get_id() if self._current_portfolio is not None else None
    key = (portfolio_id, self.account_id, value, as_of)
    if self.retirement_form is not None and key != self._retirement_context_key:
      self.retirement_form.set_portfolio_context(value, as_of, portfolio_id=portfolio_id)
      self._retirement_context_key = key

  def retirement_use_portfolio(self, **event_args):
    source = self._last_synced_analysis
    if not self._busy and source is not None and self.retirement_form is not None:
      self.retirement_form.set_starting_balance(
        source["metrics"]["market_value"], source["metadata"]["end_date"]
      )

  def retirement_busy_change(self, busy, **event_args):
    self._set_busy(busy)

  def _update_tax_estimator_context(self):
    # The planning tools use the latest complete sync, independent of filters
    # and unsynced transaction drafts. Switching tabs never fetches prices.
    source = self._last_synced_analysis
    portfolio_id = self._current_portfolio.get_id() if self._current_portfolio is not None else None
    key = (portfolio_id, self.account_id)
    if self.tax_estimator_form is not None and (self._tax_context_source is not source or self._tax_context_key != key):
      self.tax_estimator_form.set_portfolio_context(
        self._last_synced_transactions,
        source["metadata"]["end_date"] if source is not None else None,
        portfolio_id=portfolio_id, analysis=source,
      )
      self._tax_context_source, self._tax_context_key = source, key
    if self.income_form is None or (self._income_context_history is self._history and self._income_context_key == key):
      return
    transactions = []
    as_of = None
    if self._history is not None and self._selected_symbols:
      # Copy only the simulation inputs, never hand the child live editor rows.
      transactions = [{key: row.get(key) for key in
                       ("ticker", "trade_date", "action", "shares", "trade_price")}
                      for row in self._analyzed_transactions
                      if self._security_key(row) in self._selected_symbols]
      as_of = self._history["metadata"]["end_date"]
    self.income_form.set_portfolio_context(transactions, as_of)
    self._income_context_history, self._income_context_key = self._history, key

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
      if self.notifications_form is not None:
        self.notifications_form.ensure_loaded()

  def notifications_busy_change(self, busy, **event_args):
    if self._background_tool != "notifications":
      self._set_busy(busy)

  def _set_notification_unread_count(self, count):
    self._notification_badge_revision += 1
    self._notification_badge_retry_interval = 5
    count = max(0, int(count or 0)) if self.account_id else 0
    self.notification_badge.visible = count > 0
    self.notification_badge_label.text = "99+" if count > 99 else str(count)
    description = "%s unread notification%s" % (count, "" if count == 1 else "s")
    self.notification_badge_accessible_label.text = description if self.account_id else ""
    self.notifications_tab_button.tooltip = description if self.account_id else "Sign in to view your notifications"
    self.notification_badge_timer.interval = 60 if self.account_id else 0

  def notifications_unread_change(self, count, account_id, **event_args):
    if account_id == self.account_id:
      self._set_notification_unread_count(count)

  def _refresh_notification_unread_count(self):
    if not self.account_id or self._notification_badge_loading:
      return
    account_id = self.account_id
    revision = self._notification_badge_revision
    self._notification_badge_loading = True
    self.notification_badge_timer.interval = 0
    try:
      try:
        count = anvil.server.call_s("get_notification_unread_count")
      # The runtime exposes this exception; the client API stubs omit it.
      except (getattr(anvil.server, "RuntimeUnavailableError"), anvil.server.AppOfflineError):
        # This optional read must not interrupt the portfolio when the server
        # restarts or the connection drops. Keep the last known count intact.
        if account_id == self.account_id and revision == self._notification_badge_revision:
          description = "Notification count temporarily unavailable. Retrying automatically."
          self.notification_badge_accessible_label.text = description
          self.notifications_tab_button.tooltip = description
          self.notification_badge_timer.interval = self._notification_badge_retry_interval
          self._notification_badge_retry_interval = min(60, self._notification_badge_retry_interval * 3)
        return
      # A newer inbox response or account change takes precedence over a poll.
      if account_id == self.account_id and revision == self._notification_badge_revision:
        self._set_notification_unread_count(count)
    finally:
      self._notification_badge_loading = False

  @handle("notification_badge_timer", "tick")
  def notification_badge_timer_tick(self, **event_args):
    if self._initializing or self._busy or self._notification_badge_loading:
      self.notification_badge_timer.interval = 1 if self.account_id else 0
      return
    self._refresh_notification_unread_count()

  def income_busy_change(self, busy, **event_args):
    if self._background_tool != "income":
      self._set_busy(busy)

  @handle("tax_estimator_tab_button", "click")
  def tax_estimator_tab_click(self, **event_args):
    self._select_readout_page("tax_estimator")

  def tax_estimator_busy_change(self, busy, **event_args):
    if self._background_tool != "tax":
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
      self._collapsed_transaction_dates.clear()
      self._date_groups_on = None
    else:
      self._commit_transaction_inputs()
    self.transactions = imported if replacing else self.transactions + imported
    self._refresh_transactions()
    self._mark_portfolio_dirty()
    self._show_results(False)
    count = len(imported)
    self._clear_image_import()
    self._set_image_import_status("Imported %d row%s. Changes save automatically; Refresh updates prices." % (count, "" if count == 1 else "s"))
    self._set_status("Imported positions ready — sync history to calculate the updated readout.")

  def _refresh_transactions(self):
    # Keep the authoritative dictionaries current without constructing hidden
    # editors. Warm them after the readout, or immediately when editing.
    self._transaction_rows_dirty = True
    # Chart links use the original input order, including omitted blank rows,
    # even when no editor has been constructed for those transactions yet.
    for index, row in enumerate(self.transactions):
      row["_transaction_index"] = index
    if self._readout_page == "transactions" and not self._startup_loading:
      self._ensure_transaction_rows()

  def _ensure_transaction_rows(self):
    if not self._transaction_rows_dirty:
      return
    from ..transaction_groups import group_transactions_by_date, group_transactions_by_year
    self.transactions_empty_region.visible = not bool(self.transactions)
    self.add_button.text = "Add A Security!" if not self.transactions else "Add security / transaction"
    view = self.transaction_view_dropdown.selected_value
    grouped = view == "security"
    by_year = view == "year"
    by_date = view == "date"
    sections = by_year or by_date
    self.regroup_button.visible = grouped or sections
    self.collapse_prior_years_button.visible = sections
    self.collapse_prior_years_button.text = "Collapse older dates" if by_date else "Collapse prior years"
    self.collapse_all_button.text = "Collapse sections" if sections else "Collapse all"
    self.expand_all_button.text = "Expand sections" if sections else "Expand all"
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
    # Callers commit editors before user-initiated rebuilds. Do not commit here:
    # Sync and portfolio replacement already supplied the authoritative rows.
    self.holdings_panel.raise_event_on_children("x-set-editable", editable=False)
    self.transaction_years_panel.raise_event_on_children("x-set-editable", editable=False)
    self.holdings_panel.visible = bool(self.transactions) and not sections
    self.transaction_years_panel.visible = bool(self.transactions) and sections
    if by_date:
      self.holdings_panel.items = []
      self._date_groups_on = date.today()
      self.transaction_years_panel.items = group_transactions_by_date(
        self.transactions, self._collapsed_transaction_dates, self._date_groups_on
      )
    elif by_year:
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
      "Click a date heading to expand or condense its transactions. Older dates start folded; your choices stay while you switch views. Weeks start on Monday. Use Regroup after changing dates."
      if by_date else
      "Newest dates first. Click a year to fold it away. Use Regroup after changing dates."
      if by_year else
      "Trades are grouped by ticker. Use Regroup after editing a ticker or action."
      if grouped else "Transactions appear in the order you entered them."
    )
    self._update_image_import_controls()
    self._transaction_rows_dirty = False

  def _refresh_date_groups_if_needed(self):
    if self.transaction_view_dropdown.selected_value == "date" and self._date_groups_on != date.today():
      self._commit_transaction_inputs()
      self._refresh_transactions()

  def _transaction_year_forms(self):
    if self._transaction_rows_dirty:
      return []
    groups = self.transaction_years_panel.get_components()
    if not groups:
      return []
    from ..Components.TransactionYearGroup import TransactionYearGroup
    return [group for group in groups if isinstance(group, TransactionYearGroup)]

  def _transaction_row_forms(self):
    if self._transaction_rows_dirty:
      return []
    components = self.holdings_panel.get_components()
    rows = []
    if components:
      from ..HoldingRow import HoldingRow
      rows = [form for form in components if isinstance(form, HoldingRow)]
    for group in self._transaction_year_forms():
      rows.extend(group.transaction_rows())
    return rows

  def _commit_transaction_inputs(self):
    for row in self._transaction_row_forms():
      row.commit_inputs()

  @handle("transaction_years_panel", "x-group-collapsed")
  def transaction_group_collapsed(self, key, grouping, collapsed, **event_args):
    states = self._collapsed_transaction_dates if grouping == "date" else self._collapsed_transaction_years
    states[key] = collapsed

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
      panel = self.transaction_years_panel if self.transaction_view_dropdown.selected_value in ("date", "year") else self.holdings_panel
      panel.raise_event_on_children("x-set-collapsed", collapsed=True)

  @handle("expand_all_button", "click")
  def expand_all_button_click(self, **event_args):
    if not self._busy:
      panel = self.transaction_years_panel if self.transaction_view_dropdown.selected_value in ("date", "year") else self.holdings_panel
      panel.raise_event_on_children("x-set-collapsed", collapsed=False)

  @handle("collapse_prior_years_button", "click")
  def collapse_prior_years_click(self, **event_args):
    view = self.transaction_view_dropdown.selected_value
    if self._busy or view not in ("date", "year"):
      return
    self._commit_transaction_inputs()
    self._refresh_transactions()
    for group in self._transaction_year_forms():
      if view == "date":
        group.set_collapsed(not group.item["recent"])
      else:
        year = group.item["year"]
        group.set_collapsed(year is not None and year < date.today().year)

  def _show_entry_order_for_error(self, message):
    if message.startswith("Row ") or message.startswith("Add at least one transaction"):
      self._set_transactions_collapsed(False)
    # Validation row numbers refer to the original transaction order.
    if message.startswith("Row ") and self.transaction_view_dropdown.selected_value != "entry":
      if self.transaction_view_dropdown.selected_value in ("date", "year"):
        number = message.split(":", 1)[0][4:]
        if number.isdigit() and 0 < int(number) <= len(self.transactions):
          target = self.transactions[int(number) - 1]
          for group in self._transaction_year_forms():
            if any(row is target for row in group.item["transactions"]):
              group.set_collapsed(False)
          for form in self._transaction_row_forms():
            if form.item is target:
              form.set_collapsed(False)
              return
      self.transaction_view_dropdown.selected_value = "entry"
      self._refresh_transactions()

  def _set_portfolio_status(self, message, is_error=False):
    if is_error:
      self._show_entry_order_for_error(message)
    self.portfolio_status_label.text = message
    self.portfolio_status_region.classes["is-error"] = is_error

  def _set_last_refreshed(self, refreshed_at):
    self.last_refreshed_label.text = refreshed_at.strftime("%b %d, %Y") if refreshed_at is not None else "—"

  def _update_controls(self):
    if self._initializing or self._defer_control_updates:
      return
    self._update_welcome()
    setattr(self.share_performance_button, "enabled", not self._busy)
    for component in (
      self.account_button, self.profile_button, self.new_portfolio_button,
      self.refresh_membership_button,
      self.plans_button, self.subscription_button,
      self.portfolio_name_box, self.add_button, self.sample_button,
      self.analyze_button, self.as_of_date_picker, self.risk_free_box,
      self.manager_weighted_button, self.equal_weighted_button,
      self.timeframe_dropdown, self.benchmark_dropdown,
      self.benchmark_return_dropdown, self.select_all_securities_button,
      self.allocation_view_dropdown, self.allocation_quarter_dropdown,
      self.comparison_start_picker, self.comparison_end_picker,
      self.transaction_view_dropdown, self.regroup_button,
      self.collapse_all_button, self.expand_all_button, self.collapse_prior_years_button,
      self.overview_tab_button, self.transactions_tab_button, self.empty_add_security_button, self.allocation_tab_button,
      self.performance_tab_button, self.retirement_tab_button,
      self.tax_estimator_tab_button, self.dividends_tab_button, self.passive_tab_button,
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
    self.analyze_button.enabled = not self._busy and not self._refresh_requested
    self.analyze_button.text = "Refreshing…" if self._refreshing else "Waiting to refresh…" if self._refresh_requested else "Refresh"
    self.holdings_panel.raise_event_on_children("x-set-editable", editable=not self._busy)
    self.transaction_years_panel.raise_event_on_children("x-set-editable", editable=not self._busy)
    self.security_panel.raise_event_on_children("x-set-editable", editable=not self._busy, can_classify=self._user is not None)
    for form in (self.tax_estimator_form, self.income_form, self.notifications_form,
                 self.retirement_form, self.creator_workspace, self.hedge_fund_comparison):
      if form is not None:
        form.raise_event("x-set-editable", editable=not self._busy)
    self.benchmark_dropdown.enabled = False
    self.benchmark_return_dropdown.enabled = False
    self._update_image_import_controls()

  def _set_busy(self, busy):
    self._busy = busy
    self._update_controls()
    if not busy and self._portfolio_dirty and self._user is not None:
      self.autosave_timer.interval = .7
    if not busy and self._readout_page in ("dividends", "passive", "tax"):
      if self.income_form is not None:
        self.income_form.ensure_loaded()
    elif not busy and self._readout_page == "notifications":
      if self.notifications_form is not None:
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

  def _refresh_account(self, require_profile=True):
    previous_account_id = self.account_id
    self._user = anvil.users.get_user()
    self.account_id = self._user.get_id() if self._user is not None else None
    if self.account_id != previous_account_id:
      self._set_notification_unread_count(0)
      if self.share_composer is not None:
        self.share_composer.set_account_context()
    if self.notifications_form is not None:
      self.notifications_form.set_account_context(self.account_id)
    self._username = ""
    self._profile_summary = None
    workspace = None
    if self._user is not None:
      # A saved profile can be newer than the Users row cached in this browser.
      workspace = account_workspace(require_profile=require_profile)
      current_user = anvil.users.get_user()
      if workspace is None or current_user is None or current_user.get_id() != self.account_id:
        self._refresh_account()
        return
      profile = workspace["profile"]
      self._username = profile["username"] or ""
      self._profile_summary = profile
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
    self._creator_email = creator["email"]
    if self.creator_workspace is not None:
      self.creator_workspace.configure_access(self._creator_enabled, self._creator_email)
    if self.hedge_fund_comparison is not None:
      self.hedge_fund_comparison.set_creator_access(self._creator_enabled)
    self._set_creator_open(False)
    self._apply_readout_page()
    self._refresh_portfolios(workspace["portfolios"] if workspace is not None else [])

    # Start the optional badge read after the workspace is visible and ready;
    # it must never hold up Router's construction of this Form.
    self.notification_badge_timer.interval = 0.1 if self.account_id else 0

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

  def open_portfolio_page(self):
    if self._readout_page == "notifications":
      self._select_readout_page(self._portfolio_readout_page)

  def portfolio_header_click(self, event):
    event.preventDefault()
    if not self._busy:
      self.open_portfolio_page()
      set_url_hash({"page": "portfolio"})

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
    finally:
      self._set_busy(False)
    self.prepare_navigation()
    return True

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

  def creator_membership_changed(self, account_id, membership, **event_args):
    if self._user is not None and self._user.get_id() == account_id:
      self._display_membership(membership)

  def _update_welcome(self):
    signed_in = self._user is not None
    self.guest_hero.visible = not signed_in
    self.welcome_region.visible = True
    profile = self._profile_summary or {}
    self.portfolio_avatar.visible = not profile.get("investor_pending", False)
    investor = profile.get("investor") or {}
    verification = investor.get("verification") or {}
    self.portfolio_avatar.source = investor.get("avatar") or "_/theme/folio-mark.svg"
    self.portfolio_verified_label.visible = bool(verification.get("verified"))
    self.portfolio_verified_label.text = "Verified member" if verification.get("verified") else ""
    self.welcome_heading_label.text = str(self.portfolio_name_box.text or "").strip() or "Your portfolio"
    identity = profile.get("display_name") or self._username or "Personal investment workspace"
    self.welcome_detail_label.text = (
      "%s · @%s" % (identity, self._username) if signed_in and self._username else identity
    )
    self._update_profile_ranking()

  def _update_profile_ranking(self):
    if (self._profile_summary or {}).get("investor_pending"):
      self.summary_rank_label.text = "—"
      self.summary_rank_note.text = "Loading profile ranking…"
      self.summary_rank_label.tooltip = "Your saved portfolio values are ready. Profile ranking is loading."
      return
    investor = (self._profile_summary or {}).get("investor") or {}
    ranking = investor.get("ranking") or {}
    portfolio_id = self._current_portfolio.get_id() if self._current_portfolio is not None else None
    primary = bool(portfolio_id and portfolio_id == investor.get("primary_portfolio_id"))
    ranked = primary and ranking.get("quartile") is not None
    self.summary_rank_label.text = "Q%s · %.1f%%" % (ranking["quartile"], ranking["percentile"]) if ranked else "—"
    quarter = (investor.get("metrics") or {}).get("quarter_label") or "prior quarter"
    self.summary_rank_note.text = (
      "Percentile · saved primary · %s" % quarter if ranked else
      "Choose a primary portfolio in Profile" if not primary else "No completed-quarter rank yet"
    )
    self.summary_rank_label.tooltip = ranking.get("note", "Ranked among opted-in Verified members using their primary portfolios.") + (
      " Rank %s of %s." % (ranking["rank"], ranking["count"]) if ranked else ""
    )

  def _render_portfolio_summary(self):
    if self._startup_overview is not None:
      summary = self._startup_overview["summary"]
    elif self._history is None:
      summary = empty_summary()
    else:
      # Saved values and the empty shell do not need the history calculators.
      # This path first runs when history is ready, including background prep.
      from ..portfolio_summary import summarize_portfolio
      summary = summarize_portfolio(self._history)
    self.summary_value_label.text = "—" if summary["value"] is None else self._format_currency(summary["value"])
    self.summary_holdings_label.text = str(summary["holding_count"]) if self._history is not None or self._startup_overview is not None else "—"
    self.summary_date_label.text = ("As of %s%s" % (summary["as_of"], " · provisional" if summary["return_is_provisional"] else "")) if summary["as_of"] else "Sync to calculate"
    self.summary_quarter_caption.text = summary["prior_quarter_label"]
    self.summary_period_caption.text = summary["long_return_label"]
    self.summary_period_note.text = "Time weighted · includes dividends"
    self.summary_period_label.tooltip = summary["return_note"]
    self.summary_quarter_label.tooltip = summary["prior_quarter_note"]
    for label, key in ((self.summary_quarter_label, "prior_quarter_return"),
                       (self.summary_period_label, "long_return")):
      value = summary[key]
      label.text = "—" if value is None else self._format_percent(value)
      label.foreground = chart_theme.TEXT if value is None or value == 0 else chart_theme.POSITIVE if value > 0 else chart_theme.NEGATIVE
    self.summary_concentration_label.text = "—" if summary["top20_weight"] is None else "%.1f%%" % (summary["top20_weight"] * 100)
    self._update_profile_ranking()

  def _restore_last_portfolio(self, portfolio_id=None):
    account_id = self.account_id
    current_id = self._current_portfolio.get_id() if self._current_portfolio is not None else None
    generation = self._autosave_generation
    result = (anvil.server.call("load_portfolio", portfolio_id) if portfolio_id is not None
              else anvil.server.call("get_last_portfolio"))
    if generation != self._autosave_generation or not self._workspace_matches(account_id, current_id):
      return
    if not result["ok"]:
      self._set_portfolio_status(result["message"], is_error=True)
      return
    portfolio = result["portfolio"]
    if portfolio is None:
      if not self._has_unsaved_work():
        self._new_portfolio()
      self._set_portfolio_status("Welcome! Add a transaction; changes save automatically when signed in.")
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

  def _reset_autosave_scope(self):
    self._retirement_context_key = None
    self._tax_context_key = None
    self._income_context_key = None
    self._tax_context_source = None
    self._income_context_history = None
    self._autosave_generation += 1
    self._edit_revision += 1
    self._refresh_requested = False
    self.autosave_timer.interval = 0

  def _adopt_saved_portfolio(self, result, revision):
    """Update saved references without rebinding the transaction editors."""
    self._current_portfolio = result["portfolio"]
    self._portfolio_dirty = revision != self._edit_revision
    entry = result["directory_entry"]
    entries = [entry] + [row for _, row in self.saved_portfolios_dropdown.items
                        if row["portfolio_id"] != entry["portfolio_id"]]
    self.saved_portfolios_dropdown.items = [(row["name"], row) for row in entries]
    self.saved_portfolios_dropdown.selected_value = entry
    self.saved_portfolios_dropdown.placeholder = "Choose a saved portfolio"
    invalidate_workspace()

  @handle("autosave_timer", "tick")
  def autosave_timer_tick(self, **event_args):
    self.autosave_timer.interval = 0
    if self._refresh_requested and not self._busy and not self._autosaving:
      self.analyze_button_click()
    else:
      self._autosave()

  def _autosave(self):
    self.autosave_timer.interval = 0
    if self._initializing or self._busy or self._autosaving or not self._portfolio_dirty or self._user is None:
      return
    account_id = self.account_id
    portfolio_id = self._current_portfolio.get_id() if self._current_portfolio is not None else None
    generation = self._autosave_generation
    if not self._workspace_matches(account_id, portfolio_id):
      return
    try:
      transactions = self._read_transactions(allow_empty=True)
      name = str(self.portfolio_name_box.text or "").strip()
      if not name and self._current_portfolio is None and transactions:
        name = "My portfolio"
        self.portfolio_name_box.text = name
        self._update_welcome()
      if not 1 <= len(name) <= 80:
        raise ValueError("Enter a portfolio name to save changes automatically.")
      today = date.today()
      if any(row["trade_date"] > today for row in transactions):
        raise ValueError("Transaction dates cannot be after today.")
      as_of = self.as_of_date_picker.date or today
      if transactions:
        as_of = max(as_of, max(row["trade_date"] for row in transactions))
      rate = self._parse_number(self.risk_free_box.text, "risk-free rate", 1)
      if rate <= -100:
        raise ValueError("The annual risk-free rate must be greater than -100%.")
      document = [{"ticker": row["ticker"], "trade_date": row["trade_date"].isoformat(),
        "action": row["action"], "shares": row["shares"], "trade_price": row["trade_price"]} for row in transactions]
    except ValueError as error:
      self._set_portfolio_status('Not saved yet: %s' % error, is_error=True)
      return
    # Committing a pending editor can change state. Never send the captured
    # document if its account or portfolio changed while inputs were read.
    if generation != self._autosave_generation or not self._workspace_matches(account_id, portfolio_id):
      return
    revision = self._edit_revision
    self._autosaving = True
    self._set_portfolio_status("Saving changes…")
    try:
      result = anvil.server.call("save_portfolio", portfolio_id, name, document, as_of, rate)
      if generation != self._autosave_generation or not self._workspace_matches(account_id, portfolio_id):
        return
      if not result["ok"]:
        self._set_portfolio_status(result["message"], is_error=True)
        return
      self._adopt_saved_portfolio(result, revision)
      if self._portfolio_dirty:
        self._set_portfolio_status("Saving your latest changes…")
      else:
        self._set_portfolio_status("All changes saved." + (" Refresh to update prices." if self._history is None else ""))
    finally:
      self._autosaving = False
      if self._refresh_requested:
        self.autosave_timer.interval = .01
      elif self._portfolio_dirty and (generation != self._autosave_generation or revision != self._edit_revision):
        self.autosave_timer.interval = .7

  def _mark_portfolio_dirty(self):
    self._portfolio_dirty = True
    self._edit_revision += 1
    self.autosave_timer.interval = .7
    self._set_portfolio_status("Saving changes automatically…" if self._user is not None else "Sign in to save changes automatically. Refresh will guide you through sign-in.")

  def _has_unsaved_work(self):
    return self._portfolio_dirty or self.holdings_image_loader.file is not None

  def _confirm_discard(self):
    if self._portfolio_dirty and self._user is not None and not self._autosaving:
      self._autosave()
    return not self._has_unsaved_work() or confirm(
      "You have unsaved portfolio changes or an import in progress. Discard them and continue?",
      title="Unsaved changes",
      buttons=[("Keep editing", False), ("Discard changes", True)],
    )

  def _new_portfolio(self):
    self._reset_autosave_scope()
    self._collapsed_transaction_years.clear()
    self._collapsed_transaction_dates.clear()
    self._date_groups_on = None
    self._restore_on_resume = False
    self._current_portfolio = None
    self._last_synced_analysis = None
    self._last_synced_transactions = []
    self._set_last_refreshed(None)
    self._clear_image_import()
    self._reset_tools()
    self.portfolio_holdings_form.reset_workspace()
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
    self._set_status("Add A Security! Enter a transaction, then Refresh to update prices through today.")
    self._set_portfolio_status("Changes save automatically when signed in.")
    self._update_controls()

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
          # Restore here too: the Portfolio hash may already be active, so
          # selecting it again does not necessarily cause a Router event.
          if not self._has_unsaved_work():
            self._restore_last_portfolio()
          self._restore_on_resume = False
          set_url_hash({"page": "portfolio"})
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
      self._update_welcome()

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


  def _workspace_matches(self, account_id, portfolio_id):
    user = anvil.users.get_user()
    active_account = user.get_id() if user is not None else None
    active_portfolio = self._current_portfolio.get_id() if self._current_portfolio is not None else None
    return active_account == account_id and self.account_id == account_id and active_portfolio == portfolio_id


  @handle("open_portfolio_button", "click")
  def open_portfolio_button_click(self, **event_args):
    portfolio = self.saved_portfolios_dropdown.selected_value
    if self._busy or portfolio is None:
      return
    if self._current_portfolio is not None and portfolio["portfolio_id"] == self._current_portfolio.get_id():
      self._set_portfolio_status("This portfolio is already open. Your current analysis and draft are still here.")
      return
    if self.open_profile_portfolio(portfolio["portfolio_id"]):
      self._set_portfolio_status('Opened "%s". Changes save automatically; Refresh updates prices through today.' % portfolio["name"])

  def _apply_portfolio(self, portfolio, analysis=None, overview=None, holdings_snapshot=None):
    self._reset_autosave_scope()
    self._collapsed_transaction_years.clear()
    self._collapsed_transaction_dates.clear()
    self._date_groups_on = None
    self._clear_image_import()
    self._reset_tools()
    preserve_holdings = analysis is not None and holdings_snapshot is not None
    if not preserve_holdings:
      self.portfolio_holdings_form.reset_workspace()
    self._last_synced_analysis = None
    self._last_synced_transactions = []
    self._current_portfolio = portfolio
    self._set_last_refreshed(portfolio["analysis_updated_at"])
    self.portfolio_name_box.text = portfolio["name"]
    # Keep saved inputs unchanged so a simple reopen retains its cache identity.
    self.as_of_date_picker.date = portfolio["as_of_date"]
    self.risk_free_box.text = str(portfolio["risk_free_rate"])
    self.transactions = [self._new_transaction(
      transaction["ticker"],
      date(*[int(part) for part in transaction["trade_date"].split("-")]),
      transaction["action"],
      str(transaction["shares"]),
      transaction.get("trade_price"),
    ) for transaction in portfolio["transactions"]]
    self._readout_page = "overview"
    self._refresh_transactions()
    self._portfolio_dirty = False
    self._show_results(False, preserve_holdings=preserve_holdings)
    self.saved_portfolios_dropdown.selected_value = next(
      (entry for _, entry in self.saved_portfolios_dropdown.items if entry["portfolio_id"] == portfolio.get_id()), None
    )
    self._update_tax_estimator_context()
    self._set_status("Saved transactions loaded — sync to fetch daily market history.")
    if overview is not None:
      self._startup_overview = overview
      self._render_portfolio_summary()
      self._render_overview()
      self._set_transactions_collapsed(True)
      self._set_status("Saved values through %s · Loading chart history…" % overview["metadata"]["end_date"])
    elif analysis is not None:
      self.as_of_date_picker.date = portfolio["as_of_date"]
      self._apply_analysis(analysis, self._read_transactions(), holdings_snapshot=holdings_snapshot)
      self._set_transactions_collapsed(True)
      self._set_status("Saved analysis restored through %s. Sync history to refresh prices." % analysis["metadata"]["end_date"])
    else:
      self._set_transactions_collapsed(False)
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
    self._collapsed_transaction_dates.clear()
    self._date_groups_on = None
    self._clear_image_import()
    today = date.today()
    self.transactions = [
      self._new_transaction("AAPL", today - timedelta(days=365), "Buy", "20"),
      self._new_transaction("MSFT", today - timedelta(days=210), "Buy", "10"),
    ]
    self._refresh_transactions()
    self._set_status("Sample transactions loaded — sync to fetch daily market history.")

  def _show_results(self, visible, preserve_holdings=False):
    if not visible:
      self._startup_overview = None
      self._set_charts_expanded(False, resize=False)
      self._history = None
      self._comparison_window_source = None
      self._comparison_window_key = None
      self._comparison_window_data = None
      self._equal_weight_source = None
      self._equal_weight_history = None
      self._performance_weighting = "manager"
      self._performance_details_open = False
      self._performance_details_dirty = True
      self._performance_details_context = None
      self.dom_nodes["performance_details"].removeAttribute("open")
      self._analysis_source = None
      self._selected_symbols = set()
      self._analyzed_transactions = []
      self._allocation_snapshots = []
      self._allocation_snapshot = None
      self._invalidate_allocation()
      self._allocation_holdings_open = False
      self.dom_nodes["allocation_holdings"].removeAttribute("open")
      self.security_panel.items = []
      self.allocation_filter_timer.interval = 0
      self._dirty_readout_pages.update(("overview", "allocation", "performance"))
      self._plot_sizes.clear()
      self.allocation_quarter_dropdown.selected_value = "latest"
      self._clear_chart_trades()
      if self.hedge_fund_comparison is not None:
        self.hedge_fund_comparison.reset_context()
      if not preserve_holdings:
        self.portfolio_holdings_form.reset_workspace()
      self._render_portfolio_summary()
    self._apply_readout_page()

  def _sharing_context(self):
    if self._history is None or not self._selected_symbols:
      raise ValueError("Refresh your portfolio and select at least one security before sharing performance.")
    # Read cached comparison values directly; sharing never renders hidden plots.
    window = self._comparison_window()
    points = window["points"]
    if len(points) < 2:
      raise ValueError("Choose a period with at least two overlapping trading valuations.")
    dates = [points[0]["date"], points[-1]["date"]]
    transactions = [{
      "ticker": row["ticker"], "trade_date": row["trade_date"],
      "action": row["action"], "shares": row["shares"],
      "trade_price": row.get("trade_price"),
    } for row in self._analyzed_transactions]
    benchmark = self.benchmark_dropdown.selected_value
    portfolio_return = points[-1]["portfolio_index"] / points[0]["portfolio_index"] - 1
    benchmark_values = window["benchmark_by_date"]
    benchmark_return = benchmark_values[dates[-1]] / benchmark_values[dates[0]] - 1
    benchmark_label = next(label for label, key in BENCHMARK_CHOICES if key == benchmark)
    summary_lines = [
      "My portfolio performance",
      "%s – %s" % (dates[0], dates[-1]),
      "Portfolio (Manager-Weighted): %s" % self._format_percent(portfolio_return),
      "%s: %s" % (benchmark_label, self._format_percent(benchmark_return)),
      "Versus benchmark: %+.2f percentage points" % ((portfolio_return - benchmark_return) * 100),
      "Benchmark uses %s returns." % ("total" if self.benchmark_return_dropdown.selected_value == "total" else "price"),
      "Based on user-entered trades; portfolio dividends held as cash.",
    ]
    if not self._history["series"][-1].get("session_complete", True) and str(dates[-1]) == self._history["series"][-1]["date"]:
      summary_lines.append("Provisional regular-session prices as of %s." % self._history["metadata"].get("quoted_at"))
    summary = "\n".join(summary_lines)
    return {
      "transactions": transactions, "as_of_date": self._date_from_iso(self._history["metadata"]["end_date"]),
      "risk_free_rate": self._history["metadata"]["risk_free_rate"],
      "portfolio_id": self._current_portfolio.get_id() if self._current_portfolio is not None else None,
      "selected_symbols": sorted(self._selected_symbols), "benchmark": benchmark,
      "benchmark_return": self.benchmark_return_dropdown.selected_value,
      "start_date": str(dates[0]), "end_date": str(dates[-1]),
      "benchmark_label": benchmark_label, "summary": summary,
    }

  @handle("share_performance_button", "click")
  def share_performance_click(self, **event_args):
    if self._busy:
      return
    if self.share_composer is not None and self.share_composer.visible:
      self.share_composer.focus_dialog()
      return
    self._set_charts_expanded(False, resize=False)
    composer = self._ensure_tool("share_composer")
    composer.visible = True
    composer.show_for(self._sharing_context, self._ensure_signed_in)

  def close_sharing(self, **event_args):
    if self.share_composer is None:
      return
    self.share_composer.visible = False
    self._chart_button_node(self.share_performance_button).focus({"preventScroll": True})

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

  def _read_transactions(self, allow_empty=False):
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

    if not clean_transactions and not allow_empty:
      raise ValueError("Add at least one transaction before refreshing prices.")
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
    from .. import portfolio_charts
    return portfolio_charts._daily_risk(self, indices, observed)

  def _chart_layout(self):
    from .. import portfolio_charts
    return portfolio_charts._chart_layout(self)

  def _set_chart(self, plot, dates, portfolio, benchmark, benchmark_name, markers):
    from .. import portfolio_charts
    return portfolio_charts._set_chart(self, plot, dates, portfolio, benchmark, benchmark_name, markers, BENCHMARK_CHOICES)

  def _timeframe_buttons(self):
    return [("1M", self.range_1m_button), ("3M", self.range_3m_button),
            ("6M", self.range_6m_button), ("YTD", self.range_ytd_button),
            ("1Y", self.range_1y_button), ("3Y", self.range_3y_button), ("5Y", self.range_5y_button),
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

  @handle("performance_plot", "plot_click")
  @handle("drawdown_plot", "plot_click")
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
    self.expand_charts_button.text = "Restore chart size" if expanded else "Expand charts"
    self.expand_charts_button.icon = "mi:fullscreen_exit" if expanded else "mi:fullscreen"
    self.performance_plot.height = "620px" if expanded else "520px"
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
  @handle("range_5y_button", "click")
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
    from .. import portfolio_charts
    return portfolio_charts._drawdowns(self, indices)

  def _clear_comparison(self, message):
    from .. import portfolio_charts
    return portfolio_charts._clear_comparison(self, message)

  def _update_benchmark_options(self):
    if self._analysis_source is None:
      return
    benchmarks = self._analysis_source["benchmarks"]
    self.benchmark_dropdown.items = [("S&P 500 index", "sp500")]
    self.benchmark_dropdown.selected_value = "sp500"
    # Prefer total return; older saved analyses can still use S&P 500 price
    # history. Never substitute another index when S&P 500 is unavailable.
    mode = "total" if benchmarks.get("sp500_total", {}).get("series") else "price" if benchmarks.get("sp500_price", {}).get("series") else "total"
    label = "Total return · dividends reinvested" if mode == "total" else "Price return · excludes dividends"
    self.benchmark_return_dropdown.items = [(label, mode)]
    self.benchmark_return_dropdown.selected_value = mode
    self.benchmark_dropdown.enabled = False
    self.benchmark_return_dropdown.enabled = False

  def _performance_source(self, weighting):
    from .. import portfolio_charts
    return portfolio_charts._performance_source(self, weighting)

  @handle("manager_weighted_button", "click")
  @handle("equal_weighted_button", "click")
  def performance_weighting_click(self, sender, **event_args):
    if self._busy:
      return
    weighting = "equal" if sender is self.equal_weighted_button else "manager"
    if weighting != self._performance_weighting:
      self._performance_weighting = weighting
      self._render_comparison()

  def _comparison_window(self, weighting="manager"):
    from .. import portfolio_charts
    return portfolio_charts._comparison_window(self, weighting)

  def _render_comparison(self):
    from .. import portfolio_charts
    return portfolio_charts._render_comparison(self, BENCHMARK_CHOICES, HEDGE_FUND_COMPARISONS_ENABLED)

  def performance_details_toggle(self, event):
    if event.target != self.dom_nodes["performance_details"]:
      return
    self._performance_details_open = event.target.hasAttribute("open")
    if self._performance_details_open and not self._busy:
      self._render_performance_details()
      self._resize_readout_charts()

  def _render_performance_details(self):
    from .. import portfolio_charts
    return portfolio_charts._render_performance_details(self)

  def _render_quarterly(self, points, benchmark_by_date, benchmark_name):
    from .. import portfolio_charts
    return portfolio_charts._render_quarterly(self, points, benchmark_by_date, benchmark_name, BENCHMARK_CHOICES)

  def _invalidate_allocation(self):
    self._set_allocation_hover(None)
    self._allocation_trace_names = []
    self._allocation_row_names = set()
    self._allocation_data_ready = False
    self._allocation_groups_cache = {}
    self._allocation_history_key = None
    self._allocation_highlight_key = None
    self._allocation_security_rows = None

  def _allocation_groups_for_view(self):
    from .. import portfolio_charts
    return portfolio_charts._allocation_groups_for_view(self)

  def _render_sectors(self):
    from .. import portfolio_charts
    return portfolio_charts._render_sectors(self)

  def _render_allocation_snapshot(self):
    from .. import portfolio_charts
    return portfolio_charts._render_allocation_snapshot(self, HOLDINGS_MAP_ENABLED)

  def _render_allocation_map(self, allocation):
    from .. import portfolio_charts
    return portfolio_charts._render_allocation_map(self, allocation, HOLDINGS_MAP_ENABLED)

  def _allocation_selection_outline(self):
    from .. import portfolio_charts
    return portfolio_charts._allocation_selection_outline(self)

  def _render_allocation_history(self):
    from .. import portfolio_charts
    return portfolio_charts._render_allocation_history(self)

  def _set_allocation_hover(self, name, source=None):
    self.allocation_unhover_timer.interval = 0
    if name is not None and name not in self._allocation_trace_names:
      return
    if name == self._allocation_hover_name:
      if name is not None:
        self._allocation_hover_source = source
      return
    self._allocation_hover_name = name
    self._allocation_hover_source = source if name is not None else None
    # Older history can contain a sector absent from the selected snapshot.
    # Leave that table readable instead of dimming every row without a match.
    row_name = name if name in self._allocation_row_names else None
    self.sector_panel.raise_event_on_children("x-highlight-allocation", name=row_name)
    if self._allocation_trace_names and self.allocation_history_plot.visible:
      self._allocation_hover_styling = True
      try:
        self.allocation_history_plot.restyle({
          "opacity": [1 if name is None or trace == name else 0.16
                      for trace in self._allocation_trace_names],
        }, list(range(len(self._allocation_trace_names))))
      finally:
        self._allocation_hover_styling = False

  @handle("sector_panel", "x-allocation-hover")
  def allocation_row_hover(self, name, active, **event_args):
    if active and not self._busy:
      self._set_allocation_hover(name, "row")
    elif self._allocation_hover_source == "row" and self._allocation_hover_name == name:
      self._set_allocation_hover(None)

  def _allocation_hover_point_name(self, points):
    for point in points or []:
      index = point.get("curve_number")
      if isinstance(index, int) and 0 <= index < len(self._allocation_trace_names):
        return self._allocation_trace_names[index]
    return None

  @handle("allocation_history_plot", "plot_hover")
  def allocation_chart_hover(self, points, **event_args):
    if not self._busy and not self._allocation_hover_styling:
      name = self._allocation_hover_point_name(points)
      if name is not None:
        self._set_allocation_hover(name, "chart")

  @handle("allocation_history_plot", "plot_unhover")
  def allocation_chart_unhover(self, points=None, **event_args):
    if self._allocation_hover_styling or self._allocation_hover_source != "chart":
      return
    name = self._allocation_hover_point_name(points)
    if name is None or name == self._allocation_hover_name:
      # Moving to another bar of the same sector should not flash all colours.
      self.allocation_unhover_timer.interval = 0.03

  @handle("allocation_unhover_timer", "tick")
  def allocation_chart_unhover_tick(self, **event_args):
    self.allocation_unhover_timer.interval = 0
    if self._allocation_hover_source == "chart":
      self._set_allocation_hover(None)

  def allocation_hover_leave(self, event):
    self._set_allocation_hover(None)

  @handle("allocation_view_dropdown", "change")
  @handle("allocation_quarter_dropdown", "change")
  def allocation_snapshot_change(self, **event_args):
    if not self._busy and self._history is not None:
      self._allocation_security_rows = None
      self._render_allocation_snapshot()
      self._render_allocation_history()
      self._render_security_rows()

  @handle("allocation_history_plot", "plot_click")
  def allocation_history_click(self, points, **event_args):
    if not self._busy:
      for point in points:
        index = point.get("point_number")
        if isinstance(index, int) and 0 <= index < len(self._allocation_snapshots):
          self.allocation_quarter_dropdown.selected_value = self._allocation_snapshots[index]["key"]
          self.allocation_snapshot_change()
          return

  def _render_security_rows(self):
    from .. import portfolio_charts
    return portfolio_charts._render_security_rows(self)

  def allocation_holdings_toggle(self, event):
    if event.target != self.dom_nodes["allocation_holdings"]:
      return
    self._allocation_holdings_open = event.target.hasAttribute("open")
    if self._allocation_holdings_open and not self._busy:
      self._render_security_rows()

  @handle("allocation_search_box", "change")
  def allocation_search_change(self, **event_args):
    if not self._busy:
      self.allocation_filter_timer.interval = 0.12

  @handle("allocation_filter_timer", "tick")
  @handle("allocation_position_dropdown", "change")
  def allocation_filter_change(self, **event_args):
    self.allocation_filter_timer.interval = 0
    if not self._busy:
      self._render_security_rows()

  @handle("security_panel", "x-sector-changed")
  def security_sector_change(self, symbol, sector, **event_args):
    if self._busy or self._analysis_source is None or self._user is None:
      return
    from ..analysis_selection import analyze_selection, update_sector_classification
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
    self._invalidate_allocation()
    self._equal_weight_source = None
    self._equal_weight_history = None
    self._render_portfolio_summary()
    self._render_active_readout()
    self._update_readout_context()
    self.prepare_navigation()

  def _render_overview(self):
    if self._startup_overview is not None:
      self.portfolio_holdings_form.set_startup_snapshot(self._startup_overview["holdings"])
      metrics = self._startup_overview["metrics"]
    elif self._history is not None:
      self.portfolio_holdings_form.set_analysis(self._analysis_source, self._selected_symbols, self._analyzed_transactions)
      self.portfolio_holdings_form.redraw()
      metrics = self._history["metrics"]
    else:
      return
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
    from ..analysis_selection import analyze_selection
    if included:
      self._selected_symbols.add(symbol)
    else:
      self._selected_symbols.discard(symbol)
    self._history = analyze_selection(self._analysis_source, self._selected_symbols)
    self._render_readout()

  @handle("select_all_securities_button", "click")
  def select_all_securities_button_click(self, **event_args):
    if not self._busy and self._analysis_source is not None:
      from ..analysis_selection import analyze_selection
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
    self._collapsed_transaction_dates["undated"] = False
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
    if self._autosaving:
      self._refresh_requested = True
      self._set_portfolio_status("Finishing the automatic save, then refreshing prices…")
      self._update_controls()
      return
    original_account_id = self.account_id
    original_portfolio_id = self._current_portfolio.get_id() if self._current_portfolio is not None else None
    original_generation = self._autosave_generation
    if not self._workspace_matches(original_account_id, original_portfolio_id):
      return
    self._refresh_requested = False
    self.autosave_timer.interval = 0
    try:
      transactions = self._read_transactions()
      name = str(self.portfolio_name_box.text or "").strip() or "My portfolio"
      if len(name) > 80:
        raise ValueError("Give your portfolio a name of 1–80 characters.")
      risk_free_rate = self._parse_number(self.risk_free_box.text, "risk-free rate", 1)
      if risk_free_rate <= -100:
        raise ValueError("The annual risk-free rate must be greater than -100%.")
      document = [{"ticker": row["ticker"], "trade_date": row["trade_date"].isoformat(),
        "action": row["action"], "shares": row["shares"], "trade_price": row["trade_price"]} for row in transactions]
    except ValueError as error:
      self._set_status(str(error), is_error=True)
      self._update_controls()
      return
    self._refreshing = True
    self._set_busy(True)
    try:
      if self._user is None or not self._username:
        if not self._ensure_signed_in():
          return
      account_id = self.account_id
      # Guest drafts can be adopted at first sign-in. A draft already owned by
      # an account must never be submitted under a different account or target.
      if ((original_account_id is not None and account_id != original_account_id)
          or original_generation != self._autosave_generation
          or not self._workspace_matches(account_id, original_portfolio_id)):
        return
      portfolio_id = original_portfolio_id
      generation, revision = self._autosave_generation, self._edit_revision
      self._set_status("Refreshing prices through today and saving your portfolio…")
      result = anvil.server.call("refresh_portfolio", portfolio_id, name, document, risk_free_rate)
      if generation != self._autosave_generation or not self._workspace_matches(account_id, portfolio_id):
        return
      if result.get("portfolio") is not None:
        self._adopt_saved_portfolio(result, revision)
        self.portfolio_name_box.text = result["portfolio"]["name"]
        self.as_of_date_picker.date = result["portfolio"]["as_of_date"]
      if not result["ok"]:
        self._set_status(result["message"], is_error=True)
        return
      self._apply_analysis(result["analysis"], transactions)
      self._set_last_refreshed(result["portfolio"]["analysis_updated_at"])
      self._set_portfolio_status('All changes saved · prices refreshed through %s.' % result["portfolio"]["as_of_date"])
      self._readout_page = "overview"
      self._apply_readout_page(redraw=True)
      analysis = result["analysis"]
      if analysis["metadata"].get("provisional_date"):
        stamp = analysis["metadata"]["quoted_at"].replace("T", " ").replace("+00:00", " UTC")
        message = "Latest regular-session prices updated · oldest portfolio quote %s. Today's values are provisional; daily risk uses completed sessions." % stamp
      else:
        message = "Prices and analysis updated. All changes saved."
      moved = analysis["metadata"]["moved_transaction_dates"]
      if moved:
        message += " %d non-trading date(s) use the next available session." % moved
      self._set_status(message)
    finally:
      self._refreshing = False
      self._set_busy(False)
    self.prepare_navigation()


  def _apply_analysis(self, result, transactions, holdings_snapshot=None):
    startup_performance.mark("analysis_apply_start")
    from ..analysis_selection import analyze_selection
    self._analysis_source = result
    self._last_synced_analysis = result
    self._last_synced_transactions = [{key: row.get(key) for key in
                                      ("ticker", "trade_date", "action", "shares", "trade_price")}
                                     for row in transactions]
    self._selected_symbols = set(row["symbol"] for row in result["selection_data"]["securities"])
    self._history = analyze_selection(result, self._selected_symbols)
    self._analyzed_transactions = list(transactions)
    for transaction, synced in zip(transactions, result["rows"]):
      transaction.update(synced)
      price = synced["current_price"]
      transaction["quote_text"] = "No current holding" if price is None else "USD %s" % self._format_currency(price)
      transaction["dividend_text"] = "Latest session price synced" if result["metadata"].get("provisional_date") else "Daily history synced"
    self._refresh_transactions()
    if holdings_snapshot is not None:
      self.portfolio_holdings_form.set_analysis(result, self._selected_symbols, transactions, snapshot=holdings_snapshot)
    self.comparison_start_picker.date = self._date_from_iso(result["series"][0]["date"])
    self.comparison_end_picker.date = self._date_from_iso(result["series"][-1]["date"])
    self._show_results(True)
    self._update_benchmark_options()
    self._render_readout()
    self.prepare_navigation()
    startup_performance.mark("analysis_apply_end")

import anvil.server
from ._anvil_designer import HedgeFundComparisonTemplate
from anvil import *
from ... import chart_theme


class HedgeFundComparison(HedgeFundComparisonTemplate):
  """Lazy, read-only comparisons against cached public 13F holdings models."""

  def __init__(self, **properties):
    super().__init__(**properties)
    self._points = []
    self._risk_free_rate = 0
    self._creator_access = False
    self._parent_editable = True
    self._generation = 0
    self._busy = False
    self._opened = False
    self._funds_loaded = False
    self._funds = []
    self._result = None
    self._filings = []
    self._expanded = False
    self._trades_expanded = False
    self._trade_limit = 100
    self._update_job_id = None
    self._polling = False
    self.update_timer.interval = 0
    self.workspace_region.visible = False
    self.fund_dropdown.items = []
    self.fund_dropdown.selected_value = None
    self._clear_result()
    self._clear_holdings()
    self._render_profile()
    self._update_controls()
    self._render_context()

  def configure_context(self, points, risk_free_rate=0):
    points = [dict(point) for point in (points or [])]
    if points == self._points and risk_free_rate == self._risk_free_rate:
      return
    self._generation += 1
    self._points = points
    self._risk_free_rate = risk_free_rate
    self._busy = False
    self._stop_update_polling()
    self._clear_result()
    self._render_context()
    self._set_status("Portfolio or timeframe changed. Compare again to update this view." if self._opened else "")
    self._update_controls()

  def reset_context(self):
    self._generation += 1
    self._points = []
    self._busy = False
    self._stop_update_polling()
    self._clear_result()
    self._render_context()
    self._update_controls()

  def set_creator_access(self, enabled):
    self._creator_access = bool(enabled)
    self._update_controls()

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._parent_editable = bool(editable)
    self._update_controls()

  def _editable(self):
    return self._parent_editable and not self._busy

  def _update_controls(self):
    editable = self._editable()
    self.open_button.enabled = editable
    self.search_box.enabled = editable and self._funds_loaded
    self.fund_dropdown.enabled = editable and bool(self._funds)
    self.refresh_button.enabled = editable
    self.compare_button.enabled = editable and self.fund_dropdown.selected_value is not None and len(self._points) >= 2
    self.holdings_button.enabled = editable and self.fund_dropdown.selected_value is not None
    self.update_button.visible = True
    self.update_button.enabled = editable and self._update_job_id is None and self.fund_dropdown.selected_value is not None
    self.enlarge_button.enabled = editable and self._result is not None
    self.quarter_dropdown.enabled = editable and bool(self._filings)
    self.trades_toggle_button.enabled = editable and self._result is not None
    self.more_trades_button.enabled = editable
    self.ranks_button.enabled = editable and len(self._points) >= 2
    self.trade_rows.raise_event_on_children("x-set-editable", editable=editable)
    self.rank_rows.raise_event_on_children("x-set-editable", editable=editable)

  def _set_status(self, text, error=False):
    self.status_label.text = text
    self.status_region.classes["is-error"] = bool(error)

  def _render_context(self):
    if len(self._points) < 2:
      self.context_label.text = "Analyze your portfolio first to compare returns over the selected Performance timeframe."
    else:
      self.context_label.text = "Performance timeframe: {} to {} · annual risk-free rate {}. Rankings use this same window.".format(
        self._date_text(self._points[0]["date"]), self._date_text(self._points[-1]["date"]), self._percent(self._risk_free_rate))

  def _clear_result(self):
    self._result = None
    self.results_region.visible = False
    self.trades_region.visible = False
    self.comparison_plot.data = []
    self.comparison_plot.layout = chart_theme.layout({})
    self.comparison_plot.height = "390px"
    self._expanded = False
    self.enlarge_button.text = "Enlarge chart"
    self.trade_rows.items = []
    self.rank_rows.items = []
    self.rank_scope_label.text = ""
    self.rank_excluded_label.text = ""
    self.rank_excluded_label.visible = False
    self.rank_empty_label.text = "Load ranks to compare available fund models over the displayed dates."
    self.rank_empty_label.visible = True
    self._trades_expanded = False
    self.trade_details_region.visible = False
    self.trades_toggle_button.text = "Show inferred trades"
    self._trade_limit = 100

  def _date_text(self, value):
    return value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else str(value or "")[:10]

  def _stamp(self, value):
    return value.strftime("%b %d, %Y %H:%M UTC") if hasattr(value, "strftime") else str(value or "")

  def _percent(self, value):
    return "—" if value is None else "{:+.2f}%".format(value * 100)

  def _decimal(self, value):
    return "—" if value is None else "{:.2f}".format(value)

  def _quantity(self, value):
    return "—" if value is None else "{:,.4f}".format(value).rstrip("0").rstrip(".")

  def _money(self, value):
    return "—" if value is None else "${:,.2f}".format(value)

  def _selected_fund(self):
    key = self.fund_dropdown.selected_value
    return next((fund for fund in self._funds if fund["fund_key"] == key), None)

  @handle("open_button", "click")
  def toggle_open(self, **event_args):
    if not self._editable():
      return
    self._opened = not self._opened
    self.workspace_region.visible = self._opened
    self.open_button.text = "Hide hedge fund comparison" if self._opened else "Compare with hedge funds"
    if not self._opened:
      self.update_timer.interval = 0
    if self._opened:
      if self._update_job_id is not None:
        self.update_timer.interval = 5
      if not self._funds_loaded:
        self.refresh_profiles()
      self.redraw()

  @handle("refresh_button", "click")
  def refresh_profiles(self, **event_args):
    if not self._editable():
      return
    generation = self._generation
    self._busy = True
    self._set_status("Loading available public fund profiles…")
    self._update_controls()
    try:
      result = anvil.server.call("list_hedge_funds")
      if generation != self._generation:
        return
      if not result["ok"]:
        self._set_status(result["message"], True)
        return
      self._funds = result["funds"]
      self._funds_loaded = True
      self._filter_funds(invalidate=False)
      self._render_profile()
      self._set_status(result.get("message") or "Choose a profile and compare it with your selected Performance dates.")
    finally:
      if generation == self._generation:
        self._busy = False
        self._update_controls()

  def _filter_funds(self, invalidate=True):
    selected = self.fund_dropdown.selected_value
    query = str(self.search_box.text or "").strip().lower()
    funds = [fund for fund in self._funds if query in (str(fund["name"]) + " " + str(fund.get("manager") or "")).lower()]
    self.fund_dropdown.items = [(fund["name"] + (" · " + fund["manager"] if fund.get("manager") else ""), fund["fund_key"]) for fund in funds]
    keys = [fund["fund_key"] for fund in funds]
    self.fund_dropdown.selected_value = selected if selected in keys else (keys[0] if keys else None)
    if self.fund_dropdown.selected_value != selected:
      if invalidate:
        self._generation += 1
        self._stop_update_polling()
      self._clear_result()
      self._clear_holdings()
    self._render_profile()
    self._update_controls()

  @handle("search_box", "change")
  def search_changed(self, **event_args):
    if self._editable():
      self._filter_funds()
      if self.fund_dropdown.selected_value is None:
        self._set_status("No available profile matches this search.")

  @handle("fund_dropdown", "change")
  def fund_changed(self, **event_args):
    if self._editable():
      self._generation += 1
      self._stop_update_polling()
      self._clear_result()
      self._clear_holdings()
      self._render_profile()
      self._set_status("Profile changed. Compare again to update returns and holdings.")
      self._update_controls()

  def _render_profile(self):
    fund = self._selected_fund()
    self.profile_region.visible = fund is not None
    self.hedgefollow_link.visible = False
    self.sec_profile_link.visible = False
    if fund is None:
      return
    self.profile_name_label.text = fund["name"]
    detail = fund.get("manager") or "Public 13F holdings profile"
    if fund.get("latest_quarter"):
      detail += " · latest cached quarter " + str(fund["latest_quarter"])
    if fund.get("holdings_count") is not None:
      detail += " · {:,} reported positions".format(fund["holdings_count"])
    self.profile_detail_label.text = detail
    checked = fund.get("last_checked_at")
    self.checked_label.text = "SEC filings last checked " + self._stamp(checked) if checked else "No cached SEC check yet."
    url = fund.get("hedgefollow_url")
    self.hedgefollow_link.url = url or ""
    self.hedgefollow_link.visible = bool(url)
    source = fund.get("source_url")
    self.sec_profile_link.url = source or ""
    self.sec_profile_link.visible = bool(source)

  @handle("compare_button", "click")
  def compare_selected(self, **event_args):
    if not self._editable() or self.fund_dropdown.selected_value is None or len(self._points) < 2:
      return
    generation = self._generation
    self._busy = True
    self._clear_result()
    self._set_status("Comparing completed sessions, dividends, and risk over the same dates…")
    self._update_controls()
    try:
      result = anvil.server.call("get_hedge_fund_comparison", self.fund_dropdown.selected_value,
                                 self._points, self._risk_free_rate)
      if generation != self._generation:
        return
      if not result["ok"]:
        if result.get("pending") and result.get("job_id"):
          self._update_job_id = result["job_id"]
          self.update_timer.interval = 5 if self._opened else 0
          self._set_status(result["message"])
        else:
          self._set_status(result["message"], True)
        return
      result["holdings"] = sorted(result.get("holdings") or [], key=lambda filing: self._date_text(filing.get("period_end")) or str(filing["quarter"]))
      self._result = result
      self._render_result()
      self._set_status("Comparison ready. This is hypothetical 13F holdings performance; review the coverage and assumptions below.")
    finally:
      if generation == self._generation:
        self._busy = False
        self._update_controls()

  def _stats_text(self, metrics):
    return "Sharpe {} · annual volatility {} · {:,} return observations".format(
      self._decimal(metrics.get("sharpe")), self._percent(metrics.get("volatility")), metrics.get("observations") or 0)

  def _render_result(self):
    result = self._result
    if result is None:
      return
    self.results_region.visible = True
    period = result["period"]
    self.period_label.text = "{} to {} · {:,} matched completed return observations".format(
      self._date_text(period["start"]), self._date_text(period["end"]), period.get("observations") or 0)
    self.model_note_label.text = result.get("model_note") or ""
    self.benchmark_name_label.text = result.get("benchmark_label") or "S&P 500"
    warnings = result.get("warnings") or []
    self.warnings_label.text = "\n".join(str(warning) for warning in warnings)
    self.warnings_label.visible = bool(warnings)
    metrics = result["metrics"]
    for key, return_label, stats_label in (("portfolio", self.portfolio_return_label, self.portfolio_stats_label),
                                         ("fund", self.fund_return_label, self.fund_stats_label),
                                         ("benchmark", self.benchmark_return_label, self.benchmark_stats_label)):
      return_label.text = self._percent(metrics[key].get("total_return"))
      stats_label.text = self._stats_text(metrics[key])
    rank = result.get("rank") or {}
    if rank.get("position") is not None:
      self.rank_label.text = "Model rank {:,} of {:,} · Sharpe advantage vs S&P 500 {:+.2f} · {}".format(
        rank["position"], rank["total"], rank["delta_sharpe"], rank.get("scope") or "available models covering these dates")
    elif rank.get("delta_sharpe") is not None:
      self.rank_label.text = "Sharpe advantage vs S&P 500 {:+.2f}. Load the same-period ranks below to compare available models.".format(rank["delta_sharpe"])
    else:
      self.rank_label.text = "This model has no comparable rank for this period. Rankings require adequate holdings coverage, shared completed sessions, and measurable volatility."
    self._show_holdings(result.get("holdings") or [])
    self.trades_region.visible = True
    self._render_trades()
    self._render_chart()
    checked = result.get("last_checked_at")
    if checked:
      self.checked_label.text = "SEC filings last checked " + self._stamp(checked)

  def _render_chart(self):
    if self._result is None:
      return
    result = self._result
    points = result["series"]
    dates = [point["date"] for point in points]
    traces = []
    for key, name, color in (("portfolio_index", "Your portfolio", chart_theme.POSITIVE),
                             ("fund_index", result["fund"]["name"] + " · 13F model", chart_theme.COMPARISON),
                             ("benchmark_index", result.get("benchmark_label") or "S&P 500 · SPY", chart_theme.BENCHMARK)):
      traces.append({"type": "scatter", "mode": "lines", "name": name, "x": dates,
                     "y": [point[key] / 100 - 1 for point in points], "line": {"color": color, "width": 2.4},
                     "hovertemplate": "%{x}<br>%{y:+.2%}<extra>" + name + "</extra>"})
    self.comparison_plot.data = traces
    self.comparison_plot.layout = chart_theme.layout({"paper_bgcolor": chart_theme.PANEL, "plot_bgcolor": chart_theme.PANEL,
                                   "font": {"family": "Inter, sans-serif", "size": 12, "color": chart_theme.MUTED},
                                   "margin": {"l": 60, "r": 18, "t": 25, "b": 95}, "hovermode": "x unified",
                                   "xaxis": {"type": "date", "showgrid": False},
                                   "yaxis": {"title": "Total return", "tickformat": ".0%", "gridcolor": chart_theme.GRID, "zerolinecolor": chart_theme.ZERO_LINE},
                                   "legend": {"orientation": "h", "x": 0, "y": -0.23}})
    self.comparison_plot.config = {"displayModeBar": True, "displaylogo": False, "responsive": True,
                                   "scrollZoom": False, "modeBarButtonsToRemove": ["select2d", "lasso2d"]}
    self.redraw()

  def _clear_holdings(self):
    self._filings = []
    self.holdings_region.visible = False
    self.holding_rows.items = []
    self.quarter_dropdown.items = []
    self.quarter_dropdown.selected_value = None
    self.filing_label.text = ""
    self.coverage_label.text = ""
    self.coverage_label.visible = False
    self.filing_link.url = ""
    self.filing_link.visible = False
    self.holdings_empty_label.visible = False

  def _show_holdings(self, holdings):
    self._clear_holdings()
    self._filings = sorted(holdings, key=lambda filing: self._date_text(filing.get("period_end")) or str(filing["quarter"]))
    self.holdings_region.visible = True
    self.quarter_dropdown.items = [(str(item["quarter"]), index) for index, item in enumerate(self._filings)]
    self.quarter_dropdown.selected_value = len(self._filings) - 1 if self._filings else None
    self.holdings_empty_label.text = "No quarterly holdings have been cached yet. Check latest SEC filings to collect this profile's history."
    self.holdings_empty_label.visible = not self._filings
    self._render_holdings()

  @handle("holdings_button", "click")
  def view_holdings(self, **event_args):
    if not self._editable() or self.fund_dropdown.selected_value is None:
      return
    generation = self._generation
    self._busy = True
    self._set_status("Loading cached quarterly SEC holdings…")
    self._update_controls()
    try:
      result = anvil.server.call("get_hedge_fund_profile", self.fund_dropdown.selected_value)
      if generation != self._generation:
        return
      if not result["ok"]:
        self._set_status(result["message"], True)
        return
      fund = self._selected_fund()
      if fund is not None:
        fund.update(result.get("fund") or {})
      self._render_profile()
      self._show_holdings(result.get("holdings") or [])
      checked = result.get("last_checked_at")
      if checked:
        self.checked_label.text = "SEC filings last checked " + self._stamp(checked)
      self._set_status(result.get("message") or "Quarterly holdings loaded. Choose a quarter to inspect its filing, mapping coverage, and positions.")
    finally:
      if generation == self._generation:
        self._busy = False
        self._update_controls()

  @handle("quarter_dropdown", "change")
  def quarter_changed(self, **event_args):
    if self._editable():
      self._render_holdings()

  def _render_holdings(self):
    if not self._filings or self.quarter_dropdown.selected_value is None:
      return
    filing = self._filings[self.quarter_dropdown.selected_value]
    rows = filing.get("holdings") or []
    self.holding_rows.items = [{"security_text": row.get("symbol") or ("CUSIP " + row["cusip"] if row.get("cusip") else "Unmapped security"),
                               "name_text": (row.get("name") or "") + (" · " + row["class_title"] if row.get("class_title") else ""),
                               "shares_text": self._quantity(row.get("shares")),
                               "value_text": self._money(row.get("value"))} for row in rows]
    self.holdings_empty_label.text = "No eligible long equity holdings are available for this quarter."
    self.holdings_empty_label.visible = not rows
    self.filing_label.text = "Quarter ended {} · filed {} · {:,} reported positions".format(
      self._date_text(filing.get("period_end")), self._date_text(filing.get("filed_at")), len(rows))
    url = filing.get("source_url")
    self.filing_link.url = url or ""
    self.filing_link.visible = bool(url)
    coverage = filing.get("coverage")
    if isinstance(coverage, dict):
      notes = []
      if coverage.get("mapped_positions") is not None:
        notes.append("{:,} mapped positions · {:,} unmapped · {:.1f}% of eligible reported value mapped".format(
          coverage["mapped_positions"], coverage.get("unmapped_positions") or 0, coverage.get("mapped_value_pct") or 0))
      excluded = (coverage.get("excluded_option_rows") or 0) + (coverage.get("excluded_other_rows") or 0)
      if excluded:
        notes.append("{:,} option or other filing rows excluded from the long equity model".format(excluded))
      if coverage.get("confidential_omitted"):
        notes.append("The filing omits confidential positions")
      self.coverage_label.text = coverage.get("message") or ". ".join(notes)
    else:
      self.coverage_label.text = str(coverage or "")
    self.coverage_label.visible = bool(self.coverage_label.text)

  @handle("trades_toggle_button", "click")
  def trades_toggle(self, **event_args):
    if self._editable():
      self._trades_expanded = not self._trades_expanded
      self.trade_details_region.visible = self._trades_expanded
      self.trades_toggle_button.text = "Hide inferred trades" if self._trades_expanded else "Show inferred trades"

  @handle("more_trades_button", "click")
  def show_more_trades(self, **event_args):
    if self._editable():
      self._trade_limit += 100
      self._render_trades()

  def _render_trades(self):
    if self._result is None:
      return
    trades = self._result.get("trades") or []
    shown = trades[:self._trade_limit]
    rows = []
    for trade in shown:
      action = str(trade["action"])
      price_rule = "quarter low" if action.lower() == "buy" else "quarter high"
      rows.append({"title_text": "{} · {} · {} shares".format(trade["symbol"], action, self._quantity(trade["shares"])),
                   "summary_text": "{} · {} / share · {}".format(self._date_text(trade["date"]), self._money(trade.get("price")), trade["quarter"]),
                   "detail_text": "{} net share change inferred from filings. Assumed execution at the {} on {}. These filings do not establish actual trade dates or prices.".format(
                     trade["quarter"], price_rule, self._date_text(trade["date"]))})
    self.trade_rows.items = rows
    self.trade_count_label.text = "Showing {:,} of {:,} inferred trades.".format(len(shown), len(trades)) if trades else "No inferred trades fall within this comparison window."
    self.more_trades_button.visible = len(shown) < len(trades)
    self.trade_rows.raise_event_on_children("x-set-editable", editable=self._editable())

  @handle("enlarge_button", "click")
  def enlarge_chart(self, **event_args):
    if self._editable() and self._result is not None:
      self._expanded = not self._expanded
      self.comparison_plot.height = "650px" if self._expanded else "390px"
      self.enlarge_button.text = "Restore chart size" if self._expanded else "Enlarge chart"
      self.redraw()

  @handle("ranks_button", "click")
  def load_ranks(self, **event_args):
    if not self._editable() or len(self._points) < 2:
      return
    period = self._result["period"] if self._result is not None else {"start": self._points[0]["date"], "end": self._points[-1]["date"]}
    generation = self._generation
    self._busy = True
    self._set_status("Ranking cached fund models over the same completed sessions…")
    self._update_controls()
    try:
      result = anvil.server.call("get_hedge_fund_leaderboard", self._date_text(period["start"]), self._date_text(period["end"]), self._risk_free_rate, self._points)
      if generation != self._generation:
        return
      if not result["ok"]:
        self._set_status(result["message"], True)
        return
      rows = []
      for row in result["rows"]:
        rows.append(dict(row, rank_text=str(row["rank"]) if row.get("rank") is not None else "—",
                         delta_text="{:+.2f} Sharpe advantage".format(row["delta_sharpe"]) if row.get("delta_sharpe") is not None else "No rank",
                         detail_text="Model Sharpe {} · S&P 500 {} · return {} · {:,} observations".format(
                           self._decimal(row.get("sharpe")), self._decimal(row.get("benchmark_sharpe")), self._percent(row.get("total_return")), row.get("observations") or 0),
                         status_text=row.get("status") or ""))
      self.rank_rows.items = rows
      excluded = result.get("excluded") or []
      self.rank_excluded_label.text = "Not ranked: " + "; ".join(excluded) if excluded else ""
      self.rank_excluded_label.visible = bool(excluded)
      self.rank_empty_label.visible = not rows
      self.rank_empty_label.text = "No cached fund models cover this entire period. Check filings or try a shorter Performance timeframe."
      self.rank_scope_label.text = "{} to {} · {}".format(self._date_text(period["start"]), self._date_text(period["end"]), result.get("scope") or "available cached fund models")
      self._set_status("Same-period rankings updated. Unavailable models remain unranked.")
    finally:
      if generation == self._generation:
        self._busy = False
        self._update_controls()

  @handle("rank_rows", "x-compare-fund")
  def compare_ranked_fund(self, fund_key, **event_args):
    if not self._editable() or not any(fund["fund_key"] == fund_key for fund in self._funds):
      return
    self.search_box.text = ""
    self._filter_funds()
    self.fund_dropdown.selected_value = fund_key
    self._generation += 1
    self._clear_result()
    self._clear_holdings()
    self._render_profile()
    self._update_controls()
    self.compare_selected()

  @handle("update_button", "click")
  def update_filings(self, **event_args):
    if not self._editable() or self._update_job_id is not None or self.fund_dropdown.selected_value is None:
      return
    generation = self._generation
    self._busy = True
    self._set_status("Checking the SEC for this fund's newly released filings…")
    self._update_controls()
    completed = False
    try:
      result = anvil.server.call("update_hedge_fund_filings", self.fund_dropdown.selected_value)
      if generation != self._generation:
        return
      if not result["ok"]:
        self._set_status(result["message"], True)
        return
      self._clear_result()
      self._update_job_id = result.get("job_id") if result.get("state") == "running" else None
      completed = result.get("state") == "completed"
      self.update_timer.interval = 5 if self._update_job_id is not None and self._opened else 0
      self._set_status(result.get("message") or "Filings updated. Compare again to recompute this profile.")
    finally:
      if generation == self._generation:
        self._busy = False
        self._update_controls()

    if completed and generation == self._generation:
      self.refresh_profiles()
      if self._editable() and self.fund_dropdown.selected_value is not None:
        self.view_holdings()
      if self._editable() and self.fund_dropdown.selected_value is not None and len(self._points) >= 2:
        self.compare_selected()

  def _stop_update_polling(self):
    self.update_timer.interval = 0
    self._update_job_id = None
    self._polling = False

  @handle("update_timer", "tick")
  def poll_update(self, **event_args):
    if self._update_job_id is None or self._polling or self._busy or not self._parent_editable or not self._opened:
      return
    job_id = self._update_job_id
    self._polling = True
    completed = False
    try:
      result = anvil.server.call_s("get_hedge_fund_update_status", job_id)
      if job_id != self._update_job_id:
        return
      if not result["ok"]:
        self._stop_update_polling()
        self._set_status(result["message"], True)
        return
      self._set_status(result.get("message") or "Checking public SEC filings…")
      if result["state"] == "completed":
        self._stop_update_polling()
        completed = True
    finally:
      if job_id == self._update_job_id:
        self._polling = False
      self._update_controls()
    if completed:
      self.refresh_profiles()
      if self._editable() and self.fund_dropdown.selected_value is not None:
        self.view_holdings()
      if self._editable() and self.fund_dropdown.selected_value is not None and len(self._points) >= 2:
        self.compare_selected()

  @handle("", "hide")
  def hide_view(self, **event_args):
    self.update_timer.interval = 0

  @handle("", "show")
  def show_view(self, **event_args):
    if self._opened and self._update_job_id is not None:
      self.update_timer.interval = 5
    self.redraw()

  def redraw(self):
    if self._opened and self._result is not None:
      self.comparison_plot.redraw()

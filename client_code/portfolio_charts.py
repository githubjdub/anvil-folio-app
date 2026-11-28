"""Chart rendering loaded after saved portfolio values, or on first chart use.

Form1 keeps event handlers and lightweight startup state. These functions use
that workspace so selection, cache ownership and retained-page behavior stay
unchanged. The existing idle preparation warms charts after startup.
"""

from datetime import date
import math
from typing import Any
from . import chart_theme

SECTOR_COLORS = chart_theme.SECTOR_COLORS


def _daily_risk(self, indices, observed):
  if self._history is None:
    return None, None
  daily_returns = [
    indices[index] / indices[index - 1] - 1
    for index in range(1, len(indices)) if observed[index] and indices[index - 1] > 0
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
    "margin": {"l": 46, "r": 12, "t": 58, "b": 36},
    "paper_bgcolor": chart_theme.PANEL, "plot_bgcolor": chart_theme.PANEL,
    "font": {"family": "Inter, sans-serif", "size": 12, "color": chart_theme.MUTED},
    "hovermode": "closest" if self.show_trades_checkbox.checked else "x unified",
    "legend": {"orientation": "h", "x": 1, "xanchor": "right", "y": 1.04, "yanchor": "bottom", "font": {"size": 11}},
    "transition": {"duration": 0}, "dragmode": "zoom",
    "xaxis": {"type": "date", "showgrid": False, "fixedrange": False, "nticks": 6, "automargin": True},
    "yaxis": {"tickformat": ".0%", "gridcolor": chart_theme.GRID, "zerolinecolor": chart_theme.ZERO_LINE,
              "fixedrange": False, "automargin": True, "nticks": 7},
  })


def _set_chart(self, plot, dates, portfolio, benchmark, benchmark_name, markers, benchmark_choices):
  name = "Equal-Weighted" if self._performance_weighting == "equal" else "Your portfolio"
  line = {"type": "scatter", "mode": "lines", "name": name, "x": dates,
          "y": portfolio, "line": {"color": chart_theme.BENCHMARK, "width": 2.5},
          "hovertemplate": "%{y:.2%}<extra>" + name + "</extra>"}
  if plot is self.performance_plot:
    rgb = tuple(int(chart_theme.BENCHMARK[index:index + 2], 16) for index in (1, 3, 5))
    line.update(fill="tozeroy", fillcolor="rgba(%d,%d,%d,0.07)" % rgb)
  plot.figure = {"data": [line,
    {"type": "scatter", "mode": "lines", "name": next((label.split(" · ")[0] for label, key in benchmark_choices
                    if key == self.benchmark_dropdown.selected_value), "Benchmark"), "x": dates,
     "y": benchmark, "line": {"color": chart_theme.MUTED, "width": 1.3, "dash": "solid"},
     "hovertemplate": "%{y:.2%}<extra>" + benchmark_name + "</extra>"},
  ] + markers, "layout": self._chart_layout()}


def _drawdowns(self, indices):
  peak = indices[0]
  values = []
  for value in indices:
    peak = max(peak, value)
    values.append(value / peak - 1)
  return values


def _clear_comparison(self, message):
  self._clear_chart_trades()
  self._performance_details_context = None
  self._performance_details_dirty = True
  self.window_return_caption.text = "Portfolio return"
  if self.hedge_fund_comparison is not None:
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


def _performance_source(self, weighting):
  if weighting == "manager":
    return self._history
  if self._history is None:
    return None
  if self._equal_weight_source is not self._history:
    from .performance_weighting import equal_weighted_history
    self._equal_weight_history = equal_weighted_history(self._history, self._selected_symbols)
    self._equal_weight_source = self._history
  return self._equal_weight_history


def _comparison_window(self, weighting="manager"):
  """Use actual weights by default, including when creating shared charts."""
  source = self._performance_source(weighting)
  if source is None or not source["series"]:
    raise ValueError("Select at least one synced security to compare returns.")
  timeframe = self.timeframe_dropdown.selected_value or "ALL"
  self._update_benchmark_options()
  benchmark_key = "sp500_%s" % self.benchmark_return_dropdown.selected_value
  custom_start = self.comparison_start_picker.date if timeframe == "CUSTOM" else None
  custom_end = self.comparison_end_picker.date if timeframe == "CUSTOM" else None
  key = (timeframe, benchmark_key, custom_start, custom_end)
  if self._comparison_window_source is source and self._comparison_window_key == key:
    return self._comparison_window_data
  series = source["series"]
  start = self._date_from_iso(series[0]["date"])
  end = self._date_from_iso(series[-1]["date"])
  if timeframe == "CUSTOM":
    start, end = custom_start, custom_end
    if start is None or end is None or start > end:
      raise ValueError("Choose a start date on or before the end date.")
  elif timeframe == "YTD":
    start = date(end.year, 1, 1)
  elif timeframe != "ALL":
    start = self._months_before(end, {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60}[timeframe])
  first, last = start.isoformat(), end.isoformat()
  selected = [point for point in series if first <= point["date"] <= last]
  if not selected:
    raise ValueError("No portfolio history is available in this period.")
  benchmark_data = source["benchmarks"].get(benchmark_key)
  if not benchmark_data or not benchmark_data.get("series"):
    raise ValueError("S&P 500 data is unavailable in this saved analysis. Refresh your portfolio to try again.")
  benchmark_by_date = {point["date"]: point["index"] for point in benchmark_data["series"]}
  benchmark_complete = {point["date"]: point.get("session_complete", True) for point in benchmark_data["series"]}
  first_benchmark, last_benchmark = min(benchmark_by_date), max(benchmark_by_date)
  common = [point for point in selected if first_benchmark <= point["date"] <= last_benchmark]
  if not common:
    raise ValueError("No overlapping portfolio and benchmark dates. Choose another period.")
  if any(point["date"] not in benchmark_by_date for point in common):
    raise ValueError("S&P 500 has missing daily closes in this period. Refresh your portfolio to update it.")
  if common[0]["portfolio_index"] <= 0 or benchmark_by_date[common[0]["date"]] <= 0:
    raise ValueError("Choose a period with a positive return baseline.")
  notice = "S&P 500 price returns are shown; dividends are excluded." if benchmark_key == "sp500_price" else ""
  if len(common) < len(selected):
    notice += " Both lines use their overlapping dates: %s – %s." % (common[0]["date"], common[-1]["date"])
  result = {"points": common, "benchmark_by_date": benchmark_by_date,
            "benchmark_complete": benchmark_complete, "benchmark_data": benchmark_data,
            "notice": notice.strip(), "requested_start": first, "requested_end": last}
  self._comparison_window_source = source
  self._comparison_window_key = key
  self._comparison_window_data = result
  return result


def _render_comparison(self, benchmark_choices, hedge_fund_comparisons_enabled):
  if self._history is None or self._analysis_source is None:
    return
  from .chart_trades import build_trade_markers
  for weighting, button in (("manager", self.manager_weighted_button), ("equal", self.equal_weighted_button)):
    active = weighting == self._performance_weighting
    button.role = "chart-range-active" if active else "chart-range"
    self._chart_button_node(button).setAttribute("aria-pressed", "true" if active else "false")
  self.weighting_method_label.text = (
    "Equal dollars in securities held each day · Daily rebalancing · Illustrative; no rebalancing costs or taxes · Share uses actual weights."
    if self._performance_weighting == "equal" else
    "Your actual position sizes and trades · Dividends held as cash · Time-weighted returns"
  )
  timeframe = self.timeframe_dropdown.selected_value or "ALL"
  for value, button in self._timeframe_buttons():
    button.role = "chart-range-active" if value == timeframe else "chart-range"
    self._chart_button_node(button).setAttribute("aria-pressed", "true" if value == timeframe else "false")
  self._clear_chart_trades()
  self.custom_period_region.visible = timeframe == "CUSTOM"
  try:
    window = self._comparison_window(self._performance_weighting)
  except ValueError as error:
    self.benchmark_status_label.text = str(error)
    self._clear_comparison(str(error))
    return
  selected = window["points"]
  benchmark_by_date = window["benchmark_by_date"]
  benchmark_complete = window["benchmark_complete"]
  benchmark_name = window["benchmark_data"]["name"]
  self.benchmark_status_label.text = window["notice"]
  short_name = next((label.split(" · ")[0] for label, key in benchmark_choices
                     if key == self.benchmark_dropdown.selected_value), "benchmark")
  self.performance_heading_label.text = "Performance vs %s" % short_name
  if hedge_fund_comparisons_enabled:
    self._ensure_tool("hedge_fund_comparison").configure_context([{
      "date": point["date"], "index": point["portfolio_index"],
      "return_observed": point["return_observed"],
      "session_complete": point.get("session_complete", True),
    } for point in selected], self._history["metadata"]["risk_free_rate"])
  dates = [point["date"] for point in selected]
  portfolio = [point["portfolio_index"] for point in selected]
  benchmark = [benchmark_by_date[day] for day in dates]
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
  limited = (self._date_from_iso(dates[0]) - self._date_from_iso(window["requested_start"])).days > 7
  if limited:
    self.comparison_period_label.text += " · Showing available history"
  self.window_return_caption.text = ("Available-period return" if limited else
    {"3Y": "3-year return", "5Y": "5-year return"}.get(timeframe, "Portfolio return"))
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
    else "Transactions are hidden on the chart. View transactions to see activity in this period."
  )
  self._set_chart(self.performance_plot, dates, portfolio_returns, benchmark_returns, benchmark_name,
                  markers["performance_traces"] if self.show_trades_checkbox.checked else [])
  self._performance_details_context = (selected, benchmark_by_date, benchmark_name, dates,
                                       drawdowns, self._drawdowns(benchmark), markers)
  self._performance_details_dirty = True
  self._render_performance_details()


def _render_performance_details(self):
  if not self._performance_details_open or not self._performance_details_dirty or self._performance_details_context is None:
    return
  selected, benchmark_by_date, benchmark_name, dates, drawdowns, benchmark_drawdowns, markers = self._performance_details_context
  self._set_chart(self.drawdown_plot, dates, drawdowns, benchmark_drawdowns, benchmark_name,
                  markers["drawdown_traces"] if self.show_trades_checkbox.checked else [])
  self._render_quarterly(selected, benchmark_by_date, benchmark_name)
  self._performance_details_dirty = False


def _render_quarterly(self, points, benchmark_by_date, benchmark_name, benchmark_choices):
  from .portfolio_periods import quarterly_returns
  rows = quarterly_returns(points, benchmark_by_date)
  portfolio_name = "Equal-Weighted" if self._performance_weighting == "equal" else "Your portfolio"
  short_benchmark_name = next((label.split(" · ")[0] for label, key in benchmark_choices
                               if key == self.benchmark_dropdown.selected_value), "Benchmark")
  self.quarterly_panel.items = [dict(row,
    detail_text="%s – %s%s%s" % (row["start_date"], row["end_date"], " · window edge" if row["edge_window"] else "", " · provisional" if row.get("provisional") else ""),
    portfolio_text="—" if row["portfolio_return"] is None else self._format_percent(row["portfolio_return"]),
    benchmark_text="—" if row["benchmark_return"] is None else self._format_percent(row["benchmark_return"]),
    excess_text="—" if row["excess_return"] is None else "%.2f pp" % (row["excess_return"] * 100),
  ) for row in reversed(rows)]
  self.quarterly_period_label.text = "%s vs %s · Calendar quarters within the displayed period. Window edges may be partial; the latest quarter is provisional when it includes current-session prices." % (portfolio_name, benchmark_name)
  data = [[row["start_date"], row["end_date"], row["observations"]] for row in rows]
  labels = [row["label"] for row in rows]
  self.quarterly_plot.data = [
    {"type": "bar", "name": portfolio_name, "x": labels,
     "y": [row["portfolio_return"] for row in rows], "customdata": data,
     "marker": {"color": [chart_theme.NEGATIVE if (row["portfolio_return"] or 0) < 0 else chart_theme.POSITIVE for row in rows]},
     "hovertemplate": "%{x}<br>%{y:.2%}<br>%{customdata[0]} – %{customdata[1]}<br>%{customdata[2]} daily intervals<extra>" + portfolio_name + "</extra>"},
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


def _allocation_groups_for_view(self):
  security_view = self.allocation_view_dropdown.selected_value == "security"
  if security_view not in self._allocation_groups_cache:
    from .allocation_breakdown import allocation_groups
    groups, previous = {}, None
    for snapshot in self._allocation_snapshots:
      groups[snapshot["key"]] = allocation_groups(snapshot, security_view, previous)
      previous = snapshot
    self._allocation_groups_cache[security_view] = groups
  return self._allocation_groups_cache[security_view]


def _render_sectors(self):
  if self._history is None or self._analysis_source is None:
    return
  from .portfolio_periods import allocation_snapshots
  if not self._allocation_data_ready:
    self._allocation_snapshots = allocation_snapshots(self._analysis_source, self._selected_symbols)
    previous = self.allocation_quarter_dropdown.selected_value or "latest"
    self.allocation_quarter_dropdown.items = [(snapshot["label"], snapshot["key"]) for snapshot in reversed(self._allocation_snapshots)] or [("Latest", "latest")]
    keys = {snapshot["key"] for snapshot in self._allocation_snapshots}
    self.allocation_quarter_dropdown.selected_value = previous if previous in keys else "latest"
    self._allocation_data_ready = True
  palette = list(SECTOR_COLORS.values())
  symbols = sorted(row["symbol"] for row in self._analysis_source["selection_data"]["securities"])
  self._security_colors = {symbol: palette[index % len(palette)] for index, symbol in enumerate(symbols)}
  self._allocation_security_rows = None
  self._render_allocation_snapshot()
  self._render_allocation_history()


def _render_allocation_snapshot(self, holdings_map_enabled):
  if self._history is None:
    return
  self._set_allocation_hover(None)
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
  groups = self._allocation_groups_for_view().get(chosen)
  if groups is None:
    from .allocation_breakdown import allocation_groups
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
  self.allocation_legend_label.text = "Security allocation" if security_view else "Sector allocation"
  self.allocation_change_label.text = (
    "Change in percentage points since %s" % previous["label"].replace(" · latest", "")
    if previous and previous["total_value"] > 0 else "Change unavailable: no earlier invested snapshot."
  )
  self.sector_empty_label.visible = not total
  self.sector_empty_label.text = (
    "Select a security below to see its allocation." if not self._selected_symbols
    else "No selected open positions at this snapshot. Closed trades and dividends still contribute to performance."
  )
  self.sector_panel.items = [dict(group,
    color=color,
    weight_text="%.1f%%" % (group["weight"] * 100),
    value_text=self._format_currency(group["market_value"]),
    change_value_text=("—" if group["change_pp"] is None else "0.0"
                       if abs(group["change_pp"]) < 0.05 else "%+.1f" % group["change_pp"]),
  ) for group, color in zip(groups, colors)]
  self._allocation_row_names = {group["sector"] for group in groups}
  unknown = [row["symbol"] for row in holdings if row["sector"] == "Unclassified"]
  self.sector_status_label.text = (
    "Choose a sector in the holdings table for %s. Their values remain included under Unclassified." % ", ".join(unknown)
    if unknown else "Folio classifications · Sector choices can be customized in the holdings table."
  )
  if holdings_map_enabled:
    self._render_allocation_map(allocation)


def _render_allocation_map(self, allocation, holdings_map_enabled):
  if not holdings_map_enabled:
    return
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


def _allocation_selection_outline(self):
  selected = self.allocation_quarter_dropdown.selected_value
  index = next((index for index, snapshot in enumerate(self._allocation_snapshots)
                if snapshot["key"] == selected), None)
  return [] if index is None else [{
    "type": "rect", "xref": "x", "yref": "paper",
    "x0": index - 0.46, "x1": index + 0.46, "y0": 0, "y1": 1,
    "fillcolor": "rgba(0,0,0,0)", "line": {"color": chart_theme.MUTED, "width": 1},
    "layer": "below",
  }]


def _render_allocation_history(self):
  snapshots = self._allocation_snapshots
  security_view = self.allocation_view_dropdown.selected_value == "security"
  key = (security_view, chart_theme.MODE)
  selected = self.allocation_quarter_dropdown.selected_value
  if self._allocation_history_key == key:
    if snapshots and self._allocation_highlight_key != selected:
      # Selecting a quarter only moves the outline; trace arrays stay intact.
      self.allocation_history_plot.relayout({"shapes": self._allocation_selection_outline()})
      self._allocation_highlight_key = selected
    return
  self.allocation_history_title_label.text = "Security allocation over time" if security_view else "Sector allocation over time"
  self.allocation_history_plot.visible = bool(snapshots)
  self.allocation_history_status_label.text = (
    "Quarterly weights · Select a bar to view its snapshot · Cash excluded"
    if snapshots else "Refresh your portfolio to load quarterly position weights."
  )
  cached = self._allocation_groups_for_view()
  grouped = [{group["sector"]: group for group in cached[snapshot["key"]]} for snapshot in snapshots]
  names = set(name for groups in grouped for name in groups)
  latest = grouped[-1] if grouped else {}
  # Anchor both stack order and colours to the latest allocation, so choosing
  # another quarter never reshuffles the history or rebuilds its traces.
  names = sorted(names, key=lambda name: (-latest.get(name, {}).get("weight", 0), name))
  self._allocation_trace_names = names
  labels = [snapshot["label"].replace(" · latest", "") for snapshot in snapshots]
  traces = [
    {"type": "bar", "name": name, "x": labels,
     "y": [groups.get(name, {}).get("weight", 0) if snapshot["total_value"] else None for groups, snapshot in zip(grouped, snapshots)],
     "customdata": [snapshot["date"] for snapshot in snapshots],
     "marker": {"color": self._security_colors.get(name, chart_theme.MUTED) if security_view else SECTOR_COLORS.get(name, chart_theme.MUTED)},
     "hovertemplate": "%{x}<br>%{customdata}<br>%{y:.1%}<extra>" + name + "</extra>"}
    for name in names
  ]
  tick_step = max(1, (len(snapshots) + 8) // 9)
  ticks = list(range(0, len(snapshots), tick_step))
  if snapshots and len(snapshots) - 1 not in ticks:
    ticks.append(len(snapshots) - 1)
  self.allocation_history_plot.figure = {"data": traces, "layout": chart_theme.layout({
    "barmode": "stack", "bargap": 0.24, "margin": {"l": 45, "r": 8, "t": 12, "b": 40},
    "paper_bgcolor": chart_theme.PANEL, "plot_bgcolor": chart_theme.PANEL,
    "font": {"family": "Inter, sans-serif", "size": 12, "color": chart_theme.MUTED},
    "showlegend": False, "transition": {"duration": 0},
    "shapes": self._allocation_selection_outline(),
    "xaxis": {"type": "category", "showgrid": False, "fixedrange": True,
              "tickmode": "array", "tickvals": [labels[index] for index in ticks],
              "ticktext": ["Q%s '%s" % (labels[index][1], labels[index][-2:]) for index in ticks], "tickangle": 0},
    "yaxis": {"tickformat": ".0%", "dtick": 0.25, "range": [0, 1], "gridcolor": chart_theme.GRID, "fixedrange": True},
  })}
  self._allocation_history_key, self._allocation_highlight_key = key, selected


def _render_security_rows(self):
  if not self._allocation_holdings_open or self._analysis_source is None or self._history is None:
    return
  if self._allocation_security_rows is None:
    securities = self._analysis_source["selection_data"]["securities"]
    allocation = self._allocation_snapshot or self._history["sector_allocation"]
    total_value = allocation["total_value"]
    snapshot_date = allocation.get("date", allocation.get("as_of"))
    dates = self._analysis_source["selection_data"]["dates"]
    index = self._allocation_snapshot["index"] if self._allocation_snapshot else len(dates) - 1 if dates and dates[-1] == snapshot_date else None
    rows: list[dict[str, Any]] = []
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
    self._allocation_security_rows = rows
  rows = self._allocation_security_rows
  query = str(self.allocation_search_box.text or "").strip().lower()
  position = self.allocation_position_dropdown.selected_value or "all"
  filtered = [row for row in rows
              if (not query or query in ("%s %s %s" % (row["symbol"], row.get("company_name") or "", row["sector"])).lower())
              and (position == "all" or row["is_open"] == (position == "open"))]
  self.security_panel.items = sorted(filtered, key=lambda row: (-row["snapshot_value"], row["symbol"]))
  self.allocation_filter_empty_label.visible = bool(rows) and not filtered
  self.security_panel.raise_event_on_children("x-set-editable", editable=not self._busy, can_classify=self._user is not None)
  self.selection_status_label.text = (
    "%d of %d securities selected · All metrics and charts use this selection." % (len(self._selected_symbols), len(rows))
    if self._selected_symbols else "No securities selected. Check a security or choose Select all."
  )
  if query or position != "all":
    self.selection_status_label.text += " Showing %d of %d holdings; filters do not change your selection." % (len(filtered), len(rows))

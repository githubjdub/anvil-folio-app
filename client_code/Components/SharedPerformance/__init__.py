from ._anvil_designer import SharedPerformanceTemplate
from anvil import *
from ... import chart_theme


def _percentage(value, difference=False):
  if value is None:
    return "—"
  return "%s%.2f%s" % ("+" if value >= 0 else "", value * 100,
                       " pp" if difference else "%")


def _number(value):
  return "—" if value is None else "%.2f" % value


def _volatility(value):
  return "—" if value is None else "%.2f%%" % (value * 100)


class SharedPerformance(SharedPerformanceTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.performance_plot.config = {"responsive": True}
    self.set_snapshot(self.item)

  @handle("", "show")
  def form_show(self, **event_args):
    if self.item:
      self.performance_plot.redraw()

  def set_snapshot(self, snapshot):
    self.item = snapshot
    self.snapshot_content.visible = bool(snapshot)
    if not snapshot:
      self.performance_plot.data = []
      return

    metrics = snapshot["metrics"]
    benchmark = snapshot["benchmark"]
    self.title_label.text = snapshot["title"]
    self.privacy_label.text = "Percentages only · holdings and amounts hidden"
    self.date_label.text = "%s – %s · %s" % (
      snapshot["start_date"], snapshot["end_date"], benchmark["name"]
    )
    self.portfolio_return_label.text = _percentage(metrics.get("portfolio_return"))
    self.benchmark_return_label.text = _percentage(metrics.get("benchmark_return"))
    self.excess_return_label.text = _percentage(metrics.get("excess_return"), difference=True)
    self.benchmark_caption_label.text = benchmark["name"]
    self.portfolio_sharpe_label.text = _number(metrics.get("portfolio_sharpe"))
    self.benchmark_sharpe_label.text = _number(metrics.get("benchmark_sharpe"))
    self.portfolio_volatility_label.text = _volatility(metrics.get("portfolio_volatility"))
    self.benchmark_volatility_label.text = _volatility(metrics.get("benchmark_volatility"))
    self.observations_label.text = "%s completed daily return intervals · Annualized risk metrics" % metrics.get("observations", "—")
    mode = "Dividends reinvested" if benchmark.get("return_mode") == "total" else "Price return · excludes dividends"
    self.benchmark_mode_label.text = "Benchmark: %s" % mode
    self.snapshot_label.text = "Frozen snapshot created %s. Changes to the owner's portfolio do not update this page." % snapshot["created_at"].replace("T", " ")[:16]
    if snapshot.get("provisional_date"):
      self.date_label.text += " · provisional endpoint"
      self.snapshot_label.text += " Portfolio quotes as of %s; the unfinished day is excluded from risk metrics." % snapshot.get("quoted_at")
    self.method_label.text = snapshot.get("method") or "Returns are calculated from user-entered or imported trades and historical market data."
    self.source_label.text = snapshot.get("source") or "Historical prices and dividends; selected market benchmark."
    self.benchmark_source_link.url = benchmark.get("source_url") or ""
    self.benchmark_source_link.visible = bool(benchmark.get("source_url"))

    for panel, field in ((self.portfolio_return_tile, "portfolio_return"),
                         (self.benchmark_return_tile, "benchmark_return"),
                         (self.excess_return_tile, "excess_return")):
      value = metrics.get(field)
      panel.classes["is-negative"] = value is not None and value < 0

    series = snapshot.get("series", [])
    self.performance_plot.layout = chart_theme.layout({
      "paper_bgcolor": chart_theme.PANEL, "plot_bgcolor": chart_theme.PANEL,
      "font": {"family": "Arial, sans-serif", "size": 13, "color": chart_theme.MUTED},
      "margin": {"l": 58, "r": 16, "t": 22, "b": 85},
      "xaxis": {"type": "date", "showgrid": False, "fixedrange": True},
      "yaxis": {"tickformat": ".0%", "gridcolor": chart_theme.GRID, "zerolinecolor": chart_theme.ZERO_LINE, "fixedrange": True},
      "legend": {"orientation": "h", "x": 0, "y": -0.22},
      "hovermode": "x unified", "autosize": True,
    })
    benchmark_chart_name = {
      "sp500": "S&P 500", "nasdaq": "Nasdaq Composite", "spy": "SPY",
      "qqq": "QQQ", "dia": "DIA", "iwm": "IWM", "vti": "VTI",
    }.get(benchmark.get("key"), "Benchmark")
    self.performance_plot.data = [
      {"type": "scatter", "mode": "lines", "name": "Portfolio",
       "x": [point["date"] for point in series],
       "y": [point.get("portfolio_return") for point in series],
       "line": {"color": chart_theme.POSITIVE, "width": 3},
       "hovertemplate": "%{y:.2%}<extra>Portfolio</extra>"},
      {"type": "scatter", "mode": "lines", "name": benchmark_chart_name,
       "x": [point["date"] for point in series],
       "y": [point.get("benchmark_return") for point in series],
       "line": {"color": chart_theme.BENCHMARK, "width": 2},
       "hovertemplate": "%{y:.2%}<extra>Benchmark</extra>"},
    ]

    self.performance_plot.redraw()

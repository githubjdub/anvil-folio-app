"""Shared Plotly colours for the app's obsidian theme."""

BACKGROUND = "#0B0F19"
PANEL = "#121A28"
TEXT = "#E6EDF7"
MUTED = "#8A99AD"
GRID = "#283346"
ZERO_LINE = "#526079"
POSITIVE = "#00E676"
NEGATIVE = "#FF8190"
BENCHMARK = "#90B6EF"
COMPARISON = "#C4A4EE"
AMBER = "#F2BA73"

# Categorical colours distinguish allocation groups; green/red alone do not
# encode portfolio weights or sector performance.
SECTOR_COLORS = {
  "Technology": "#70C9B6", "Financial": BENCHMARK,
  "Healthcare": COMPARISON, "Consumer Cyclical": "#F0AA82",
  "Consumer Defensive": "#D5C47B", "Communication Services": "#76C9D8",
  "Industrials": "#A4B7D0", "Energy": "#E9A1B4",
  "Basic Materials": "#B1C58A", "Real Estate": "#D3A6D0",
  "Utilities": "#A5D2E6", "Funds / ETFs": "#AAB6C9", "Unclassified": MUTED,
}


def hover_label():
  return {"bgcolor": PANEL, "bordercolor": GRID,
          "font": {"family": "Inter, sans-serif", "color": TEXT, "size": 13}}


def layout(options):
  """Add readable dark defaults without changing chart ranges or interactions."""
  result = dict(options)
  result.setdefault("paper_bgcolor", PANEL)
  result.setdefault("plot_bgcolor", PANEL)
  result.setdefault("font", {"family": "Inter, sans-serif", "size": 13, "color": MUTED})
  result["hoverlabel"] = hover_label()
  result["modebar"] = {"bgcolor": PANEL, "color": MUTED, "activecolor": TEXT}
  for name in ("xaxis", "yaxis"):
    if name in result:
      axis = dict(result[name])
      axis.setdefault("gridcolor", GRID)
      axis.setdefault("zerolinecolor", ZERO_LINE)
      axis.setdefault("linecolor", GRID)
      result[name] = axis
  return result

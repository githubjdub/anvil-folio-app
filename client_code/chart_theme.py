"""Shared Plotly palettes; runtime changes preserve imported sector dictionaries."""

MODE = "light"
_PALETTES = {
  "dark": {
    "BACKGROUND": "#0B0F19", "PANEL": "#121A28", "TEXT": "#E6EDF7",
    "MUTED": "#8A99AD", "GRID": "#283346", "ZERO_LINE": "#526079",
    "POSITIVE": "#00E676", "NEGATIVE": "#FF8190", "BENCHMARK": "#90B6EF",
    "COMPARISON": "#C4A4EE", "AMBER": "#F2BA73", "PIE_TEXT": "#0B0F19",
  },
  "light": {
    "BACKGROUND": "#F4F6F8", "PANEL": "#FFFFFF", "TEXT": "#192B3D",
    "MUTED": "#5D6C7D", "GRID": "#DDE3EA", "ZERO_LINE": "#8A99AD",
    "POSITIVE": "#137A4D", "NEGATIVE": "#B63851", "BENCHMARK": "#315F9D",
    "COMPARISON": "#7952A7", "AMBER": "#96601B", "PIE_TEXT": "#FFFFFF",
  },
}
_SECTORS = {
  "dark": {
    "Technology": "#70C9B6", "Financial": "#90B6EF",
    "Healthcare": "#C4A4EE", "Consumer Cyclical": "#F0AA82",
    "Consumer Defensive": "#D5C47B", "Communication Services": "#76C9D8",
    "Industrials": "#A4B7D0", "Energy": "#E9A1B4",
    "Basic Materials": "#B1C58A", "Real Estate": "#D3A6D0",
    "Utilities": "#A5D2E6", "Funds / ETFs": "#AAB6C9", "Unclassified": "#8A99AD",
  },
  "light": {
    "Technology": "#277A68", "Financial": "#315F9D",
    "Healthcare": "#7952A7", "Consumer Cyclical": "#A25330",
    "Consumer Defensive": "#786A29", "Communication Services": "#237084",
    "Industrials": "#516883", "Energy": "#A44864",
    "Basic Materials": "#59703C", "Real Estate": "#8A5286",
    "Utilities": "#3D7088", "Funds / ETFs": "#5B687E", "Unclassified": "#5D6C7D",
  },
}

# Some callers keep this dictionary as an alias. Never replace its identity.
SECTOR_COLORS = {}
# Bind the exported colours before set_mode initializes them at import time.
BACKGROUND = PANEL = TEXT = MUTED = GRID = ZERO_LINE = ""
POSITIVE = NEGATIVE = BENCHMARK = COMPARISON = AMBER = PIE_TEXT = ""


def set_mode(mode):
  """Change chart colours without fetching or recalculating financial data."""
  global MODE, BACKGROUND, PANEL, TEXT, MUTED, GRID, ZERO_LINE
  global POSITIVE, NEGATIVE, BENCHMARK, COMPARISON, AMBER, PIE_TEXT
  mode = "light" if mode == "light" else "dark"
  changed = mode != MODE
  MODE = mode
  palette = _PALETTES[mode]
  BACKGROUND, PANEL, TEXT = palette["BACKGROUND"], palette["PANEL"], palette["TEXT"]
  MUTED, GRID, ZERO_LINE = palette["MUTED"], palette["GRID"], palette["ZERO_LINE"]
  POSITIVE, NEGATIVE = palette["POSITIVE"], palette["NEGATIVE"]
  BENCHMARK, COMPARISON = palette["BENCHMARK"], palette["COMPARISON"]
  AMBER, PIE_TEXT = palette["AMBER"], palette["PIE_TEXT"]
  SECTOR_COLORS.clear()
  SECTOR_COLORS.update(_SECTORS[mode])
  return changed


set_mode(MODE)


def hover_label():
  return {"bgcolor": PANEL, "bordercolor": GRID,
          "font": {"family": "Inter, sans-serif", "color": TEXT, "size": 13}}


def layout(options):
  """Add readable defaults for the selected appearance without changing chart ranges or interactions."""
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


def redraw(plot, previous_size=None, force=False):
  """Resize only visible charts whose measured size or content changed."""
  import anvil.js
  bounds = anvil.js.get_dom_node(plot).getBoundingClientRect()
  size = (round(bounds.width), round(bounds.height))
  if size[0] <= 0 or size[1] <= 0:
    return None
  if force or size != previous_size:
    plot.redraw()
  return size

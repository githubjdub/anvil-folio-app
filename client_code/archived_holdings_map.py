"""Retired holdings treemap renderer, kept for a possible future view.

This module is not imported or mounted by the portfolio. It only builds a
Plotly figure from an existing snapshot and makes no data requests.
"""
from . import chart_theme


def holdings_map_figure(snapshot):
    if snapshot is None:
      return {"data": [], "layout": {}, "config": {}}
    rows = [row for row in snapshot["rows"] if row["market_value"] > 0]
    total = snapshot["total"]
    # Symbols and company names enter Plotly as text, with markup escaped.
    def plain(value):
      return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    colors = [chart_theme.MUTED if row["return"] is None else
              "#32864D" if row["return"] > 0 else "#A53836" if row["return"] < 0 else "#637183"
              for row in rows]
    data = [{
      "type": "treemap", "ids": ["holding:" + row["symbol"] for row in rows],
      "labels": [plain(row["symbol"]) for row in rows], "parents": [""] * len(rows),
      "values": [row["market_value"] for row in rows], "branchvalues": "total",
      "customdata": [[plain(row["company_name"]), row["weight"],
                      "—" if row["return"] is None else "%+.1f%%" % (row["return"] * 100)] for row in rows],
      "texttemplate": "<b>%{label}</b><br>%{customdata[1]:.1%} · %{customdata[2]}",
      "hovertemplate": "%{label}<br>%{customdata[0]}<br>$%{value:,.2f}<br>%{customdata[1]:.2%} of invested value<br>Price return while held: %{customdata[2]}<extra></extra>",
      "marker": {"colors": colors, "line": {"color": chart_theme.PANEL, "width": 3}},
      "textfont": {"color": "#FFFFFF", "size": 19},
      "pathbar": {"visible": False}, "tiling": {"pad": 2},
    }] if total > 0 else []
    layout = chart_theme.layout({
      "margin": {"l": 0, "r": 0, "t": 0, "b": 0},
      "uniformtext": {"minsize": 11, "mode": "hide"},
    })
    return {"data": data, "layout": layout,
            "config": {"displayModeBar": False, "responsive": True}}

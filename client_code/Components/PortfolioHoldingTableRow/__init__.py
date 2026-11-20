from anvil import *
from ._anvil_designer import PortfolioHoldingTableRowTemplate
from ...holdings_dashboard import display_share_history


class PortfolioHoldingTableRow(PortfolioHoldingTableRowTemplate):
  def __init__(self, **properties):
    self._history_mounted = False
    self._history_rows = []
    super().__init__(**properties)
    self.weight_fill.style["width"] = "%s%%" % (self.item["weight"] * 100)
    value = self.item["return"]
    self.return_cell.classes["is-positive"] = value is not None and value > 0
    self.return_cell.classes["is-negative"] = value is not None and value < 0
    self.classes["is-closed"] = not self.item["is_open"]
    history = display_share_history(self.item["share_history"])
    self._history_rows = history
    self.share_bars.items = history
    self.history_empty_label.visible = not history
    tooltip = "Shares owned · changes include stock splits\n" + "\n".join(
      "%s: %s%s · %s" % (row["label_text"], row["shares_text"],
                           " (%s)" % row["change_text"] if row["change"] is not None else "",
                           row["date"]) for row in history)
    self.history_summary.attrs["title"] = tooltip
    self.history_summary.attrs["aria-label"] = "%s shares owned. Open quarterly history. %s" % (self.item["shares_text"], tooltip)

  def history_toggle(self, event):
    if event.currentTarget.open and not self._history_mounted:
      self.share_history.items = self._history_rows
      self._history_mounted = True

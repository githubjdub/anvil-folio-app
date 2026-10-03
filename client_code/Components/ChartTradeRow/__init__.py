from ._anvil_designer import ChartTradeRowTemplate
from anvil import *


class ChartTradeRow(ChartTradeRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.notes_label.visible = bool(self.item.get("notes"))

  @handle("open_transaction_button", "click")
  def open_transaction_button_click(self, **event_args):
    self.parent.raise_event(
      "x-open-chart-transaction",
      transaction_index=self.item["transaction_index"],
    )

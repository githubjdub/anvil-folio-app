from ._anvil_designer import HoldingRowTemplate
from anvil import *


class HoldingRow(HoldingRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)

  def _update_item(self, key, value):
    self.item[key] = value
    self.item.update({
      "entry_price": None,
      "current_price": None,
      "dividends_per_share": None,
      "quote_text": "Not synced",
      "dividend_text": "—",
    })
    self.refresh_data_bindings()
    self.parent.raise_event("x-holding-changed")

  def set_editable(self, editable, **event_args):
    for component in (
      self.ticker_box, self.trade_date_picker, self.action_dropdown,
      self.shares_box, self.trade_price_box, self.remove_button,
    ):
      setattr(component, "enabled", editable)

  @handle("ticker_box", "change")
  def ticker_box_change(self, **event_args):
    self._update_item("ticker", self.ticker_box.text)

  @handle("trade_date_picker", "change")
  def trade_date_picker_change(self, **event_args):
    self._update_item("trade_date", self.trade_date_picker.date)

  @handle("action_dropdown", "change")
  def action_dropdown_change(self, **event_args):
    self._update_item("action", self.action_dropdown.selected_value)

  @handle("shares_box", "change")
  def shares_box_change(self, **event_args):
    self._update_item("shares", self.shares_box.text)

  @handle("trade_price_box", "change")
  def trade_price_box_change(self, **event_args):
    self._update_item("trade_price", self.trade_price_box.text)

  @handle("remove_button", "click")
  def remove_button_click(self, **event_args):
    self.parent.raise_event("x-remove-holding", item=self.item)

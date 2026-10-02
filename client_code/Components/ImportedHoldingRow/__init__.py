from ._anvil_designer import ImportedHoldingRowTemplate
from anvil import *


class ImportedHoldingRow(ImportedHoldingRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True
    self.set_event_handler("x-set-editable", self.set_editable)
    self.classes["is-excluded"] = not self.item.get("include", True)
    self.notes_label.visible = bool(self.item.get("notes"))

  def set_editable(self, editable, **event_args):
    if self._editable == editable:
      return
    self._editable = editable
    for component in (
      self.include_checkbox, self.ticker_box, self.shares_box,
      self.trade_date_picker, self.action_dropdown, self.trade_price_box,
    ):
      setattr(component, "enabled", editable)

  def _update_item(self, key, value):
    if self._editable:
      self.item[key] = value
      # Preserve unfinished decimals and the editing cursor: the scratch item
      # changes without rebinding the editable fields on each keystroke.
      self.parent.raise_event("x-import-row-changed", item=self.item)

  @handle("include_checkbox", "change")
  def include_checkbox_change(self, **event_args):
    included = bool(self.include_checkbox.checked)
    self.classes["is-excluded"] = not included
    self._update_item("include", included)

  @handle("ticker_box", "change")
  def ticker_box_change(self, **event_args):
    self._update_item("ticker", self.ticker_box.text)

  @handle("shares_box", "change")
  def shares_box_change(self, **event_args):
    self._update_item("shares", self.shares_box.text)

  @handle("trade_date_picker", "change")
  def trade_date_picker_change(self, **event_args):
    self._update_item("trade_date", self.trade_date_picker.date)

  @handle("action_dropdown", "change")
  def action_dropdown_change(self, **event_args):
    self._update_item("action", self.action_dropdown.selected_value)

  @handle("trade_price_box", "change")
  def trade_price_box_change(self, **event_args):
    self._update_item("trade_price", self.trade_price_box.text)

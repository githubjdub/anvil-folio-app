import math

from ._anvil_designer import HoldingRowTemplate
from anvil import *


class HoldingRow(HoldingRowTemplate):
  def __init__(self, **properties):
    # Template bindings can raise input events before the row is attached.
    self._editable = False
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)
    self.set_event_handler("x-set-collapsed", self.set_collapsed)
    group = self.item.get("_security_group", "")
    self.security_group_header.visible = bool(group)
    self.classes["starts-security-group"] = bool(group)
    self.classes["is-chart-target"] = bool(self.item.get("_chart_target", False))
    self._refresh_summary()
    self._show_expanded()
    self._editable = True

  def _positive_number(self, value):
    try:
      number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
      return None
    if math.isnan(number) or math.isinf(number) or number <= 0:
      return None
    return number

  def _compact_number(self, value):
    text = format(value, ",.8f").rstrip("0").rstrip(".")
    return str(value) if text == "0" and value > 0 else text

  def _refresh_summary(self):
    ticker = str(self.item.get("ticker") or "").strip().upper() or "New transaction"
    action = self.item.get("action") or "Buy"
    shares = self._positive_number(self.item.get("shares"))
    quantity = ("%s shares" % self._compact_number(shares)) if shares is not None else "Add shares"
    self.summary_title_label.text = "%s · %s · %s" % (ticker, action, quantity)
    trade_date = self.item.get("trade_date")
    dated = trade_date.isoformat() if trade_date is not None else "Choose date"
    entered_price = self.item.get("trade_price")
    automatic = entered_price is None or str(entered_price).strip() == ""
    price = self._positive_number(self.item.get("entry_price") if automatic else entered_price)
    if automatic and price is None:
      price_text = "Price: historical close"
      value_text = "Trade value calculated on sync"
    elif price is None:
      price_text = "Check price / share"
      value_text = "Complete the transaction details"
    else:
      shown_price = format(price, ",.2f") if automatic and price >= 1 else self._compact_number(price)
      price_text = "$%s / share" % shown_price
      if automatic:
        price_text += " (historical)"
      total = shares * price if shares is not None else None
      value_text = "$%s trade value" % format(total, ",.2f") if total is not None and not math.isinf(total) else "Add a valid share count"
    self.summary_details_label.text = "%s · %s" % (dated, price_text)
    self.summary_value_label.text = value_text

  def _show_expanded(self):
    collapsed = bool(self.item.get("_collapsed", False))
    self.editor_region.visible = not collapsed
    self.toggle_button.text = "Expand" if collapsed else "Collapse"
    self.toggle_button.icon = "mi:expand_more" if collapsed else "mi:expand_less"
    self.classes["is-collapsed"] = collapsed

  def _update_item(self, key, value):
    self._apply_inputs({key: value})

  def commit_inputs(self):
    """Capture the current editor values before the parent validates a sync/save."""
    if not self._editable or self.parent is None:
      return
    self._apply_inputs({
      "ticker": self.ticker_box.text,
      "trade_date": self.trade_date_picker.date,
      "action": self.action_dropdown.selected_value,
      "shares": self.shares_box.text,
      "trade_price": self.trade_price_box.text,
    })

  def _apply_inputs(self, values):
    if not self._editable or self.parent is None:
      return
    changed = {key: value for key, value in values.items() if not self._same_input(key, value)}
    if not changed:
      return
    self.item.update(changed)
    self.item.update({
      "entry_price": None,
      "current_price": None,
      "dividends_per_share": None,
      "quote_text": "Not synced",
      "dividend_text": "—",
    })
    # Only refresh read-only outputs. Rebinding the numeric inputs on each
    # keystroke normalizes unfinished decimals and moves the editing cursor.
    self.quote_label.text = self.item["quote_text"]
    self.dividend_label.text = self.item["dividend_text"]
    self._refresh_summary()
    self.parent.raise_event("x-holding-changed")

  def _same_input(self, key, value):
    existing = self.item.get(key)
    if existing == value:
      return True
    if key == "trade_price" and (existing is None or not str(existing).strip()) and (value is None or not str(value).strip()):
      return True
    if key in ("shares", "trade_price"):
      number = self._positive_number(value)
      return number is not None and number == self._positive_number(existing)
    return False

  def set_editable(self, editable, **event_args):
    # Disabling a focused editor can trigger a pending blur/change event.
    # The parent commits editor values before locking the rows for a request.
    self._editable = False
    for component in (
      self.ticker_box, self.trade_date_picker, self.action_dropdown,
      self.shares_box, self.trade_price_box, self.remove_button, self.toggle_button,
    ):
      setattr(component, "enabled", editable)
    self._editable = editable

  def set_collapsed(self, collapsed, **event_args):
    if self._editable:
      self.item["_collapsed"] = collapsed
      self._show_expanded()

  @handle("toggle_button", "click")
  def toggle_button_click(self, **event_args):
    if self._editable:
      self.item["_collapsed"] = not self.item.get("_collapsed", False)
      self._show_expanded()

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
    if self._editable and self.parent is not None:
      self.parent.raise_event("x-remove-holding", item=self.item)

from anvil import *
from ._anvil_designer import DividendDateRowTemplate


class DividendDateRow(DividendDateRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._update_state()

  def set_editable(self, editable):
    self.payment_date_picker.enabled = editable

  def commit_date(self):
    changed = self.item.get("_payment_date") != self.payment_date_picker.date
    self.item["_payment_date"] = self.payment_date_picker.date
    self._update_state()
    return changed

  def _update_state(self):
    value = self.item.get("_payment_date")
    self.classes["is-missing"] = value is None
    self.refresh_data_bindings()

  def finviz_url(self, dividends=False):
    # Symbols come from the server's validated US-security inputs.
    return "https://finviz.com/quote.ashx?t=%s%s" % (
      self.item["symbol"], "&ty=dv" if dividends else "")

  def date_status(self):
    value = self.item.get("_payment_date")
    if value is None:
      return "Payment date not confirmed"
    if value != self.item.get("_original_payment_date"):
      return "Payment date supplied by you"
    return self.item.get("source") or "Check this payment date"

  @handle("payment_date_picker", "change")
  def payment_date_change(self, **event_args):
    self.commit_date()
    self.parent.raise_event("x-date-changed")

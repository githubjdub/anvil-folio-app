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

  def date_status(self):
    value = self.item.get("_payment_date")
    if value is None:
      return "Payment date not confirmed"
    if value != self.item.get("_original_payment_date"):
      return "User-entered payment date"
    return "Payment date confirmed"

  def date_note(self):
    if self.item.get("unsupported"):
      return "This distribution requires special due-bill handling and is not supported."
    value = self.item.get("_payment_date")
    if value is None:
      return "Refresh to check again, or enter a confirmed payment date. No date will be guessed."
    if value != self.item.get("_original_payment_date"):
      return "Your entered date will be used when you update the comparison."
    return "Payment date matched to this dividend's ex-date and cash amount."

  @handle("payment_date_picker", "change")
  def payment_date_change(self, **event_args):
    self.commit_date()
    self.parent.raise_event("x-date-changed")

from anvil import *
from ._anvil_designer import InsiderWatchRowTemplate


class InsiderWatchRow(InsiderWatchRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True
    self.activity_dropdown.items = [("Buying & selling", "both"), ("Buying only", "buys"), ("Selling only", "sells")]
    self.refresh_data_bindings()
    self.set_event_handler("x-set-editable", self.set_editable)

  def set_editable(self, editable=True, **event_args):
    self._editable = editable
    self.activity_dropdown.enabled = editable
    self.pause_button.enabled = editable
    self.remove_button.enabled = editable

  def check_status(self):
    if self.item["last_error"]:
      return "Last check: " + self.item["last_error"]
    checked = self.item["last_checked_at"]
    return "Last checked %s UTC" % checked.strftime("%b %d, %Y %H:%M") if checked else "Waiting for the first scheduled check."

  @handle("activity_dropdown", "change")
  def activity_change(self, **event_args):
    if self._editable:
      self.parent.raise_event("x-watch-update", watch=self.item, activity=self.activity_dropdown.selected_value, active=self.item["active"])

  @handle("pause_button", "click")
  def pause_click(self, **event_args):
    if self._editable:
      self.parent.raise_event("x-watch-update", watch=self.item, activity=self.item["activity"], active=not self.item["active"])

  @handle("remove_button", "click")
  def remove_click(self, **event_args):
    if self._editable:
      self.parent.raise_event("x-watch-remove", watch=self.item)

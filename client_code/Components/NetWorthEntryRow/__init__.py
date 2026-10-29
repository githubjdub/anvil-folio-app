from anvil import *
from ._anvil_designer import NetWorthEntryRowTemplate
from ...net_worth_display import money, entry_detail


class NetWorthEntryRow(NetWorthEntryRowTemplate):
  money = staticmethod(money)
  entry_detail = staticmethod(entry_detail)

  def __init__(self, **properties):
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)

  def set_editable(self, editable=True, **event_args):
    self.edit_button.enabled = editable
    self.remove_button.enabled = editable

  @handle("edit_button", "click")
  def edit_click(self, **event_args):
    self.parent.raise_event("x-edit-entry", entry=self.item)

  @handle("remove_button", "click")
  def remove_click(self, **event_args):
    self.parent.raise_event("x-remove-entry", entry=self.item)

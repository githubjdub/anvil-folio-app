from anvil import *
from ._anvil_designer import NetWorthSnapshotRowTemplate
from ...net_worth_display import money


class NetWorthSnapshotRow(NetWorthSnapshotRowTemplate):
  money = staticmethod(money)

  def __init__(self, **properties):
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)

  def detail(self):
    return "Assets %s · Debts %s" % (money(self.item["assets"]), money(self.item["debts"]))

  def set_editable(self, editable=True, **event_args):
    self.remove_button.enabled = editable

  @handle("remove_button", "click")
  def remove_click(self, **event_args):
    self.parent.raise_event("x-remove-snapshot", snapshot=self.item)

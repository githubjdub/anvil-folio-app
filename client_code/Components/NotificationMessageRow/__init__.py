from anvil import *
from ._anvil_designer import NotificationMessageRowTemplate


class NotificationMessageRow(NotificationMessageRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True
    self.classes["is-unread"] = not bool(self.item["read_at"])
    self.set_event_handler("x-set-editable", self.set_editable)

  def set_editable(self, editable=True, **event_args):
    self._editable = editable
    self.read_button.enabled = editable

  def activity_label(self):
    return "%s · %s" % (self.item["symbol"], "Insider buying" if self.item["action"] == "buy" else "Insider selling")

  def trade_details(self):
    shares, price, value = self.item["shares"], self.item["price"], self.item["value"]
    parts = ["{:,.4f} shares".format(shares).replace(".0000 shares", " shares") if shares is not None else "Shares not reported"]
    parts.append("${:,.4f} per share".format(price) if price is not None else "Price not reported")
    parts.append("${:,.2f} reported value".format(value) if value is not None else "Value not reported")
    return " · ".join(parts)

  def date_details(self):
    trade, filed = self.item["transaction_date"], self.item["filed_at"]
    return "Trade %s · Filed %s" % (str(trade or "date not reported"), str(filed or "date not reported"))

  @handle("read_button", "click")
  def read_click(self, **event_args):
    if self._editable:
      self.parent.raise_event("x-message-read", message=self.item, read=not bool(self.item["read_at"]))

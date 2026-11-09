from anvil import *
from ._anvil_designer import PortfolioShareBarTemplate


class PortfolioShareBar(PortfolioShareBarTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.bar.style["height"] = "%s%%" % self.item["height"]
    self.classes["is-latest"] = self.item["latest"]
    self.classes["is-unavailable"] = self.item["shares"] is None
    self.attrs["title"] = "%s · %s shares · %s · %s" % (
      self.item["label_text"], self.item["shares_text"], self.item["change_text"], self.item["date"])

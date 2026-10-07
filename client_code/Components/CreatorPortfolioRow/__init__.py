from ._anvil_designer import CreatorPortfolioRowTemplate
from anvil import *


class CreatorPortfolioRow(CreatorPortfolioRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    setattr(self.import_button, "enabled", self._editable)

  @handle("import_button", "click")
  def import_click(self, **event_args):
    if self._editable:
      self.parent.raise_event("x-import-portfolio", portfolio_id=self.item["portfolio"].get_id())

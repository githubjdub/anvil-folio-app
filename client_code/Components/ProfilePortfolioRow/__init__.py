from ._anvil_designer import ProfilePortfolioRowTemplate
from anvil import *


class ProfilePortfolioRow(ProfilePortfolioRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

  @handle('open_button', 'click')
  def open_click(self, **event_args):
    self.parent.raise_event('x-open-portfolio', portfolio_id=self.item.get_id())

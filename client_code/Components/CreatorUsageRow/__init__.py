from ._anvil_designer import CreatorUsageRowTemplate
from anvil import *


class CreatorUsageRow(CreatorUsageRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.time_bar.style['width'] = '{}%'.format(min(100, max(0, self.item['share'])))

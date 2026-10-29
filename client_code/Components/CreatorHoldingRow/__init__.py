from ._anvil_designer import CreatorHoldingRowTemplate
from anvil import *


class CreatorHoldingRow(CreatorHoldingRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

  def display_cost(self):
    value = self.item['average_cost']
    return '${:,.2f}'.format(value) if value is not None else 'Not entered'

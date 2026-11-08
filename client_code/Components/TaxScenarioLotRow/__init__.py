from anvil import *
from ._anvil_designer import TaxScenarioLotRowTemplate


class TaxScenarioLotRow(TaxScenarioLotRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

  def details(self):
    return '%s → %s · %s · %s shares' % (self.item['acquired_date'], self.item['sale_date'],
      self.item['term'], '{:,.6f}'.format(self.item['shares']).rstrip('0').rstrip('.'))

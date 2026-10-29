from ._anvil_designer import TaxNettingRowTemplate
from ...tax_scenarios import money, value_role


class TaxNettingRow(TaxNettingRowTemplate):
  money = staticmethod(money)
  value_role = staticmethod(value_role)

  def __init__(self, **properties):
    super().__init__(**properties)

  def holding_period(self):
    return "One year or less" if self.item["term"] == "Short-term" else "More than one year"

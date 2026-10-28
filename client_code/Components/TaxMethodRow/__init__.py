from ._anvil_designer import TaxMethodRowTemplate
from ...tax_scenarios import money, value_role


class TaxMethodRow(TaxMethodRowTemplate):
  money = staticmethod(money)
  value_role = staticmethod(value_role)

  def __init__(self, **properties):
    super().__init__(**properties)

  def terms(self):
    return "ST %s · LT %s" % (money(self.item["short_term"], True), money(self.item["long_term"], True)) if self.item["available"] else "Complete purchase prices to compare this method."

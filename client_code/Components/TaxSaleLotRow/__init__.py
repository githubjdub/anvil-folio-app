from ._anvil_designer import TaxSaleLotRowTemplate
from ...tax_scenarios import money, value_role, format_shares


class TaxSaleLotRow(TaxSaleLotRowTemplate):
  money = staticmethod(money)
  value_role = staticmethod(value_role)

  def __init__(self, **properties):
    super().__init__(**properties)

  def details(self):
    return "%s · %s shares · %s" % (self.item["acquired_date"], format_shares(self.item["shares"]), self.item["term"])

  def values(self):
    return "Basis %s · proceeds %s%s%s" % (money(self.item["cost_basis"]), money(self.item["proceeds"]),
      " · estimated basis" if self.item["basis_estimated"] else "",
      " · wash-sale review" if self.item["possible_wash_sale"] else "")

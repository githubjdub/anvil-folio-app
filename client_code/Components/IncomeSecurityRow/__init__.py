from ._anvil_designer import IncomeSecurityRowTemplate


class IncomeSecurityRow(IncomeSecurityRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

  def money(self, value):
    return "Unavailable" if value is None else "${:,.2f}".format(value)

  def yield_text(self):
    value = self.item.get("yield_on_cost")
    return "Unavailable" if value is None else "%.2f%%" % value

  def basis_note(self):
    notes = ["{:,.6f} shares · FIFO basis {}".format(self.item["shares"], self.money(self.item.get("cost_basis")))]
    if self.item.get("basis_estimated"):
      notes.append("Basis includes estimated purchase prices")
    if self.item.get("history_limited"):
      notes.append("Less than 12 months of available history; no annualization")
    return " · ".join(notes)

from ._anvil_designer import TaxTermRowTemplate


class TaxTermRow(TaxTermRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

  def money(self, value):
    if value is None:
      return "Unavailable"
    return "{}${:,.2f}".format("−" if value < 0 else "+" if value > 0 else "", abs(value))

  def value_role(self, value):
    if value is None or value == 0:
      return ["security-value"]
    return ["security-value", "tax-value-positive" if value > 0 else "tax-value-negative"]

  def holding_period(self):
    if self.item.get("term") == "Short-term":
      return "Held one year or less"
    if self.item.get("term") == "Long-term":
      return "Held more than one year"
    return "Holding period unavailable"

  def coverage_note(self):
    notes = []
    open_count = self.item.get("lot_count")
    realized_count = self.item.get("realized_lot_count")
    if open_count is not None:
      notes.append("{} open lot{}".format(open_count, "" if open_count == 1 else "s"))
    if realized_count is not None:
      notes.append("{} realized lot{} this year".format(realized_count, "" if realized_count == 1 else "s"))
    if any(self.item.get(key) is None for key in ("unrealized_gains", "unrealized_losses", "unrealized_net")):
      notes.append("Unrealized totals unavailable: incomplete basis or prices")
    if any(self.item.get(key) is None for key in ("realized_gains", "realized_losses", "realized_net")):
      notes.append("Realized totals unavailable: incomplete purchase or sale data")
    if self.item.get("estimated_basis_count"):
      notes.append("Includes estimated purchase prices")
    return " · ".join(notes)

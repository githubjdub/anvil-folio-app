from ._anvil_designer import TaxPositionRowTemplate


class TaxPositionRow(TaxPositionRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

  def money(self, value, signed=False):
    if value is None:
      return "Unavailable"
    prefix = "−" if value < 0 else "+" if signed and value > 0 else ""
    return "{}${:,.2f}".format(prefix, abs(value))

  def value_role(self, value):
    if value is None or value == 0:
      return "security-value"
    return ["security-value", "tax-value-positive" if value > 0 else "tax-value-negative"]

  def position_details(self):
    shares = self.item.get("shares")
    count = self.item.get("lot_count")
    share_text = "Shares unavailable" if shares is None else "{:,.6f} shares".format(shares)
    lot_text = "Lot count unavailable" if count is None else "{} open lot{}".format(count, "" if count == 1 else "s")
    return "{} · {}".format(share_text, lot_text)

  def review_note(self):
    notes = []
    if self.item.get("price_date"):
      notes.append("Market price as of {}".format(self.item["price_date"]))
    if self.item.get("basis_estimated"):
      notes.append("Basis includes estimated purchase prices")
    if self.item.get("possible_wash_sale"):
      notes.append("Possible wash-sale conflict: review recent purchases and reinvestments before selling")
    return " · ".join(notes)

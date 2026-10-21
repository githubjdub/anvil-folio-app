from ._anvil_designer import DripSecurityRowTemplate


class DripSecurityRow(DripSecurityRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.difference_region.classes["is-negative"] = self.item["negative"]

from ._anvil_designer import DripEventRowTemplate


class DripEventRow(DripEventRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

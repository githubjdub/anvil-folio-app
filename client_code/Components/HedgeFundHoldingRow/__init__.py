from ._anvil_designer import HedgeFundHoldingRowTemplate
from anvil import *


class HedgeFundHoldingRow(HedgeFundHoldingRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

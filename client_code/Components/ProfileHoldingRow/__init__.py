from anvil import *
from ._anvil_designer import ProfileHoldingRowTemplate


class ProfileHoldingRow(ProfileHoldingRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

from ._anvil_designer import RetrospectiveRowTemplate
from anvil import *


class RetrospectiveRow(RetrospectiveRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)


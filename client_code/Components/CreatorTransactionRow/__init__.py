from ._anvil_designer import CreatorTransactionRowTemplate
from anvil import *


class CreatorTransactionRow(CreatorTransactionRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

  def display_shares(self, value):
    text = str(value)
    return text[:-2] if text.endswith(".0") else text

  def display_price(self, value):
    if value is None or value == "":
      return "Market estimate"
    return "$" + self.display_shares(value)

from ._anvil_designer import QuarterReturnRowTemplate
from anvil import *


class QuarterReturnRow(QuarterReturnRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    for component, field in (
      (self.portfolio_label, "portfolio_return"),
      (self.benchmark_label, "benchmark_return"),
      (self.excess_label, "excess_return"),
    ):
      value = self.item.get(field)
      if value is None:
        component.role = "quarter-value-neutral"
      elif value >= 0:
        component.role = "quarter-value-positive"
      else:
        component.role = "quarter-value-negative"

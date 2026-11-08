from anvil import *
from ._anvil_designer import TaxTradeRowTemplate


class TaxTradeRow(TaxTradeRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.action_dropdown.items = ['Buy', 'Sell']
    self.refresh_data_bindings()
    self.set_event_handler('x-set-editable', self.set_editable)

  def set_editable(self, editable=True, **event_args):
    for control in (self.ticker_box, self.action_dropdown, self.date_picker, self.shares_box,
                    self.price_box, self.fee_box, self.remove_button):
      control.enabled = editable

  def payload(self):
    def value(text):
      try:
        return float(text)
      except (ValueError, TypeError):
        return text
    day = self.date_picker.date
    return {'ticker': self.ticker_box.text, 'action': self.action_dropdown.selected_value,
            'trade_date': day.isoformat() if day else None,
            'shares': value(self.shares_box.text), 'trade_price': value(self.price_box.text),
            'fee': value(self.fee_box.text or '0')}

  @handle('remove_button', 'click')
  def remove_click(self, **event_args):
    self.parent.raise_event('x-remove-trade', item=self.item)

  @handle('ticker_box', 'change')
  @handle('action_dropdown', 'change')
  @handle('date_picker', 'change')
  @handle('shares_box', 'change')
  @handle('price_box', 'change')
  @handle('fee_box', 'change')
  def trade_change(self, **event_args):
    self.parent.raise_event('x-trade-changed')

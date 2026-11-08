from datetime import date
import anvil.server
import anvil.users
from anvil import *
from ._anvil_designer import TaxEstimatorTemplate
from ..TaxTradeRow import TaxTradeRow


class TaxEstimator(TaxEstimatorTemplate):
  PAGE_SIZE = 20

  def __init__(self, **properties):
    super().__init__(**properties)
    self._catalog = None
    self._busy = False
    self._parent_editable = True
    self._generation = 0
    self._fingerprint = None
    self._rows = []
    self._result = None
    self._transactions = []
    self._as_of = None
    self._page = 0
    self.set_event_handler('x-set-editable', self.set_editable)
    self.reset_workspace()

  def reset_workspace(self):
    self._generation += 1
    self._transactions = []
    self._as_of = None
    self._fingerprint = None
    self._rows = []
    self.trades_panel.items = []
    self.ordinary_box.text = ''
    self.magi_box.text = ''
    self.investment_box.text = '0'
    self.qualified_box.text = '0'
    self.other_short_box.text = '0'
    self.other_long_box.text = '0'
    self.wash_checkbox.checked = False
    self.state_short_box.text = '0'
    self.state_long_box.text = '0'
    self.state_note.text = 'Federal only: state and local taxes are excluded.'
    self.state_source_link.visible = False
    if self._catalog:
      self.filing_dropdown.selected_value = 'single'
      self.state_dropdown.selected_value = 'federal'
    self._invalidate()
    self._status('Add hypothetical buys and sells, or sync a portfolio to use its existing lots.')
    self.context_label.text = 'Empty starting portfolio · USD · taxable accounts'
    self._controls()

  def set_portfolio_context(self, transactions, as_of=None):
    fingerprint = (str(as_of), str(transactions))
    if fingerprint == self._fingerprint:
      return
    self._generation += 1
    self._fingerprint = fingerprint
    self._transactions = [dict(row) for row in transactions or []]
    self._as_of = date.fromisoformat(as_of) if as_of else None
    self.context_label.text = ('Selected portfolio snapshot · %s · %s logged trades · USD' % (as_of, len(transactions))
                               if transactions else 'Empty starting portfolio · USD · taxable accounts')
    self._invalidate()

  def redraw(self):
    if self._catalog is None:
      generation = self._generation
      catalog = anvil.server.call('get_tax_estimator_catalog')
      if generation != self._generation:
        return
      self._catalog = catalog
      self.filing_dropdown.items = catalog['filing_statuses']
      self.filing_dropdown.selected_value = 'single'
      self.state_dropdown.items = catalog['states']
      self.state_dropdown.selected_value = 'federal'
      self.location_change()
    self._controls()

  def apply_appearance(self):
    pass

  def set_editable(self, editable=True, **event_args):
    self._parent_editable = editable
    self._controls()

  def _controls(self):
    enabled = self._parent_editable and not self._busy
    for control in (self.add_button, self.filing_dropdown, self.state_dropdown, self.ordinary_box,
                    self.magi_box, self.investment_box, self.qualified_box, self.other_short_box, self.other_long_box, self.wash_checkbox):
      control.enabled = enabled
    self.estimate_button.enabled = enabled and bool(self._rows) and self._catalog is not None
    self.estimate_button.text = 'Estimating…' if self._busy else 'Estimate taxes'
    self.state_short_box.enabled = enabled and self.state_dropdown.selected_value != 'federal'
    self.state_long_box.enabled = self.state_short_box.enabled
    self.previous_button.enabled = enabled and self._page > 0
    count = len(self._result['realized']) if self._result else 0
    self.next_button.enabled = enabled and (self._page + 1) * self.PAGE_SIZE < count
    for row in self.trades_panel.get_components():
      row.raise_event('x-set-editable', editable=enabled)

  def _invalidate(self, **event_args):
    self._result = None
    self.results_region.visible = False
    self.lots_panel.items = []
    self._page = 0

  def _status(self, text):
    self.status_label.text = text

  def _commit_rows(self):
    for form in self.trades_panel.get_components():
      if not isinstance(form, TaxTradeRow):
        continue
      payload = form.payload()
      form.item.update(ticker=payload['ticker'], action=payload['action'],
        date=form.date_picker.date, shares=form.shares_box.text,
        trade_price=form.price_box.text, fee=form.fee_box.text)

  @handle('add_button', 'click')
  def add_click(self, **event_args):
    if self._busy or len(self._rows) >= 100:
      return
    self._commit_rows()
    self._rows.append({'ticker': '', 'action': 'Sell' if self._transactions else 'Buy',
      'date': max(date.today(), self._as_of) if self._as_of else date.today(),
      'shares': '', 'trade_price': '', 'fee': '0'})
    self.trades_panel.items = self._rows
    self._invalidate()
    self._controls()

  @handle('trades_panel', 'x-remove-trade')
  def remove_trade(self, item, **event_args):
    if self._busy:
      return
    self._commit_rows()
    self._rows = [row for row in self._rows if row is not item]
    self.trades_panel.items = self._rows
    self._invalidate()
    self._controls()

  @handle('trades_panel', 'x-trade-changed')
  @handle('filing_dropdown', 'change')
  @handle('ordinary_box', 'change')
  @handle('magi_box', 'change')
  @handle('investment_box', 'change')
  @handle('qualified_box', 'change')
  @handle('other_short_box', 'change')
  @handle('other_long_box', 'change')
  @handle('state_short_box', 'change')
  @handle('state_long_box', 'change')
  @handle('wash_checkbox', 'change')
  def inputs_change(self, **event_args):
    self._invalidate()

  @handle('state_dropdown', 'change')
  def location_change(self, **event_args):
    state = self.state_dropdown.selected_value
    preset = self._catalog['presets'].get(state) if self._catalog else None
    self.state_short_box.text = str(preset[0]) if preset else ('0' if state == 'federal' else '')
    self.state_long_box.text = self.state_short_box.text
    self.state_note.text = ('Federal only: state and local taxes are excluded.' if state == 'federal' else
      'A state flat-rate preset is filled in. Adjust both rates for your situation and local taxes; this is a marginal-rate estimate.' if preset else
      'Enter your combined state/local marginal rates. Automatic brackets are not available for this location. Washington requires its own annual capital-gains calculation; use Federal only for a federal estimate.')
    self.state_source_link.visible = preset is not None
    if preset:
      self.state_source_link.url = preset[1]
    self._invalidate()
    self._controls()

  @handle('estimate_button', 'click')
  def estimate_click(self, **event_args):
    if self._busy or not self._rows:
      return
    def value(text):
      try:
        return float(text)
      except (ValueError, TypeError):
        return text
    settings = {'tax_year': 2026, 'filing_status': self.filing_dropdown.selected_value,
      'state': self.state_dropdown.selected_value, 'ordinary_income': value(self.ordinary_box.text),
      'magi': value(self.magi_box.text), 'other_investment_income': value(self.investment_box.text),
      'qualified_dividends': value(self.qualified_box.text),
      'other_short': value(self.other_short_box.text), 'other_long': value(self.other_long_box.text),
      'state_short_rate': value(self.state_short_box.text), 'state_long_rate': value(self.state_long_box.text),
      'wash_sale_reviewed': self.wash_checkbox.checked}
    rows = [form.payload() for form in self.trades_panel.get_components() if isinstance(form, TaxTradeRow)]
    generation = self._generation
    user = anvil.users.get_user()
    account_id = user.get_id() if user else None
    self._invalidate()
    self._busy = True
    self.raise_event('x-tax-estimator-busy', busy=True)
    self._controls()
    try:
      result = anvil.server.call('estimate_trade_taxes', self._transactions, self._as_of or date.today(), rows, settings)
      current = anvil.users.get_user()
      if generation != self._generation or (current.get_id() if current else None) != account_id:
        return
      if not result['ok']:
        self._status(result['message'])
        return
      self._result = result
      self._render_result()
    finally:
      self._busy = False
      self.raise_event('x-tax-estimator-busy', busy=False)
      self._controls()

  def _render_result(self):
    result = self._result
    if result is None:
      return
    self.results_region.visible = True
    self.tax_breakdown.visible = result['tax'] is not None
    def money(value):
      return '${:,.2f}'.format(value) if value is not None else 'Review required'
    self.short_label.text = money(result['short_gain'])
    self.long_label.text = money(result['long_gain'])
    self.cash_label.text = money(result['after_tax_cash'])
    self.proceeds_label.text = 'Sale proceeds %s · purchase cost %s · before-tax cash change %s' % (
      money(result['sale_proceeds']), money(result['purchase_cost']), money(result['cash_change']))
    self.tax_total_label.text = money(result['tax']['change']['total'] if result['tax'] else None)
    self.remaining_label.text = 'Remaining shares: ' + (', '.join('%s %s' % (p['symbol'], '{:,.6f}'.format(p['shares']).rstrip('0').rstrip('.')) for p in result['remaining_holdings']) or 'None')
    if result['tax']:
      tax = result['tax']
      self.federal_label.text = money(tax['change']['federal'])
      self.niit_label.text = money(tax['change']['niit'])
      self.state_label.text = money(tax['change']['state'])
      self.loss_label.text = 'After scenario: federal capital-loss deduction %s · unused loss %s (carryforward estimate)' % (
        money(tax['after']['capital_loss_deduction']), money(tax['after']['loss_carryforward']))
    notes = 'Estimate updated. Negative tax changes represent estimated savings. No trades or portfolio changes were saved.'
    if result['requires_wash_review']:
      notes = 'Potential wash-sale losses detected. Tax totals are withheld. Review substantially identical securities, other accounts and replacement-lot basis; confirm below only if the entered basis and loss deductibility are correct, then estimate again.'
    elif result['wash_sale_flag']:
      notes += ' Potential wash-sale losses included using your confirmation; no automatic wash-sale basis adjustment is performed.'
    if result['estimated_prices']:
      notes += ' Some historical basis/proceeds use estimated prices. Check them against your broker.'
    self._status(notes)
    self._render_lots()

  def _render_lots(self):
    rows = self._result['realized'] if self._result else []
    start = self._page * self.PAGE_SIZE
    self.lots_panel.items = rows[start:start + self.PAGE_SIZE]
    self.lot_page_label.text = '%s–%s of %s realized lots' % (start + 1 if rows else 0, min(start + self.PAGE_SIZE, len(rows)), len(rows))
    self._controls()

  @handle('previous_button', 'click')
  def previous_click(self, **event_args):
    if not self._busy and self._page > 0:
      self._page -= 1
      self._render_lots()

  @handle('next_button', 'click')
  def next_click(self, **event_args):
    if not self._busy and self._result and (self._page + 1) * self.PAGE_SIZE < len(self._result['realized']):
      self._page += 1
      self._render_lots()

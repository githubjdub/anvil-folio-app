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
    self._portfolio_id = None
    self._context_account_id = None
    self._analysis = None
    self._context = None
    self._synced_context_rendered = False
    self._tax_year = 2026
    self._loaded_scope = None
    self._inputs_dirty = False
    self._year_drafts = {}
    self._positions = {}
    self._auto_estimate_key = None
    self.set_event_handler('x-set-editable', self.set_editable)
    self.reset_workspace()

  def reset_workspace(self):
    self._generation += 1
    self._transactions = []
    self._as_of = None
    self._fingerprint = None
    self._portfolio_id = None
    user = anvil.users.get_user()
    self._context_account_id = user.get_id() if user else None
    self._analysis = None
    self._context = None
    self._synced_context_rendered = False
    self._loaded_scope = None
    self._inputs_dirty = False
    self._year_drafts = {}
    self._positions = {}
    self._auto_estimate_key = None
    self._tax_year = 2026
    self._rows = []
    self.trades_panel.items = []
    self.ordinary_box.text = ''
    self.magi_box.text = ''
    self.income_box.text = ''
    self.detailed_checkbox.checked = False
    self.scenario_dropdown.items = [('This year’s portfolio activity', 'recorded'), ('Sell all current holdings', 'liquidate')]
    self.scenario_dropdown.selected_value = 'recorded'
    self.investment_box.text = '0'
    self.qualified_box.text = '0'
    self.other_short_box.text = '0'
    self.other_long_box.text = '0'
    self.wash_checkbox.checked = False
    self.dividend_checkbox.checked = True
    self.portfolio_qualified_box.text = '0'
    self.state_short_box.text = '0'
    self.state_long_box.text = '0'
    self.state_note.text = 'Federal only: state and local taxes are excluded.'
    self.state_source_link.visible = False
    if self._catalog:
      self.filing_dropdown.selected_value = 'single'
      self.state_dropdown.selected_value = 'federal'
      self._set_year_choices()
    self._invalidate()
    self._status('Save and sync a portfolio to get an estimate automatically.')
    self.context_label.text = 'Your synced holdings, sales, and dividends fill in automatically.'
    self.synced_summary_label.text = 'No synced portfolio yet.'
    self.dividend_summary_label.text = ''
    self.year_label.text = 'US · 2026 · USD'
    self.holding_dropdown.items = []
    self._controls()

  def set_portfolio_context(self, transactions, as_of=None, portfolio_id=None, analysis=None):
    user = anvil.users.get_user()
    account_id = user.get_id() if user else None
    if (self._context_account_id is not None and account_id != self._context_account_id) or (self._portfolio_id is not None and portfolio_id != self._portfolio_id):
      self.reset_workspace()
    self._context_account_id = account_id
    fingerprint = (str(as_of), str(transactions), portfolio_id)
    if fingerprint == self._fingerprint and analysis is self._analysis:
      return
    self._generation += 1
    self._fingerprint = fingerprint
    self._portfolio_id = portfolio_id
    self._analysis = analysis
    self._context = analysis.get('planning_context') if analysis else None
    self._synced_context_rendered = False
    self._transactions = [dict(row) for row in transactions or []]
    self._as_of = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    year = self._as_of.year if self._as_of else date.today().year
    if year != self._tax_year:
      if self._catalog:
        self._year_drafts[self._tax_year] = self._input_document()
      self._tax_year = year
      self._loaded_scope = None
      self._inputs_dirty = year in self._year_drafts
      if self._catalog:
        self._set_year_choices()
        self._restore_document(self._year_drafts.get(year, {}))
    self.context_label.text = ('Portfolio synced %s · %s recorded trades · USD' % (as_of, len(transactions or []))
                               if transactions else 'Sync a portfolio to load its lots and recorded sales automatically.')
    self._render_synced_context()
    self._invalidate()

  def _set_year_choices(self):
    catalog = self._catalog
    if catalog is None:
      return
    years = list(catalog['tax_years'])
    if self._tax_year not in years:
      years.append(self._tax_year)
    self.year_dropdown.items = [(str(year) + (' · unavailable' if year not in catalog['tax_years'] else ''), year) for year in sorted(years, reverse=True)]
    self.year_dropdown.selected_value = self._tax_year
    self.year_label.text = 'US · %s · USD' % self._tax_year

  def _year_report(self):
    if not self._context:
      return None
    return self._context.get('tax_by_year', {}).get(str(self._tax_year))

  def _render_synced_context(self):
    if self._synced_context_rendered:
      return
    self._synced_context_rendered = True
    report = self._year_report()
    self._positions = report['positions'] if report else {}
    self.holding_dropdown.items = [('%s · %s shares · %s' % (symbol, '{:,.6f}'.format(position['shares']).rstrip('0').rstrip('.'), self._money(position['current_price'])), symbol)
      for symbol, position in sorted(self._positions.items()) if position['shares'] > 0]
    if self.holding_dropdown.items:
      self.holding_dropdown.selected_value = self.holding_dropdown.items[0][1]
    if report:
      summary = report['summary']
      value = sum(position['current_value'] for position in self._positions.values() if position['current_value'] is not None)
      complete = all(position['current_value'] is not None for position in self._positions.values())
      self.synced_summary_label.text = '%s portfolio value · %s holdings · %s recorded gains / losses in %s' % (
        self._money(value) if complete else 'Price needed for full',
        sum(position['shares'] > 0 for position in self._positions.values()),
        self._money(summary['realized_short_term'] + summary['realized_long_term']) if summary['realized_short_term'] is not None and summary['realized_long_term'] is not None else 'Unknown', self._tax_year)
      dividends = (self._context or {}).get('earned_dividends_by_year', {}).get(str(self._tax_year), 0)
      self.dividend_summary_label.text = 'Synced accrued dividends: %s. The quick estimate assumes payment in this tax year and treats them as ordinary dividends. Adjust below using your broker records.' % self._money(dividends)
    elif self._analysis:
      self.synced_summary_label.text = 'Sync prices once to prepare tax lots for this portfolio and year. This tab uses your saved sync without fetching prices.'
      self.dividend_summary_label.text = ''
    else:
      self.synced_summary_label.text = 'Sync your portfolio to load tax lots and recorded gains automatically.'
      self.dividend_summary_label.text = ''

  @staticmethod
  def _money(value):
    return '${:,.2f}'.format(value) if value is not None else 'Unknown — check broker basis'

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
      self._set_year_choices()
      self.location_change()
      self._inputs_dirty = False
    self._load_inputs()
    self._render_synced_context()
    self._controls()
    self._auto_estimate()

  def apply_appearance(self):
    pass

  def set_editable(self, editable=True, **event_args):
    self._parent_editable = editable
    self._controls()

  def _controls(self):
    enabled = self._parent_editable and not self._busy
    for control in (self.add_button, self.year_dropdown, self.holding_dropdown, self.filing_dropdown, self.state_dropdown, self.ordinary_box,
                    self.income_box, self.detailed_checkbox, self.scenario_dropdown,
                    self.magi_box, self.investment_box, self.qualified_box, self.other_short_box, self.other_long_box, self.wash_checkbox, self.dividend_checkbox, self.portfolio_qualified_box):
      control.enabled = enabled
    supported = self._catalog is not None and self._tax_year in self._catalog['tax_years']
    self.estimate_button.enabled = enabled and supported and bool(self._portfolio_id) and self._context is not None
    custom = self.scenario_dropdown.selected_value != 'liquidate'
    self.add_button.enabled = enabled and custom and len(self._rows) < 100
    self.holding_dropdown.enabled = enabled and custom
    self.sell_holding_button.enabled = enabled and custom and bool(self.holding_dropdown.selected_value) and len(self._rows) < 100
    self.save_inputs_button.enabled = enabled and supported and bool(self._portfolio_id)
    self.estimate_button.text = 'Estimating…' if self._busy else 'Update estimate'
    self.income_box.enabled = enabled and not self.detailed_checkbox.checked
    self.detailed_income_region.visible = self.detailed_checkbox.checked
    self.state_rates_region.visible = self.state_dropdown.selected_value != 'federal'
    self.state_short_box.enabled = enabled and self.state_dropdown.selected_value != 'federal'
    self.state_long_box.enabled = self.state_short_box.enabled
    self.previous_button.enabled = enabled and self._page > 0
    count = len(self._result['logged_realized']) + len(self._result['realized']) if self._result else 0
    self.next_button.enabled = enabled and (self._page + 1) * self.PAGE_SIZE < count
    for row in self.trades_panel.get_components():
      row.raise_event('x-set-editable', editable=enabled and custom)
    self._render_assumptions()

  def _render_assumptions(self):
    if self.detailed_checkbox.checked:
      income = 'Using your detailed income and outside-investment inputs'
    else:
      amount = self.income_box.text or '0'
      income = 'Other annual income $%s · standard deduction · outside investments $0' % amount
    state = 'federal tax only' if self.state_dropdown.selected_value in (None, 'federal') else 'includes your state/local rate estimate'
    dividends = 'accrued dividends included as ordinary unless adjusted below' if self.dividend_checkbox.checked else 'portfolio dividends excluded'
    scenario = 'Unrealized gains are excluded.' if self.scenario_dropdown.selected_value != 'liquidate' else 'Assumes all holdings sell at synced prices; individual trade drafts are paused.'
    self.assumptions_label.text = '%s · %s · %s. %s' % (income, state, dividends, scenario)

  def _auto_estimate(self):
    if self._result is not None or self._busy or not self._parent_editable or not self._portfolio_id or self._context is None or not self._catalog or self._tax_year not in self._catalog['tax_years']:
      return
    key = (self._generation, str(self._input_document()))
    if self._result is None and key != self._auto_estimate_key:
      self.estimate_click()
      self._auto_estimate_key = key

  def _invalidate(self, **event_args):
    self._result = None
    self._auto_estimate_key = None
    self.results_region.visible = False
    self.lots_panel.items = []
    self._page = 0
    self.wash_review_region.visible = False

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
      'date': self._scenario_date(),
      'shares': '', 'trade_price': '', 'fee': '0'})
    self.trades_panel.items = self._rows
    self._inputs_dirty = True
    self._invalidate()
    self._controls()

  def _scenario_date(self):
    snapshot = min(self._as_of or date.today(), date(self._tax_year, 12, 31))
    return max(date(self._tax_year, 1, 1), min(date.today(), date(self._tax_year, 12, 31)), snapshot)

  @handle('sell_holding_button', 'click')
  def sell_holding_click(self, **event_args):
    symbol = self.holding_dropdown.selected_value
    position = self._positions.get(symbol)
    if self._busy or not position or len(self._rows) >= 100:
      return
    self._commit_rows()
    self._rows.append({'ticker': symbol, 'action': 'Sell', 'date': self._scenario_date(),
      'shares': str(position['shares']), 'trade_price': str(position['current_price'] or ''), 'fee': '0'})
    self.trades_panel.items = self._rows
    self._inputs_dirty = True
    self._invalidate()
    self._controls()

  @handle('trades_panel', 'x-remove-trade')
  def remove_trade(self, item, **event_args):
    if self._busy:
      return
    self._commit_rows()
    self._rows = [row for row in self._rows if row is not item]
    self.trades_panel.items = self._rows
    self._inputs_dirty = True
    self._invalidate()
    self._controls()

  @handle('trades_panel', 'x-trade-changed')
  @handle('ordinary_box', 'change')
  @handle('magi_box', 'change')
  @handle('investment_box', 'change')
  @handle('qualified_box', 'change')
  @handle('other_short_box', 'change')
  @handle('other_long_box', 'change')
  @handle('state_short_box', 'change')
  @handle('state_long_box', 'change')
  @handle('wash_checkbox', 'change')
  @handle('dividend_checkbox', 'change')
  @handle('portfolio_qualified_box', 'change')
  def inputs_change(self, **event_args):
    self._inputs_dirty = True
    self._invalidate()
    self._status('Preferences changed. Update the estimate to refresh your results.')
    self._controls()

  @handle('filing_dropdown', 'change')
  @handle('income_box', 'change')
  @handle('scenario_dropdown', 'change')
  def quick_inputs_change(self, **event_args):
    self.inputs_change()
    self._auto_estimate()

  @handle('detailed_checkbox', 'change')
  def detailed_change(self, **event_args):
    if self.detailed_checkbox.checked and self._catalog:
      try:
        income = float(str(self.income_box.text or '0').replace(',', '').replace('$', '').strip())
      except (ValueError, TypeError):
        self._status('Enter a number for annual income before switching to detailed inputs.')
        self.detailed_checkbox.checked = False
        self._controls()
        return
      deduction = self._catalog['standard_deductions'].get(str(self._tax_year), {}).get(self.filing_dropdown.selected_value, 0)
      if not self.ordinary_box.text:
        self.ordinary_box.text = str(income - deduction)
      if not self.magi_box.text:
        self.magi_box.text = str(income)
    self.inputs_change()

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
    self._inputs_dirty = True
    self._invalidate()
    self._controls()

  def _account_scope(self):
    user = anvil.users.get_user()
    return (user.get_id() if user else None, self._portfolio_id, self._tax_year)

  def _input_document(self):
    self._commit_rows()
    mapping = {'ordinary_income': 'ordinary_box', 'magi': 'magi_box', 'other_investment_income': 'investment_box',
      'qualified_dividends': 'qualified_box', 'other_short': 'other_short_box', 'other_long': 'other_long_box',
      'state_short_rate': 'state_short_box', 'state_long_rate': 'state_long_box', 'portfolio_qualified': 'portfolio_qualified_box'}
    document = {'filing_status': self.filing_dropdown.selected_value or 'single', 'state': self.state_dropdown.selected_value or 'federal',
      'input_mode': 'detailed' if self.detailed_checkbox.checked else 'quick',
      'annual_income': str(self.income_box.text or ''), 'scenario_mode': self.scenario_dropdown.selected_value or 'recorded',
      'wash_sale_reviewed': self.wash_checkbox.checked, 'include_portfolio_dividends': self.dividend_checkbox.checked,
      'scenario_trades': [form.payload() for form in self.trades_panel.get_components() if isinstance(form, TaxTradeRow)]}
    for key, name in mapping.items():
      document[key] = str(getattr(self, name).text or '')
    return document

  def _restore_document(self, document):
    was_dirty = self._inputs_dirty
    mapping = {'ordinary_income': 'ordinary_box', 'magi': 'magi_box', 'other_investment_income': 'investment_box',
      'qualified_dividends': 'qualified_box', 'other_short': 'other_short_box', 'other_long': 'other_long_box',
      'state_short_rate': 'state_short_box', 'state_long_rate': 'state_long_box', 'portfolio_qualified': 'portfolio_qualified_box'}
    self.filing_dropdown.selected_value = document.get('filing_status', 'single')
    self.income_box.text = document.get('annual_income', '')
    self.detailed_checkbox.checked = document.get('input_mode', 'detailed' if document else 'quick') == 'detailed'
    self.scenario_dropdown.selected_value = document.get('scenario_mode', 'recorded')
    self.state_dropdown.selected_value = document.get('state', 'federal')
    self.location_change()
    for key, name in mapping.items():
      getattr(self, name).text = str(document.get(key, '' if key in ('ordinary_income', 'magi') else '0'))
    self.wash_checkbox.checked = document.get('wash_sale_reviewed', False)
    self.dividend_checkbox.checked = document.get('include_portfolio_dividends', True)
    self._rows = []
    for row in document.get('scenario_trades', []):
      restored = dict(row)
      restored['date'] = date.fromisoformat(row['trade_date']) if row.get('trade_date') else None
      self._rows.append(restored)
    self.trades_panel.items = self._rows
    self._invalidate()
    self._inputs_dirty = was_dirty
    self._controls()

  def _load_inputs(self):
    scope = self._account_scope()
    catalog = self._catalog
    if catalog is None or not scope[0] or not scope[1] or scope == self._loaded_scope or self._tax_year not in catalog['tax_years']:
      return
    generation = self._generation
    result = anvil.server.call('get_planning_inputs', self._portfolio_id, 'tax', self._tax_year)
    if generation != self._generation or scope != self._account_scope():
      return
    self._loaded_scope = scope
    if not result['ok']:
      self._status(result['message'])
      return
    if not self._inputs_dirty and result['plan'] is not None:
      self._restore_document(result['plan']['inputs'] or {})
      self._status('Saved %s tax inputs and hypothetical trades restored. Recorded portfolio sales come from the latest sync.' % self._tax_year)
    elif result['plan'] is None:
      self._status('Your portfolio is ready. Add an approximate annual income to refine the tax bracket, or use the defaults shown.')

  @handle('year_dropdown', 'change')
  def year_change(self, **event_args):
    year = self.year_dropdown.selected_value
    if year is None or year == self._tax_year or self._busy:
      return
    self._year_drafts[self._tax_year] = self._input_document()
    self._tax_year = year
    self._synced_context_rendered = False
    self._generation += 1
    self._loaded_scope = None
    self._inputs_dirty = year in self._year_drafts
    self._restore_document(self._year_drafts.get(year, {}))
    self.year_label.text = 'US · %s · USD' % year
    self._load_inputs()
    self._render_synced_context()
    self._controls()
    self._auto_estimate()

  @handle('save_inputs_button', 'click')
  def save_inputs_click(self, **event_args):
    if self._busy or not self._portfolio_id:
      return
    scope, generation = self._account_scope(), self._generation
    document = self._input_document()
    self._busy = True
    self._controls()
    try:
      result = anvil.server.call('save_planning_inputs', self._portfolio_id, 'tax', document, self._tax_year)
      if scope != self._account_scope() or generation != self._generation:
        return
      if result['ok']:
        self._inputs_dirty = False
        self._status('%s tax inputs and hypothetical trades saved to this portfolio.' % self._tax_year)
      else:
        self._status(result['message'])
    finally:
      self._busy = False
      self._controls()

  @handle('estimate_button', 'click')
  def estimate_click(self, **event_args):
    if self._busy:
      return
    def value(text):
      try:
        return float(str(text).replace(',', '').replace('$', '').strip())
      except (ValueError, TypeError):
        return text
    settings = {'tax_year': self._tax_year, 'filing_status': self.filing_dropdown.selected_value,
      'input_mode': 'detailed' if self.detailed_checkbox.checked else 'quick',
      'annual_income': value(self.income_box.text or '0'), 'scenario_mode': self.scenario_dropdown.selected_value,
      'state': self.state_dropdown.selected_value, 'ordinary_income': value(self.ordinary_box.text),
      'magi': value(self.magi_box.text), 'other_investment_income': value(self.investment_box.text),
      'qualified_dividends': value(self.qualified_box.text),
      'other_short': value(self.other_short_box.text), 'other_long': value(self.other_long_box.text),
      'state_short_rate': value(self.state_short_box.text), 'state_long_rate': value(self.state_long_box.text),
      'wash_sale_reviewed': self.wash_checkbox.checked,
      'include_portfolio_dividends': self.dividend_checkbox.checked,
      'portfolio_qualified': value(self.portfolio_qualified_box.text)}
    rows = [form.payload() for form in self.trades_panel.get_components() if isinstance(form, TaxTradeRow)]
    document = self._input_document()
    if settings['scenario_mode'] == 'liquidate':
      rows = []
    generation = self._generation
    user = anvil.users.get_user()
    account_id = user.get_id() if user else None
    self._invalidate()
    self._busy = True
    self.raise_event('x-tax-estimator-busy', busy=True)
    self._controls()
    try:
      if self._portfolio_id:
        result = anvil.server.call('estimate_saved_trade_taxes', self._portfolio_id, rows, settings)
      else:
        self._status('Save this portfolio once so the estimator can reuse its synced tax lots securely, then sync prices.')
        return
      current = anvil.users.get_user()
      if generation != self._generation or (current.get_id() if current else None) != account_id:
        return
      if not result['ok']:
        self._status(result['message'])
        return
      self._result = result
      self._render_result()
      if self._inputs_dirty:
        saved = anvil.server.call('save_planning_inputs', self._portfolio_id, 'tax', document, self._tax_year)
        current = anvil.users.get_user()
        if generation != self._generation or (current.get_id() if current else None) != account_id:
          return
        if saved['ok']:
          self._inputs_dirty = False
        else:
          self._status('Estimate ready. Preferences were not saved: ' + saved['message'])
    finally:
      self._busy = False
      self.raise_event('x-tax-estimator-busy', busy=False)
      self._controls()

  def _render_result(self):
    result = self._result
    if result is None:
      return
    self.results_region.visible = True
    self.tax_breakdown.visible = result['portfolio_tax'] is not None
    self.wash_review_region.visible = result['wash_sale_flag']
    has_scenario = result['scenario_mode'] == 'liquidate' or bool(self._rows)
    self.scenario_result_region.visible = has_scenario
    def money(value):
      return '${:,.2f}'.format(value) if value is not None else 'Review required'
    self.short_label.text = money(result['base_short'] + result['short_gain'])
    self.long_label.text = money(result['base_long'] + result['long_gain'])
    self.cash_caption.text = ('After-tax sale proceeds' if result['scenario_mode'] == 'liquidate' else
      'After-tax cash from hypothetical trades' if has_scenario else 'Portfolio income after tax')
    self.cash_label.text = money(result['after_tax_cash'] if has_scenario else result['portfolio_after_tax_income'])
    self.proceeds_label.text = 'Sale proceeds %s · purchase cost %s · before-tax cash change %s' % (
      money(result['sale_proceeds']), money(result['purchase_cost']), money(result['cash_change']))
    self.tax_total_label.text = money(result['tax']['change']['total'] if result['tax'] else None)
    self.annual_tax_label.text = money(result['portfolio_tax']['total'] if result['portfolio_tax'] else None)
    self.logged_gains_label.text = '%s · %s recorded sale lots · portfolio dividends included %s. %s' % (
      self._tax_year, len(result['logged_realized']), money(result.get('portfolio_dividends', 0)),
      'Includes a hypothetical sale of every open holding at its synced price.' if result['scenario_mode'] == 'liquidate' else 'Includes recorded activity and any individual hypothetical trades below.')
    self.remaining_label.text = 'Remaining shares: ' + (', '.join('%s %s' % (p['symbol'], '{:,.6f}'.format(p['shares']).rstrip('0').rstrip('.')) for p in result['remaining_holdings']) or 'None')
    if result['tax']:
      tax = result['tax']
      self.federal_label.text = money(result['portfolio_tax']['federal'])
      self.niit_label.text = money(result['portfolio_tax']['niit'])
      self.state_label.text = money(result['portfolio_tax']['state'])
      self.loss_label.text = 'After scenario: federal capital-loss deduction %s · unused loss %s (carryforward estimate)' % (
        money(tax['after']['capital_loss_deduction']), money(tax['after']['loss_carryforward']))
    notes = 'Portfolio estimate updated for %s. Income is used to estimate your tax bracket; the headline shows tax attributable to this portfolio. Changed preferences are saved automatically.' % self._tax_year
    if result['requires_wash_review']:
      notes = 'Potential wash-sale losses detected. Review the notice below and confirm the loss deductibility and basis before updating the estimate. Tax totals are withheld until reviewed.'
    elif result['wash_sale_flag']:
      notes += ' Potential wash-sale losses included using your confirmation; no automatic wash-sale basis adjustment is performed.'
    if result['estimated_prices']:
      notes += ' Some historical basis/proceeds use estimated prices. Check them against your broker.'
    self._status(notes)
    self._render_lots()

  def _render_lots(self):
    rows = self._result['logged_realized'] + self._result['realized'] if self._result else []
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
    if not self._busy and self._result and (self._page + 1) * self.PAGE_SIZE < len(self._result['logged_realized']) + len(self._result['realized']):
      self._page += 1
      self._render_lots()

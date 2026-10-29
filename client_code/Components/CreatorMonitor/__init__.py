from ._anvil_designer import CreatorMonitorTemplate
from anvil import *
import anvil.server


class CreatorMonitor(CreatorMonitorTemplate):
  def __init__(self, **properties):
    self._initializing = True
    super().__init__(**properties)
    self._enabled = False
    self._editable = True
    self._generation = 0
    self._loading = False
    self._include_own = False
    self._loaded = False
    self._holdings = None
    self.range_dropdown.items = [('Past 7 days', 7), ('Past 30 days', 30), ('Past 90 days', 90)]
    self.range_dropdown.selected_value = 30
    self.rank_dropdown.items = [('Unique users', 'users'), ('Market value', 'value')]
    self.rank_dropdown.selected_value = 'users'
    self._reset()
    self._initializing = False

  def _reset(self):
    self._loaded = False
    self._holdings = None
    self.usage_rows.items = []
    self.account_rows.items = []
    self.holdings_rows.items = []
    self.accounts_label.text = self.minutes_label.text = self.today_label.text = self.top_label.text = '—'
    self.usage_empty_label.text = 'Open Monitor to load usage. New activity appears after it is recorded.'
    self.usage_empty_label.visible = True
    self.accounts_empty_label.visible = True
    self.holdings_note.text = 'Load the latest saved portfolio per account to compare ownership. Prices are refreshed on request.'
    self.holdings_empty_label.visible = False
    self.updated_label.text = ''
    self.status_label.text = ''

  def configure_access(self, enabled, email=None):
    self._generation += 1
    self._enabled = bool(enabled)
    self._loading = False
    self._reset()
    self._controls()

  def set_scope(self, include_own):
    if bool(include_own) != self._include_own:
      self._generation += 1
      self._include_own = bool(include_own)
      self._loading = False
      self._reset()
      self._controls()

  @handle('', 'x-set-editable')
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    self._controls()

  def _controls(self):
    enabled = self._enabled and self._editable and not self._loading
    for component in (self.range_dropdown, self.refresh_usage_button, self.refresh_holdings_button, self.rank_dropdown):
      component.enabled = enabled
    self.account_rows.raise_event_on_children('x-set-editable', editable=enabled)

  def open_dashboard(self):
    if not self._loaded:
      self.refresh()

  def refresh(self, **event_args):
    if not self._enabled or not self._editable or self._loading:
      return
    self._loading = True
    generation = self._generation
    self._controls()
    self.status_label.text = 'Loading account and tab activity…'
    try:
      result = anvil.server.call('get_creator_monitoring', self.range_dropdown.selected_value, self._include_own)
      if generation != self._generation or not self._enabled:
        return
      if not result['ok']:
        self.status_label.text = result['message']
        return
      self._loaded = True
      self.usage_rows.items = result['tabs']
      self.account_rows.items = result['accounts']
      self.usage_empty_label.visible = not result['tabs']
      self.accounts_empty_label.visible = not result['accounts']
      self.accounts_label.text = str(result['account_count'])
      self.minutes_label.text = '{:,.1f}'.format(result['seconds'] / 60)
      self.today_label.text = str(result['active_today'])
      timed = [item for item in result['tabs'] if item['seconds'] > 0]
      self.top_label.text = timed[0]['label'] if timed else 'Not timed yet'
      self.updated_label.text = 'Updated ' + result['updated_at'].strftime('%b %d, %H:%M UTC')
      self.status_label.text = '{} browsing visits · {} anonymous / unlinked visits · latest 20 active accounts shown'.format(result['visits'], result['guest_visits'])
    finally:
      if generation == self._generation:
        self._loading = False
        self._controls()

  @handle('refresh_usage_button', 'click')
  @handle('range_dropdown', 'change')
  def refresh_usage(self, **event_args):
    if not self._initializing:
      self.refresh()

  @handle('refresh_holdings_button', 'click')
  def refresh_holdings(self, **event_args):
    if not self._enabled or not self._editable or self._loading:
      return
    self._loading = True
    generation = self._generation
    self._controls()
    self.status_label.text = 'Loading saved positions and current market data…'
    try:
      result = anvil.server.call('get_creator_holdings', self._include_own)
      if generation != self._generation or not self._enabled:
        return
      if not result['ok']:
        self.status_label.text = result['message']
        return
      self._holdings = result
      self._render_holdings()
      self.status_label.text = 'Ownership reference refreshed. Portfolio and price dates may differ; see coverage below.'
    finally:
      if generation == self._generation:
        self._loading = False
        self._controls()

  @handle('rank_dropdown', 'change')
  def rank_changed(self, **event_args):
    if not self._initializing:
      self._render_holdings()

  def _render_holdings(self):
    if self._holdings is None:
      return
    result = self._holdings
    metric = 'account_count' if self.rank_dropdown.selected_value == 'users' else 'market_value'
    items = sorted(result['items'], key=lambda item: (-item[metric], item['ticker']))
    self.holdings_rows.items = [dict(item, rank=index + 1) for index, item in enumerate(items[:30])]
    self.holdings_empty_label.visible = not items
    cutoff = result['holdings_cutoff']
    self.holdings_note.text = (
      '${:,.0f} priced holdings · {} accounts with priced open positions / {} valid account portfolios · '
      '{}/{} symbols priced · {} invalid portfolios and {} invalid or unpriceable positions excluded. '
      'Showing top 30 of {} open securities. Oldest saved cutoff: {}. Refreshed {}.'
    ).format(result['market_value'], result['covered_accounts'], result['accounts'], result['priced_symbols'],
             result['requested_symbols'], result['skipped_portfolios'], result['skipped_positions'], len(items),
             cutoff.isoformat() if cutoff else 'none', result['updated_at'].strftime('%b %d, %H:%M UTC'))

  @handle('account_rows', 'x-browse-account')
  def browse_account(self, account_id, **event_args):
    if self._enabled and self._editable and not self._loading:
      self.raise_event('x-browse-account', account_id=account_id)

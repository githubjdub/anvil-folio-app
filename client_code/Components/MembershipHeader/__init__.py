from time import time

import anvil.server
from anvil import *
from ._anvil_designer import MembershipHeaderTemplate


# Market hours are public metadata, shared across the two membership views.
_market_result = None
_market_checked_at = 0
_market_loading = False


class MembershipHeader(MembershipHeaderTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._shown = False

  @handle('', 'show')
  def form_show(self, **event_args):
    self._shown = True
    self.market_status_timer.interval = 0.2

  @handle('', 'hide')
  def form_hide(self, **event_args):
    self._shown = False
    self.market_status_timer.interval = 0

  def _display_market_status(self, status):
    self.market_status_label.text = status['label']
    self.market_detail_label.text = status['detail']
    self.market_status_label.tooltip = '\n'.join(
      str(status[key]) for key in ('scope', 'checked_at', 'schedule_source') if status.get(key)
    )
    for state in ('open', 'closed', 'pre_market', 'after_hours', 'unavailable'):
      self.market_status_region.classes['is-' + state] = status['state'] == state
    self.market_status_region.classes['is-extended'] = status['state'] in ('pre_market', 'after_hours')

  @handle('market_status_timer', 'tick')
  def update_market_status(self, **event_args):
    global _market_result, _market_checked_at, _market_loading
    self.market_status_timer.interval = 0
    if not self._shown:
      return
    age = time() - _market_checked_at
    if _market_result is not None and age < 60:
      self._display_market_status(_market_result)
      self.market_status_timer.interval = max(1, 60 - age)
      return
    if _market_loading:
      self.market_status_timer.interval = 1
      return
    _market_loading = True
    try:
      try:
        status = anvil.server.call_s('get_us_market_status')
      except (getattr(anvil.server, 'RuntimeUnavailableError'), anvil.server.AppOfflineError):
        if self._shown:
          self._display_market_status({
            'label': 'Market status temporarily unavailable',
            'detail': 'Updates automatically when the connection returns.', 'state': 'unavailable',
          })
        return
      _market_result = status
      _market_checked_at = time()
      if self._shown:
        self._display_market_status(status)
    finally:
      _market_loading = False
      self.market_status_timer.interval = 60 if self._shown else 0

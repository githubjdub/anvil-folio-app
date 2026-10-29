from ._anvil_designer import CreatorRecentAccountRowTemplate
from anvil import *


class CreatorRecentAccountRow(CreatorRecentAccountRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True

  def seen_at(self):
    value = self.item['last_seen']
    return value.strftime('%b %d, %H:%M UTC') if value else 'No timestamp'

  @handle('', 'x-set-editable')
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    self.account_button.enabled = self._editable

  @handle('account_button', 'click')
  def browse(self, **event_args):
    if self._editable:
      self.parent.raise_event('x-browse-account', account_id=self.item['account_id'])

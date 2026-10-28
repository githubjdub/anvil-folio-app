import anvil.server
import anvil.users
import anvil.tz
from ._anvil_designer import ProfileHomeTemplate
from anvil import *
from ..account_profile import sign_in, ensure_username


class ProfileHome(ProfileHomeTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._can_edit_name = False
    self.account_id = None
    self.status_label.visible = False
    self._notification_count_loading = False
    self._notification_count_revision = 0
    self._set_notification_count(0)
    self._refresh()

  def _refresh(self):
    user = anvil.users.get_user()
    account_id = user.get_id() if user is not None else None
    if account_id != self.account_id:
      self._set_notification_count(0)
    self.profile_region.visible = user is not None
    self.signed_out_region.visible = user is None
    self.signout_button.visible = user is not None
    if user is None:
      self.account_id = None
      return
    account_id = user.get_id()
    profile = anvil.server.call('get_my_profile')
    usage = anvil.server.call('get_portfolio_usage')
    portfolios = anvil.server.call('list_portfolios')
    current = anvil.users.get_user()
    if current is None or current.get_id() != account_id:
      self._refresh()
      return
    self.account_id = account_id
    # A route can open beneath username setup for an older account.
    username = profile['username'] or ''
    self.greeting_label.text = 'Welcome back, %s.' % username if username else 'Welcome back.'
    self.display_name_label.text = profile['display_name'] or username or 'Your account'
    self.username_label.text = '@' + username if username else 'Choose your username to finish setup'
    self.email_label.text = profile['email']
    self.display_name_box.text = profile['display_name']
    self._can_edit_name = bool(username) and profile['can_change_display_name']
    self.plan_label.text = usage['plan']
    self.usage_label.text = '%s of %s portfolios saved' % (usage['count'], usage['limit'])
    self.usage_note.text = ('Your existing portfolios remain available. Your plan is currently at its saving limit.'
                            if not usage['can_create'] else 'Room for your next investment idea.')
    self.portfolios_panel.items = portfolios
    self.empty_label.visible = not portfolios
    self.member_since_label.text = 'Member since ' + profile['member_since'].strftime('%B %Y') if profile['member_since'] else 'Your personal investing workspace'
    change = profile['next_display_name_change_at']
    if not username:
      self.change_note.text = 'Choose your permanent username before editing your display name.'
    else:
      self.change_note.text = ('Next display-name change: %s UTC.' % change.astimezone(anvil.tz.tzutc()).strftime('%b %d, %Y at %H:%M')
                              if not self._can_edit_name else 'You can change your display name now. After that, once every 7 days.')
    self._set_busy(self._busy)

  def _set_notification_count(self, count):
    self._notification_count_revision += 1
    count = max(0, int(count or 0))
    self.notification_badge.visible = count > 0
    self.notification_count_label.visible = count > 0
    self.notification_count_label.text = '99+' if count > 99 else str(count)
    self.notification_count_description.text = '%s unread notification%s' % (count, '' if count == 1 else 's')

  @handle('', 'show')
  def profile_show(self, **event_args):
    # Fetch after the page opens, keeping this shortcut out of account startup.
    self.notification_count_timer.interval = 0.1

  @handle('', 'hide')
  def profile_hide(self, **event_args):
    self.notification_count_timer.interval = 0
    self._notification_count_revision += 1

  @handle('notification_count_timer', 'tick')
  def notification_count_tick(self, **event_args):
    if get_open_form() is not self:
      self.notification_count_timer.interval = 0
      return
    self.notification_count_timer.interval = 60
    if self._busy or self._notification_count_loading:
      return
    user = anvil.users.get_user()
    account_id = user.get_id() if user is not None else None
    if account_id is None or account_id != self.account_id:
      self._set_notification_count(0)
      return
    revision = self._notification_count_revision
    self._notification_count_loading = True
    try:
      count = anvil.server.call_s('get_notification_unread_count')
      current = anvil.users.get_user()
      if (get_open_form() is self and revision == self._notification_count_revision
          and current is not None and current.get_id() == account_id
          and self.account_id == account_id):
        self._set_notification_count(count)
    finally:
      self._notification_count_loading = False

  def _set_busy(self, busy):
    self._busy = busy
    for button in (self.save_name_button, self.portfolio_button, self.subscription_button, self.signout_button, self.signin_button):
      button.enabled = not busy
    self.save_name_button.enabled = not busy and self._can_edit_name
    self.display_name_box.enabled = not busy and self._can_edit_name

  @handle('save_name_button', 'click')
  def save_name(self, **event_args):
    if self._busy or not self._can_edit_name:
      return
    account_id = self.account_id
    self._set_busy(True)
    try:
      result = anvil.server.call('set_my_display_name', self.display_name_box.text)
      user = anvil.users.get_user()
      if user is None or user.get_id() != account_id or get_open_form() is not self:
        return
      if result['ok'] or result.get('next_display_name_change_at'):
        self._refresh()
      user = anvil.users.get_user()
      if user is None or user.get_id() != account_id or get_open_form() is not self:
        return
      self.status_label.text = result['message']
      self.status_label.visible = True
    finally:
      self._set_busy(False)

  @handle('portfolio_button', 'click')
  def portfolio_click(self, **event_args):
    if not self._busy:
      set_url_hash({'page': 'portfolio'})

  @handle('portfolios_panel', 'x-open-portfolio')
  def open_portfolio(self, portfolio_id, **event_args):
    if not self._busy:
      set_url_hash({'page': 'portfolio', 'portfolio': portfolio_id})

  @handle('subscription_button', 'click')
  def subscription_click(self, **event_args):
    if not self._busy:
      set_url_hash({'page': 'subscription'})

  @handle('signout_button', 'click')
  def signout_click(self, **event_args):
    if not self._busy:
      anvil.users.logout(invalidate_client_objects=True)
      set_url_hash({'page': 'portfolio'})

  @handle('signin_button', 'click')
  def signin_click(self, **event_args):
    if not self._busy and sign_in() is not None and ensure_username():
      self._refresh()

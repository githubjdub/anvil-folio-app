import anvil.server
import anvil.users
import anvil.tz
from ._anvil_designer import ProfileHomeTemplate
from anvil import *
from ..account_profile import sign_in, ensure_username
from ..workspace_cache import account_workspace, invalidate_workspace
from ..Components.PlanComparison import open_stripe_url


class ProfileHome(ProfileHomeTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._can_edit_name = False
    self._verified = False
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
    workspace = account_workspace()
    current = anvil.users.get_user()
    if workspace is None or current is None or current.get_id() != account_id:
      self._refresh()
      return
    profile = workspace['profile']
    usage = workspace['usage']
    portfolios = workspace['portfolios']
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
    self._render_investor(profile['investor'], portfolios)
    self.empty_label.visible = not portfolios
    self.member_since_label.text = 'Member since ' + profile['member_since'].strftime('%B %Y') if profile['member_since'] else 'Your personal investing workspace'
    change = profile['next_display_name_change_at']
    if not username:
      self.change_note.text = 'Choose your permanent username before editing your display name.'
    else:
      self.change_note.text = ('Next display-name change: %s UTC.' % change.astimezone(anvil.tz.tzutc()).strftime('%b %d, %Y at %H:%M')
                              if not self._can_edit_name else 'You can change your display name now. After that, once every 7 days.')
    self._set_busy(self._busy)

  def _render_investor(self, investor, portfolios):
    metrics = investor['metrics']
    verification = investor['verification']
    ranking = investor['ranking']
    self._verified = verification['verified']
    self.avatar_image.source = investor['avatar'] or '_/theme/folio-mark.svg'
    self.verification_label.text = verification['label']
    self.verification_label.role = 'profile-verified' if self._verified else 'portfolio-help'
    self.verify_button.text = 'Included with your plan' if verification['included'] else 'Verified membership active' if self._verified else 'Get Verified · $1/month'
    self.primary_dropdown.items = [(p['name'], p['portfolio_id']) for p in portfolios]
    self.primary_dropdown.selected_value = investor['primary_portfolio_id']
    self.ranking_checkbox.checked = investor['ranking_opt_in']
    self.featured_label.text = metrics['portfolio_name']
    self.quarter_label.text = metrics['quarter_label'] + ' return'
    self.quarter_return_label.text = '{:+.2f}%'.format(metrics['return'] * 100) if metrics['return'] is not None else '—'
    self.aum_label.text = '${:,.2f}'.format(metrics['value']) if metrics['value'] is not None else '—'
    self.holdings_count_label.text = str(metrics['holding_count']) if metrics['holding_count'] is not None else '—'
    self.profile_stats_note.text = (('As of %s · ' % metrics['as_of']) if metrics.get('as_of') else '') + metrics['note']
    self.profile_holdings_panel.items = metrics['holdings']
    self.holdings_empty_label.visible = not metrics['holdings']
    self.percentile_label.text = '{:.1f}th'.format(ranking['percentile']) if ranking['percentile'] is not None else '—'
    self.quartile_label.text = 'Q%s · Rank %s of %s' % (ranking['quartile'], ranking['rank'], ranking['count']) if ranking['quartile'] else 'Not ranked yet'
    self.ranking_note.text = ranking['note'] + (' Last update: %s UTC.' % ranking['updated_at'].strftime('%b %d, %H:%M') if ranking.get('updated_at') else '')
    for index, cell in enumerate((self.quartile_one, self.quartile_two, self.quartile_three, self.quartile_four), 1):
      cell.classes['is-active'] = ranking['quartile'] == index
    self.primary_dropdown.enabled = not self._busy and bool(portfolios)

  def _accept_result(self, result, account_id):
    user = anvil.users.get_user()
    if user is None or user.get_id() != account_id or get_open_form() is not self:
      return False
    if result['ok']:
      invalidate_workspace()
      self._refresh()
    self.status_label.text = result.get('message', '')
    self.status_label.visible = bool(self.status_label.text)
    return True

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
    for control in (self.save_preferences_button, self.avatar_loader, self.remove_avatar_button,
                    self.ranking_checkbox, self.refresh_summary_button):
      control.enabled = not busy
    self.primary_dropdown.enabled = not busy and bool(self.primary_dropdown.items)
    self.verify_button.enabled = not busy and not self._verified

  @handle('save_preferences_button', 'click')
  def save_preferences(self, **event_args):
    if self._busy:
      return
    account_id = self.account_id
    self._set_busy(True)
    try:
      result = anvil.server.call('set_investor_profile', self.primary_dropdown.selected_value, bool(self.ranking_checkbox.checked))
      self._accept_result(result, account_id)
    finally:
      self._set_busy(False)

  @handle('avatar_loader', 'change')
  def avatar_change(self, file, **event_args):
    if self._busy or file is None:
      return
    self._save_avatar(file)

  @handle('remove_avatar_button', 'click')
  def remove_avatar(self, **event_args):
    if not self._busy:
      self._save_avatar(None)

  def _save_avatar(self, media):
    account_id = self.account_id
    self._set_busy(True)
    try:
      self._accept_result(anvil.server.call('set_profile_avatar', media), account_id)
      self.avatar_loader.clear()
    finally:
      self._set_busy(False)

  @handle('refresh_summary_button', 'click')
  def refresh_summary(self, **event_args):
    if self._busy:
      return
    account_id = self.account_id
    self._set_busy(True)
    try:
      result = anvil.server.call('refresh_my_billing')
      self._accept_result(result, account_id)
    finally:
      self._set_busy(False)

  @handle('verify_button', 'click')
  def verify_click(self, **event_args):
    if self._busy or self._verified:
      return
    account_id = self.account_id
    self._set_busy(True)
    try:
      if not confirm('Verified is $1 USD per month, billed monthly until canceled. It is a paid membership badge, not identity verification or an audit of investment results. It is included with Plus and Pro. Continue to secure Stripe checkout?', title='Folio Verified'):
        return
      result = anvil.server.call('start_verification_checkout')
      if not self._accept_result(result, account_id):
        return
      if result['ok']:
        open_stripe_url(result['url'])
    finally:
      self._set_busy(False)

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
        invalidate_workspace()
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
      invalidate_workspace()
      set_url_hash({'page': 'portfolio'})

  @handle('signin_button', 'click')
  def signin_click(self, **event_args):
    if not self._busy and sign_in() is not None and ensure_username():
      self._refresh()

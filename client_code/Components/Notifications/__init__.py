import anvil.server
from time import time
from anvil import *
from ._anvil_designer import NotificationsTemplate


class Notifications(NotificationsTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._parent_editable = True
    self._generation = 0
    self._account_id = None
    self.activity_dropdown.items = [("Buying & selling", "both"), ("Buying only", "buys"), ("Selling only", "sells")]
    self.activity_dropdown.selected_value = "both"
    self.channel_dropdown.items = [("In-app inbox only", "in_app"), ("Email digest · currently paused", "email"), ("Text digest · currently paused", "sms")]
    self.watches_panel.set_event_handler("x-watch-update", self.watch_update)
    self.watches_panel.set_event_handler("x-watch-remove", self.watch_remove)
    self.messages_panel.set_event_handler("x-message-read", self.message_read)
    self.set_event_handler("x-set-editable", self.set_editable)
    self.set_event_handler("hide", self.form_hide)
    self.set_event_handler("show", self.form_show)
    self.reset_workspace()

  def reset_workspace(self):
    self._generation += 1
    self._loaded = False
    self._auto_load_attempted = False
    self._preferences_dirty = False
    self._settings = None
    self._capabilities = {}
    self._cursors = [None]
    self._page = 0
    self._next_cursor = None
    self._unread_count = 0
    self._active = False
    self._last_check_at = 0
    self.check_timer.interval = 0
    self.watches_panel.items = []
    self.messages_panel.items = []
    self.symbol_box.text = ""
    self.alerts_checkbox.checked = True
    self.channel_dropdown.selected_value = "in_app"
    self.delivery_time_box.text = "18:00"
    self.timezone_box.text = "UTC"
    self.phone_box.text = ""
    self.consent_checkbox.checked = False
    self.unread_label.text = "Your personal insider-activity inbox"
    self.capability_label.text = ""
    self.delivery_status_label.text = ""
    self._set_status("")
    self._update_controls()

  def set_account_context(self, account_id):
    if account_id != self._account_id:
      self._account_id = account_id
      self.reset_workspace()

  def set_editable(self, editable=True, **event_args):
    self._parent_editable = editable
    self._update_controls()

  def ensure_loaded(self):
    if not self._loaded and not self._auto_load_attempted and self._can_act():
      self.refresh_click()
    elif self._loaded and self._can_act() and time() - self._last_check_at >= 60:
      self.refresh_click()

  def set_active(self, active):
    """Parent controls polling so other portfolio tabs make no SEC requests."""
    self._active = bool(active)
    self.check_timer.interval = 60 if self._active and self._account_id else 0

  @handle("check_timer", "tick")
  def check_timer_tick(self, **event_args):
    if self._active and self._can_act():
      self.ensure_loaded()

  def form_hide(self, **event_args):
    self.check_timer.interval = 0

  def form_show(self, **event_args):
    self.check_timer.interval = 60 if self._active and self._account_id else 0

  def _can_act(self):
    return bool(self._account_id) and not self._busy and self._parent_editable

  def _set_status(self, message, error=False):
    self.status_label.text = message
    self.status_region.classes["is-error"] = error

  def _update_controls(self):
    signed_in = bool(self._account_id)
    enabled = self._can_act()
    self.sign_in_region.visible = not signed_in
    self.workspace_region.visible = signed_in and self._loaded
    self.refresh_button.enabled = enabled
    self.refresh_button.text = "Loading inbox…" if self._busy else "Refresh inbox"
    for component in (self.symbol_box, self.activity_dropdown, self.add_watch_button,
                      self.alerts_checkbox, self.channel_dropdown, self.save_preferences_button):
      component.enabled = enabled
    channel = self.channel_dropdown.selected_value or "in_app"
    self.schedule_region.visible = channel != "in_app"
    self.sms_region.visible = channel == "sms"
    self.delivery_time_box.enabled = enabled and channel != "in_app"
    self.timezone_box.enabled = enabled and channel != "in_app"
    self.phone_box.enabled = enabled and channel == "sms"
    self.consent_checkbox.enabled = enabled and channel == "sms"
    self.previous_button.enabled = enabled and self._page > 0
    self.next_button.enabled = enabled and self._next_cursor is not None
    self.mark_all_button.enabled = enabled and self._unread_count > 0
    self.watches_panel.raise_event_on_children("x-set-editable", editable=enabled)
    self.messages_panel.raise_event_on_children("x-set-editable", editable=enabled)
    self._show_channel_capability()

  def _show_channel_capability(self):
    channel = self.channel_dropdown.selected_value
    capabilities = self._capabilities
    if channel == "email":
      if capabilities.get("email"):
        self.capability_label.text = "Email digest delivery is paused until a scheduler is connected. Saved destination: %s." % capabilities.get("email_address", "")
      else:
        self.capability_label.text = capabilities.get("email_setup_message") or "Email delivery needs to be configured by the platform owner. Your in-app inbox is available."
    elif channel == "sms":
      self.capability_label.text = "Text digest delivery is paused until a scheduler is connected. Your saved number and consent are kept." if capabilities.get("sms") else (capabilities.get("sms_setup_message") or "Text delivery needs to be configured by the platform owner. Your in-app inbox is available.")
    else:
      self.capability_label.text = "Open or refresh Notifications to check for matching filings. Checks advance in small steps once a minute while this page is open. Each completed security check waits 15 minutes before starting again; large filing backlogs need several visits."

  def _set_busy(self, busy):
    self._busy = busy
    self._update_controls()
    self.raise_event("x-notifications-busy", busy=busy)

  def _apply_workspace(self, result, refresh_preferences=False):
    self._loaded = True
    self._settings = result["settings"]
    self._capabilities = result["capabilities"]
    self._next_cursor = result.get("next_cursor")
    self._unread_count = result["unread_count"]
    self.watches_panel.items = result["watches"]
    self.messages_panel.items = result["messages"]
    self.watch_empty_label.visible = not bool(result["watches"])
    self.inbox_empty_label.visible = not bool(result["messages"])
    self.inbox_empty_region.visible = not bool(result["messages"])
    self.inbox_empty_label.text = "No messages on this page. Go back or refresh your inbox." if self._page else "No insider alerts yet. Add a security to start watching future filings. Existing trades are not backfilled."
    self.unread_label.text = "%s unread message%s" % (self._unread_count, "" if self._unread_count == 1 else "s")
    self.page_label.text = "Page %s · %s message%s" % (self._page + 1, len(result["messages"]), "" if len(result["messages"]) == 1 else "s")
    self.pagination_region.visible = self._page > 0 or self._next_cursor is not None
    if refresh_preferences or not self._preferences_dirty:
      settings = self._settings
      self.alerts_checkbox.checked = settings["enabled"]
      self.channel_dropdown.selected_value = settings["channel"]
      self.delivery_time_box.text = settings["delivery_time"]
      self.timezone_box.text = settings["timezone"]
      self.phone_box.text = settings["phone"] or ""
      self.consent_checkbox.checked = bool(settings["sms_consent_at"])
      self._preferences_dirty = False
    delivery_status = self._settings["delivery_status"] or ""
    delivery_error = self._settings["delivery_error"] or ""
    self.delivery_status_label.text = ("Last delivery: %s" % delivery_status.replace("_", " ")) + (" · " + delivery_error if delivery_error else "") if delivery_status or delivery_error else "No external digest has been sent yet."
    if self._settings["channel"] != "in_app":
      self.delivery_status_label.text += " " + result.get("delivery_limit", "")
    if not self._settings["enabled"]:
      self.delivery_status_label.text = "Alerts are paused. Enable alerts and save preferences to resume watching."
    self._update_controls()
    self.raise_event("x-notifications-unread", count=self._unread_count, account_id=self._account_id)

  def _load_page(self, page, cursor, check_for_updates=False):
    if not self._can_act():
      return
    generation = self._generation
    # Releasing the parent busy state calls ensure_loaded() again. An initial
    # load failure must reach Anvil's error handler, without starting a retry loop.
    self._auto_load_attempted = True
    if check_for_updates:
      self._last_check_at = time()
    self._set_busy(True)
    self._set_status("Loading your notifications…")
    try:
      result = anvil.server.call("get_notification_workspace", before=cursor, check_for_updates=check_for_updates)
      if generation != self._generation:
        return
      self._page = page
      if page == 0:
        self._cursors = [None]
      elif page == len(self._cursors):
        self._cursors.append(cursor)
      self._apply_workspace(result)
      polling = result.get("polling") or {}
      self._set_status(polling.get("message") or "Inbox refreshed.", error=bool(polling.get("source_errors")))
    finally:
      if check_for_updates and generation == self._generation:
        # A slow source request must not make the parent immediately start
        # another poll when its busy state is released.
        self._last_check_at = time()
      self._set_busy(False)

  @handle("refresh_button", "click")
  def refresh_click(self, **event_args):
    self._load_page(0, None, check_for_updates=True)

  @handle("previous_button", "click")
  def previous_click(self, **event_args):
    if self._page > 0:
      self._load_page(self._page - 1, self._cursors[self._page - 1])

  @handle("next_button", "click")
  def next_click(self, **event_args):
    if self._next_cursor is not None:
      self._load_page(self._page + 1, self._next_cursor)

  def _run_change(self, callable_name, args, refresh_preferences=False, clear_symbol=False):
    if not self._can_act():
      return
    generation = self._generation
    self._set_busy(True)
    self._set_status("Saving…")
    try:
      result = anvil.server.call(callable_name, *args)
      if generation != self._generation:
        return
      if not result["ok"]:
        self._set_status(result["message"], error=True)
        return
      workspace = anvil.server.call("get_notification_workspace", before=self._cursors[self._page])
      if generation != self._generation:
        return
      self._apply_workspace(workspace, refresh_preferences=refresh_preferences)
      if clear_symbol:
        self.symbol_box.text = ""
      self._set_status(result["message"])
    finally:
      self._set_busy(False)

  @handle("add_watch_button", "click")
  @handle("symbol_box", "pressed_enter")
  def add_watch_click(self, **event_args):
    self._run_change("add_insider_watch", (self.symbol_box.text, self.activity_dropdown.selected_value), clear_symbol=True)

  @handle("save_preferences_button", "click")
  def save_preferences_click(self, **event_args):
    self._run_change("save_notification_preferences", (
      bool(self.alerts_checkbox.checked), self.channel_dropdown.selected_value,
      self.delivery_time_box.text, self.timezone_box.text, self.phone_box.text,
      bool(self.consent_checkbox.checked)), refresh_preferences=True)

  @handle("alerts_checkbox", "change")
  @handle("channel_dropdown", "change")
  @handle("delivery_time_box", "change")
  @handle("timezone_box", "change")
  @handle("consent_checkbox", "change")
  def preference_change(self, **event_args):
    self._preferences_dirty = True
    self._update_controls()

  @handle("phone_box", "change")
  def phone_change(self, **event_args):
    # Consent saved for one number does not automatically apply to a new one.
    self.consent_checkbox.checked = False
    self.preference_change()

  def watch_update(self, watch, activity, active, **event_args):
    self._run_change("update_insider_watch", (watch.get_id(), activity, active))

  def watch_remove(self, watch, **event_args):
    self._run_change("remove_insider_watch", (watch.get_id(),))

  def message_read(self, message, read, **event_args):
    self._run_change("mark_notification_read", (message.get_id(), read))

  @handle("mark_all_button", "click")
  def mark_all_click(self, **event_args):
    self._run_change("mark_all_notifications_read", ())

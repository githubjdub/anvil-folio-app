import anvil.server
from ._anvil_designer import CreatorWorkspaceTemplate
from anvil import *


class CreatorWorkspace(CreatorWorkspaceTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._access_enabled = False
    self._creator_email = ""
    self._access_generation = 0
    self._parent_editable = True
    self.activity_mode_dropdown.items = [("Most added", "added"), ("Most bought", "bought"), ("Most sold", "sold")]
    self.trade_action_dropdown.items = [("Any action", None), ("Buy entries", "Buy"), ("Sell entries", "Sell")]
    self._reset_private_state()
    self.site_analytics.configure_access(False, None)
    self.monitor.configure_access(False, None)
    self.creator_email_label.text = ""
    self.visible = False
    self.show_status("Open the creator workspace to browse accounts and saved portfolios.")
    self._update_controls()

  def _reset_private_state(self):
    self._loading = False
    self._loaded = False
    self._accounts_loaded = False
    self._activity_loaded = False
    self._active_tab = "accounts"
    self._page = 0
    self._has_previous = False
    self._has_next = False
    self._accounts_page = 0
    self._accounts_has_previous = False
    self._accounts_has_next = False
    self._accounts_search = ""
    self._search_text = ""
    self._include_own = False
    self._selected_account_id = None
    self._trade_action = None
    self._stock_popularity = None
    self._stock_activity = None
    self.accounts_panel.items = []
    self.portfolios_panel.items = []
    self.selected_account_panel.items = []
    self.selected_account_region.visible = False
    self.search_box.text = ""
    self.accounts_search_box.text = ""
    self.include_own_checkbox.checked = False
    self.trade_action_dropdown.selected_value = None
    self.activity_mode_dropdown.selected_value = "bought"
    self.accounts_summary_label.text = ""
    self.accounts_page_label.text = ""
    self.page_label.text = ""
    self.portfolio_scope_label.text = "All saved portfolios"
    self.empty_label.visible = False
    self.accounts_empty_label.visible = False
    self.clear_account_button.visible = False
    self._clear_stock_popularity()
    self._apply_tab_views()

  def configure_access(self, enabled, email):
    enabled = bool(enabled)
    email = str(email or "").strip().lower() if enabled else ""
    if enabled == self._access_enabled and email == self._creator_email:
      return
    # An account change invalidates all outstanding requests before clearing
    # private rows, account summaries, filters, previews, and activity caches.
    self._access_generation += 1
    self._access_enabled = enabled
    self._creator_email = email
    self._reset_private_state()
    self.site_analytics.configure_access(enabled, email)
    self.monitor.configure_access(enabled, email)
    self.creator_email_label.text = email
    self.show_status("Open the creator workspace to browse accounts and saved portfolios." if enabled else "")
    self.visible = enabled
    self._update_controls()

  def apply_appearance(self):
    if self._access_enabled and self._active_tab == "analytics":
      return self.site_analytics.apply_appearance()
    return False

  def open_library(self):
    if self._can_edit():
      self._select_tab(self._active_tab)

  def show_status(self, message, is_error=False):
    self.status_label.text = str(message or "")
    self.status_region.classes["is-error"] = bool(is_error)

  def _can_edit(self):
    return self._access_enabled and self._parent_editable and not self._loading

  def _apply_tab_views(self):
    for name, region, button in (
      ("accounts", self.accounts_region, self.accounts_tab_button),
      ("portfolios", self.portfolios_region, self.portfolios_tab_button),
      ("activity", self.activity_region, self.activity_tab_button),
      ("analytics", self.analytics_region, self.analytics_tab_button),
      ("monitor", self.monitor_region, self.monitor_tab_button),
    ):
      region.visible = self._active_tab == name
      button.role = "creator-tab-active" if self._active_tab == name else "creator-tab"
    self.portfolio_scope_region.visible = self._active_tab != "analytics"
    self.status_region.visible = self._active_tab not in ("analytics", "monitor")

  def _select_tab(self, tab):
    if not self._can_edit():
      return
    self._active_tab = tab
    self._apply_tab_views()
    if tab == "accounts" and not self._accounts_loaded:
      self._load_accounts(0)
    elif tab == "portfolios" and not self._loaded:
      self._load_page(0)
    elif tab == "activity" and not self._activity_loaded:
      self._load_activity()
    elif tab == "analytics":
      self.site_analytics.open_dashboard()
    elif tab == "monitor":
      self.monitor.set_scope(self._include_own)
      self.monitor.open_dashboard()

  @handle("monitor_tab_button", "click")
  def show_monitor(self, **event_args):
    self._select_tab("monitor")

  @handle("accounts_tab_button", "click")
  @handle("back_accounts_button", "click")
  def show_accounts(self, **event_args):
    self._select_tab("accounts")

  @handle("portfolios_tab_button", "click")
  def show_portfolios(self, **event_args):
    self._select_tab("portfolios")

  @handle("activity_tab_button", "click")
  def show_activity(self, **event_args):
    self._select_tab("activity")

  @handle("analytics_tab_button", "click")
  def show_analytics(self, **event_args):
    self._select_tab("analytics")

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._parent_editable = bool(editable)
    self._update_controls()

  def _update_controls(self):
    editable = self._can_edit()
    for component in (
      self.accounts_search_box, self.accounts_search_button,
      self.accounts_tab_button, self.portfolios_tab_button, self.activity_tab_button,
      self.analytics_tab_button, self.monitor_tab_button,
      self.search_box, self.search_button, self.refresh_button,
      self.include_own_checkbox, self.trade_action_dropdown, self.activity_mode_dropdown,
      self.clear_account_button, self.back_accounts_button,
    ):
      setattr(component, "enabled", editable)
    setattr(self.previous_button, "enabled", editable and self._has_previous)
    setattr(self.next_button, "enabled", editable and self._has_next)
    setattr(self.accounts_previous_button, "enabled", editable and self._accounts_has_previous)
    setattr(self.accounts_next_button, "enabled", editable and self._accounts_has_next)
    for panel in (self.accounts_panel, self.selected_account_panel, self.portfolios_panel,
                  self.stock_popularity_panel, self.more_stocks_panel):
      panel.raise_event_on_children("x-set-editable", editable=editable)
    self.site_analytics.raise_event("x-set-editable", editable=editable)
    self.monitor.raise_event("x-set-editable", editable=editable)

  def _begin_load(self, message):
    if not self._can_edit():
      return None
    self._loading = True
    self.show_status(message)
    self._update_controls()
    return self._access_generation

  def _finish_load(self, generation):
    if generation == self._access_generation:
      self._loading = False
      self._update_controls()

  @handle("accounts_panel", "x-save-membership")
  @handle("selected_account_panel", "x-save-membership")
  def save_membership(self, account_id, tier, expires_on=None, **event_args):
    generation = self._begin_load("Saving account membership…")
    if generation is None:
      return
    try:
      result = anvil.server.call("creator_set_membership", account_id, tier, expires_on)
      if generation != self._access_generation or not self._access_enabled:
        return
      if not result["ok"]:
        self.show_status(result["message"], is_error=True)
        return
      membership = result["membership"]
      for panel in (self.accounts_panel, self.selected_account_panel):
        panel.raise_event_on_children("x-membership-updated", account_id=account_id,
                                      membership=membership)
      self.show_status(result["message"])
      self.raise_event("x-membership-changed", account_id=account_id, membership=membership)
    finally:
      self._finish_load(generation)

  def _load_accounts(self, page):
    generation = self._begin_load("Loading account directory…")
    if generation is None:
      return
    try:
      result = anvil.server.call("list_creator_accounts", self._accounts_search,
                                 page, self._include_own)
      if generation != self._access_generation or not self._access_enabled:
        return
      if not result["ok"]:
        self.show_status(result["message"], is_error=True)
        return
      self._accounts_loaded = True
      self._accounts_page = result["page"]
      self._accounts_has_previous = result["has_previous"]
      self._accounts_has_next = result["has_next"]
      self.accounts_panel.items = result["items"]
      self.accounts_summary_label.text = "{} accounts · {} saved portfolios".format(
        result["account_count"], result["portfolio_count"])
      self.accounts_page_label.text = "Page {}".format(self._accounts_page + 1) if result["total"] else ""
      self.accounts_empty_label.visible = not result["items"]
      self.show_status("Showing {} of {} accounts.".format(len(result["items"]), result["total"]))
    finally:
      self._finish_load(generation)

  def _load_page(self, page):
    generation = self._begin_load("Loading saved portfolios and recorded activity…")
    if generation is None:
      return
    try:
      result = anvil.server.call("list_creator_portfolios", self._search_text,
                                 page, self._include_own, self._selected_account_id,
                                 self._trade_action)
      if generation != self._access_generation or not self._access_enabled:
        return
      if not result["ok"]:
        self.show_status(result["message"], is_error=True)
        return
      self._loaded = True
      self._activity_loaded = True
      self._page = result["page"]
      self._has_previous = result["has_previous"]
      self._has_next = result["has_next"]
      self.portfolios_panel.items = result["items"]
      selected_account = result.get("selected_account")
      if selected_account is not None:
        summary = dict(selected_account)
        summary["selected"] = True
        self.selected_account_panel.items = [summary]
        self.portfolio_scope_label.text = "Saved portfolios for " + (selected_account.get("email") or "Account without email")
      else:
        self.selected_account_panel.items = []
        self.portfolio_scope_label.text = "All saved portfolios"
      self.selected_account_region.visible = selected_account is not None
      self.clear_account_button.visible = self._selected_account_id is not None
      self._stock_popularity = result.get("stock_popularity")
      self._stock_activity = result.get("stock_activity")
      self._render_activity()
      count = len(result["items"])
      total = result["total"]
      self.page_label.text = "Page {}".format(self._page + 1) if total else ""
      self.empty_label.visible = not count
      self.show_status("Recorded activity updated across the saved portfolio library." if self._active_tab == "activity"
                       else "Showing {} of {} saved portfolios.".format(count, total))
    finally:
      self._finish_load(generation)

  def _load_activity(self):
    generation = self._begin_load("Loading recorded stock and fund activity…")
    if generation is None:
      return
    try:
      # Activity always describes the whole eligible library. A deleted account
      # or an old portfolio filter must not interfere with global rankings.
      result = anvil.server.call("list_creator_portfolios", "", 0,
                                 self._include_own, None, None)
      if generation != self._access_generation or not self._access_enabled:
        return
      if not result["ok"]:
        self.show_status(result["message"], is_error=True)
        return
      self._activity_loaded = True
      self._stock_popularity = result.get("stock_popularity")
      self._stock_activity = result.get("stock_activity")
      self._render_activity()
      self.show_status("Recorded activity updated across the saved portfolio library.")
    finally:
      self._finish_load(generation)

  @handle("accounts_search_button", "click")
  @handle("accounts_search_box", "pressed_enter")
  def search_accounts(self, **event_args):
    if self._can_edit():
      self._accounts_search = str(self.accounts_search_box.text or "").strip()
      self._load_accounts(0)

  @handle("accounts_previous_button", "click")
  def previous_accounts(self, **event_args):
    if self._accounts_has_previous:
      self._load_accounts(self._accounts_page - 1)

  @handle("accounts_next_button", "click")
  def next_accounts(self, **event_args):
    if self._accounts_has_next:
      self._load_accounts(self._accounts_page + 1)

  @handle("accounts_panel", "x-browse-account")
  @handle("monitor", "x-browse-account")
  def browse_account(self, account_id, **event_args):
    if not self._can_edit():
      return
    self._selected_account_id = account_id
    self._search_text = ""
    self.search_box.text = ""
    self._trade_action = None
    self.trade_action_dropdown.selected_value = None
    self._loaded = False
    self._select_tab("portfolios")

  @handle("clear_account_button", "click")
  def clear_account(self, **event_args):
    if self._can_edit():
      self._selected_account_id = None
      self._load_page(0)

  def _apply_filters(self, refresh=False):
    if not self._can_edit():
      return
    search_text = str(self.search_box.text or "").strip()
    trade_action = self.trade_action_dropdown.selected_value
    changed = search_text != self._search_text or trade_action != self._trade_action
    self._search_text = search_text
    self._trade_action = trade_action
    self._load_page(self._page if refresh and not changed else 0)

  @handle("search_button", "click")
  @handle("search_box", "pressed_enter")
  @handle("trade_action_dropdown", "change")
  def search(self, **event_args):
    self._apply_filters()

  @handle("include_own_checkbox", "change")
  def change_scope(self, **event_args):
    if not self._can_edit():
      return
    self._include_own = bool(self.include_own_checkbox.checked)
    self._loaded = self._accounts_loaded = self._activity_loaded = False
    self._selected_account_id = None
    self._page = self._accounts_page = 0
    self._has_previous = self._has_next = False
    self._accounts_has_previous = self._accounts_has_next = False
    self.accounts_panel.items = []
    self.portfolios_panel.items = []
    self.selected_account_panel.items = []
    self.selected_account_region.visible = False
    self.clear_account_button.visible = False
    self.accounts_summary_label.text = self.accounts_page_label.text = self.page_label.text = ""
    self.portfolio_scope_label.text = "All saved portfolios"
    self._stock_popularity = self._stock_activity = None
    self._clear_stock_popularity()
    self._select_tab(self._active_tab)

  @handle("refresh_button", "click")
  def refresh(self, **event_args):
    if not self._can_edit():
      return
    if self._active_tab == "accounts":
      text = str(self.accounts_search_box.text or "").strip()
      page = self._accounts_page if text == self._accounts_search else 0
      self._accounts_search = text
      self._load_accounts(page)
    elif self._active_tab == "portfolios":
      self._apply_filters(refresh=True)
    elif self._active_tab == "analytics":
      self.site_analytics.refresh()
    elif self._active_tab == "monitor":
      self.monitor.refresh()
    else:
      self._load_activity()

  @handle("previous_button", "click")
  def previous_page(self, **event_args):
    if self._has_previous:
      self._load_page(self._page - 1)

  @handle("next_button", "click")
  def next_page(self, **event_args):
    if self._has_next:
      self._load_page(self._page + 1)

  def _clear_stock_popularity(self):
    self.stock_popularity_panel.items = []
    self.more_stocks_panel.items = []
    self.stock_summary_label.text = ""
    self.stock_updated_label.text = ""
    self.stock_empty_label.text = "Open Activity to load recorded stock and fund activity."
    self.stock_empty_label.visible = True
    self.more_stocks_region.visible = False
    self.activity_title_label.text = "Most bought stocks"
    self.activity_description_label.text = "Ranked by recorded buy entries across the saved portfolio library."

  def _render_activity(self):
    mode = self.activity_mode_dropdown.selected_value
    titles = {"added": "Most added stocks", "bought": "Most bought stocks", "sold": "Most sold stocks"}
    self.activity_title_label.text = titles[mode]
    self.activity_description_label.text = (
      "Ranked by the number of saved portfolios with buy entries. Multiple buys count once per portfolio."
      if mode == "added" else "Ranked by recorded {} entries across saved portfolios.".format("buy" if mode == "bought" else "sell"))
    popularity = self._stock_popularity if mode == "added" else (self._stock_activity or {}).get(mode)
    if popularity is None:
      self.stock_popularity_panel.items = []
      self.more_stocks_panel.items = []
      self.stock_summary_label.text = self.stock_updated_label.text = ""
      self.more_stocks_region.visible = False
      self.stock_empty_label.text = "This ranking is not available yet. Refresh to load current activity."
      self.stock_empty_label.visible = True
      return
    items = []
    for value in popularity["items"]:
      entry = dict(value)
      entry["transaction_count"] = entry.get("transaction_count", entry.get("buy_transactions", 0))
      entry["action"] = entry.get("action", "Buy")
      items.append(entry)
    self.stock_popularity_panel.items = items[:5]
    self.more_stocks_panel.items = items[5:]
    self.more_stocks_region.visible = len(items) > 5
    self.stock_summary_label.text = "{} saved portfolios · {} accounts · {} securities".format(
      popularity["portfolio_count"], popularity["account_count"], popularity["ticker_count"])
    updated_at = popularity.get("updated_at")
    self.stock_updated_label.text = "Updated " + updated_at.strftime("%b %d, %Y %H:%M UTC") if updated_at is not None else ""
    self.stock_empty_label.text = "No recorded {} entries match this ranking yet.".format("sell" if mode == "sold" else "buy")
    self.stock_empty_label.visible = not items

  @handle("activity_mode_dropdown", "change")
  def activity_mode_change(self, **event_args):
    if self._can_edit():
      self._render_activity()
      self._update_controls()

  @handle("stock_popularity_panel", "x-filter-stock")
  @handle("more_stocks_panel", "x-filter-stock")
  def filter_stock(self, ticker, action="Buy", **event_args):
    if not self._can_edit():
      return
    self._selected_account_id = None
    self.search_box.text = ticker
    self.trade_action_dropdown.selected_value = action
    self._search_text = ticker
    self._trade_action = action
    self._loaded = False
    self._select_tab("portfolios")

  @handle("portfolios_panel", "x-import-portfolio")
  def import_portfolio(self, portfolio_id, **event_args):
    if self._can_edit():
      self.raise_event("x-import-creator-portfolio", portfolio_id=portfolio_id)

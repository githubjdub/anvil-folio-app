import anvil.server
from ._anvil_designer import CreatorWorkspaceTemplate
from anvil import *


class CreatorWorkspace(CreatorWorkspaceTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._access_enabled = False
    self._creator_email = ""
    self._access_generation = 0
    self._loaded = False
    self._loading = False
    self._parent_editable = True
    self._page = 0
    self._has_previous = False
    self._has_next = False
    self._search_text = ""
    self._include_own = False
    self.portfolios_panel.items = []
    self.include_own_checkbox.checked = False
    self.empty_label.visible = False
    self.creator_email_label.text = ""
    self.show_status("Open the creator library to browse saved portfolios.")
    self.page_label.text = ""
    self.visible = False
    self._update_controls()

  def configure_access(self, enabled, email):
    enabled = bool(enabled)
    email = str(email or "").strip().lower() if enabled else ""
    if enabled == self._access_enabled and email == self._creator_email:
      return
    # Signing out or switching accounts invalidates an in-flight response and
    # removes every private list item before the next account can use this Form.
    self._access_generation += 1
    self._access_enabled = enabled
    self._creator_email = email
    self._loaded = False
    self._loading = False
    self._page = 0
    self._has_previous = False
    self._has_next = False
    self._search_text = ""
    self._include_own = False
    self.portfolios_panel.items = []
    self.search_box.text = ""
    self.include_own_checkbox.checked = False
    self.creator_email_label.text = email
    self.page_label.text = ""
    self.empty_label.visible = False
    self.show_status("Open the creator library to browse saved portfolios." if enabled else "")
    self.visible = enabled
    self._update_controls()

  def open_library(self):
    if self._access_enabled and not self._loaded:
      self._load_page(0)

  def show_status(self, message, is_error=False):
    self.status_label.text = str(message or "")
    self.status_region.classes["is-error"] = bool(is_error)

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._parent_editable = bool(editable)
    self._update_controls()

  def _update_controls(self):
    editable = self._access_enabled and self._parent_editable and not self._loading
    for component in (self.search_box, self.search_button, self.refresh_button,
                      self.include_own_checkbox):
      setattr(component, "enabled", editable)
    setattr(self.previous_button, "enabled", editable and self._has_previous)
    setattr(self.next_button, "enabled", editable and self._has_next)
    self.portfolios_panel.raise_event_on_children("x-set-editable", editable=editable)

  def _load_page(self, page):
    if not self._access_enabled or not self._parent_editable or self._loading:
      return
    generation = self._access_generation
    self._loading = True
    self.show_status("Loading saved portfolios…")
    self.empty_label.visible = False
    self._update_controls()
    try:
      result = anvil.server.call("list_creator_portfolios", self._search_text,
                                 page, self._include_own)
      if generation != self._access_generation or not self._access_enabled:
        return
      if not result["ok"]:
        self.show_status(result["message"], is_error=True)
        return
      self._loaded = True
      self._page = result["page"]
      self._has_previous = result["has_previous"]
      self._has_next = result["has_next"]
      self.portfolios_panel.items = result["items"]
      count = len(result["items"])
      total = result["total"]
      self.page_label.text = "Page {}".format(self._page + 1) if total else ""
      self.show_status("Showing {} of {} saved portfolios.".format(count, total))
      self.empty_label.visible = not count
    finally:
      if generation == self._access_generation:
        self._loading = False
        self._update_controls()

  def _apply_filters(self, refresh=False):
    if not self._access_enabled or not self._parent_editable or self._loading:
      return
    search_text = str(self.search_box.text or "").strip()
    include_own = bool(self.include_own_checkbox.checked)
    changed = search_text != self._search_text or include_own != self._include_own
    self._search_text = search_text
    self._include_own = include_own
    self._load_page(self._page if refresh and not changed else 0)

  @handle("search_button", "click")
  @handle("search_box", "pressed_enter")
  @handle("include_own_checkbox", "change")
  def search(self, **event_args):
    self._apply_filters()

  @handle("refresh_button", "click")
  def refresh(self, **event_args):
    self._apply_filters(refresh=True)

  @handle("previous_button", "click")
  def previous_page(self, **event_args):
    if self._has_previous:
      self._load_page(self._page - 1)

  @handle("next_button", "click")
  def next_page(self, **event_args):
    if self._has_next:
      self._load_page(self._page + 1)

  @handle("portfolios_panel", "x-import-portfolio")
  def import_portfolio(self, portfolio_id, **event_args):
    if self._access_enabled and self._parent_editable and not self._loading:
      self.raise_event("x-import-creator-portfolio", portfolio_id=portfolio_id)

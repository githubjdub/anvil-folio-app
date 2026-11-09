import anvil.js
import anvil.server
import anvil.users
from time import time
from ._anvil_designer import ShareComposerTemplate
from anvil import *


class ShareComposer(ShareComposerTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._context_getter = self._missing_context
    self._ensure_signed_in = lambda: False
    self._base_url = ""
    self._origin_ready = False
    self._origin_loaded = False
    self._origin_message = ""
    self._account_id = None
    self._generation = 0
    self._loading_details = False
    self._links = []
    self._links_loaded_at = None
    self._busy = False
    self._latest_share_id = None
    self._latest_url = ""
    self._summary = ""
    self.result_region.visible = False
    self.preview_region.visible = False
    self.live_app_link.visible = False
    self.share_status.text = ""
    self.native_share_button.visible = bool(getattr(anvil.js.window.navigator, "share", None))
    self._set_busy(False)

  def apply_appearance(self):
    if self.preview_region.visible:
      return self.snapshot_card.apply_appearance()
    return False

  def _missing_context(self):
    raise ValueError("Sync your portfolio before sharing performance.")

  def show_for(self, context_getter, ensure_signed_in):
    self._generation += 1
    self._sync_account()
    self._context_getter = context_getter
    self._ensure_signed_in = ensure_signed_in
    self._latest_share_id = None
    self._latest_url = ""
    self.result_region.visible = False
    self.preview_region.visible = False
    self.share_load_timer.interval = 0
    self.dom_nodes["chart_options"].removeAttribute("open")
    self.dom_nodes["saved_links"].removeAttribute("open")
    self._set_status("")
    self._update_summary()
    self._set_busy(False)

  def _sync_account(self):
    user = anvil.users.get_user()
    account_id = user.get_id() if user is not None else None
    if account_id != self._account_id:
      self._generation += 1
      self._account_id = account_id
      self._links = []
      self._links_loaded_at = None
      self.share_links_panel.items = []
      self._latest_share_id = None
      self._latest_url = ""
      self.result_region.visible = False
      self.preview_region.visible = False
    return account_id

  def set_account_context(self):
    self._sync_account()

  def details_toggle(self, event):
    if event.currentTarget.hasAttribute("open"):
      self.share_load_timer.interval = .01

  @handle("share_load_timer", "tick")
  def load_details(self, **event_args):
    self.share_load_timer.interval = 0
    if self._loading_details or not self.visible:
      return
    self._sync_account()
    generation = self._generation
    self._loading_details = True
    try:
      self._ensure_origin()
      if generation == self._generation and self.visible and self.dom_nodes["saved_links"].hasAttribute("open"):
        self._refresh_links()
    finally:
      self._loading_details = False
      if generation != self._generation and self.visible and (self.dom_nodes["chart_options"].hasAttribute("open") or self.dom_nodes["saved_links"].hasAttribute("open")):
        self.share_load_timer.interval = .01

  def _ensure_origin(self):
    if not self._origin_loaded:
      self.link_availability_label.text = "Checking chart link availability…"
      origin = anvil.server.call("get_performance_share_origin")
      self._base_url = origin["base_url"].rstrip("/")
      self._origin_ready = origin["ok"]
      self._origin_message = origin.get("message", "")
      self._origin_loaded = True
      self.live_app_link.url = self._base_url
      self.live_app_link.visible = bool(self._base_url) and not self._origin_ready
    self.link_availability_label.text = (
      (self._origin_message or "Chart links are available on the live site. You can copy percentages here, or open live Folio below.")
      if not self._origin_ready else
      "Sign in when creating a chart link. Copying percentages works without an account."
      if anvil.users.get_user() is None else
      "Create a fixed comparison chart from your latest portfolio sync."
    )
    self._set_busy(self._busy)

  def _update_summary(self):
    try:
      context = self._context_getter()
    except ValueError as error:
      self._summary = ""
      self.summary_label.text = str(error)
      self._set_status(str(error), is_error=True)
      self._set_busy(self._busy)
      return None
    self._summary = context["summary"]
    self.summary_label.text = self._summary
    self._set_busy(self._busy)
    return context

  def _share_text(self):
    return self._summary + (
      "\n\nBuild your own comparison on Folio: " + self._base_url
      if self._base_url else ""
    )

  def _refresh_links(self):
    account_id = self._sync_account()
    if account_id is None:
      self._render_links()
      return
    if self._links_loaded_at is not None and time() - self._links_loaded_at < 60:
      self._render_links()
      return
    generation = self._generation
    self.links_empty_label.text = "Loading your shared snapshots…"
    self.links_empty_label.visible = not bool(self._links)
    links = anvil.server.call("list_performance_shares", True)
    user = anvil.users.get_user()
    if generation != self._generation or user is None or user.get_id() != account_id:
      return
    self._links = list(links)
    self._links_loaded_at = time()
    self._render_links()

  def _render_links(self):
    self.share_links_panel.items = self._links
    self.share_links_panel.raise_event_on_children("x-share-origin", base_url=self._base_url)
    self.share_links_panel.raise_event_on_children("x-sharing-busy", busy=self._busy)
    self.links_empty_label.text = "No shared snapshots yet." if self._account_id is not None else "Your share links will appear here after you sign in."
    self.links_empty_label.visible = not bool(self._links)

  def _remember_share(self, share):
    self._links = [share] + [row for row in self._links if row["share_id"] != share["share_id"]]
    self._links.sort(key=lambda row: row["created_at"], reverse=True)
    self._render_links()

  def _set_status(self, text, is_error=False):
    self.share_status.text = text
    self.status_region.classes["is-error"] = is_error

  def _set_busy(self, busy):
    self._busy = busy
    setattr(self.create_button, "enabled", not busy and self._origin_ready and bool(self._summary))
    setattr(self.close_button, "enabled", not busy)
    setattr(self.copy_summary_button, "enabled", not busy and bool(self._summary))
    setattr(self.copy_link_button, "enabled", not busy and bool(self._latest_url))
    setattr(self.native_share_button, "enabled", not busy and bool(self._summary))
    self.create_button.text = "Please wait…" if busy else "Create chart link"
    self.share_links_panel.raise_event_on_children("x-sharing-busy", busy=busy)

  @handle("close_button", "click")
  def close_click(self, **event_args):
    self._generation += 1
    self.share_load_timer.interval = 0
    self.raise_event("x-close-sharing")

  @handle("create_button", "click")
  def create_click(self, **event_args):
    if self._busy or not self._origin_ready:
      return
    context = self._update_summary()
    if context is None:
      return
    self._set_busy(True)
    try:
      if not self._ensure_signed_in():
        self._set_status("Sign-in canceled. No snapshot was created.")
        return
      account_id = self._sync_account()
      self._set_status("Creating your share from the latest sync…")
      result = anvil.server.call(
        "create_performance_share", context["transactions"], context["as_of_date"],
        context["risk_free_rate"], context["selected_symbols"], context["benchmark"],
        context["benchmark_return"], context["start_date"], context["end_date"],
        "private", "My portfolio performance", context["portfolio_id"], True,
      )
      user = anvil.users.get_user()
      if user is None or user.get_id() != account_id:
        self._sync_account()
        return
      if not result["ok"]:
        self._set_status(result["message"], is_error=True)
        return
      share = result["share"]
      self._latest_share_id = share["share_id"]
      self._latest_url = self._base_url + "/#?share=" + share["token"]
      self.share_link.text = self._latest_url
      self.share_link.url = self._latest_url
      self.result_region.visible = True
      self.preview_region.visible = True
      self.snapshot_card.set_snapshot(result["snapshot"])
      self._remember_share(share)
      self._set_status("Chart link ready. It shows percentages only. Copy the link or review the preview below.")
    finally:
      self._set_busy(False)

  def _copy_text(self, text, success, fallback):
    clipboard = getattr(anvil.js.window.navigator, "clipboard", None)
    if clipboard is None:
      self._set_status(fallback)
      return
    try:
      clipboard.writeText(text)
    except anvil.js.ExternalError:
      self._set_status(fallback)
      return
    self._set_status(success)

  def _copy_url(self, url):
    self._copy_text(url, "Share link copied.", "Select the link above to copy it, or open it and copy its address.")

  @handle("copy_summary_button", "click")
  def copy_summary_click(self, **event_args):
    if self._busy or self._update_summary() is None:
      return
    self._copy_text(self._share_text(), "Percentages copied. Paste them into a message or post.",
                    "Your browser blocked copying. Select the percentage summary above and copy it.")

  @handle("copy_link_button", "click")
  def copy_link_click(self, **event_args):
    if self._latest_url:
      self._copy_url(self._latest_url)

  @handle("share_links_panel", "x-copy-share")
  def copy_saved_link(self, token, **event_args):
    if self._base_url:
      self._copy_url(self._base_url + "/#?share=" + token)

  @handle("native_share_button", "click")
  def native_share_click(self, **event_args):
    if self._busy or self._update_summary() is None:
      return
    try:
      anvil.js.window.navigator.share({
        "title": "My Folio performance",
        "text": self._share_text(),
      })
    except anvil.js.ExternalError as error:
      if getattr(error.original_error, "name", "") == "AbortError":
        return
      self._set_status("Sharing is unavailable in this browser. Use Copy percentages instead.")

  @handle("share_links_panel", "x-revoke-share")
  def revoke_share(self, share, **event_args):
    if self._busy or not confirm("Revoke this link? It will stop working for everyone who has it.", title="Revoke shared snapshot"):
      return
    self._set_busy(True)
    try:
      account_id = self._sync_account()
      result = anvil.server.call("revoke_performance_share", share["share_id"], True)
      user = anvil.users.get_user()
      if user is None or user.get_id() != account_id:
        self._sync_account()
        return
      if not result["ok"]:
        self._set_status(result["message"], is_error=True)
        return
      if share["share_id"] == self._latest_share_id:
        self._latest_share_id = None
        self._latest_url = ""
        self.result_region.visible = False
        self.preview_region.visible = False
      self._remember_share(result["share"])
      self._set_status("Share link revoked. New visits cannot view this snapshot.")
    finally:
      self._set_busy(False)

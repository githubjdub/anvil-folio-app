import anvil.js
import anvil.server
import anvil.users
from ._anvil_designer import ShareComposerTemplate
from anvil import *


class ShareComposer(ShareComposerTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._context_getter = self._missing_context
    self._ensure_signed_in = lambda: False
    self._base_url = ""
    self._origin_ready = False
    self._busy = False
    self._latest_share_id = None
    self._latest_url = ""
    self.visibility_dropdown.items = [
      ("Percentages only · hide holdings and amounts", "private"),
      ("Show it all · holdings and real amounts", "full"),
    ]
    self.visibility_dropdown.selected_value = "private"
    self.result_region.visible = False
    self.preview_region.visible = False
    self.live_app_link.visible = False
    self.share_status.text = ""
    self._update_visibility()

  def _missing_context(self):
    raise ValueError("Sync your portfolio before sharing performance.")

  def show_for(self, context_getter, ensure_signed_in):
    self._context_getter = context_getter
    self._ensure_signed_in = ensure_signed_in
    self.visibility_dropdown.selected_value = "private"
    self.title_box.text = "My portfolio performance"
    self._latest_share_id = None
    self._latest_url = ""
    self.result_region.visible = False
    self.preview_region.visible = False
    self._update_visibility()
    self._set_status("Loading sharing options…")
    self._set_busy(True)
    try:
      origin = anvil.server.call("get_performance_share_origin")
      self._base_url = origin["base_url"].rstrip("/")
      self._origin_ready = origin["ok"]
      self.live_app_link.url = self._base_url
      self.live_app_link.visible = not self._origin_ready
      self._refresh_links()
      self._update_period()
      self._set_status(
        origin.get("message", "") if not self._origin_ready else
        "Sign in to create and manage your share links." if anvil.users.get_user() is None else
        "Choose what to reveal, then create your snapshot."
      )
    finally:
      self._set_busy(False)

  def _update_period(self):
    try:
      context = self._context_getter()
    except ValueError as error:
      self.period_label.text = str(error)
      return
    self.period_label.text = "%s – %s · %s · %s" % (
      context["start_date"], context["end_date"], context["benchmark_label"],
      "Total return benchmark" if context["benchmark_return"] == "total" else "Price return benchmark",
    )

  def _refresh_links(self):
    signed_in = anvil.users.get_user() is not None
    links = anvil.server.call("list_performance_shares") if signed_in else []
    self.share_links_panel.items = links
    self.share_links_panel.raise_event_on_children("x-share-origin", base_url=self._base_url)
    self.links_empty_label.text = "No shared snapshots yet." if signed_in else "Your share links will appear here after you sign in."
    self.links_empty_label.visible = not bool(links)

  def _set_status(self, text, is_error=False):
    self.share_status.text = text
    self.status_region.classes["is-error"] = is_error

  def _set_busy(self, busy):
    self._busy = busy
    setattr(self.create_button, "enabled", not busy and self._origin_ready)
    setattr(self.close_button, "enabled", not busy)
    self.title_box.enabled = not busy
    self.visibility_dropdown.enabled = not busy
    setattr(self.copy_link_button, "enabled", not busy and bool(self._latest_url))
    setattr(self.native_share_button, "enabled", not busy and bool(self._latest_url))
    self.create_button.text = "Please wait…" if busy else "Create share link"
    self.share_links_panel.raise_event_on_children("x-sharing-busy", busy=busy)

  def _update_visibility(self):
    full = self.visibility_dropdown.selected_value == "full"
    self.privacy_note.text = (
      "Anyone with this link can see your selected holdings, share quantities, dollar values, invested capital, sales, profit and dividends, alongside percentage returns."
      if full else
      "Only percentage returns, the benchmark comparison, date range, growth chart and risk statistics are shared. Security names, quantities and all dollar amounts are hidden."
    )
    self.privacy_region.classes["is-full"] = full

  @handle("visibility_dropdown", "change")
  def visibility_change(self, **event_args):
    self._update_visibility()

  @handle("close_button", "click")
  def close_click(self, **event_args):
    self.raise_event("x-close-sharing")

  @handle("create_button", "click")
  def create_click(self, **event_args):
    if self._busy or not self._origin_ready:
      return
    try:
      context = self._context_getter()
    except ValueError as error:
      self._set_status(str(error), is_error=True)
      return
    self._set_busy(True)
    try:
      if not self._ensure_signed_in():
        self._set_status("Sign-in canceled. No snapshot was created.")
        return
      self._set_status("Computing your share snapshot…")
      result = anvil.server.call(
        "create_performance_share", context["transactions"], context["as_of_date"],
        context["risk_free_rate"], context["selected_symbols"], context["benchmark"],
        context["benchmark_return"], context["start_date"], context["end_date"],
        self.visibility_dropdown.selected_value, self.title_box.text,
      )
      if not result["ok"]:
        self._set_status(result["message"], is_error=True)
        return
      share = result["share"]
      self._latest_share_id = share.get_id()
      self._latest_url = self._base_url + "/#?share=" + share["token"]
      self.share_link.text = self._latest_url
      self.share_link.url = self._latest_url
      self.result_region.visible = True
      self.snapshot_card.set_snapshot(result["snapshot"])
      self.preview_region.visible = True
      self.native_share_button.visible = bool(getattr(anvil.js.window.navigator, "share", None))
      self._refresh_links()
      self._set_status("Snapshot ready. The preview below shows what others can see. Copy the link to share it.")
    finally:
      self._set_busy(False)

  def _copy_url(self, url):
    clipboard = getattr(anvil.js.window.navigator, "clipboard", None)
    if clipboard is None:
      self._set_status("Clipboard is unavailable. Open the snapshot link, then copy its address.")
      return
    try:
      clipboard.writeText(url)
    except anvil.js.ExternalError:
      self._set_status("Your browser blocked copying. Open the snapshot link, then copy its address.")
      return
    self._set_status("Share link copied.")

  @handle("copy_link_button", "click")
  def copy_link_click(self, **event_args):
    if self._latest_url:
      self._copy_url(self._latest_url)

  @handle("share_links_panel", "x-copy-share")
  def copy_saved_link(self, token, **event_args):
    self._copy_url(self._base_url + "/#?share=" + token)

  @handle("native_share_button", "click")
  def native_share_click(self, **event_args):
    if not self._latest_url:
      return
    try:
      anvil.js.window.navigator.share({
        "title": "My Portfoliora performance",
        "text": "See my portfolio performance and build your own comparison on Portfoliora.",
        "url": self._latest_url,
      })
    except anvil.js.ExternalError as error:
      if getattr(error.original_error, "name", "") == "AbortError":
        return
      self._set_status("Sharing is unavailable in this browser. Use Copy link instead.")

  @handle("share_links_panel", "x-revoke-share")
  def revoke_share(self, share, **event_args):
    if self._busy or not confirm("Revoke this link? It will stop working for everyone who has it.", title="Revoke shared snapshot"):
      return
    self._set_busy(True)
    try:
      result = anvil.server.call("revoke_performance_share", share.get_id())
      if not result["ok"]:
        self._set_status(result["message"], is_error=True)
        return
      if share.get_id() == self._latest_share_id:
        self._latest_url = ""
        self.result_region.visible = False
      self._refresh_links()
      self._set_status("Share link revoked. New visits cannot view this snapshot.")
    finally:
      self._set_busy(False)

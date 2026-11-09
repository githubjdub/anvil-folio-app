from ._anvil_designer import ShareLinkRowTemplate
from anvil import *


class ShareLinkRow(ShareLinkRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._busy = False
    self._base_url = ""
    self._update_controls()

  def _update_controls(self):
    active = not self.item["revoked"]
    self.open_link.visible = active and bool(self._base_url)
    setattr(self.copy_button, "enabled", active and bool(self._base_url) and not self._busy)
    setattr(self.revoke_button, "enabled", active and not self._busy)

  @handle("", "x-share-origin")
  def share_origin(self, base_url, **event_args):
    self._base_url = base_url.rstrip("/")
    self.open_link.url = self._base_url + "/#?share=" + self.item["token"] if self._base_url else ""
    self._update_controls()

  @handle("", "x-sharing-busy")
  def sharing_busy(self, busy, **event_args):
    self._busy = busy
    self._update_controls()

  @handle("copy_button", "click")
  def copy_click(self, **event_args):
    self.parent.raise_event("x-copy-share", token=self.item["token"])

  @handle("revoke_button", "click")
  def revoke_click(self, **event_args):
    self.parent.raise_event("x-revoke-share", share=self.item)

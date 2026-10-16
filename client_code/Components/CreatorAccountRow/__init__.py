from ._anvil_designer import CreatorAccountRowTemplate
from anvil import *


class CreatorAccountRow(CreatorAccountRowTemplate):
  def __init__(self, **properties):
    self._editable = False
    self._editing_membership = False
    super().__init__(**properties)
    self.membership_tier_dropdown.items = [("Free", "free"), ("Premium", "premium")]
    self._editable = True
    self.browse_button.visible = not self.item.get("selected", False)
    self._set_editing_membership(False)

  def display_date(self, value):
    return value.strftime("%b %d, %Y %H:%M UTC") if value is not None else "Not recorded"

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    self._update_controls()

  def _update_controls(self):
    setattr(self.browse_button, "enabled", self._editable)
    setattr(self.manage_membership_button, "enabled", self._editable)
    for component in (self.membership_tier_dropdown, self.membership_save_button,
                      self.membership_cancel_button):
      setattr(component, "enabled", self._editable and self._editing_membership)
    setattr(self.membership_expiry_picker, "enabled", self._editable and
            self._editing_membership and self.membership_tier_dropdown.selected_value == "premium")

  def _set_editing_membership(self, editing):
    self._editing_membership = bool(editing)
    self.membership_editor.visible = self._editing_membership
    self.manage_membership_button.visible = not self._editing_membership
    if self._editing_membership:
      membership = self.item.get("membership") or {}
      self.membership_tier_dropdown.selected_value = membership.get("configured_tier", "free")
      self.membership_expiry_picker.date = membership.get("expires_on")
    self._update_controls()

  @handle("manage_membership_button", "click")
  def manage_membership(self, **event_args):
    if self._editable:
      self._set_editing_membership(True)

  @handle("membership_cancel_button", "click")
  def cancel_membership(self, **event_args):
    if self._editable:
      self._set_editing_membership(False)

  @handle("membership_tier_dropdown", "change")
  def membership_tier_change(self, **event_args):
    if self._editable:
      if self.membership_tier_dropdown.selected_value == "free":
        self.membership_expiry_picker.date = None
      self._update_controls()

  @handle("membership_save_button", "click")
  def save_membership(self, **event_args):
    if not self._editable or not self._editing_membership:
      return
    tier = self.membership_tier_dropdown.selected_value
    expires_on = self.membership_expiry_picker.date if tier == "premium" else None
    self.parent.raise_event("x-save-membership", account_id=self.item["account_id"],
                            tier=tier, expires_on=expires_on)

  @handle("", "x-membership-updated")
  def membership_updated(self, account_id, membership, **event_args):
    if self.item["account_id"] == account_id:
      self.item["membership"] = dict(membership)
      self.refresh_data_bindings()
      self._set_editing_membership(False)

  @handle("browse_button", "click")
  def browse(self, **event_args):
    if self._editable and not self.item.get("selected", False):
      self.parent.raise_event("x-browse-account", account_id=self.item["account_id"])

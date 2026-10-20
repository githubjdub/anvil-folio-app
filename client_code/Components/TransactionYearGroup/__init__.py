from ._anvil_designer import TransactionYearGroupTemplate
from anvil import *
from ... import transaction_groups


class TransactionYearGroup(TransactionYearGroupTemplate):
  """A year section that mounts editors only while it is expanded."""

  def __init__(self, **properties):
    self._editable = False
    self._ready = False
    self._mounting = False
    self._mounted = False
    super().__init__(**properties)
    self.set_event_handler("x-set-editable", self.set_editable)
    self.set_event_handler("x-set-collapsed", self.set_collapsed)
    self.rows_panel.set_event_handler("x-holding-changed", self._holding_changed)
    self.rows_panel.set_event_handler("x-remove-holding", self._remove_holding)
    self._editable = True
    self._show_group()
    self._ready = True

  def transaction_rows(self):
    """Return only the editors currently mounted in this section."""
    return list(self.rows_panel.get_components()) if self._mounted else []

  def commit_inputs(self):
    if self._editable:
      for row in self.transaction_rows():
        row.commit_inputs()

  def _refresh_summary(self):
    summary = transaction_groups.year_summary(self.item["transactions"])
    self.item["summary"] = summary
    self.summary_label.text = summary

  def _mount_rows(self):
    if self._mounted:
      return
    self._mounting = True
    try:
      # These are the original transaction dictionaries, never copies. Mounting
      # preserves each row's own expansion state and any unfinished input.
      self.rows_panel.items = self.item["transactions"]
      self._mounted = True
      for row in self.transaction_rows():
        row.set_editable(self._editable)
    finally:
      self._mounting = False

  def _show_group(self):
    collapsed = bool(self.item.get("collapsed", False))
    self.classes["is-collapsed"] = collapsed
    self.toggle_button.icon = "fa:chevron-right" if collapsed else "fa:chevron-down"
    action = "Expand" if collapsed else "Collapse"
    self.toggle_button.tooltip = "%s %s transactions" % (action, self.item["title"])
    self.rows_panel.visible = not collapsed
    if not collapsed:
      self._mount_rows()
    self._refresh_summary()

  def set_collapsed(self, collapsed, **event_args):
    if not self._editable:
      return
    collapsed = bool(collapsed)
    if collapsed == bool(self.item.get("collapsed", False)):
      return
    if collapsed:
      # A focused numeric editor may still contain an unfinished decimal. Commit
      # before destroying editors, without rebinding or normalizing their values.
      self.commit_inputs()
      self.rows_panel.items = []
      self._mounted = False
    self.item["collapsed"] = collapsed
    self._show_group()
    if self._ready and self.parent is not None:
      self.parent.raise_event("x-year-collapsed", year=self.item["year"], collapsed=collapsed)

  def set_editable(self, editable, **event_args):
    self._editable = False
    self.toggle_button.enabled = editable
    for row in self.transaction_rows():
      row.set_editable(editable)
    self._editable = bool(editable)

  def _holding_changed(self, **event_args):
    if not self._ready or self._mounting or not self._editable:
      return
    # Do not regroup or rebind editors while the user is typing.
    self._refresh_summary()
    if self.parent is not None:
      self.parent.raise_event("x-holding-changed")

  def _remove_holding(self, item, **event_args):
    if self._ready and not self._mounting and self._editable and self.parent is not None:
      self.parent.raise_event("x-remove-holding", item=item)

  @handle("toggle_button", "click")
  def toggle_button_click(self, **event_args):
    self.set_collapsed(not self.item.get("collapsed", False))

from ._anvil_designer import CreatorPortfolioRowTemplate
from anvil import *


class CreatorPortfolioRow(CreatorPortfolioRowTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._editable = True
    self._preview_open = False
    self._preview_page = 0
    self.preview_region.visible = False
    self.transactions_panel.items = []
    self._update_preview_controls()

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._editable = bool(editable)
    setattr(self.import_button, "enabled", self._editable)
    setattr(self.preview_button, "enabled", self._editable)
    self._update_preview_controls()

  def _preview_items(self):
    return self.item.get("preview_transactions", []) if self.item.get("preview_available", False) else []

  def _update_preview_controls(self):
    count = len(self._preview_items())
    setattr(self.preview_previous_button, "enabled", self._editable and self._preview_open and self._preview_page > 0)
    setattr(self.preview_next_button, "enabled", self._editable and self._preview_open and (self._preview_page + 1) * 25 < count)

  def _render_preview(self):
    items = self._preview_items()
    count = len(items)
    self._preview_page = min(self._preview_page, max(0, (count - 1) // 25))
    start = self._preview_page * 25
    self.transactions_panel.items = items[start:start + 25]
    self.preview_range_label.text = "Transactions {}–{} of {}".format(start + 1, min(start + 25, count), count) if count else ""
    self.preview_empty_label.visible = not count
    self.preview_pagination.visible = bool(count)
    self._update_preview_controls()

  @handle("preview_button", "click")
  def toggle_preview(self, **event_args):
    if not self._editable:
      return
    self._preview_open = not self._preview_open
    self.preview_region.visible = self._preview_open
    self.preview_button.text = "Hide transactions" if self._preview_open else "View transactions"
    if self._preview_open:
      self._render_preview()
    else:
      self.transactions_panel.items = []
      self._update_preview_controls()

  @handle("preview_previous_button", "click")
  def previous_transactions(self, **event_args):
    if self._editable and self._preview_open and self._preview_page > 0:
      self._preview_page -= 1
      self._render_preview()

  @handle("preview_next_button", "click")
  def next_transactions(self, **event_args):
    if self._editable and self._preview_open and (self._preview_page + 1) * 25 < len(self._preview_items()):
      self._preview_page += 1
      self._render_preview()

  @handle("import_button", "click")
  def import_click(self, **event_args):
    if self._editable:
      self.parent.raise_event("x-import-portfolio", portfolio_id=self.item["portfolio"].get_id())

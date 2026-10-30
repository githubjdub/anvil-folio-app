import anvil.js
from anvil import *
from ._anvil_designer import PrivacySwitchTemplate
from ... import privacy


class PrivacySwitch(PrivacySwitchTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._update_button()

  def _update_button(self):
    hidden = privacy.is_enabled()
    self.toggle_button.text = "Show amounts" if hidden else "Hide amounts"
    self.toggle_button.icon = "fa:eye" if hidden else "fa:eye-slash"
    self.toggle_button.role = ["appearance-toggle", "privacy-toggle-active"] if hidden else "appearance-toggle"
    self.toggle_button.tooltip = (
      "Privacy mode on · amounts and quantities are hidden. Show amounts to edit hidden fields."
      if hidden else "Hide amounts and quantities for public viewing · percentages stay visible · saved in this browser"
    )
    button = anvil.js.get_dom_node(self.toggle_button).querySelector("button")
    assert button is not None
    button.setAttribute("aria-pressed", "true" if hidden else "false")
    button.setAttribute("aria-label", "Hide sensitive amounts")

  @handle("", "show")
  def form_show(self, **event_args):
    self._update_button()

  @handle("toggle_button", "click")
  def toggle_click(self, **event_args):
    privacy.set_enabled(not privacy.is_enabled())
    self._update_button()

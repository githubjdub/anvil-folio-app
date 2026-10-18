from ._anvil_designer import ThemeSwitchTemplate
from anvil import *
from ... import appearance


class ThemeSwitch(ThemeSwitchTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._update_button()

  def _update_button(self):
    dark = appearance.current_mode() == "dark"
    self.toggle_button.text = "Light mode" if dark else "Dark mode"
    self.toggle_button.icon = "fa:sun-o" if dark else "fa:moon-o"
    self.toggle_button.tooltip = "Switch to %s mode · preference saved in this browser" % ("light" if dark else "dark")

  @handle("", "show")
  def form_show(self, **event_args):
    self._update_button()

  @handle("toggle_button", "click")
  def toggle_click(self, **event_args):
    self.toggle_button.enabled = False
    try:
      appearance.set_mode("light" if appearance.current_mode() == "dark" else "dark")
      self._update_button()
    finally:
      self.toggle_button.enabled = True

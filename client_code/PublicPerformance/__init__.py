import anvil.server
import anvil.users
from ._anvil_designer import PublicPerformanceTemplate
from anvil import *


class PublicPerformance(PublicPerformanceTemplate):
  def __init__(self, token=None, **properties):
    super().__init__(**properties)
    self.share_token = token
    self.return_to_account = False
    self.performance_card.visible = False
    self.unavailable_region.visible = False
    self.loading_region.visible = True
    result = anvil.server.call("get_performance_share", token)
    self.loading_region.visible = False
    if result["ok"]:
      self.performance_card.visible = True
      self.performance_card.set_snapshot(result["snapshot"])
    else:
      self.unavailable_region.visible = True

  @handle("build_comparison_button", "click")
  def build_comparison_button_click(self, **event_args):
    self.return_to_account = False
    set_url_hash("")

  @handle("join_button", "click")
  def join_button_click(self, **event_args):
    if anvil.users.get_user() is None:
      user = anvil.users.login_with_form(allow_cancel=True)
      if user is None:
        return
    self.return_to_account = True
    set_url_hash("")

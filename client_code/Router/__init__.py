from ._anvil_designer import RouterTemplate
from anvil import *


class Router(RouterTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._routed = False

  @handle("", "show")
  def form_show(self, **event_args):
    if self._routed:
      return
    self._routed = True
    route = get_url_hash()
    token = route.get("share") if isinstance(route, dict) else None
    if token is not None:
      open_form("PublicPerformance", token=token)
    else:
      open_form("Form1")

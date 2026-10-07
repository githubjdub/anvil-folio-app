import anvil.js
from ._anvil_designer import RouterTemplate
from anvil import *
from ..Form1 import Form1
from ..PublicPerformance import PublicPerformance
from ..site_tracking import record_page


class Router(RouterTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._routed = False
    self._editor_form = None
    self._hash_listener = anvil.js.report_exceptions(self.hash_changed)

  @handle("", "show")
  def form_show(self, **event_args):
    if self._routed:
      return
    self._routed = True
    # Keep routing after this startup Form has handed over to the editor.
    anvil.js.window.addEventListener("hashchange", self._hash_listener)
    self._open_route()

  def hash_changed(self, event):
    self._open_route()

  def _share_token(self):
    route = get_url_hash()
    return route.get("share") if isinstance(route, dict) else None

  def _open_route(self):
    token = self._share_token()
    active = get_open_form()
    if token is not None:
      if isinstance(active, Form1):
        self._editor_form = active
      if isinstance(active, PublicPerformance) and active.share_token == token:
        return
      reader = PublicPerformance(token=token)
      # A server call can yield while the user changes the URL again.
      if self._share_token() == token:
        open_form(reader)
        record_page("shared")
    else:
      if isinstance(active, Form1):
        self._editor_form = active
        return
      reload_account = isinstance(active, PublicPerformance) and active.return_to_account
      editor = Form1() if reload_account or self._editor_form is None else self._editor_form
      if self._share_token() is None:
        self._editor_form = editor
        open_form(editor)
        record_page("dashboard")

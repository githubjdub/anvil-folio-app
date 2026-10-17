import anvil.js
import anvil.users
from ._anvil_designer import RouterTemplate
from anvil import *
from ..Form1 import Form1
from ..PublicPerformance import PublicPerformance
from ..Subscribe import Subscribe
from ..MySubscription import MySubscription
from ..site_tracking import record_page


class Router(RouterTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._routed = False
    self._route_generation = 0
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

  def _route_key(self):
    route = get_url_hash()
    if isinstance(route, dict):
      if route.get("share") is not None:
        return "shared", route["share"]
      if route.get("page") in ("subscribe", "subscription"):
        return route["page"], None
    return "dashboard", None

  def _open_route(self):
    self._route_generation += 1
    generation = self._route_generation
    route = self._route_key()
    page, token = route
    active = get_open_form()
    if isinstance(active, Form1):
      self._editor_form = active
    if page == "shared":
      if isinstance(active, PublicPerformance) and active.share_token == token:
        return
      reader = PublicPerformance(token=token)
      # Constructors can yield during server calls, even across a round trip
      # back to this URL. Only the latest route may replace the open Form.
      if generation == self._route_generation and self._route_key() == route:
        open_form(reader)
        record_page("shared")
    elif page in ("subscribe", "subscription"):
      form_type = Subscribe if page == "subscribe" else MySubscription
      if isinstance(active, form_type):
        return
      destination = form_type()
      if generation == self._route_generation and self._route_key() == route:
        open_form(destination)
        record_page(page)
    else:
      if isinstance(active, Form1):
        return
      user = anvil.users.get_user()
      account_id = user.get_id() if user is not None else None
      # Keep drafts for the same account, including a guest who just signed in.
      # A different signed-in account must never inherit the cached workspace.
      editor = self._editor_form
      if editor is None or editor.account_id not in (None, account_id):
        editor = Form1()
      else:
        editor.resume_account()
      # Resuming or constructing a Form can yield while account state changes.
      user = anvil.users.get_user()
      current_account_id = user.get_id() if user is not None else None
      if generation == self._route_generation and self._route_key() == route:
        if editor.account_id not in (None, current_account_id):
          self._editor_form = None
          self._open_route()
          return
        self._editor_form = editor
        open_form(editor)
        record_page("dashboard")

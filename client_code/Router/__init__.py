import anvil.js
import anvil.users
from ._anvil_designer import RouterTemplate
from anvil import *
from ..Form1 import Form1
from ..PublicPerformance import PublicPerformance
from ..Subscribe import Subscribe
from ..MySubscription import MySubscription
from ..ProfileHome import ProfileHome
from ..Disclosures import Disclosures
from ..site_tracking import record_page
from ..account_profile import ensure_username, username_prompt_open
from ..appearance import initialize


class Router(RouterTemplate):
  def __init__(self, **properties):
    initialize()
    super().__init__(**properties)
    self._routed = False
    self._route_generation = 0
    self._username_account_id = None
    self._editor_form = None
    self._discarded_editor = None
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
      if route.get("page") in ("subscribe", "subscription", "profile", "disclosures"):
        return route["page"], None
      if route.get("page") == "notifications":
        return "notifications", None
      if route.get("page") == "portfolio":
        return "dashboard", route.get("portfolio")
    return ("profile", None) if anvil.users.get_user() is not None else ("dashboard", None)

  def _open_route(self):
    self._route_generation += 1
    generation = self._route_generation
    # Remembered sessions need the same setup as a fresh login. Navigation may
    # continue underneath an existing modal, without creating a second prompt.
    user = anvil.users.get_user()
    account_id = user.get_id() if user is not None else None
    if account_id is None:
      self._username_account_id = None
    if (account_id is not None and account_id != self._username_account_id
        and self._route_key()[0] != "disclosures" and not username_prompt_open()):
      ready = ensure_username()
      current_user = anvil.users.get_user()
      if ready and current_user is not None and current_user.get_id() == account_id:
        # Usernames are permanent; a successful check remains valid for this
        # account until sign-out. Other routes need not fetch it again.
        self._username_account_id = account_id
      if generation != self._route_generation:
        # A route opened beneath username setup may still show the incomplete
        # profile. Resume the latest destination after setup or sign-out.
        self._open_route()
        return
      if not ready and current_user is not None:
        return
    route = self._route_key()
    page, token = route
    active = get_open_form()
    if isinstance(active, Form1) and active is not self._discarded_editor:
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
    elif page in ("subscribe", "subscription", "profile", "disclosures"):
      form_type = {"subscribe": Subscribe, "subscription": MySubscription, "profile": ProfileHome, "disclosures": Disclosures}[page]
      if isinstance(active, form_type):
        if isinstance(active, ProfileHome):
          active._refresh()
        elif isinstance(active, MySubscription):
          active._refresh_details()
        elif isinstance(active, Subscribe):
          active.plan_comparison.refresh_plan()
        return
      destination = form_type()
      if generation == self._route_generation and self._route_key() == route:
        open_form(destination)
        record_page(page)
    else:
      user = anvil.users.get_user()
      account_id = user.get_id() if user is not None else None
      # Keep drafts for the same account, including a guest who just signed in.
      # A different signed-in account must never inherit the cached workspace.
      editor = self._editor_form
      if editor is None or editor.account_id not in (None, account_id):
        editor = Form1()
      else:
        editor.resume_account()
      if token is not None and generation == self._route_generation and self._route_key() == route:
        if not editor.open_profile_portfolio(token):
          set_url_hash({"page": "profile"})
          return
      # Resuming or constructing a Form can yield while account state changes.
      user = anvil.users.get_user()
      current_account_id = user.get_id() if user is not None else None
      if generation == self._route_generation and self._route_key() == route:
        if (account_id is not None and current_account_id != account_id) or editor.account_id not in (None, current_account_id):
          # Refreshing account details can change editor.account_id while its
          # transactions still belong to the previous account. Discard that
          # workspace, including when it is already the visible Form.
          if get_open_form() is editor:
            self._discarded_editor = editor
          self._editor_form = None
          self._open_route()
          return
        self._editor_form = editor
        if get_open_form() is not editor:
          open_form(editor)
        self._discarded_editor = None
        if page == "notifications":
          editor.open_notifications()

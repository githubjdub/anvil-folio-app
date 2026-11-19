from time import time
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
from ..workspace_cache import CACHE_SECONDS, workspace_revision


class Router(RouterTemplate):
  def __init__(self, **properties):
    initialize()
    super().__init__(**properties)
    self._routed = False
    self._routing = False
    self._route_pending = False
    self._route_generation = 0
    self._username_account_id = None
    self._cache_account_id = None
    self._editor_form = None
    self._editor_owner = None
    self._editor_stamp = None
    self._page_forms = {}
    self._navigation_prepared = False
    self._hash_listener = anvil.js.report_exceptions(self.hash_changed)

  @handle("", "show")
  def form_show(self, **event_args):
    if self._routed:
      return
    self._routed = True
    # This startup Form retains the route Forms after open_form detaches it.
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
    return "dashboard", None

  def _current_account_id(self):
    user = anvil.users.get_user()
    return user.get_id() if user is not None else None

  def _sync_account(self, account_id):
    if account_id != self._cache_account_id:
      # Cache ownership is separate from Form.account_id, which can change
      # during a yielding account refresh. Never relabel another user's draft.
      keep_guest = (self._cache_account_id is None and account_id is not None
                    and self._editor_owner is None)
      if not keep_guest:
        self._editor_form = None
        self._editor_owner = None
      self._editor_stamp = None
      self._page_forms.clear()
      self._navigation_prepared = False
      self._username_account_id = None
      self._cache_account_id = account_id
    workspace_revision()

  def _stamp(self):
    return workspace_revision(), time()

  def _needs_refresh(self, stamp):
    # workspace_revision schedules expired metadata for a silent refresh.
    # Rebind retained pages only when a new snapshot is actually available.
    return stamp is None or stamp[0] != workspace_revision()

  def _is_current(self, generation, route, account_id):
    if self._current_account_id() != account_id:
      self._route_pending = True
      return False
    return generation == self._route_generation and self._route_key() == route

  def _prepare_navigation(self, account_id, portfolio_id=None):
    """Retain one prepared instance per private page for this account."""
    # Public links and the plans/disclosures landing pages stay lightweight.
    if self._navigation_prepared or account_id is None:
      return
    self._navigation_prepared = True
    self.startup_status.text = "Loading your portfolio…"
    if self._editor_form is None:
      editor = Form1(initial_portfolio_id=portfolio_id)
    else:
      editor = self._editor_form
      editor.resume_account()
    if self._current_account_id() != account_id:
      self._route_pending = True
      return
    self._editor_form = editor
    self._editor_owner = account_id
    if editor._busy:
      self._navigation_prepared = False
      return
    self.startup_status.text = "Preparing charts, income and planning tools…"
    editor.prepare_navigation()
    if self._current_account_id() != account_id:
      self._route_pending = True
      return
    self._editor_stamp = self._stamp()
    self.startup_status.text = "Preparing your profile and membership pages…"
    billing = None
    for page, form_type in (("profile", ProfileHome), ("subscription", MySubscription),
                            ("subscribe", Subscribe), ("disclosures", Disclosures)):
      if page in self._page_forms:
        continue
      destination = form_type()
      if isinstance(destination, MySubscription):
        destination.load_details()
        billing = destination._billing_workspace
      elif isinstance(destination, Subscribe) and billing is not None:
        destination.plan_comparison.set_billing_context(billing)
      if self._current_account_id() != account_id:
        self._route_pending = True
        return
      self._page_forms[page] = {"form": destination, "stamp": self._stamp()}

  def _open_route(self):
    self._route_generation += 1
    self._route_pending = True
    if self._routing:
      return
    self._routing = True
    try:
      while self._route_pending:
        self._route_pending = False
        self._open_latest_route(self._route_generation)
    finally:
      self._routing = False

  def _open_latest_route(self, generation):
    # Serialize yielding constructors: rapid hash changes reuse the first
    # completed Form instead of fetching and initializing duplicate workspaces.
    account_id = self._current_account_id()
    self._sync_account(account_id)
    if (account_id is not None and account_id != self._username_account_id
        and self._route_key()[0] != "disclosures" and not username_prompt_open()):
      ready = ensure_username()
      current_account_id = self._current_account_id()
      if ready and current_account_id == account_id:
        self._username_account_id = account_id
      if generation != self._route_generation or current_account_id != account_id:
        self._route_pending = True
        return
      if not ready and current_account_id is not None:
        return
    route = self._route_key()
    page, token = route
    if page in ("dashboard", "notifications", "profile"):
      self._prepare_navigation(account_id, token if page == "dashboard" else None)
      if not self._is_current(generation, route, account_id):
        return
    active = get_open_form()
    if page == "shared":
      if isinstance(active, PublicPerformance) and active.share_token == token:
        return
      reader = PublicPerformance(token=token)
      if self._is_current(generation, route, account_id):
        open_form(reader)
        record_page("shared")
      return
    if page in ("subscribe", "subscription", "profile", "disclosures"):
      self._open_account_page(page, route, generation, account_id)
      return

    editor = self._editor_form
    refreshed = False
    if editor is None:
      editor = Form1()
      refreshed = True
    elif (self._editor_owner != account_id or self._needs_refresh(self._editor_stamp)
          or editor._restore_on_resume):
      editor.resume_account()
      refreshed = not editor._busy
    else:
      editor.apply_appearance()
    if self._current_account_id() != account_id:
      self._route_pending = True
      return
    if editor.account_id not in (None, account_id):
      self._editor_form = None
      self._editor_stamp = None
      self._route_pending = True
      return
    self._editor_form = editor
    self._editor_owner = account_id
    if refreshed and not editor._busy:
      self._editor_stamp = self._stamp()
    if not self._is_current(generation, route, account_id):
      return
    if token is not None and not editor.open_profile_portfolio(token):
      set_url_hash({"page": "profile"})
      return
    if self._is_current(generation, route, account_id):
      if get_open_form() is not editor:
        open_form(editor)
        record_page("dashboard")
      if page == "notifications":
        editor.open_notifications()
      else:
        editor.open_portfolio_page()

  def _open_account_page(self, page, route, generation, account_id):
    entry = self._page_forms.get(page)
    refreshed = entry is None or (page != "disclosures" and self._needs_refresh(entry["stamp"]))
    if entry is not None and page in ("subscription", "subscribe"):
      refreshed = refreshed or time() - entry["stamp"][1] >= CACHE_SECONDS
    if entry is None:
      form_type = {"subscribe": Subscribe, "subscription": MySubscription,
                   "profile": ProfileHome, "disclosures": Disclosures}[page]
      destination = form_type()
    else:
      destination = entry["form"]
      if refreshed:
        if page == "profile":
          destination._refresh()
        elif page == "subscription":
          destination._refresh_details()
        else:
          destination.plan_comparison.refresh_plan()
    if self._current_account_id() != account_id:
      self._route_pending = True
      return
    # MySubscription invalidates only after an explicit billing reconciliation;
    # merely visiting the page must not force every other page to refetch.
    stamp = self._stamp() if refreshed or entry is None else entry["stamp"]
    self._page_forms[page] = {"form": destination, "stamp": stamp}
    if self._is_current(generation, route, account_id) and get_open_form() is not destination:
      open_form(destination)
      record_page(page)

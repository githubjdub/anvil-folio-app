from time import time
import anvil.js
import anvil.users
from ._anvil_designer import RouterTemplate
from anvil import *
from ..site_tracking import record_page
from ..account_profile import ensure_username, username_prompt_open
from ..appearance import initialize
from ..workspace_cache import CACHE_SECONDS, StartupRequest, workspace_revision
from ..deferred_work import defer
from .. import startup_performance


def _create_page(page, **properties):
  # Import only the requested route. Optional pages do not belong on the
  # startup import path; their modules load during idle preparation instead.
  if page == "dashboard":
    startup_performance.mark("portfolio_import_start")
    from ..Form1 import Form1
    startup_performance.mark("portfolio_import_end")
    return Form1(**properties)
  if page == "shared":
    from ..PublicPerformance import PublicPerformance
    return PublicPerformance(**properties)
  if page == "profile":
    from ..ProfileHome import ProfileHome
    return ProfileHome(**properties)
  if page == "subscription":
    from ..MySubscription import MySubscription
    return MySubscription(**properties)
  if page == "subscribe":
    from ..Subscribe import Subscribe
    return Subscribe(**properties)
  if page == "disclosures":
    from ..Disclosures import Disclosures
    return Disclosures(**properties)
  raise ValueError("Unknown page")


class Router(RouterTemplate):
  def __init__(self, **properties):
    startup_performance.mark("router_started")
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
    self._startup_request = None
    self._page_forms = {}
    self._navigation_prepared = False
    self._warming_pages = set()
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
      self._cancel_startup_request()
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

  def _cancel_startup_request(self):
    if self._startup_request is not None:
      self._startup_request.cancel()
      self._startup_request = None

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

  def _prepare_navigation(self, account_id):
    """Retain secondary pages after the requested page is visible."""
    if self._navigation_prepared or account_id is None:
      return
    self._navigation_prepared = True
    for page in ("profile", "subscription", "subscribe", "disclosures"):
      defer(lambda page=page: self._prepare_page(page, account_id),
            lambda: self._current_account_id() == account_id and self._cache_account_id == account_id)

  def _prepare_page(self, page, account_id):
    key = (account_id, page)
    if page in self._page_forms or key in self._warming_pages:
      return
    self._warming_pages.add(key)
    try:
      destination = _create_page(page)
      if self._current_account_id() != account_id or self._cache_account_id != account_id:
        return
      self._page_forms[page] = {"form": destination, "stamp": self._stamp()}
      if page == "subscription":
        getattr(destination, "load_details")()
      elif page == "subscribe":
        subscription = self._page_forms.get("subscription")
        billing = getattr(subscription["form"], "_billing_workspace") if subscription is not None else None
        if billing is not None:
          getattr(destination, "plan_comparison").set_billing_context(billing)
    finally:
      self._warming_pages.discard(key)
    if self._current_account_id() == account_id and self._route_key()[0] == page:
      self._open_route()

  def _editor_ready(self, editor):
    if editor is self._editor_form and editor.account_id == self._current_account_id():
      self._editor_stamp = self._stamp()
      # Apply the latest route if a hash changed during the initial data read.
      self._open_route()

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
    if (self._route_key()[0] in ("dashboard", "notifications", "profile")
        and self._startup_request is None
        and (self._editor_form is None or not self._editor_form._startup_loading)):
      self._prepare_navigation(self._current_account_id())

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
    if page not in ("dashboard", "notifications"):
      self._cancel_startup_request()
    active = get_open_form()
    if page == "shared":
      if getattr(active, "share_token", None) == token:
        return
      reader = _create_page("shared", token=token)
      if self._is_current(generation, route, account_id):
        open_form(reader)
        startup_performance.complete()
        record_page("shared")
      return
    if page in ("subscribe", "subscription", "profile", "disclosures"):
      self._open_account_page(page, route, generation, account_id)
      return

    editor = self._editor_form
    refreshed = False
    if editor is None:
      request = self._startup_request
      if account_id is not None:
        if request is None or request.portfolio_id != token:
          self._cancel_startup_request()
          request = self._startup_request = StartupRequest(account_id, token)
          request.start(lambda: self._open_route() if self._startup_request is request else None)
          return
        if not request.started:
          return
      editor = _create_page("dashboard", initial_portfolio_id=token, defer_load=True,
                            startup_ready=self._editor_ready, startup_request=request)
      self._startup_request = None
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
    if token is not None and not editor._startup_loading and not editor.open_profile_portfolio(token):
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
      editor.prepare_navigation()

  def _open_account_page(self, page, route, generation, account_id):
    entry = self._page_forms.get(page)
    refreshed = entry is None or (page != "disclosures" and self._needs_refresh(entry["stamp"]))
    if entry is not None and page in ("subscription", "subscribe"):
      refreshed = refreshed or time() - entry["stamp"][1] >= CACHE_SECONDS
    if entry is None:
      if (account_id, page) in self._warming_pages:
        return
      destination = _create_page(page)
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
      startup_performance.complete()
      record_page(page)

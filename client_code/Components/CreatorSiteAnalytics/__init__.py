from datetime import date, timedelta
import anvil.server
from ._anvil_designer import CreatorSiteAnalyticsTemplate
from anvil import *
from ... import chart_theme


class CreatorSiteAnalytics(CreatorSiteAnalyticsTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self._chart_mode = None
    self._access_enabled = False
    self._email = ""
    self._generation = 0
    self._parent_editable = True
    self.range_dropdown.items = [("Past 7 days", "7d"), ("Past 30 days", "30d"), ("Past 90 days", "90d"),
                                 ("Past year", "1y"), ("All recorded history", "all"), ("Custom dates", "custom")]
    self.metric_dropdown.items = [("Interest (0–100)", "interest"), ("Visits", "visits"), ("Pageviews", "pageviews")]
    self._reset_dashboard()
    self._update_controls()

  def _reset_dashboard(self):
    self._loaded = False
    self._loading = False
    self._result = None
    self._expanded = False
    self._country_labels = {"XX": "Unknown country"}
    self._page_labels = {}
    self.range_dropdown.selected_value = "30d"
    self.metric_dropdown.selected_value = "interest"
    self.country_dropdown.items = [("Worldwide", None), ("Unknown country", "XX")]
    self.country_dropdown.selected_value = None
    self.page_dropdown.items = [("Whole site", None)]
    self.page_dropdown.selected_value = None
    self.include_my_visits_checkbox.checked = False
    self.start_picker.date = date.today() - timedelta(days=29)
    self.end_picker.date = date.today()
    self.custom_dates_region.visible = False
    self.interest_plot.data = []
    self.interest_plot.layout = chart_theme.layout({})
    self.interest_plot.visible = False
    self.interest_plot.height = "370px"
    self.country_plot.data = []
    self.country_plot.layout = chart_theme.layout({})
    self.country_plot.visible = False
    self.country_plot.height = "330px"
    self.country_rows.items = []
    self.page_rows.items = []
    self.expand_button.text = "Enlarge charts"
    self.range_label.text = ""
    self.chart_caption_label.text = "Open this view to load recorded site traffic."
    self.visits_label.text = self.pageviews_label.text = self.countries_label.text = self.index_label.text = "—"
    self.index_basis_label.text = "Based on visits"
    self.collection_label.text = "Traffic collection begins when this feature is enabled."
    self.updated_label.text = ""
    self.methodology_label.text = ""
    self.empty_label.text = "Open this view to see traffic collected since recording began."
    self.empty_label.visible = True
    self.country_empty_label.text = "No geographic traffic to display yet. Unknown locations remain in the country breakdown."
    self.country_empty_label.visible = True
    self.pages_empty_label.visible = True
    self._set_status("")

  def configure_access(self, enabled, email):
    enabled = bool(enabled)
    email = str(email or "").strip().lower() if enabled else ""
    if enabled == self._access_enabled and email == self._email:
      return
    self._generation += 1
    self._access_enabled = enabled
    self._email = email
    self._reset_dashboard()
    self._update_controls()

  def open_dashboard(self):
    if not self._access_enabled:
      return
    if not self._loaded:
      self.refresh()
    else:
      self.redraw()

  @handle("", "x-set-editable")
  def set_editable(self, editable=True, **event_args):
    self._parent_editable = bool(editable)
    self._update_controls()

  def _can_edit(self):
    return self._access_enabled and self._parent_editable and not self._loading

  def _update_controls(self):
    editable = self._can_edit()
    for component in (self.range_dropdown, self.country_dropdown, self.page_dropdown,
                      self.metric_dropdown, self.include_my_visits_checkbox,
                      self.start_picker, self.end_picker, self.apply_dates_button):
      setattr(component, "enabled", editable)
    setattr(self.expand_button, "enabled", editable and self._result is not None and not self._result["empty"])
    self.country_rows.raise_event_on_children("x-set-editable", editable=editable)
    self.page_rows.raise_event_on_children("x-set-editable", editable=editable)

  def _set_status(self, text, error=False):
    self.status_label.text = text
    self.status_region.classes["is-error"] = bool(error)

  def refresh(self):
    if not self._can_edit():
      return
    range_key = self.range_dropdown.selected_value
    metric = "pageviews" if self.metric_dropdown.selected_value == "pageviews" else "visits"
    start_date = self.start_picker.date if range_key == "custom" else None
    end_date = self.end_picker.date if range_key == "custom" else None
    if range_key == "custom":
      if start_date is None or end_date is None:
        self._set_status("Choose both a start date and an end date.", error=True)
        return
      if start_date > end_date:
        self._set_status("Choose an end date on or after the start date.", error=True)
        return
    generation = self._generation
    self._loading = True
    self._set_status("Loading recorded traffic…")
    self._update_controls()
    try:
      result = anvil.server.call("get_creator_site_analytics", range_key,
                                 self.country_dropdown.selected_value, self.page_dropdown.selected_value,
                                 bool(self.include_my_visits_checkbox.checked), metric, start_date, end_date)
      if generation != self._generation or not self._access_enabled:
        return
      if not result["ok"]:
        self._set_status(result["message"], error=True)
        return
      self._result = result
      self._loaded = True
      self._render_dashboard()
      self._set_status("Recorded traffic updated. The current UTC bucket is still in progress." if any(
        not point["complete"] for point in result["series"]) else "Recorded traffic updated.")
    finally:
      if generation == self._generation:
        self._loading = False
        self._update_controls()

  @handle("range_dropdown", "change")
  def range_changed(self, **event_args):
    self.custom_dates_region.visible = self.range_dropdown.selected_value == "custom"
    if self._can_edit():
      self.refresh()

  @handle("country_dropdown", "change")
  @handle("page_dropdown", "change")
  @handle("metric_dropdown", "change")
  @handle("include_my_visits_checkbox", "change")
  @handle("apply_dates_button", "click")
  @handle("start_picker", "change")
  @handle("end_picker", "change")
  def filters_changed(self, **event_args):
    if self._can_edit():
      self.refresh()

  def _filter_options(self, result):
    country_value = self.country_dropdown.selected_value
    page_value = self.page_dropdown.selected_value
    countries = []
    countries.append(("Worldwide", None))
    codes = set()
    for item in result["available_countries"]:
      code = item["country_code"]
      self._country_labels[code] = item["country_name"]
      if code not in codes:
        countries.append((item["country_name"], code))
        codes.add(code)
    if "XX" not in codes:
      countries.append(("Unknown country", "XX"))
      codes.add("XX")
    if country_value is not None and country_value not in codes:
      countries.append((self._country_labels.get(country_value) or str(country_value), country_value))
    self.country_dropdown.items = countries
    self.country_dropdown.selected_value = country_value
    pages = [("Whole site", None)]
    page_keys = set()
    for item in result["available_pages"]:
      self._page_labels[item["key"]] = item["label"]
      pages.append((item["label"], item["key"]))
      page_keys.add(item["key"])
    if page_value is not None and page_value not in page_keys:
      pages.append((self._page_labels.get(page_value, page_value), page_value))
    self.page_dropdown.items = pages
    self.page_dropdown.selected_value = page_value

  def _render_dashboard(self):
    result = self._result
    if result is None:
      return
    self._filter_options(result)
    summary = result["summary"]
    self.visits_label.text = "{:,}".format(summary["visits"])
    self.pageviews_label.text = "{:,}".format(summary["pageviews"])
    self.countries_label.text = "{:,}".format(summary["countries"])
    self.index_label.text = "{:.0f}".format(summary["interest"])
    self.index_basis_label.text = "Based on " + result["metric"]
    self.range_label.text = "{} to {} · {} buckets · UTC".format(
      result["range"]["start_date"], result["range"]["end_date"], result["range"]["bucket"])
    if self.range_dropdown.selected_value != "custom":
      self.start_picker.date = date(*[int(part) for part in result["range"]["start_date"].split("-")])
      self.end_picker.date = date(*[int(part) for part in result["range"]["end_date"].split("-")])
    first = result["first_collected_at"]
    self.collection_label.text = "Recording began " + first.strftime("%b %d, %Y %H:%M UTC") if first is not None else "No traffic has been recorded yet. Collection starts with visits from now onward."
    self.updated_label.text = "Updated " + result["last_updated_at"].strftime("%b %d, %Y %H:%M UTC") if result["last_updated_at"] is not None else ""
    self.methodology_label.text = result.get("methodology") or ""
    self.empty_label.text = "No recorded traffic matches these filters. Try a wider date range, Worldwide, or Whole site."
    self.empty_label.visible = result["empty"]
    self.pages_empty_label.visible = not result["pages"]
    self.page_rows.items = result["pages"]
    countries = []
    for rank, value in enumerate(result["countries"], 1):
      item = dict(value)
      item["rank"] = rank
      countries.append(item)
    self.country_rows.items = countries
    self._render_charts()

  def _recorded_series(self):
    result = self._result
    if result is None:
      return []
    first = result["first_collected_at"]
    if result["empty"] or first is None:
      return []
    first_date = first.strftime("%Y-%m-%d")
    return [point for point in result["series"] if str(point.get("end_date", point["date"]))[:10] >= first_date]

  def _render_charts(self):
    result = self._result
    if result is None:
      return
    self._chart_mode = chart_theme.MODE
    points = self._recorded_series()
    mode = self.metric_dropdown.selected_value
    names = {"interest": "Interest index", "visits": "Visits", "pageviews": "Pageviews"}
    self.chart_caption_label.text = (
      "100 is the peak recorded visit volume in this range; 0 means no recorded visits in a bucket."
      if mode == "interest" else "Recorded {} per {} bucket. Hover for visits, pageviews, and completion status.".format(mode, result["range"]["bucket"]))
    self.interest_plot.visible = bool(points)
    self.interest_plot.data = []
    if points:
      first_date = result["first_collected_at"].strftime("%Y-%m-%d")
      labels = []
      for point in points:
        status = "Completed UTC bucket" if point["complete"] else "In progress · UTC"
        if str(point["date"])[:10] < first_date:
          status += " · collection began within this bucket"
        labels.append([point["label"], point["visits"], point["pageviews"], point["interest"], status])
      self.interest_plot.data = [{
        "type": "scatter", "mode": "lines+markers", "name": names[mode],
        "x": [max(str(point["date"])[:10], first_date) for point in points],
        "y": [point["interest"] if mode == "interest" else point[mode] for point in points],
        "line": {"color": chart_theme.BENCHMARK, "width": 3}, "marker": {"size": 5},
        "customdata": labels,
        "hovertemplate": "%{customdata[0]}<br>Visits: %{customdata[1]:,}<br>Pageviews: %{customdata[2]:,}<br>Interest: %{customdata[3]:.0f}<br>%{customdata[4]}<extra></extra>",
      }]
      yaxis = {"title": names[mode], "gridcolor": chart_theme.GRID, "rangemode": "tozero", "fixedrange": False}
      if mode == "interest":
        yaxis["range"] = [0, 105]
      self.interest_plot.layout = chart_theme.layout({
        "paper_bgcolor": chart_theme.PANEL, "plot_bgcolor": chart_theme.PANEL,
        "font": {"family": "Inter, sans-serif", "size": 12, "color": chart_theme.MUTED},
        "margin": {"l": 58, "r": 18, "t": 20, "b": 60}, "hovermode": "x unified", "showlegend": False,
        "xaxis": {"type": "date", "showgrid": False, "fixedrange": False}, "yaxis": yaxis,
      })
      self.interest_plot.config = {"displayModeBar": True, "displaylogo": False, "responsive": True,
                                   "scrollZoom": False, "modeBarButtonsToRemove": ["select2d", "lasso2d"]}
    mapped = [item for item in result["countries"] if item.get("iso3") is not None]
    self.country_plot.visible = bool(mapped)
    self.country_empty_label.visible = not mapped
    self.country_plot.data = []
    if mapped:
      self.country_plot.data = [{
        "type": "choropleth", "locations": [item["iso3"] for item in mapped], "locationmode": "ISO-3",
        "z": [item["interest"] for item in mapped], "zmin": 0, "zmax": 100,
        "text": [item["country_name"] for item in mapped],
        "colorscale": [[0, chart_theme.GRID], [0.5, chart_theme.ZERO_LINE], [1, chart_theme.BENCHMARK]],
        "colorbar": {"title": "Relative index", "thickness": 10, "len": 0.7, "outlinewidth": 0,
                     "tickfont": {"color": chart_theme.MUTED}},
        "marker": {"line": {"color": chart_theme.PANEL, "width": 0.7}},
        "customdata": [[item["visits"], item["pageviews"], item["percent"]] for item in mapped],
        "hovertemplate": "%{text}<br>Visits: %{customdata[0]:,}<br>Pageviews: %{customdata[1]:,}<br>Share: %{customdata[2]:.1f}%<br>Relative index: %{z:.0f}<extra></extra>",
      }]
      self.country_plot.layout = chart_theme.layout({
        "paper_bgcolor": chart_theme.PANEL, "font": {"family": "Inter, sans-serif", "size": 11, "color": chart_theme.MUTED},
        "margin": {"l": 0, "r": 0, "t": 15, "b": 0},
        "geo": {"scope": "world", "projection": {"type": "natural earth"}, "showframe": False,
                "showcoastlines": False, "showcountries": True, "countrycolor": chart_theme.GRID,
                "showland": True, "landcolor": chart_theme.BACKGROUND, "bgcolor": chart_theme.PANEL},
      })
      self.country_plot.config = {"displayModeBar": False, "displaylogo": False, "responsive": True, "scrollZoom": False}
    self.redraw()

  def apply_appearance(self):
    if self._result is not None and self._chart_mode != chart_theme.MODE:
      self._render_charts()
      return True
    return False

  def redraw(self):
    if self.apply_appearance():
      return
    if self._result is not None:
      if self.interest_plot.visible:
        self.interest_plot.redraw()
      if self.country_plot.visible:
        self.country_plot.redraw()

  @handle("", "show")
  def shown(self, **event_args):
    self.redraw()

  @handle("expand_button", "click")
  def expand(self, **event_args):
    if not self._can_edit() or self._result is None or self._result["empty"]:
      return
    self._expanded = not self._expanded
    self.interest_plot.height = "540px" if self._expanded else "370px"
    self.country_plot.height = "480px" if self._expanded else "330px"
    self.expand_button.text = "Use compact charts" if self._expanded else "Enlarge charts"
    self.redraw()

  @handle("country_rows", "x-filter-country")
  def filter_country(self, country_code, **event_args):
    if self._can_edit():
      self.country_dropdown.selected_value = country_code
      self.refresh()

  @handle("page_rows", "x-filter-page")
  def filter_page(self, page_key, **event_args):
    if self._can_edit():
      self.page_dropdown.selected_value = page_key
      self.refresh()

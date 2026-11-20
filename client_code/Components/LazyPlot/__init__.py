"""Keep chart construction off the initial portfolio page's critical path."""

import anvil.js
from anvil import Plot
from ._anvil_designer import LazyPlotTemplate


class LazyPlot(LazyPlotTemplate):
  def __init__(self, **properties):
    self._plot = None
    self._figure = {"data": [], "layout": {}}
    self._config = {}
    self._height = 360
    self._dirty = True
    self._initialized = False
    super().__init__(**properties)
    self._initialized = True
    self.style.update(height=self._height, width="100%")

  @property
  def height(self):
    return self._height

  @height.setter
  def height(self, value):
    if isinstance(value, str) and value.replace(".", "", 1).isdigit():
      value = float(value)
    self._height = value
    if self._initialized:
      self.style["height"] = value
    if self._plot is not None:
      self._plot.height = value

  @property
  def config(self):
    return self._config

  @config.setter
  def config(self, value):
    self._config = value
    if self._plot is not None:
      self._plot.config = value

  @property
  def figure(self):
    return self._figure

  @figure.setter
  def figure(self, value):
    self._figure = dict(value) if isinstance(value, dict) else {"data": value or [], "layout": {}}
    self._dirty = True
    self._update_if_visible()

  @property
  def data(self):
    return self._figure.get("data", [])

  @data.setter
  def data(self, value):
    self._figure["data"] = value
    self._dirty = True
    self._update_if_visible()

  @property
  def layout(self):
    return self._figure.get("layout", {})

  @layout.setter
  def layout(self, value):
    self._figure["layout"] = value or {}
    self._dirty = True
    self._update_if_visible()

  def _is_visible(self):
    if not self.visible:
      return False
    bounds = anvil.js.get_dom_node(self).getBoundingClientRect()
    return bounds.width > 0 and bounds.height > 0

  def prepare(self):
    """Warm the chart library after the portfolio is ready, without drawing data."""
    if self._plot is None:
      self._plot = Plot(height=self._height, config=self._config,
                        spacing_above="none", spacing_below="none")
      for event in ("click", "hover", "unhover"):
        self._plot.set_event_handler(event,
          lambda event=event, **args: self._forward(event, args))
      self.add_component(self._plot)
    return self._plot

  def _forward(self, event, args):
    args.pop("sender", None)
    self.raise_event("plot_" + event, **args)

  def _update_if_visible(self):
    if self._initialized and self._is_visible():
      plot = self.prepare()
      if self._dirty:
        plot.figure = self._figure
        self._dirty = False

  def redraw(self):
    if not self._is_visible():
      return
    was_dirty = self._dirty
    self._update_if_visible()
    if not was_dirty and self._plot is not None:
      self._plot.redraw()

  def relayout(self, update):
    layout = self._figure.get("layout")
    if not isinstance(layout, dict):
      layout = {}
      self._figure["layout"] = layout
    layout.update(update)
    if self._plot is not None and not self._dirty:
      self._plot.relayout(update)

  def restyle(self, update, traces):
    plot = self.prepare()
    if self._dirty:
      plot.figure = self._figure
      self._dirty = False
    plot.restyle(update, traces)
    for path, values in update.items():
      parts = path.split(".")
      for position, index in enumerate(traces):
        target = self.data[index]
        for part in parts[:-1]:
          target = target.setdefault(part, {})
        target[parts[-1]] = values[position % len(values)] if isinstance(values, list) else values

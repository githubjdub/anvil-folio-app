"""Charts stay unconstructed during startup and retain data and interactions."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

APP = Path(__file__).resolve().parents[1]


class LazyPlotTests(unittest.TestCase):
    def setUp(self):
        self.bounds = SimpleNamespace(width=0, height=0)
        self.plots = []
        owner = self
        class Template:
            def __init__(self, **props):
                self.style, self.events, self.children = {}, [], []
                self.visible = True
                for name, value in props.items():
                    setattr(self, name, value)
            def add_component(self, plot):
                self.children.append(plot)
            def raise_event(self, name, **args):
                self.events.append((name, args))
        class Plot:
            def __init__(self, **props):
                self.figure = None
                self.handlers = {}
                self.redraws = 0
                self.relayouts, self.restyles = [], []
                self.__dict__.update(props)
                owner.plots.append(self)
            def set_event_handler(self, name, callback):
                self.handlers[name] = callback
            def redraw(self):
                self.redraws += 1
            def relayout(self, update):
                self.relayouts.append(update)
            def restyle(self, update, traces):
                self.restyles.append((update, traces))
        path = APP / 'client_code/Components/LazyPlot/__init__.py'
        tree = ast.parse(path.read_text())
        tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        namespace = dict(LazyPlotTemplate=Template, Plot=Plot,
            anvil=SimpleNamespace(js=SimpleNamespace(get_dom_node=lambda form:
                SimpleNamespace(getBoundingClientRect=lambda: owner.bounds))))
        exec(compile(tree, str(path), 'exec'), namespace)
        self.form = namespace['LazyPlot'](height='520')

    def show(self):
        self.bounds.width, self.bounds.height = 800, 520

    def test_empty_resets_and_hidden_figures_create_no_plot(self):
        self.form.config = {'displayModeBar': False}
        self.form.data = []
        self.form.layout = {'margin': {'l': 0}}
        self.form.figure = {'data': [{'x': [1], 'y': [2]}], 'layout': {'title': 'History'}}
        self.form.redraw()
        self.assertEqual(self.plots, [])
        self.assertEqual(self.form.style['height'], 520)

    def test_first_visible_redraw_applies_cached_figure_once(self):
        figure = {'data': [{'x': [1, 2], 'y': [2, 3]}], 'layout': {'title': 'History'}}
        self.form.figure = figure
        self.show()
        self.form.redraw()
        self.assertEqual(len(self.plots), 1)
        self.assertEqual(self.plots[0].figure, figure)
        self.assertEqual(self.plots[0].redraws, 0)
        self.form.redraw()
        self.assertEqual(len(self.plots), 1)
        self.assertEqual(self.plots[0].redraws, 1)

    def test_background_prepare_warms_library_without_drawing_hidden_history(self):
        self.form.figure = {'data': [{'x': list(range(1000))}], 'layout': {}}
        self.form.prepare()
        self.form.prepare()
        self.assertEqual(len(self.plots), 1)
        self.assertIsNone(self.plots[0].figure)
        self.show()
        self.form.redraw()
        self.assertEqual(self.plots[0].figure, self.form.figure)

    def test_selection_and_hover_updates_remain_in_cached_figure(self):
        self.form.figure = {'data': [{'name': 'A'}, {'name': 'B'}], 'layout': {}}
        self.form.relayout({'shapes': [{'type': 'rect'}]})
        self.show()
        self.form.redraw()
        self.form.restyle({'opacity': [1, .16]}, [0, 1])
        self.assertEqual(self.form.layout['shapes'], [{'type': 'rect'}])
        self.assertEqual([trace['opacity'] for trace in self.form.data], [1, .16])
        self.assertEqual(self.plots[0].restyles, [({'opacity': [1, .16]}, [0, 1])])

    def test_click_hover_and_unhover_forward_points_to_parent(self):
        self.form.prepare()
        for name in ('click', 'hover', 'unhover'):
            points = [{'curve_number': 0, 'point_index': 2}]
            self.plots[0].handlers[name](points=points, sender=self.plots[0])
            self.assertEqual(self.form.events[-1], ('plot_' + name, {'points': points}))


if __name__ == '__main__':
    unittest.main()

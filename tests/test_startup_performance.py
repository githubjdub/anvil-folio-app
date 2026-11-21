"""Browser timing records navigation stages locally, once per launch."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

APP = Path(__file__).resolve().parents[1]


class StartupPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.frames, self.logs = [], []
        def entries(kind):
            if kind == 'paint':
                return [SimpleNamespace(name='first-contentful-paint', startTime=8.1)]
            if kind == 'navigation':
                return [SimpleNamespace(requestStart=1, responseEnd=6, transferSize=1000)]
            return []
        self.window = SimpleNamespace(
            performance=SimpleNamespace(now=lambda: self.now, mark=lambda name: None,
                                        getEntriesByType=entries),
            requestAnimationFrame=lambda fn: self.frames.append(fn),
            console=SimpleNamespace(info=lambda *args: self.logs.append(args), table=lambda *a: None))
        path = APP / 'client_code/startup_performance.py'
        tree = ast.parse(path.read_text())
        tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        self.ns = dict(anvil=SimpleNamespace(js=SimpleNamespace(window=self.window,
                                                               report_exceptions=lambda fn: fn)))
        exec(compile(tree, str(path), 'exec'), self.ns)

    def frame(self):
        callbacks, self.frames = self.frames, []
        for fn in callbacks:
            fn(self.now)

    def test_report_separates_navigation_paint_and_phase_durations(self):
        self.ns['mark']('portfolio_build_start')
        self.now = 35
        self.ns['mark']('portfolio_template_ready')
        self.now = 40
        self.ns['mark']('portfolio_shell_ready')
        self.ns['complete']()
        self.assertFalse(hasattr(self.window, 'folioStartupTimings'))
        self.frame()
        self.now = 60
        self.frame()
        report = self.window.folioStartupTimings
        self.assertEqual(report['phases_ms'], {'template_construction': 25, 'form_setup': 5})
        self.assertEqual(report['navigation_ms']['ready_frame'], 60)
        self.assertEqual(report['browser_paint_ms']['first-contentful-paint'], 8.1)
        self.assertEqual(report['document_ms'], 5)
        self.assertEqual(len(self.logs), 1)

    def test_revisits_and_refreshes_do_not_replace_startup_measurements(self):
        self.ns['after_frame']('portfolio_frame')
        self.ns['after_frame']('portfolio_frame')
        self.assertEqual(len(self.frames), 1)
        self.ns['mark']('portfolio_build_start')
        self.now = 20
        self.ns['mark']('portfolio_build_start')
        self.ns['complete']()
        self.frame()
        self.frame()
        report = self.window.folioStartupTimings
        self.assertEqual(report['navigation_ms']['portfolio_build_start'], 10)
        self.ns['complete']()
        self.ns['mark']('analysis_apply_start')
        self.assertEqual(len(self.logs), 1)
        self.assertNotIn('analysis_apply_start', report['navigation_ms'])

    def test_values_have_a_separate_paint_mark_before_history_finishes(self):
        self.ns['mark']('values_load_start')
        self.now = 20
        self.ns['mark']('values_load_end')
        self.ns['after_frame']('values_frame')
        self.frame()
        self.now = 30
        self.frame()
        self.now = 100
        self.ns['complete']()
        self.frame()
        self.frame()
        report = self.window.folioStartupTimings
        self.assertEqual(report['phases_ms']['saved_values_load'], 10)
        self.assertEqual(report['navigation_ms']['values_frame'], 30)
        self.assertEqual(report['navigation_ms']['ready_frame'], 100)


if __name__ == '__main__':
    unittest.main()

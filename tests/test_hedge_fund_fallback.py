"""Visible-page polling and SEC update safety at the Anvil boundary."""
import ast
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
import unittest

APP = Path(__file__).resolve().parents[1]


class Controls(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.names = set()
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        self.names.update(value for key, value in attributes if key in ('name', 'anvil:name'))


class Control:
    def __init__(self):
        self.text = ''
        self.visible = self.enabled = True
        self.items = []
        self.selected_value = None
        self.classes = {}
        self.interval = 0

    def raise_event_on_children(self, *args, **kwargs):
        pass


class Harness:
    def __init__(self):
        self.calls = []
        self.hook = None
        self.response = {'ok': True, 'state': 'running', 'message': 'One step finished'}
        harness = self
        path = APP / 'client_code/Components/HedgeFundComparison'
        names = Controls((path / 'form_template.html').read_text()).names

        class Template:
            def __init__(self, **properties):
                for name in names:
                    setattr(self, name, Control())

        def call(name, *args):
            self.calls.append((name, args))
            if self.hook:
                hook, self.hook = self.hook, None
                hook()
            return self.response

        namespace = dict(HedgeFundComparisonTemplate=Template, handle=lambda *args: lambda fn: fn,
                         chart_theme=SimpleNamespace(layout=lambda value: value, MODE='light'),
                         anvil=SimpleNamespace(server=SimpleNamespace(call=call, call_s=call)))
        tree = ast.parse((path / '__init__.py').read_text())
        tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
        exec(compile(tree, str(path), 'exec'), namespace)
        self.form = namespace['HedgeFundComparison']()
        self.form._opened = True
        self.form.fund_dropdown.selected_value = 'Fixture'
        self.form._follow_update({'ok': True, 'state': 'running', 'job_id': 'page-job'})


class PagePollingTests(unittest.TestCase):
    def test_hidden_panel_pauses_and_show_resumes_same_job(self):
        h = Harness()
        self.assertEqual(h.form.update_timer.interval, 5)
        h.form.hide_view()
        self.assertEqual(h.form.update_timer.interval, 0)
        h.form._follow_update({'ok': True, 'state': 'running', 'job_id': 'page-job'})
        self.assertEqual(h.form.update_timer.interval, 0)
        h.form.show_view()
        self.assertEqual(h.form.update_timer.interval, 5)
        self.assertEqual(h.calls, [])

    def test_hidden_and_closed_panels_never_advance_provider_work(self):
        h = Harness()
        h.form.hide_view()
        h.form.poll_update()
        h.form._view_visible = True
        h.form._opened = False
        h.form.poll_update()
        self.assertEqual(h.calls, [])

    def test_inflight_step_finishing_after_hide_does_not_restart_timer(self):
        h = Harness()
        h.hook = h.form.hide_view
        h.form.poll_update()
        self.assertEqual(len(h.calls), 1)
        self.assertEqual(h.form.update_timer.interval, 0)
        self.assertFalse(h.form._polling)

    def test_changed_context_discards_inflight_update_status(self):
        h = Harness()
        h.hook = h.form.reset_context
        h.form.poll_update()
        self.assertIsNone(h.form._update_job_id)
        self.assertEqual(h.form.update_timer.interval, 0)
        self.assertEqual(h.form.status_label.text, '')

    def test_provider_error_preserves_previous_comparison(self):
        h = Harness()
        previous = h.form._result = object()
        h.response = {'ok': False, 'message': 'Provider unavailable'}
        h.form.poll_update()
        self.assertIs(h.form._result, previous)
        self.assertEqual(h.form.status_label.text, 'Provider unavailable')
        self.assertIsNone(h.form._update_job_id)

    def test_manual_refresh_preserves_previous_comparison_while_running(self):
        h = Harness()
        h.form._stop_update_polling()
        previous = h.form._result = object()
        h.response = {'ok': True, 'state': 'running', 'job_id': 'page-next', 'message': 'Resuming'}
        h.form.update_filings()
        self.assertIs(h.form._result, previous)
        self.assertEqual(h.form._update_job_id, 'page-next')
        self.assertEqual(h.form.update_timer.interval, 5)

    def test_unexpected_transport_error_raises_and_releases_poll_guard(self):
        h = Harness()
        def fail():
            raise RuntimeError('transport')
        h.hook = fail
        with self.assertRaisesRegex(RuntimeError, 'transport'):
            h.form.poll_update()
        self.assertFalse(h.form._polling)
        self.assertEqual(h.form._update_job_id, 'page-job')


class FilingSelectionTests(unittest.TestCase):
    def setUp(self):
        class SourceError(Exception):
            pass
        self.error = SourceError
        tree = ast.parse((APP / 'server_code/hedge_fund_refresh.py').read_text())
        tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_select_records']
        namespace = dict(date=date, _data=SimpleNamespace(_CURRENT_FROM={'Pershing': date(2026, 6, 30)}, _SourceError=SourceError))
        exec(compile(tree, 'hedge_fund_refresh', 'exec'), namespace)
        self.select = namespace['_select_records']

    def record(self, period, priority=0, amendment=False, accession='1'):
        return dict(period_end=period, priority=priority, form='13F-HR/A' if amendment else '13F-HR',
                    filed_at=period, accession=accession)

    def test_reporting_entity_transition_keeps_its_own_amendment_chain(self):
        records = [self.record('2026-03-31', 0), self.record('2026-03-31', 1),
                   self.record('2026-06-30', 0), self.record('2026-06-30', 1),
                   self.record('2026-06-30', 0, True, '2')]
        result = self.select('Pershing', records)
        self.assertEqual([(item['period_end'], item['priority'], item['form']) for item in result],
                         [('2026-03-31', 1, '13F-HR'), ('2026-06-30', 0, '13F-HR'), ('2026-06-30', 0, '13F-HR/A')])

    def test_amendment_alone_cannot_create_an_effective_quarter(self):
        with self.assertRaises(self.error):
            self.select('Other', [self.record('2026-06-30', amendment=True)])

    def test_oversized_amendment_chain_fails_instead_of_truncating_history(self):
        records = [self.record('2026-06-30')] + [self.record('2026-06-30', amendment=True, accession=str(n)) for n in range(40)]
        with self.assertRaises(self.error):
            self.select('Other', records)


if __name__ == '__main__':
    unittest.main()

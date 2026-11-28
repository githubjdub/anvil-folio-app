"""First values stay independent of history imports; later summaries stay exact."""
import ast
import builtins
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest

from client_test_loader import client_module, PACKAGE
from test_portfolio_workspace import Control


APP = Path(__file__).resolve().parents[1]
display = client_module('portfolio_display')


class PortfolioDisplayTests(unittest.TestCase):
    def summary_form(self):
        path = APP / 'client_code/Form1/__init__.py'
        tree = ast.parse(path.read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_render_portfolio_summary')
        imports = []

        def import_client(name, globals=None, locals=None, fromlist=(), level=0):
            if level:
                imports.append(name)
            return builtins.__import__(name, globals, locals, fromlist, level)

        namespace = dict(__package__=PACKAGE + '.Form1', empty_summary=display.empty_summary,
                         chart_theme=SimpleNamespace(TEXT='neutral', POSITIVE='gain', NEGATIVE='loss'),
                         __builtins__=dict(vars(builtins), __import__=import_client))
        tree.body = [method]
        exec(compile(tree, str(path), 'exec'), namespace)
        form = SimpleNamespace(_history=None, _startup_overview=None,
            _format_currency=lambda value: '$%.2f' % value,
            _format_percent=lambda value: '%+.2f%%' % (100 * value),
            _update_profile_ranking=lambda: None)
        for node in ast.walk(method):
            if isinstance(node, ast.Attribute) and node.attr.startswith('summary_'):
                setattr(form, node.attr, Control())
        form.render = lambda: namespace['_render_portfolio_summary'](form)
        return form, imports

    def test_empty_and_saved_values_render_without_history_imports(self):
        form, imports = self.summary_form()
        form.render()
        self.assertEqual(form.summary_value_label.text, '—')
        self.assertEqual(form.summary_holdings_label.text, '—')
        summary = display.empty_summary()
        summary.update(value=12345, holding_count=8, as_of='2026-11-27',
                       prior_quarter_return=.12, long_return=-.03, top20_weight=.9,
                       return_is_provisional=True)
        original = copy.deepcopy(summary)
        form._startup_overview = {'summary': summary}
        # A saved overview takes precedence even while history is being replaced.
        form._history = {'not_ready': True}
        form.render()
        self.assertEqual(form.summary_value_label.text, '$12345.00')
        self.assertEqual(form.summary_holdings_label.text, '8')
        self.assertEqual(form.summary_quarter_label.text, '+12.00%')
        self.assertEqual(form.summary_period_label.text, '-3.00%')
        self.assertEqual(form.summary_period_label.foreground, 'loss')
        self.assertEqual(form.summary_concentration_label.text, '90.0%')
        self.assertIn('provisional', form.summary_date_label.text)
        self.assertEqual(summary, original)
        self.assertEqual(imports, [])

    def test_history_summary_loads_calculator_and_account_reset_clears_values(self):
        from test_portfolio_summary import history, summarize_portfolio
        form, imports = self.summary_form()
        form._history = history()
        expected = summarize_portfolio(form._history)
        form.render()
        self.assertEqual(imports, ['portfolio_summary'])
        self.assertEqual(form.summary_value_label.text, form._format_currency(expected['value']))
        self.assertEqual(form.summary_period_label.text, form._format_percent(expected['long_return']))
        form._history = None
        form.render()
        self.assertEqual(form.summary_value_label.text, '—')
        self.assertEqual(form.summary_period_label.text, '—')
        self.assertEqual(form.summary_period_label.foreground, 'neutral')
        self.assertEqual(form.summary_date_label.text, 'Sync to calculate')
        self.assertEqual(imports, ['portfolio_summary'])

    def test_empty_summaries_are_independent_and_match_full_calculator(self):
        summary = display.empty_summary()
        summary['value'] = 123
        self.assertIsNone(display.empty_summary()['value'])
        self.assertEqual(display.empty_summary(), client_module('portfolio_summary').summarize_portfolio(None))


if __name__ == '__main__':
    unittest.main()

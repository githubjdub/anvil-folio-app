"""Dividend date semantics and local controls; no market or billing requests."""
import ast
from datetime import date
from html.parser import HTMLParser
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dividend_periods', APP / 'client_code/dividend_periods.py')
periods = importlib.util.module_from_spec(spec)
spec.loader.exec_module(periods)


def event(ex_date, payment, amount=10, symbol='TEST', **extra):
    return dict(ex_date=ex_date, payment_date=payment, earned=amount,
                symbol=symbol, amount=1, status='Paid (modeled)', **extra)


class PeriodTests(unittest.TestCase):
    def test_payment_and_ex_date_cross_year(self):
        rows = [event('2025-12-20', '2026-01-05', 15),
                event('2026-11-02', '2026-12-01', 20),
                event('2026-11-03', None, 30)]
        cash = periods.dividend_window(rows, date(2026, 11, 13))
        earned = periods.dividend_window(rows, date(2026, 11, 13), basis='ex')
        self.assertEqual(cash['total'], 15)
        self.assertEqual(earned['total'], 50)
        self.assertEqual(cash['unconfirmed_count'], 1)

    def test_month_end_and_leap_year(self):
        cases = [('2024-03-31', '1M', '2024-02-29'),
                 ('2025-03-31', '1M', '2025-02-28'),
                 ('2024-02-29', '1Y', '2023-02-28'),
                 ('2026-11-13', '3M', '2026-08-13'),
                 ('2026-11-13', '6M', '2026-05-13'),
                 ('2026-11-13', '3Y', '2023-11-13'),
                 ('2026-11-13', '5Y', '2021-11-13')]
        for end, timeframe, expected in cases:
            with self.subTest(end=end, timeframe=timeframe):
                self.assertEqual(periods.dividend_window([], end, timeframe)['start'], expected)

    def test_custom_boundaries_and_newest_payment_sort(self):
        rows = [event('2026-01-01', '2026-02-03', 3),
                event('2026-01-20', '2026-02-01', 1),
                event('2026-01-19', '2026-01-31', 99)]
        window = periods.dividend_window(rows, '2026-11-13', 'CUSTOM',
            custom_start=date(2026, 2, 1), custom_end=date(2026, 2, 3))
        self.assertEqual(window['total'], 4)
        self.assertEqual([row['payment_date'] for row in window['rows']], ['2026-02-03', '2026-02-01'])

    def test_invalid_custom_dates(self):
        for start, end in [(None, None), ('2026-03-02', '2026-03-01'),
                           ('2026-01-01', '2026-12-01')]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                periods.dividend_window([], '2026-11-13', 'CUSTOM', custom_start=start, custom_end=end)

    def test_unsupported_cash_never_counted(self):
        rows = [event('2026-01-10', '2026-01-20', None, unsupported=True)]
        cash = periods.dividend_window(rows, '2026-11-13')
        earned = periods.dividend_window(rows, '2026-11-13', basis='ex')
        self.assertEqual(cash['total'], 0)
        self.assertEqual(cash['rows'], [])
        self.assertIsNone(earned['total'])
        self.assertEqual(earned['unavailable_count'], 1)

    def test_zero_months_and_bucket_totals(self):
        rows = [event('2026-01-10', '2026-01-15', 10), event('2026-03-10', '2026-03-15', 25)]
        window = periods.dividend_window(rows, '2026-03-31', history_start='2025-01-01')
        self.assertEqual([row['amount'] for row in window['chart']], [10, 0, 25])
        self.assertEqual(sum(row['amount'] for row in window['chart']), window['total'])

    def test_long_history_and_source_are_preserved(self):
        rows = [event('2020-01-10', '2020-01-15', 10)]
        saved = [dict(row) for row in rows]
        quarterly = periods.dividend_window(rows, '2026-11-13', 'ALL')
        yearly = periods.dividend_window(rows, '2026-11-13', 'ALL', history_start='2000-01-01')
        self.assertEqual(quarterly['grouping'], 'quarter')
        self.assertEqual(yearly['grouping'], 'year')
        self.assertEqual(rows, saved)
        self.assertEqual(sum(row['amount'] for row in yearly['chart']), yearly['total'])

    def test_empty_period_and_future_entitlement(self):
        rows = [event('2027-01-01', '2027-01-10', 50)]
        window = periods.dividend_window(rows, '2026-11-13', history_start='2024-01-01')
        self.assertEqual(window['total'], 0)
        self.assertFalse(window['rows'])
        before = periods.dividend_window([], '2026-11-13', 'CUSTOM',
            custom_start='2020-01-01', custom_end='2020-12-31', history_start='2024-01-01')
        self.assertEqual(before['chart'], [])


class Names(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.names = set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('name', 'anvil:name'):
                self.names.add(value)


class Control(SimpleNamespace):
    def __init__(self):
        super().__init__(text='', visible=True, enabled=True, classes={}, date=None,
                         items=[], data=[], selected_value=None)

    def set_event_handler(self, *args, **kwargs):
        pass

    def raise_event_on_children(self, *args, **kwargs):
        pass


def form_fixture():
    directory = APP / 'client_code/Components/IncomeTracker'
    names = Names((directory / 'form_template.html').read_text()).names

    class Template:
        def __init__(self, **properties):
            self.classes = {}
            for name in names:
                setattr(self, name, Control())

        def set_event_handler(self, *args, **kwargs):
            pass

    def forbidden_call(*args):
        raise AssertionError('Date controls must not make a server request')

    theme = SimpleNamespace(MODE='light', POSITIVE='#008800',
                            layout=lambda value: value, redraw=lambda *args: (600, 300))
    namespace = dict(IncomeTrackerTemplate=Template, chart_theme=theme,
        handle=lambda *args: (lambda fn: fn), dividend_window=periods.dividend_window,
        RANGES=periods.RANGES, anvil=SimpleNamespace(server=SimpleNamespace(call=forbidden_call)))
    tree = ast.parse((directory / '__init__.py').read_text())
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    exec(compile(tree, str(directory), 'exec'), namespace)
    form = namespace['IncomeTracker']()
    form._as_of = '2026-11-13'
    form._transactions = [dict(trade_date='2025-01-01')]
    form._report = {'dividends': {'ledger': [event('2026-01-%02d' % day, '2026-01-%02d' % day)
                                            for day in range(1, 26)]}}
    form._render_dividends()
    return form


class ControlTests(unittest.TestCase):
    def test_timeframe_changes_total_chart_and_pagination_without_rpc(self):
        form = form_fixture()
        self.assertEqual(form.paid_label.text, '$250.00')
        form._move_page('ledger', 1)
        self.assertEqual(len(form.ledger_panel.items), 5)
        form.dividend_range_click(form.dividend_range_1m_button)
        self.assertEqual(form._ledger_page, 0)
        self.assertEqual(form.paid_label.text, '$0.00')
        self.assertFalse(form.ledger_navigation.visible)
        self.assertFalse(form.ledger_next_button.enabled)
        self.assertEqual(form.dividend_range_1m_button.role, 'chart-range-active')

    def test_custom_validation_clears_stale_totals(self):
        form = form_fixture()
        form.dividend_range_click(form.dividend_range_custom_button)
        self.assertTrue(form.dividend_custom_region.visible)
        form.dividend_start_picker.date = '2026-12-01'
        form.dividend_period_change()
        self.assertIsNone(form._dividend_window)
        self.assertFalse(form.dividend_summary_region.visible)
        self.assertEqual(form.dividend_history_plot.data, [])
        self.assertEqual(form.ledger_panel.items, [])
        self.assertTrue(form.dividend_period_region.classes['is-error'])
        form.dividend_range_click(form.dividend_range_all_button)
        self.assertEqual(form.paid_label.text, '$250.00')
        self.assertFalse(form.dividend_period_region.classes['is-error'])

    def test_basis_changes_and_unconfirmed_rows_are_visible(self):
        form = form_fixture()
        form._report['dividends']['ledger'] = [event('2026-10-01', None, 20)]
        form.dividend_basis_dropdown.selected_value = 'ex'
        form.dividend_period_change()
        self.assertEqual(form.paid_label.text, '$20.00')
        self.assertIn('unconfirmed', form.ledger_panel.items[0]['dates'])
        self.assertTrue(form.dividend_unresolved_label.visible)

    def test_busy_controls_do_not_change_period(self):
        form = form_fixture()
        form._busy = True
        form._update_controls()
        form.dividend_range_click(form.dividend_range_1m_button)
        self.assertEqual(form._dividend_range, 'YTD')
        self.assertFalse(form.dividend_range_1m_button.enabled)
        self.assertFalse(form.dividend_basis_dropdown.enabled)


if __name__ == '__main__':
    unittest.main()

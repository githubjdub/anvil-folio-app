"""Saved startup avoids hidden editor work while editing and details stay usable."""
import ast
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_portfolio_workspace import Control, Portfolio, WorkspaceHarness

APP = Path(__file__).resolve().parents[1]


class TransactionPreparationTests(unittest.TestCase):
    def restored(self):
        h = WorkspaceHarness(transaction_render=True)
        portfolio = Portfolio('restored')
        portfolio.update(risk_free_rate=4.5, transactions=[{
            'ticker': 'TEST', 'trade_date': '2026-01-02', 'action': 'Buy',
            'shares': 5, 'trade_price': 185,
        }])
        h.form._apply_portfolio(portfolio, h.result)
        return h

    def test_restore_does_not_build_editors_and_idle_builds_them_once(self):
        h = self.restored()
        form = h.form
        self.assertEqual(form._readout_page, 'overview')
        self.assertEqual(form.holdings_panel.items_assignments, 0)
        self.assertEqual(form.transaction_years_panel.items_assignments, 0)
        self.assertTrue(form._transaction_rows_dirty)
        # Saving before background preparation reads the restored dictionaries.
        self.assertEqual(form._read_transactions()[0]['shares'], 5)
        h.run_idle()
        self.assertFalse(form._transaction_rows_dirty)
        self.assertEqual(form.transaction_years_panel.items_assignments, 1)
        rows = [row for group in form.transaction_years_panel.items for row in group['transactions']]
        self.assertEqual(rows, form.transactions)
        self.assertEqual(rows[0]['current_price'], 250)
        for page in ('transactions', 'overview', 'transactions'):
            form._select_readout_page(page)
        self.assertEqual(form.transaction_years_panel.items_assignments, 1)

    def test_edit_before_idle_prepares_once_and_uses_latest_rows(self):
        h = self.restored()
        h.form.transactions_tab_click()
        self.assertFalse(h.form._transaction_rows_dirty)
        self.assertEqual(h.form.transaction_years_panel.items_assignments, 1)
        h.run_idle()
        self.assertEqual(h.form.transaction_years_panel.items_assignments, 1)
        # An active edit/rebuild must still immediately update the editor view.
        h.form.transactions.append(h.form._new_transaction('NEW', date.today(), 'Buy', '2', 10))
        h.form._refresh_transactions()
        self.assertEqual(h.form.transaction_years_panel.items_assignments, 2)
        self.assertTrue(any(row['ticker'] == 'NEW' for group in h.form.transaction_years_panel.items
                            for row in group['transactions']))

    def test_invalid_input_opens_editors_before_focusing_the_error(self):
        h = self.restored()
        h.form.transactions[0]['shares'] = '-1'
        with self.assertRaisesRegex(ValueError, 'Row 1: shares'):
            h.form._read_transactions()
        h.form._show_entry_order_for_error('Row 1: shares must be greater than zero.')
        self.assertEqual(h.form._readout_page, 'transactions')
        self.assertFalse(h.form._transaction_rows_dirty)
        self.assertEqual(h.form.holdings_panel.items[0]['shares'], '-1')

    def test_replaced_rows_cannot_commit_previous_portfolio_editors(self):
        h = self.restored()
        commits = []
        row = h.namespace['HoldingRow']()
        row.commit_inputs = lambda: commits.append('stale editor')
        h.form.holdings_panel.children = [row]
        h.form._commit_transaction_inputs()
        self.assertEqual(commits, [])

    def test_hidden_editor_keeps_original_chart_indices_across_blank_rows(self):
        h = self.restored()
        h.form.transactions.insert(0, h.form._new_transaction())
        h.form._refresh_transactions()
        clean = h.form._read_transactions()
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean[0]['_transaction_index'], 1)
        self.assertEqual(h.form.transaction_years_panel.items_assignments, 0)


class HoldingsDetailsTests(unittest.TestCase):
    def test_share_details_mount_on_first_open_and_remain_reusable(self):
        source = APP / 'client_code/Components/PortfolioHoldingTableRow/__init__.py'
        tree = ast.parse(source.read_text())
        tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        history = [{'label_text': 'Q1 2026', 'shares_text': '5', 'change_text': '—',
                    'change': None, 'date': '2026-03-31'}]
        class Template:
            def __init__(self, **properties):
                self.item = dict(weight=.5, is_open=True, shares_text='5',
                                 share_history=history, **{'return': .1})
                self.classes = {}
                for name in ('weight_fill', 'return_cell', 'share_bars', 'share_history',
                             'history_empty_label', 'history_summary'):
                    control = Control()
                    control.style = {}
                    setattr(self, name, control)
        namespace = dict(PortfolioHoldingTableRowTemplate=Template,
                         display_share_history=lambda rows: rows)
        exec(compile(tree, str(source), 'exec'), namespace)
        row = namespace['PortfolioHoldingTableRow']()
        self.assertEqual(row.share_bars.items, history)
        self.assertEqual(row.share_history.items_assignments, 0)
        for opened in (False, True, False, True):
            row.history_toggle(SimpleNamespace(currentTarget=SimpleNamespace(open=opened)))
        self.assertEqual(row.share_history.items, history)
        self.assertEqual(row.share_history.items_assignments, 1)
        self.assertIn('Q1 2026: 5', row.history_summary.attrs['title'])


if __name__ == '__main__':
    unittest.main()

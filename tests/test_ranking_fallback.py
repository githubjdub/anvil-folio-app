"""Visitor refresh keeps bounded work and never publishes partial cohorts."""
import ast
import copy
from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid


class Row(dict):
    def __getitem__(self, key):
        return self.get(key)

    def get_id(self):
        return self['_id']


class Table:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.fetched = []

    def get(self, **filters):
        return next((row for row in self.rows if all(row[key] == value for key, value in filters.items())), None)

    def add_row(self, **values):
        row = Row(values, _id=uuid.uuid4().hex)
        self.rows.append(row)
        return row

    def search(self, *orders, **filters):
        rows = self.rows
        for key, value in filters.items():
            rows = [row for row in rows if row[key] > value[1]] if isinstance(value, tuple) else [row for row in rows if row[key] == value]
        for key in orders:
            rows = sorted(rows, key=lambda row: row[key])
        owner = self

        class Search:
            def __getitem__(self, index):
                found = rows[index]
                owner.fetched.append(len(found))
                return found
        return Search()


def load():
    tables = SimpleNamespace(investor_profiles=Table(), profile_rankings=Table())
    source = Path(__file__).resolve().parents[1] / 'server_code/investor_profiles.py'
    tree = ast.parse(source.read_text())
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    namespace = dict(date=date, datetime=datetime, timedelta=timedelta, timezone=timezone,
                     math=math, uuid=uuid, Any=object, app_tables=tables,
                     in_transaction=lambda fn: fn, order_by=lambda key: key,
                     q=SimpleNamespace(greater_than=lambda val: ('gt', val)),
                     anvil=SimpleNamespace(server=SimpleNamespace(
                         callable=lambda **kwargs: lambda fn: fn, background_task=lambda fn: fn)))
    exec(compile(tree, str(source), 'exec'), namespace)
    # Eligibility itself is exercised in the real server fixture. Here each
    # profile's materialized return stands in for already-verified eligibility.
    namespace['_ranking_candidate'] = lambda row, quarter: (
        dict(user_id=row['owner'].get_id(), **copy.deepcopy(row['candidate'])) if row['ranking_opt_in'] else None)
    return tables, namespace


class RankingFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tables, self.ns = load()
        self.quarter = self.ns['previous_quarter']()[2]

    def add_profiles(self, count, legacy=False):
        rows = []
        for index in range(count):
            rows.append(self.tables.investor_profiles.add_row(
                owner=Row(_id='user%s' % index), ranking_opt_in=True,
                ranking_key=None if legacy else '%08d' % index,
                candidate=dict(return_value=.1)))
            rows[-1]['candidate'] = {'return': index / 10000, 'valid_until': None}
        return rows

    def advance(self):
        return self.ns['advance_profile_rankings'](bypass_step_cooldown=True)

    def snapshot(self):
        return self.tables.profile_rankings.get(quarter=self.quarter)

    def test_3000_profiles_are_processed_in_small_batches(self):
        self.add_profiles(3000)
        output = self.advance()
        self.assertTrue(output['pending'])
        self.assertEqual(self.snapshot()['participants'], [])
        steps = 1
        while output['pending']:
            output = self.advance()
            steps += 1
            self.assertLess(steps, 130)
        self.assertEqual(len(self.snapshot()['participants']), 3000)
        self.assertLessEqual(max(self.tables.investor_profiles.fetched), 25)
        self.assertIsNotNone(output['updated_at'])

    def test_previous_complete_snapshot_remains_during_partial_refresh(self):
        self.add_profiles(40)
        previous = [dict(user_id='old', **{'return': .3, 'valid_until': None})]
        stamp = datetime.now(timezone.utc) - timedelta(hours=2)
        self.tables.profile_rankings.add_row(quarter=self.quarter, participants=previous, generated_at=stamp)
        self.advance()
        self.advance()
        self.assertEqual(self.snapshot()['participants'], previous)
        self.assertEqual(self.snapshot()['generated_at'], stamp)
        self.assertTrue(self.advance()['updated_at'] > stamp)

    def test_live_lease_prevents_duplicate_work_and_stale_worker_cannot_commit(self):
        self.add_profiles(1)
        claim = self.ns['_claim_ranking_batch'](self.quarter)
        self.assertIsNone(self.ns['_claim_ranking_batch'](self.quarter))
        state = self.snapshot()['refresh_state']
        state['lease_until'] = 0
        replacement = self.ns['_claim_ranking_batch'](self.quarter, True)
        self.assertNotEqual(claim['token'], replacement['token'])
        self.assertFalse(self.ns['_commit_ranking_batch'](claim, [], []))
        self.assertTrue(self.ns['_commit_ranking_batch'](replacement, [], []))

    def test_concurrent_optout_cannot_be_readded_by_inflight_batch(self):
        rows = self.add_profiles(2)
        self.advance()  # Finish key backfill phase.
        claim = self.ns['_claim_ranking_batch'](self.quarter, True)
        candidate = dict(user_id=rows[0]['owner'].get_id(), **rows[0]['candidate'])
        rows[0]['ranking_opt_in'] = False
        self.ns['invalidate_rankings_for'](rows[0]['owner'])
        self.ns['_commit_ranking_batch'](claim, rows, [candidate])
        self.assertEqual(self.snapshot()['participants'], [])

    def test_backfill_completes_before_random_keys_are_paginated(self):
        self.add_profiles(55, legacy=True)
        for _ in range(3):
            self.advance()
        self.assertEqual(self.snapshot()['refresh_state']['phase'], 'scan')
        self.assertTrue(all(row['ranking_key'] for row in self.tables.investor_profiles.rows))
        for _ in range(3):
            output = self.advance()
        self.assertFalse(output['pending'])
        self.assertEqual(len(self.snapshot()['participants']), 55)

    def test_expired_peers_never_publish_and_completed_cycle_obeys_hour_cooldown(self):
        rows = self.add_profiles(4)
        rows[0]['candidate']['valid_until'] = datetime.now(timezone.utc).timestamp() - 1
        self.advance()
        self.advance()
        self.assertEqual(len(self.snapshot()['participants']), 3)
        before = len(self.tables.investor_profiles.fetched)
        self.assertFalse(self.advance()['pending'])
        self.assertEqual(len(self.tables.investor_profiles.fetched), before)

    def test_server_step_throttle_survives_new_requests(self):
        self.add_profiles(30)
        self.ns['advance_profile_rankings']()
        self.assertIsNone(self.ns['_claim_ranking_batch'](self.quarter))
        self.assertIsNotNone(self.ns['_claim_ranking_batch'](self.quarter, True))

    def test_ties_have_same_quartile_and_midrank(self):
        self.assertEqual(self.ns['_percentile'](.1, [.1, .1, .1, .1]),
                         dict(percentile=50, quartile=2, rank=1, count=4))


if __name__ == '__main__':
    unittest.main()

"""Resumable SEC reads preserve complete-day cursors within one request budget."""
import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "server_code/insider_source.py"


class SourceFailure(Exception):
    pass


class InsiderStepTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.fail_once = None
        self.failed = False
        self.now = datetime.now(timezone.utc)
        self.day = (self.now - timedelta(days=2)).date()
        self.records = []
        self.archives = {}
        self.files = []

        def request(url, provider, timeout=20):
            self.calls.append((url, timeout))
            if self.fail_once and self.fail_once in url and not self.failed:
                self.failed = True
                raise SourceFailure("Fixture timeout")
            if "company_tickers" in url:
                return json.dumps({"0": dict(ticker="ABC", cik_str=1, title="Fixture Company")}).encode()
            if url.endswith("CIK0000000001.json"):
                return json.dumps(dict(filings=dict(recent=self.document(self.records), files=self.files))).encode()
            for name, records in self.archives.items():
                if url.endswith(name):
                    return json.dumps(self.document(records)).encode()
            record = next(row for row in self.records + sum(self.archives.values(), []) if row["accession"].replace("-", "") in url)
            if url.endswith("-index-headers.html"):
                return ("ACCESSION NUMBER: %s\n<ACCEPTANCE-DATETIME>%s100000" %
                        (record["accession"], record["day"].strftime("%Y%m%d"))).encode()
            return ("<ownershipDocument><documentType>4</documentType><issuer><issuerCik>1</issuerCik></issuer>"
                    "<reportingOwner><reportingOwnerId><rptOwnerName>Fixture Insider</rptOwnerName></reportingOwnerId></reportingOwner>"
                    "<nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Common stock</value></securityTitle>"
                    "<transactionDate><value>%s</value></transactionDate><transactionCoding><transactionCode>P</transactionCode></transactionCoding>"
                    "<transactionAmounts><transactionShares><value>10</value></transactionShares><transactionPricePerShare><value>5</value></transactionPricePerShare>"
                    "<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>"
                    "</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>" % record["day"].isoformat()).encode()

        tree = ast.parse(SOURCE.read_text())
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or node.level == 0]
        namespace = dict(hedge_fund_data=SimpleNamespace(_SourceError=SourceFailure, _request=request))
        exec(compile(tree, str(SOURCE), "exec"), namespace)
        self.source = namespace

    def add(self, count, day=None):
        for _ in range(count):
            self.records.append(dict(accession="0000000001-26-%06d" % (len(self.records) + 1), day=day or self.day))

    def document(self, records):
        return dict(form=["4"] * len(records), filingDate=[row["day"].isoformat() for row in records],
                    acceptanceDateTime=[row["day"].isoformat() + "T14:00:00Z" for row in records],
                    accessionNumber=[row["accession"] for row in records], primaryDocument=["filing.xml"] * len(records))

    def run_batch(self, since, progress=None):
        events = []
        for _ in range(400):
            before = len(self.calls)
            result = self.source["fetch_insider_transactions_step"]("ABC", since, progress)
            self.assertLessEqual(len(self.calls) - before, 1)
            self.assertTrue(all(timeout == 6 for _, timeout in self.calls))
            events.extend(result["events"])
            progress = result.get("progress")
            if progress is None:
                return result, events
            self.assertNotIn("checked_at", result)
            progress = json.loads(json.dumps(progress))
        self.fail("Source did not finish within bounded fixture steps")

    def test_dense_day_finishes_without_cursor_skipping_next_day(self):
        self.add(41)
        self.add(1, self.day + timedelta(days=1))
        since = self.now - timedelta(days=4)
        first, events = self.run_batch(since)
        self.assertEqual(len(events), 41)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_since"], first["checked_at"] + timedelta(microseconds=1))
        second, more = self.run_batch(first["next_since"])
        self.assertEqual(len(more), 1)
        self.assertFalse(second["has_more"])
        self.assertEqual(len({row["id"] for row in events + more}), 42)

    def test_timeout_retains_exact_filing_and_retries_without_losing_it(self):
        self.add(3)
        since, progress = self.now - timedelta(days=4), None
        for _ in range(3):
            result = self.source["fetch_insider_transactions_step"]("ABC", since, progress)
            progress = result["progress"]
        self.assertEqual(progress["stage"], "filing")
        saved = json.loads(json.dumps(progress))
        self.fail_once = "/filing.xml"
        with self.assertRaises(self.source["InsiderSourceError"]):
            self.source["fetch_insider_transactions_step"]("ABC", since, progress)
        self.assertEqual(progress, saved)
        result, events = self.run_batch(since, progress)
        self.assertEqual(len(events), 3)
        self.assertFalse(result["has_more"])

    def test_archive_read_is_a_separate_bounded_step(self):
        self.add(2)
        name = "CIK0000000001-submissions-001.json"
        self.archives[name] = self.records[:1]
        self.records = self.records[1:]
        self.files = [dict(name=name, filingTo=self.day.isoformat())]
        result, events = self.run_batch(self.now - timedelta(days=4))
        self.assertEqual(len(events), 2)
        self.assertFalse(result["has_more"])
        self.assertTrue(any(url.endswith(name) for url, _ in self.calls))


if __name__ == "__main__":
    unittest.main()

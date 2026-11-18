import json
import sqlite3

from intake.accept import apply
from inventory.capture import capture
from ledger.entries import EvidenceError
from ledger.transactions import source_write
from replica.load import restore
from replica.reports import report
from tests.support import LakeTestCase, accounts, change, issued_counts, mixed_origins
from transfer.publish import issue, package


class HandoffTests(LakeTestCase):
    def test_same_cut_offline(self):
        source = self.source()
        cut = apply(source, 'initial', [change('x', None, ('A', 17)), change('y', None, ('B', 23))])
        a, b = capture(source, 'A', cut), capture(source, 'B', cut)
        package_id = issue(source, cut, [b.snapshot_id, a.snapshot_id])
        payload = package(source, package_id)
        self.assertEqual(issued_counts(source), (1, 1))
        source.close()
        result = restore(self.replica(), payload)
        self.assertEqual(accounts(result), ([('A', 'x', 17), ('B', 'y', 23)], [('A', 1, 17), ('B', 1, 23)]))
        self.assertEqual(result.receipt.package_id, package_id)
        self.assertEqual(result.target, cut)

    def test_same_cut_forward(self):
        source = self.source(('A', 'B', 'C'))
        cut = apply(source, 'initial', [change('a', None, ('A', 17)), change('b', None, ('B', 23)),
                                        change('c', None, ('C', 29))])
        a, b = capture(source, 'A', cut), capture(source, 'B', cut)
        target = apply(source, 'moves', [change('a', ('A', 17), ('C', 18)), change('b', ('B', 23), None),
                                         change('c', ('C', 29), ('A', 31))])
        payload = package(source, issue(source, target, [a.snapshot_id, b.snapshot_id]))
        source.close()
        self.assertEqual(accounts(restore(self.replica(), payload)), ([('A', 'c', 31)], [('A', 1, 31), ('B', 0, 0)]))

    def test_late_inventory_rejected(self):
        source = self.source()
        _, b = mixed_origins(source)
        with self.assertRaisesRegex(ValueError, 'later than target'):
            issue(source, 11, [b.snapshot_id])
        self.assertEqual(issued_counts(source), (0, 0))

    def test_duplicate_partition(self):
        source = self.source()
        a, _ = mixed_origins(source)
        newer = capture(source, 'A', 12)
        with self.assertRaisesRegex(ValueError, 'multiple origins'):
            issue(source, 12, [a.snapshot_id, newer.snapshot_id])
        self.assertEqual(issued_counts(source), (0, 0))

    def test_unknown_issue_reference(self):
        source = self.source()
        cut = apply(source, 'empty', [])
        a = capture(source, 'A', cut)
        with self.assertRaises(KeyError):
            issue(source, cut, [a.snapshot_id + 1])
        with self.assertRaisesRegex(ValueError, 'not sealed'):
            issue(source, cut + 1, [a.snapshot_id])
        with self.assertRaises(ValueError):
            issue(source, cut, [])
        self.assertEqual(issued_counts(source), (0, 0))

    def test_issued_departure(self):
        source = self.source()
        a, b = mixed_origins(source)
        data = json.loads(package(source, issue(source, 12, [a.snapshot_id, b.snapshot_id])))
        self.assertEqual([(origin['snapshot_id'], origin['partition'], origin['cut']) for origin in data['origins']],
                         [(a.snapshot_id, 'A', 10), (b.snapshot_id, 'B', 12)])
        self.assertEqual([block['number'] for block in data['commits']], [11, 12])
        departure = data['commits'][0]['changes'][0]
        self.assertEqual((departure['record_id'], departure['before']['partition'], int(departure['before']['reading']),
                          departure['after']['partition']), ('x', 'A', 17, 'B'))

    def test_mixed_cut_move_and_return(self):
        source = self.source()
        a, b = mixed_origins(source)
        ids = [issue(source, target, [b.snapshot_id, a.snapshot_id]) for target in (12, 13)]
        payloads = [package(source, number) for number in ids]
        source.close()
        replica = self.replica()
        at_twelve, at_thirteen = [restore(replica, payload) for payload in payloads]
        self.assertEqual((at_twelve.target, at_thirteen.target), (12, 13))
        self.assertEqual(accounts(at_twelve), ([('B', 'x', 19)], [('A', 0, 0), ('B', 1, 19)]))
        self.assertEqual(accounts(at_thirteen), ([('A', 'x', 23)], [('A', 1, 23), ('B', 0, 0)]))
        source = self.source()
        self.assertEqual(apply(source, 'later', [change('x', ('A', 23), ('A', 29))]), 14)
        self.assertEqual(capture(source, 'A', 14).rows[0].reading, 29)
        self.assertEqual([package(source, number) for number in ids], payloads)
        source.close()
        replica.close()
        replica = self.replica()
        self.assertEqual(report(replica, at_twelve.result_id), at_twelve)
        self.assertEqual(report(replica, at_thirteen.result_id), at_thirteen)

    def test_selected_a_only(self):
        source = self.source()
        a, _ = mixed_origins(source)
        payload = package(source, issue(source, 12, [a.snapshot_id]))
        source.close()
        self.assertEqual(accounts(restore(self.replica(), payload)), ([], [('A', 0, 0)]))

    def test_issue_gap(self):
        source = self.source()
        start = apply(source, 'initial', [change('x', None, ('A', 17))])
        a = capture(source, 'A', start)
        empty = apply(source, 'empty-commit', [])
        target = apply(source, 'later', [change('x', ('A', 17), ('A', 19))])
        healthy = package(source, issue(source, target, [a.snapshot_id]))
        self.assertEqual(json.loads(healthy)['commits'][0], {'number': empty, 'change_count': 0, 'changes': []})
        with source_write(source):
            source.execute('DELETE FROM request_bindings WHERE commit_number = ?', (empty,))
            source.execute('DELETE FROM ledger_commits WHERE number = ?', (empty,))
        with self.assertRaises(EvidenceError):
            issue(source, target, [a.snapshot_id])
        self.assertEqual(issued_counts(source), (1, 1))
        self.assertEqual(package(source, 1), healthy)

    def test_issue_receipt_write_failure(self):
        source = self.source()
        cut = apply(source, 'initial', [])
        a = capture(source, 'A', cut)
        source.execute('''
            CREATE TRIGGER fail_issue_receipt BEFORE INSERT ON transfer_receipts
            BEGIN SELECT RAISE(ABORT, 'receipt write failure'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt write failure'):
            issue(source, cut, [a.snapshot_id])
        self.assertEqual(issued_counts(source), (0, 0))

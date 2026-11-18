from hashlib import sha256

from intake.accept import apply
from intake.store import fetch_request
from inventory.capture import capture
from inventory.store import fetch_snapshot
from ledger.store import last_commit_number
from replica.load import restore
from replica.reports import report
from tests.support import LakeTestCase, accounts, change, issued_counts, restore_counts
from transfer.publish import issue, package


def first_handoff(source):
    cut = apply(source, 'station', [change('x', None, ('A', 17))])
    origin = capture(source, 'A', cut)
    number = issue(source, cut, [origin.snapshot_id])
    return origin, number, package(source, number)


class RetentionTests(LakeTestCase):
    def test_reopen_after_source_correction(self):
        source = self.source()
        origin, number, original = first_handoff(source)
        source.close()
        replica = self.replica()
        accepted = restore(replica, original)
        replica.close()
        source = self.source()
        cut = apply(source, 'correction', [change('x', ('A', 17), ('A', 19))])
        newer = capture(source, 'A', cut)
        self.assertEqual(newer.rows[0].reading, 19)
        self.assertNotEqual(origin.snapshot_id, newer.snapshot_id)
        self.assertEqual(fetch_snapshot(source, origin.snapshot_id), origin)
        source.close()
        source = self.source()
        self.assertEqual(package(source, number), original)
        source.close()
        self.assertEqual(accounts(accepted), ([('A', 'x', 17)], [('A', 1, 17)]))
        self.assertEqual(report(self.replica(), accepted.result_id), accepted)

    def test_repeated_restore_receipt(self):
        source = self.source()
        _, number, original = first_handoff(source)
        source.close()
        replica = self.replica()
        first = restore(replica, original)
        second = restore(replica, original)
        self.assertNotEqual(first.result_id, second.result_id)
        self.assertNotEqual(first.receipt.receipt_id, second.receipt.receipt_id)
        for result in (first, second):
            self.assertEqual(result.receipt.result_id, result.result_id)
            self.assertEqual(result.receipt.package_id, number)
            self.assertEqual(result.receipt.package_digest, sha256(original).hexdigest())
            self.assertEqual(accounts(result), ([('A', 'x', 17)], [('A', 1, 17)]))
        replica.close()
        source = self.source()
        apply(source, 'correction', [change('x', ('A', 17), ('B', 19))])
        source.close()
        replica = self.replica()
        self.assertEqual(report(replica, second.result_id), second)
        self.assertEqual(report(replica, first.result_id), first)

    def test_unknown_numbers(self):
        source = self.source()
        _, number, original = first_handoff(source)
        with self.assertRaises(KeyError):
            package(source, number + 1)
        self.assertEqual(issued_counts(source), (1, 1))
        replica = self.replica()
        known = restore(replica, original)
        with self.assertRaises(KeyError):
            report(replica, known.result_id + 1)
        self.assertEqual(restore_counts(replica), (1, 1, 1, 1))

    def test_repeat_initialization(self):
        source = self.source()
        origin, number, original = first_handoff(source)
        replica = self.replica()
        accepted = restore(replica, original)
        cut = apply(source, 'correction', [change('x', ('A', 17), ('A', 19))])
        newer = capture(source, 'A', cut)
        source.close()
        replica.close()
        with self.assertRaisesRegex(ValueError, 'conflicts with the stored catalog'):
            self.source(('A', 'Z'))
        source = self.source(('B', 'A'))
        self.assertEqual([row[0] for row in source.execute('SELECT partition FROM partitions ORDER BY partition')], ['A', 'B'])
        self.assertEqual(last_commit_number(source), 2)
        self.assertEqual(fetch_request(source, 'correction').commit_number, cut)
        self.assertEqual(fetch_snapshot(source, origin.snapshot_id), origin)
        self.assertEqual(fetch_snapshot(source, newer.snapshot_id), newer)
        self.assertEqual(package(source, number), original)
        self.assertEqual(issued_counts(source), (1, 1))
        self.assertEqual(apply(source, 'station', [change('x', None, ('A', 17))]), 1)
        self.assertEqual(apply(source, 'empty', []), 3)
        next_snapshot = capture(source, 'A', 3)
        self.assertEqual(next_snapshot.snapshot_id, newer.snapshot_id + 1)
        self.assertEqual(issue(source, 3, [next_snapshot.snapshot_id]), number + 1)
        replica = self.replica()
        self.assertEqual(report(replica, accepted.result_id), accepted)
        self.assertEqual(restore(replica, original).result_id, accepted.result_id + 1)

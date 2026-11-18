import sqlite3

from intake.accept import apply
from intake.records import InvalidInputError
from intake.requests import RequestConflictError
from intake.store import fetch_request
from inventory.capture import capture
from inventory.store import fetch_snapshot
from ledger.entries import LedgerRow, PriorMismatchError
from ledger.store import fetch_current_row, last_commit_number
from replica.load import restore
from replica.reports import report
from tests.support import LakeTestCase, accounts, change
from transfer.publish import issue, package


class IntakeTests(LakeTestCase):
    def test_ingest_replay_and_rollup(self):
        source = self.source()
        batch = [change('x', None, ('A', 17)), change('y', None, ('A', 23))]
        first = apply(source, 'station', batch)
        self.assertEqual(first, 1)
        self.assertEqual(apply(source, 'station', batch), first)
        self.assertEqual(last_commit_number(source), first)
        snapshot = capture(source, 'A', first)
        self.assertEqual([(row.record_id, row.reading) for row in snapshot.rows], [('x', 17), ('y', 23)])
        self.assertEqual((snapshot.totals.row_count, snapshot.totals.reading_sum), (2, 40))

    def test_ordered_request_conflict(self):
        source = self.source()
        batch = [change('x', None, ('A', 17)), change('y', None, ('A', 23))]
        first = apply(source, 'station', batch)
        reordered_fields = [dict(reversed(tuple(item.items()))) for item in batch]
        self.assertEqual(apply(source, 'station', reordered_fields), first)
        with self.assertRaises(RequestConflictError):
            apply(source, 'station', batch[::-1])
        self.assertEqual(fetch_request(source, 'station').commit_number, first)
        self.assertEqual(last_commit_number(source), first)
        totals = capture(source, 'A', first).totals
        self.assertEqual((totals.row_count, totals.reading_sum), (2, 40))

    def test_move_old_value_conflict(self):
        source = self.source()
        first = apply(source, 'x', [change('x', None, ('A', 17))])
        with self.assertRaises(PriorMismatchError):
            apply(source, 'bad-move', [change('x', ('A', 16), ('B', 17))])
        self.assertEqual(last_commit_number(source), first)
        self.assertIsNone(fetch_request(source, 'bad-move'))
        self.assertEqual(capture(source, 'A', first).rows, (LedgerRow('x', 'A', 17),))
        self.assertEqual(capture(source, 'B', first).rows, ())

    def test_batch_rollback(self):
        source = self.source()
        first = apply(source, 'x', [change('x', None, ('A', 17))])
        with self.assertRaises(PriorMismatchError):
            apply(source, 'batch', [change('y', None, ('A', 23)), change('x', ('A', 16), ('B', 17))])
        source.close()
        source = self.source()
        self.assertEqual(last_commit_number(source), first)
        self.assertIsNone(fetch_request(source, 'batch'))
        self.assertIsNone(fetch_current_row(source, 'y'))
        self.assertEqual(fetch_current_row(source, 'x'), LedgerRow('x', 'A', 17))
        self.assertEqual(apply(source, 'batch', [change('y', None, ('A', 23))]), first + 1)

    def test_bad_format(self):
        source = self.source()
        for reading in (None, True, 17.0, '17'):
            with self.subTest(reading=reading), self.assertRaises(InvalidInputError):
                apply(source, 'bad', [change('x', None, ('A', reading))])
        missing = {'record_id': 'x', 'before': None, 'after': {'partition': 'A'}}
        with self.assertRaises(InvalidInputError):
            apply(source, 'bad', [missing])
        with self.assertRaises(KeyError):
            apply(source, 'unknown', [change('x', None, ('Z', 17))])
        self.assertEqual(last_commit_number(source), 0)
        self.assertIsNone(fetch_request(source, 'bad'))
        self.assertIsNone(fetch_request(source, 'unknown'))

    def test_historical_cut(self):
        source = self.source()
        first = apply(source, 'initial', [change('z', None, ('A', 23)), change('x', None, ('A', 13))])
        middle = apply(source, 'checked', [change('x', ('A', 13), ('A', 17))])
        latest = apply(source, 'checked-again', [change('x', ('A', 17), ('A', 19))])
        self.assertEqual((first, middle, latest), (1, 2, 3))
        for cut, reading, total in ((middle, 17, 40), (latest, 19, 42)):
            snapshot = capture(source, 'A', cut)
            self.assertEqual([(row.record_id, row.reading) for row in snapshot.rows], [('x', reading), ('z', 23)])
            self.assertEqual(snapshot.totals.reading_sum, total)

    def test_batch_uses_staged_prior_and_preserves_history(self):
        source = self.source()
        cut = apply(source, 'staged', [
            change('x', None, ('A', 17)), change('x', ('A', 17), ('B', 19)),
            change('x', ('B', 19), None), change('x', None, ('A', 23)),
        ])
        self.assertEqual(capture(source, 'A', cut).rows, (LedgerRow('x', 'A', 23),))
        self.assertEqual(capture(source, 'B', cut).rows, ())
        self.assertEqual(fetch_current_row(source, 'x'), LedgerRow('x', 'A', 23))

    def test_invalid_capture(self):
        source = self.source()
        cut = apply(source, 'empty', [])
        with self.assertRaises(KeyError):
            capture(source, 'Z', cut)
        with self.assertRaises(ValueError):
            capture(source, 'A', cut + 1)
        source.execute('''
            CREATE TRIGGER fail_inventory_write BEFORE INSERT ON inventory_rows
            BEGIN SELECT RAISE(ABORT, 'inventory write failure'); END
        ''')
        populated = apply(source, 'x', [change('x', None, ('A', 17))])
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'inventory write failure'):
            capture(source, 'A', populated)
        self.assertEqual(source.execute('SELECT COUNT(*) FROM inventory_snapshots').fetchone()[0], 0)
        self.assertEqual(capture(source, 'B', cut).snapshot_id, 1)

    def test_zero_and_integer_totals(self):
        source = self.source()
        half = 1 << 62
        cut = apply(source, 'measurements', [change('z', None, ('A', 0)),
                                           change('x', None, ('A', half)), change('y', None, ('A', half))])
        a, b = capture(source, 'A', cut), capture(source, 'B', cut)
        self.assertEqual([row.reading for row in a.rows], [half, half, 0])
        self.assertEqual((a.totals.row_count, a.totals.reading_sum), (3, 9223372036854775808))
        self.assertEqual((b.totals.row_count, b.totals.reading_sum), (0, 0))
        source.close()
        source = self.source()
        self.assertEqual(fetch_snapshot(source, a.snapshot_id), a)
        payload = package(source, issue(source, cut, [b.snapshot_id, a.snapshot_id]))
        source.close()
        replica = self.replica()
        restored = restore(replica, payload)
        self.assertEqual(accounts(restored),
                         ([('A', 'x', half), ('A', 'y', half), ('A', 'z', 0)],
                          [('A', 3, 9223372036854775808), ('B', 0, 0)]))
        replica.close()
        self.assertEqual(report(self.replica(), restored.result_id), restored)

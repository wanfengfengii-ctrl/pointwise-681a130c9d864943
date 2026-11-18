import sqlite3
from hashlib import sha256

from ledger.entries import EvidenceError, PriorMismatchError
from replica.load import restore
from replica.reports import report
from tests.support import LakeTestCase, accounts, change, external_mixed, external_same_cut, raw, restore_counts


class ReplicaTests(LakeTestCase):
    def test_independent_package(self):
        replica = self.replica()
        for target, expected in ((12, ([('B', 'x', 19)], [('A', 0, 0), ('B', 1, 19)])),
                                 (13, ([('A', 'x', 23)], [('A', 1, 23), ('B', 0, 0)]))):
            with self.subTest(target=target):
                payload = raw(external_mixed(target))
                restored = restore(replica, payload)
                self.assertEqual(restored.target, target)
                self.assertEqual(accounts(restored), expected)
                self.assertEqual(restored.receipt.package_id, 71)
                self.assertEqual(restored.receipt.package_digest, sha256(payload).hexdigest())
                self.assertEqual(report(replica, restored.result_id), restored)

    def test_empty_commit_and_missing_evidence(self):
        replica = self.replica()
        known = restore(replica, raw(external_same_cut(10)))
        with_empty = external_same_cut()
        accepted = restore(replica, raw(with_empty))
        self.assertEqual(accepted.target, 11)
        self.assertEqual(accounts(accepted), ([('A', 'x', 17)], [('A', 1, 17), ('B', 0, 0)]))
        with_empty['commits'].clear()
        published = restore_counts(replica)
        with self.assertRaises(EvidenceError):
            restore(replica, raw(with_empty))
        self.assertEqual(restore_counts(replica), published)
        self.assertEqual(report(replica, known.result_id), known)

    def test_applicable_prior_mismatch(self):
        replica = self.replica()
        known = restore(replica, raw(external_same_cut(10)))
        data = external_mixed(12)
        data['commits'][0]['changes'][0]['before']['reading'] = 16
        published = restore_counts(replica)
        with self.assertRaises(PriorMismatchError):
            restore(replica, raw(data))
        self.assertEqual(restore_counts(replica), published)
        self.assertEqual(report(replica, known.result_id), known)

    def test_duplicate_final_ownership_rejected(self):
        replica = self.replica()
        data = external_same_cut(10)
        data['origins'][1]['rows'] = [{'partition': 'B', 'record_id': 'x', 'reading': 19}]
        data['origins'][1]['totals'] = {'row_count': 1, 'reading_sum': 19}
        with self.assertRaisesRegex(ValueError, 'multiple owners'):
            restore(replica, raw(data))
        self.assertEqual(restore_counts(replica), (0, 0, 0, 0))

    def test_inconsistent_origin_totals_rejected(self):
        replica = self.replica()
        data = external_same_cut(10)
        data['origins'][0]['totals']['reading_sum'] = 16
        with self.assertRaisesRegex(ValueError, 'inconsistent totals'):
            restore(replica, raw(data))
        self.assertEqual(restore_counts(replica), (0, 0, 0, 0))

    def test_restore_commit_failure(self):
        replica = self.replica()
        old = restore(replica, raw(external_same_cut(10)))
        newer = external_same_cut()
        newer['package_id'] = 42
        newer['commits'][0].update(change_count=1, changes=[change('x', ('A', 17), ('A', 23))])
        replica.execute('CREATE TABLE test_publish_permission (result_id INTEGER PRIMARY KEY)')
        replica.execute('''
            CREATE TABLE test_publish_check (
                result_id INTEGER REFERENCES test_publish_permission(result_id) DEFERRABLE INITIALLY DEFERRED
            )
        ''')
        replica.execute('''
            CREATE TRIGGER require_publish_permission AFTER INSERT ON restore_receipts
            BEGIN INSERT INTO test_publish_check VALUES (NEW.result_id); END
        ''')
        before = restore_counts(replica)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            restore(replica, raw(newer))
        replica.close()
        replica = self.replica()
        self.assertEqual(restore_counts(replica), before)
        self.assertEqual(replica.execute('SELECT COUNT(*) FROM test_publish_check').fetchone()[0], 0)
        self.assertEqual(report(replica, old.result_id), old)
        with self.assertRaises(KeyError):
            report(replica, old.result_id + 1)
        replica.execute('INSERT INTO test_publish_permission VALUES (?)', (old.result_id + 1,))
        recovered = restore(replica, raw(newer))
        self.assertEqual(recovered.result_id, old.result_id + 1)
        self.assertEqual(accounts(recovered), ([('A', 'x', 23)], [('A', 1, 23), ('B', 0, 0)]))

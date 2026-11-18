import json
import sqlite3

from intake.accept import apply, merge_sample
from intake.requests import RequestConflictError
from intake.store import fetch_request
from inventory.capture import capture
from ledger.entries import LedgerRow, PriorMismatchError
from ledger.identity import direct_identity, fetch_link, resolve_identity
from ledger.store import fetch_current_row, last_commit_number
from replica.load import restore
from replica.reports import lookup_sample, report
from tests.support import LakeTestCase, accounts, change, restore_counts
from transfer.identity_encoding import package_format_version
from transfer.publish import issue, issue_identity, package


def image(partition, reading):
    return {'partition': partition, 'reading': reading}


def merge(source, request_id, retired, kept, retired_image, kept_image, reading):
    return merge_sample(source, request_id, retired, kept, retired_image, kept_image, reading)


def open_three(source):
    apply(source, 'x', [change('x', None, ('A', 17))])
    apply(source, 'y', [change('y', None, ('B', 19))])
    apply(source, 'z', [change('z', None, ('B', 5))])


class MergeSealTests(LakeTestCase):
    def test_merge_seals_rows_and_identity_together(self):
        source = self.source()
        open_three(source)
        number = merge(source, 'm1', 'x', 'y', image('A', 17), image('B', 19), 21)
        self.assertEqual(number, 4)
        self.assertIsNone(fetch_current_row(source, 'x'))
        self.assertEqual(fetch_current_row(source, 'y'), LedgerRow('y', 'B', 21))
        self.assertEqual(direct_identity(source, 'x'), 'y')
        link = fetch_link(source, 'x')
        self.assertEqual((link.effective_commit, link.retired_before, link.kept_before, link.kept_after),
                         (4, LedgerRow('x', 'A', 17), LedgerRow('y', 'B', 19), LedgerRow('y', 'B', 21)))
        binding = fetch_request(source, 'm1')
        self.assertEqual((binding.commit_number, binding.kind), (4, 'merge'))
        # History remains replayable through the ordinary row ledger.
        self.assertEqual(capture(source, 'B', number).rows, (LedgerRow('y', 'B', 21), LedgerRow('z', 'B', 5)))
        self.assertEqual(capture(source, 'A', number).rows, ())

    def test_same_number_and_retired_operand_rejected(self):
        source = self.source()
        open_three(source)
        merge(source, 'm1', 'x', 'y', image('A', 17), image('B', 19), 21)
        with self.assertRaises(ValueError):
            merge(source, 'm-same', 'y', 'y', image('B', 21), image('B', 21), 22)
        with self.assertRaises(ValueError):
            merge(source, 'm-old', 'x', 'z', image('A', 17), image('B', 5), 22)
        with self.assertRaises(ValueError):
            merge(source, 'm-old2', 'z', 'x', image('B', 5), image('A', 17), 22)
        self.assertEqual(last_commit_number(source), 4)

    def test_prior_mismatch_and_missing_operand(self):
        source = self.source()
        open_three(source)
        with self.assertRaises(PriorMismatchError):
            merge(source, 'bad', 'x', 'y', image('A', 16), image('B', 19), 21)
        with self.assertRaises(PriorMismatchError):
            merge(source, 'missing', 'nope', 'y', image('A', 1), image('B', 19), 21)
        self.assertIsNone(fetch_request(source, 'bad'))
        self.assertEqual(last_commit_number(source), 3)
        self.assertEqual(fetch_current_row(source, 'x'), LedgerRow('x', 'A', 17))

    def test_replay_returns_original_commit_even_after_chain_moves(self):
        source = self.source()
        open_three(source)
        args = ('m1', 'x', 'y', image('A', 17), image('B', 19), 21)
        self.assertEqual(merge(source, *args), 4)
        merge(source, 'm2', 'y', 'z', image('B', 21), image('B', 5), 22)
        # Same content, shuffled mapping key order, still the same request.
        shuffled = ('m1', 'x', 'y', {'reading': 17, 'partition': 'A'},
                    {'reading': 19, 'partition': 'B'}, 21)
        self.assertEqual(merge(source, *shuffled), 4)
        with self.assertRaises(RequestConflictError):
            merge(source, 'm1', 'x', 'y', image('A', 17), image('B', 19), 20)
        # Cross-kind reuse of a request id is a conflict.
        with self.assertRaises(RequestConflictError):
            apply(source, 'm1', [change('q', None, ('A', 1))])
        with self.assertRaises(RequestConflictError):
            merge(source, 'x', 'x', 'y', image('A', 17), image('B', 19), 21)
        self.assertEqual(last_commit_number(source), 5)

    def test_failed_commit_leaves_nothing(self):
        source = self.source()
        open_three(source)

        def deny_commit(action, operation, *_):
            if action == sqlite3.SQLITE_TRANSACTION and operation == 'COMMIT':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        source.set_authorizer(deny_commit)
        with self.assertRaises(sqlite3.DatabaseError):
            merge(source, 'm1', 'x', 'y', image('A', 17), image('B', 19), 21)
        source.set_authorizer(None)
        self.assertEqual(last_commit_number(source), 3)
        self.assertIsNone(direct_identity(source, 'x'))
        self.assertIsNone(fetch_request(source, 'm1'))
        # Retry after the failure seals normally.
        self.assertEqual(merge(source, 'm1', 'x', 'y', image('A', 17), image('B', 19), 21), 4)


class AliasApplyTests(LakeTestCase):
    def test_correction_transfer_and_retire_through_old_number(self):
        source = self.source()
        open_three(source)
        merge(source, 'm1', 'x', 'y', image('A', 17), image('B', 19), 21)
        # Correction through the retired number lands on the surviving row.
        cut = apply(source, 'fix-x', [change('x', ('B', 21), ('B', 23))])
        self.assertEqual(fetch_current_row(source, 'y'), LedgerRow('y', 'B', 23))
        self.assertIsNone(fetch_current_row(source, 'x'))
        stored = source.execute('SELECT record_id FROM ledger_changes WHERE commit_number = ?', (cut,)).fetchall()
        self.assertEqual(stored, [('y',)])
        # Transfer through the old number moves the main row.
        apply(source, 'move-x', [change('x', ('B', 23), ('A', 29))])
        self.assertEqual(fetch_current_row(source, 'y'), LedgerRow('y', 'A', 29))
        # Retire through the old number withdraws the main row; no new identity link.
        apply(source, 'retire-x', [change('x', ('A', 29), None)])
        self.assertIsNone(fetch_current_row(source, 'y'))
        self.assertEqual(direct_identity(source, 'x'), 'y')
        self.assertEqual(resolve_identity(source, 'x'), 'y')

    def test_insert_through_retired_number_rejected(self):
        source = self.source()
        open_three(source)
        merge(source, 'm1', 'x', 'y', image('A', 17), image('B', 19), 21)
        with self.assertRaises(ValueError):
            apply(source, 'revive', [change('x', None, ('A', 9))])
        self.assertIsNone(fetch_request(source, 'revive'))
        self.assertEqual(last_commit_number(source), 4)

    def test_batch_sees_updated_row_under_old_and_main_numbers(self):
        source = self.source()
        open_three(source)
        merge(source, 'm1', 'x', 'y', image('A', 17), image('B', 19), 21)
        cut = apply(source, 'mixed-batch', [
            change('x', ('B', 21), ('B', 22)),
            change('y', ('B', 22), ('B', 23)),
        ])
        self.assertEqual(fetch_current_row(source, 'y'), LedgerRow('y', 'B', 23))
        self.assertEqual(len(source.execute(
            'SELECT 1 FROM ledger_changes WHERE commit_number = ?', (cut,)).fetchall()), 2)
        # A bad prior through the alias rolls the whole batch back.
        with self.assertRaises(PriorMismatchError):
            apply(source, 'bad-batch', [change('x', ('B', 99), ('B', 24))])
        self.assertEqual(fetch_current_row(source, 'y'), LedgerRow('y', 'B', 23))

    def test_chain_followed_for_deeply_merged_number(self):
        source = self.source()
        open_three(source)
        merge(source, 'm1', 'x', 'y', image('A', 17), image('B', 19), 21)
        merge(source, 'm2', 'y', 'z', image('B', 21), image('B', 5), 22)
        self.assertEqual(resolve_identity(source, 'x'), 'z')
        apply(source, 'deep-fix', [change('x', ('B', 22), ('B', 23))])
        self.assertEqual(fetch_current_row(source, 'z'), LedgerRow('z', 'B', 23))


class IdentityPackageTests(LakeTestCase):
    def _scenario(self):
        source = self.source()
        for number in range(1, 10):
            apply(source, f'opening-{number}', [])
        apply(source, 'initial', [change('x', None, ('A', 17)),
                                  change('y', None, ('B', 19)), change('z', None, ('B', 5))])  # 10
        merge(source, 'm1', 'x', 'y', image('A', 17), image('B', 19), 21)  # 11
        apply(source, 'empty-12', [])  # 12
        a10 = capture(source, 'A', 10).snapshot_id
        a12 = capture(source, 'A', 12).snapshot_id
        b12 = capture(source, 'B', 12).snapshot_id
        merge(source, 'm2', 'y', 'z', image('B', 21), image('B', 5), 22)  # 13
        apply(source, 'fix', [change('x', ('B', 22), ('B', 23))])  # 14
        return source, a10, a12, b12

    def test_v2_layout_distinct_and_restores_with_lookup(self):
        source, a10, a12, b12 = self._scenario()
        package_id = issue_identity(source, 14, [a10, b12])
        payload = package(source, package_id)
        self.assertEqual(package_format_version(payload), 2)
        data = json.loads(payload)
        self.assertEqual(data['version'], 2)
        self.assertEqual([(link['retired_id'], link['kept_id'], link['effective_commit'])
                          for link in data['identities']], [('x', 'y', 11), ('y', 'z', 13)])
        source.close()
        replica = self.replica()
        result = restore(replica, payload)
        self.assertEqual(accounts(result), ([('B', 'z', 23)], [('A', 0, 0), ('B', 1, 23)]))
        self.assertEqual(lookup_sample(replica, result.result_id, 'x'), LedgerRow('z', 'B', 23))
        self.assertEqual(lookup_sample(replica, result.result_id, 'y'), LedgerRow('z', 'B', 23))
        self.assertEqual(lookup_sample(replica, result.result_id, 'z'), LedgerRow('z', 'B', 23))
        self.assertIsNone(lookup_sample(replica, result.result_id, 'unknown'))

    def test_mixed_cuts_and_empty_selection(self):
        source, a10, a12, b12 = self._scenario()
        mixed = package(source, issue_identity(source, 12, [a10, b12]))
        late = package(source, issue_identity(source, 12, [a12, b12]))
        only_a = package(source, issue_identity(source, 12, [a10]))
        replica = self.replica()
        for payload in (mixed, late):
            result = restore(replica, payload)
            self.assertEqual(accounts(result),
                             ([('B', 'y', 21), ('B', 'z', 5)], [('A', 0, 0), ('B', 2, 26)]))
            self.assertEqual(lookup_sample(replica, result.result_id, 'x'), LedgerRow('y', 'B', 21))
            self.assertEqual(lookup_sample(replica, result.result_id, 'y'), LedgerRow('y', 'B', 21))
        partial = restore(replica, only_a)
        self.assertEqual(accounts(partial), ([], [('A', 0, 0)]))
        self.assertIsNone(lookup_sample(replica, partial.result_id, 'x'))
        self.assertIsNone(lookup_sample(replica, partial.result_id, 'y'))

    def test_pre_origin_merge_evidence(self):
        source = self.source()
        open_three(source)  # commits 1-3
        merge(source, 'early', 'x', 'y', image('A', 17), image('B', 19), 21)  # 4
        cut = apply(source, 'seal', [])  # 5
        a = capture(source, 'A', cut)
        b = capture(source, 'B', cut)
        payload = package(source, issue_identity(source, cut, [a.snapshot_id, b.snapshot_id]))
        data = json.loads(payload)
        # The merge commit is older than both origins yet still declared.
        self.assertEqual(data['commits'], [])
        self.assertEqual([link['effective_commit'] for link in data['identities']], [4])
        result = restore(self.replica(), payload)
        self.assertEqual(lookup_sample(self.replica(), result.result_id, 'x'), LedgerRow('y', 'B', 21))

    def test_pre_origin_image_tamper_rejected(self):
        source = self.source()
        open_three(source)
        merge(source, 'early', 'x', 'y', image('A', 17), image('B', 19), 21)
        cut = apply(source, 'seal', [])
        a = capture(source, 'A', cut)
        b = capture(source, 'B', cut)
        data = json.loads(package(source, issue_identity(source, cut, [a.snapshot_id, b.snapshot_id])))
        replica = self.replica()
        before = restore_counts(replica)

        tampered = json.loads(json.dumps(data))
        tampered['identities'][0]['kept_after']['reading'] = 99
        with self.assertRaises(ValueError):
            restore(replica, json.dumps(tampered, separators=(',', ':')).encode())
        # A retired number still present in a later origin inventory is a mismatch.
        leaked = json.loads(json.dumps(data))
        for origin in leaked['origins']:
            if origin['partition'] == 'A':
                origin['rows'].append({'record_id': 'x', 'partition': 'A', 'reading': 17})
                origin['totals'] = {'row_count': 1, 'reading_sum': 17}
        with self.assertRaises(ValueError):
            restore(replica, json.dumps(leaked, separators=(',', ':')).encode())
        self.assertEqual(restore_counts(replica), before)

    def test_ordinary_withdrawal_needs_no_identity(self):
        source = self.source(('A', 'B'))
        cut = apply(source, 'open', [change('q', None, ('A', 8))])
        a = capture(source, 'A', cut)
        apply(source, 'withdraw', [change('q', ('A', 8), None)])
        target = apply(source, 'seal', [])
        b = capture(source, 'B', target)
        payload = package(source, issue_identity(source, target, [a.snapshot_id, b.snapshot_id]))
        self.assertEqual(json.loads(payload)['identities'], [])
        result = restore(self.replica(), payload)
        self.assertEqual(accounts(result), ([], [('A', 0, 0), ('B', 0, 0)]))
        self.assertIsNone(lookup_sample(self.replica(), result.result_id, 'q'))

    def test_v1_result_lookup_raises_unknown_raises_keyerror(self):
        source, a10, _, b12 = self._scenario()
        v1_payload = package(source, issue(source, 12, [a10, b12]))
        v2_payload = package(source, issue_identity(source, 12, [a10, b12]))
        replica = self.replica()
        v1 = restore(replica, v1_payload)
        with self.assertRaises(ValueError):
            lookup_sample(replica, v1.result_id, 'x')
        v2 = restore(replica, v2_payload)
        with self.assertRaises(KeyError):
            lookup_sample(replica, v2.result_id + 99, 'x')
        # V1 report/receipt entry points keep working.
        self.assertEqual(report(replica, v1.result_id), v1)
        self.assertEqual(restore(replica, v1_payload).receipt.package_id, v1.receipt.package_id)

    def test_reopen_keeps_issued_packages_and_results(self):
        source, a10, _, b12 = self._scenario()
        number = issue_identity(source, 14, [a10, b12])
        original = package(source, number)
        source.close()
        replica = self.replica()
        result = restore(replica, original)
        replica.close()
        source = self.source()
        apply(source, 'later', [change('z', ('B', 23), ('A', 31))])
        self.assertEqual(package(source, number), original)
        source.close()
        replica = self.replica()
        self.assertEqual(report(replica, result.result_id), result)
        self.assertEqual(lookup_sample(replica, result.result_id, 'x'), LedgerRow('z', 'B', 23))

    def test_missing_identity_evidence_rejected(self):
        source, a10, _, b12 = self._scenario()
        payload = bytearray(package(source, issue_identity(source, 12, [a10, b12])))
        data = json.loads(payload.decode('utf-8'))
        data['identities'] = []
        stripped = json.dumps(data, separators=(',', ':')).encode('utf-8')
        replica = self.replica()
        before = restore_counts(replica)
        with self.assertRaises(ValueError):
            restore(replica, stripped)
        self.assertEqual(restore_counts(replica), before)

    def test_identity_ledger_mismatch_rejected(self):
        source, a10, _, b12 = self._scenario()
        data = json.loads(package(source, issue_identity(source, 12, [a10, b12])))
        data['identities'][0]['retired_before']['reading'] = 16
        tampered = json.dumps(data, separators=(',', ':')).encode('utf-8')
        replica = self.replica()
        before = restore_counts(replica)
        with self.assertRaises(ValueError):
            restore(replica, tampered)
        self.assertEqual(restore_counts(replica), before)

    def test_contradictory_destination_and_cycle_rejected(self):
        source, a10, _, b12 = self._scenario()
        data = json.loads(package(source, issue_identity(source, 14, [a10, b12])))
        conflicted = json.loads(json.dumps(data))
        second = dict(conflicted['identities'][0])
        second['kept_id'] = 'z'
        conflicted['identities'].append(second)
        with self.assertRaises(ValueError):
            restore(self.replica(), json.dumps(conflicted, separators=(',', ':')).encode('utf-8'))
        cyclic = json.loads(json.dumps(data))
        for link in cyclic['identities']:
            if link['retired_id'] == 'y':
                link['kept_id'] = 'x'
        with self.assertRaises(ValueError):
            restore(self.replica(), json.dumps(cyclic, separators=(',', ':')).encode('utf-8'))

    def test_restore_failure_publishes_nothing(self):
        source, a10, _, b12 = self._scenario()
        payload = package(source, issue_identity(source, 14, [a10, b12]))
        replica = self.replica()
        replica.execute('CREATE TABLE guard (result_id INTEGER PRIMARY KEY)')
        replica.execute('''
            CREATE TABLE guard_check (
                result_id INTEGER REFERENCES guard(result_id) DEFERRABLE INITIALLY DEFERRED
            )
        ''')
        replica.execute('''
            CREATE TRIGGER require_guard AFTER INSERT ON restore_receipts
            BEGIN INSERT INTO guard_check VALUES (NEW.result_id); END
        ''')
        before = restore_counts(replica)
        with self.assertRaises(sqlite3.IntegrityError):
            restore(replica, payload)
        self.assertEqual(restore_counts(replica), before)
        replica.execute('INSERT INTO guard VALUES (1)')
        result = restore(replica, payload)
        self.assertEqual(result.result_id, 1)
        self.assertEqual(lookup_sample(replica, 1, 'x'), LedgerRow('z', 'B', 23))

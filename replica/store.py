import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

from inventory.rollup import PartitionTotals
from ledger.entries import LedgerRow, decode_measurement, encode_measurement, require_number


@dataclass(frozen=True, slots=True)
class ReportDraft:
    target: int
    rows: tuple[LedgerRow, ...]
    totals: tuple[tuple[str, PartitionTotals], ...]


@dataclass(frozen=True, slots=True)
class RestoreReceipt:
    receipt_id: str
    result_id: int
    package_id: int
    target: int
    package_digest: str


@dataclass(frozen=True, slots=True)
class RestoredReport:
    result_id: int
    target: int
    rows: tuple[LedgerRow, ...]
    totals: tuple[tuple[str, PartitionTotals], ...]
    receipt: RestoreReceipt


@contextmanager
def _write(replica: sqlite3.Connection):
    replica.execute('BEGIN IMMEDIATE')
    try:
        yield
        replica.commit()
    except BaseException:
        replica.rollback()
        raise


def initialize_replica_schema(replica: sqlite3.Connection) -> None:
    with _write(replica):
        replica.execute('''
            CREATE TABLE IF NOT EXISTS restore_results (
                result_id INTEGER PRIMARY KEY,
                package_id INTEGER NOT NULL,
                target INTEGER NOT NULL,
                package_digest TEXT NOT NULL
            )
        ''')
        replica.execute('''
            CREATE TABLE IF NOT EXISTS restore_rows (
                result_id INTEGER NOT NULL REFERENCES restore_results(result_id),
                partition TEXT NOT NULL,
                record_id TEXT NOT NULL,
                reading TEXT NOT NULL,
                PRIMARY KEY (result_id, record_id)
            )
        ''')
        replica.execute('''
            CREATE TABLE IF NOT EXISTS restore_totals (
                result_id INTEGER NOT NULL REFERENCES restore_results(result_id),
                partition TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                reading_sum TEXT NOT NULL,
                PRIMARY KEY (result_id, partition)
            )
        ''')
        replica.execute('''
            CREATE TABLE IF NOT EXISTS restore_receipts (
                result_id INTEGER PRIMARY KEY REFERENCES restore_results(result_id),
                receipt_id TEXT NOT NULL UNIQUE
            )
        ''')
        # V2 identity index: published in the same transaction as the report so
        # a result can only be queried by old number once its evidence is sealed.
        replica.execute('''
            CREATE TABLE IF NOT EXISTS restore_identity_results (
                result_id INTEGER PRIMARY KEY REFERENCES restore_results(result_id)
            )
        ''')
        replica.execute('''
            CREATE TABLE IF NOT EXISTS restore_identities (
                result_id INTEGER NOT NULL REFERENCES restore_results(result_id),
                retired_id TEXT NOT NULL,
                kept_id TEXT NOT NULL,
                effective_commit INTEGER NOT NULL,
                PRIMARY KEY (result_id, retired_id)
            )
        ''')


def open_replica(path) -> sqlite3.Connection:
    replica = sqlite3.connect(path, isolation_level=None)
    try:
        replica.execute('PRAGMA foreign_keys = ON')
        initialize_replica_schema(replica)
    except BaseException:
        replica.close()
        raise
    return replica


def publish_report(replica: sqlite3.Connection, draft: ReportDraft, package_id: int, digest: str,
                   identities=(), identity_result: bool = False) -> RestoredReport:
    with _write(replica):
        cursor = replica.execute('INSERT INTO restore_results (package_id, target, package_digest) VALUES (?, ?, ?)',
                                 (package_id, draft.target, digest))
        result_id = cursor.lastrowid
        replica.executemany('INSERT INTO restore_rows VALUES (?, ?, ?, ?)', (
            (result_id, row.partition, row.record_id, encode_measurement(row.reading)) for row in draft.rows
        ))
        replica.executemany('INSERT INTO restore_totals VALUES (?, ?, ?, ?)', (
            (result_id, partition, totals.row_count, encode_measurement(totals.reading_sum))
            for partition, totals in draft.totals
        ))
        receipt_id = f'restore-{result_id}'
        replica.execute('INSERT INTO restore_receipts VALUES (?, ?)', (result_id, receipt_id))
        if identity_result:
            replica.execute('INSERT INTO restore_identity_results VALUES (?)', (result_id,))
            replica.executemany('INSERT INTO restore_identities VALUES (?, ?, ?, ?)', (
                (result_id, link.retired_id, link.kept_id, link.effective_commit) for link in identities
            ))
    receipt = RestoreReceipt(receipt_id, result_id, package_id, draft.target, digest)
    return RestoredReport(result_id, draft.target, draft.rows, draft.totals, receipt)


def fetch_report(replica: sqlite3.Connection, result_id: int) -> RestoredReport:
    require_number(result_id, 'result_id')
    head = replica.execute('''
        SELECT p.package_id, p.target, p.package_digest, r.receipt_id
        FROM restore_results AS p JOIN restore_receipts AS r USING (result_id)
        WHERE p.result_id = ?
    ''', (result_id,)).fetchone()
    if head is None:
        raise KeyError(result_id)
    package_id, target, digest, receipt_id = head
    rows = tuple(LedgerRow(record_id, partition, decode_measurement(reading))
                 for partition, record_id, reading in replica.execute('''
        SELECT partition, record_id, reading FROM restore_rows WHERE result_id = ? ORDER BY partition, record_id
    ''', (result_id,)))
    totals = tuple((partition, PartitionTotals(count, decode_measurement(total)))
                   for partition, count, total in replica.execute('''
        SELECT partition, row_count, reading_sum FROM restore_totals WHERE result_id = ? ORDER BY partition
    ''', (result_id,)))
    receipt = RestoreReceipt(receipt_id, result_id, package_id, target, digest)
    return RestoredReport(result_id, target, rows, totals, receipt)

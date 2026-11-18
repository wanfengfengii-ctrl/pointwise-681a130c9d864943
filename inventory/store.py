from sqlite3 import Connection

from inventory.reconcile import InventorySnapshot
from inventory.rollup import PartitionTotals
from ledger.entries import LedgerRow, decode_measurement, encode_measurement, require_number


def initialize_inventory_schema(source: Connection) -> None:
    source.execute('''
        CREATE TABLE IF NOT EXISTS inventory_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            partition TEXT NOT NULL REFERENCES partitions(partition),
            cut INTEGER NOT NULL REFERENCES ledger_commits(number),
            row_count INTEGER NOT NULL,
            reading_sum TEXT NOT NULL
        )
    ''')
    source.execute('''
        CREATE TABLE IF NOT EXISTS inventory_rows (
            snapshot_id INTEGER NOT NULL REFERENCES inventory_snapshots(snapshot_id),
            record_id TEXT NOT NULL,
            reading TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, record_id)
        )
    ''')


def allocate_snapshot_id(source: Connection) -> int:
    return source.execute('SELECT COALESCE(MAX(snapshot_id), 0) FROM inventory_snapshots').fetchone()[0] + 1


def save_snapshot(source: Connection, snapshot: InventorySnapshot) -> None:
    source.execute('INSERT INTO inventory_snapshots VALUES (?, ?, ?, ?, ?)', (
        snapshot.snapshot_id, snapshot.partition, snapshot.cut,
        snapshot.totals.row_count, encode_measurement(snapshot.totals.reading_sum),
    ))
    source.executemany('INSERT INTO inventory_rows VALUES (?, ?, ?)', (
        (snapshot.snapshot_id, row.record_id, encode_measurement(row.reading)) for row in snapshot.rows
    ))


def fetch_snapshot(source: Connection, snapshot_id: int) -> InventorySnapshot:
    require_number(snapshot_id, 'snapshot_id')
    head = source.execute('''
        SELECT partition, cut, row_count, reading_sum FROM inventory_snapshots WHERE snapshot_id = ?
    ''', (snapshot_id,)).fetchone()
    if head is None:
        raise KeyError(snapshot_id)
    partition, cut, count, total = head
    rows = tuple(LedgerRow(record_id, partition, decode_measurement(reading)) for record_id, reading in source.execute('''
        SELECT record_id, reading FROM inventory_rows WHERE snapshot_id = ? ORDER BY record_id
    ''', (snapshot_id,)))
    return InventorySnapshot(snapshot_id, partition, cut, rows, PartitionTotals(count, decode_measurement(total)))

from sqlite3 import Connection

from inventory.reconcile import InventorySnapshot, build_snapshot
from inventory.store import allocate_snapshot_id, save_snapshot
from ledger.history import rows_at_cut
from ledger.partitions import require_partition
from ledger.transactions import source_write


def capture(source: Connection, partition: str, cut: int) -> InventorySnapshot:
    with source_write(source):
        require_partition(source, partition)
        rows = rows_at_cut(source, partition, cut)
        snapshot = build_snapshot(allocate_snapshot_id(source), partition, cut, rows)
        save_snapshot(source, snapshot)
    return snapshot

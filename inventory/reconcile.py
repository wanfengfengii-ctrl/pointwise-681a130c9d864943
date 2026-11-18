from collections.abc import Iterable
from dataclasses import dataclass

from inventory.rollup import PartitionTotals, rollup_rows
from ledger.entries import LedgerRow, require_label, require_number, validate_row


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    snapshot_id: int
    partition: str
    cut: int
    rows: tuple[LedgerRow, ...]
    totals: PartitionTotals


def build_snapshot(snapshot_id: int, partition: str, cut: int, rows: Iterable[LedgerRow]) -> InventorySnapshot:
    ordered = tuple(sorted(rows, key=lambda row: row.record_id))
    return InventorySnapshot(snapshot_id, partition, cut, ordered, rollup_rows(ordered))


def verify_snapshot(snapshot: InventorySnapshot) -> None:
    require_number(snapshot.snapshot_id, 'snapshot_id')
    require_number(snapshot.cut, 'snapshot cut')
    require_label(snapshot.partition, 'snapshot partition')
    previous = None
    for row in snapshot.rows:
        validate_row(row)
        if row.partition != snapshot.partition:
            raise ValueError(f'row {row.record_id!r} belongs to a different partition')
        if previous is not None and row.record_id <= previous:
            raise ValueError('snapshot rows must have unique, increasing record identifiers')
        previous = row.record_id
    count, total = snapshot.totals.row_count, snapshot.totals.reading_sum
    if type(count) is not int or type(total) is not int or snapshot.totals != rollup_rows(snapshot.rows):
        raise ValueError(f'snapshot {snapshot.snapshot_id} has inconsistent totals')

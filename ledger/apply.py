from sqlite3 import Connection

from ledger.entries import CommitBlock, LedgerChange, LedgerRow, require_prior, validate_change_shape
from ledger.partitions import require_partition
from ledger.store import fetch_current_row, last_commit_number, write_commit


def apply_commit(source: Connection, changes: tuple[LedgerChange, ...]) -> int:
    number = last_commit_number(source) + 1
    working: dict[str, LedgerRow | None] = {}
    checked_partitions = set()
    for ordinal, change in enumerate(changes):
        validate_change_shape(change)
        for row in (change.before, change.after):
            if row is not None and row.partition not in checked_partitions:
                require_partition(source, row.partition)
                checked_partitions.add(row.partition)
        record_id = change.record_id
        actual = working[record_id] if record_id in working else fetch_current_row(source, record_id)
        require_prior(actual, change.before, context=f'commit {number}, change {ordinal}, record {record_id!r}')
        working[record_id] = change.after
    write_commit(source, CommitBlock(number, changes), working)
    return number

from sqlite3 import Connection

from ledger.entries import CommitBlock, LedgerRow, require_commit_span, require_number, require_prior
from ledger.store import commit_exists, fetch_commit_blocks


def require_sealed_cut(source: Connection, cut: int) -> None:
    require_number(cut, 'cut')
    if not commit_exists(source, cut):
        raise ValueError(f'cut {cut} is not sealed')


def read_commit_range(source: Connection, after: int, through: int) -> tuple[CommitBlock, ...]:
    return fetch_commit_blocks(source, after, through)


def rows_at_cut(source: Connection, partition: str, cut: int) -> tuple[LedgerRow, ...]:
    require_sealed_cut(source, cut)
    blocks = read_commit_range(source, 0, cut)
    require_commit_span(blocks, 0, cut)
    rows: dict[str, LedgerRow] = {}
    for block in blocks:
        for change in block.changes:
            record_id = change.record_id
            require_prior(rows.get(record_id), change.before, context=f'history {block.number}, record {record_id!r}')
            if change.after is None:
                del rows[record_id]
            else:
                rows[record_id] = change.after
    return tuple(row for row in rows.values() if row.partition == partition)

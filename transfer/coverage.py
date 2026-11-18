from sqlite3 import Connection

from ledger.entries import CommitBlock, require_commit_span
from ledger.history import read_commit_range


def collect_commit_evidence(source: Connection, after: int, through: int) -> tuple[CommitBlock, ...]:
    blocks = read_commit_range(source, after, through)
    require_commit_span(blocks, after, through)
    return blocks

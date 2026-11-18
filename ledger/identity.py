"""Identity links recorded by sealed sample-number merges.

A merge makes one main number (*retired*) point at another (*kept*).  The link
is effective from the sealed commit that performed the merge and never changes;
later corrections and transfers move the surviving main number only.
"""
from dataclasses import dataclass
from sqlite3 import Connection

from ledger.entries import LedgerRow, decode_measurement, encode_measurement, require_number


@dataclass(frozen=True, slots=True)
class IdentityLink:
    retired_id: str
    kept_id: str
    effective_commit: int
    retired_before: LedgerRow
    kept_before: LedgerRow
    kept_after: LedgerRow


def initialize_identity_schema(source: Connection) -> None:
    source.execute('''
        CREATE TABLE IF NOT EXISTS identity_links (
            retired_id TEXT PRIMARY KEY,
            kept_id TEXT NOT NULL,
            effective_commit INTEGER NOT NULL UNIQUE REFERENCES ledger_commits(number)
        )
    ''')
    source.execute('''
        CREATE TABLE IF NOT EXISTS identity_evidence (
            effective_commit INTEGER PRIMARY KEY REFERENCES identity_links(effective_commit),
            retired_id TEXT NOT NULL,
            retired_partition TEXT NOT NULL,
            retired_reading TEXT NOT NULL,
            kept_partition TEXT NOT NULL,
            kept_reading_before TEXT NOT NULL,
            kept_reading_after TEXT NOT NULL
        )
    ''')


def _row(record_id: str, partition: str, reading: str) -> LedgerRow:
    return LedgerRow(record_id, partition, decode_measurement(reading))


def save_identity_link(source: Connection, link: IdentityLink) -> None:
    require_number(link.effective_commit, 'merge commit')
    source.execute('INSERT INTO identity_links VALUES (?, ?, ?)',
                   (link.retired_id, link.kept_id, link.effective_commit))
    source.execute('INSERT INTO identity_evidence VALUES (?, ?, ?, ?, ?, ?, ?)', (
        link.effective_commit, link.retired_id,
        link.retired_before.partition, encode_measurement(link.retired_before.reading),
        link.kept_before.partition, encode_measurement(link.kept_before.reading),
        encode_measurement(link.kept_after.reading),
    ))


def direct_identity(source: Connection, record_id: str) -> str | None:
    row = source.execute('SELECT kept_id FROM identity_links WHERE retired_id = ?',
                         (record_id,)).fetchone()
    return None if row is None else row[0]


def fetch_links_at(source: Connection, cut: int) -> tuple[IdentityLink, ...]:
    """All links effective no later than ``cut`` in seal order."""
    links = []
    for effective, retired, kept, rp, rr, kp, krb, kra in source.execute('''
        SELECT l.effective_commit, l.retired_id, l.kept_id,
               e.retired_partition, e.retired_reading,
               e.kept_partition, e.kept_reading_before, e.kept_reading_after
        FROM identity_links AS l JOIN identity_evidence AS e USING (effective_commit)
        WHERE l.effective_commit <= ? ORDER BY l.effective_commit
    ''', (cut,)):
        links.append(IdentityLink(
            retired, kept, effective,
            _row(retired, rp, rr),
            _row(kept, kp, krb),
            _row(kept, kp, kra),
        ))
    return tuple(links)


def fetch_link(source: Connection, retired_id: str) -> IdentityLink | None:
    found = source.execute('''
        SELECT l.effective_commit, l.kept_id,
               e.retired_partition, e.retired_reading,
               e.kept_partition, e.kept_reading_before, e.kept_reading_after
        FROM identity_links AS l JOIN identity_evidence AS e USING (effective_commit)
        WHERE l.retired_id = ?
    ''', (retired_id,)).fetchone()
    if found is None:
        return None
    effective, kept, rp, rr, kp, krb, kra = found
    return IdentityLink(
        retired_id, kept, effective,
        _row(retired_id, rp, rr),
        _row(kept, kp, krb),
        _row(kept, kp, kra),
    )


def resolve_identity(source: Connection, record_id: str) -> str:
    """Follow the merge chain to the current surviving main number."""
    seen = set()
    current = record_id
    while True:
        if current in seen:
            raise ValueError(f'identity chain for {record_id!r} contains a cycle')
        seen.add(current)
        target = direct_identity(source, current)
        if target is None:
            return current
        current = target

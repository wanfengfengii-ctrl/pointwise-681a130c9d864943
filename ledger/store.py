from sqlite3 import Connection

from ledger.entries import CommitBlock, EvidenceError, LedgerChange, LedgerRow
from ledger.entries import decode_measurement, encode_measurement


def initialize_ledger_schema(source: Connection) -> None:
    source.execute('''
        CREATE TABLE IF NOT EXISTS ledger_commits (
            number INTEGER PRIMARY KEY,
            change_count INTEGER NOT NULL CHECK (change_count >= 0)
        )
    ''')
    source.execute('''
        CREATE TABLE IF NOT EXISTS ledger_changes (
            commit_number INTEGER NOT NULL REFERENCES ledger_commits(number),
            ordinal INTEGER NOT NULL,
            record_id TEXT NOT NULL,
            before_partition TEXT REFERENCES partitions(partition),
            before_reading TEXT,
            after_partition TEXT REFERENCES partitions(partition),
            after_reading TEXT,
            PRIMARY KEY (commit_number, ordinal),
            CHECK ((before_partition IS NULL) = (before_reading IS NULL)),
            CHECK ((after_partition IS NULL) = (after_reading IS NULL)),
            CHECK (before_partition IS NOT NULL OR after_partition IS NOT NULL)
        )
    ''')
    source.execute('''
        CREATE TABLE IF NOT EXISTS ledger_current (
            record_id TEXT PRIMARY KEY,
            partition TEXT NOT NULL REFERENCES partitions(partition),
            reading TEXT NOT NULL
        )
    ''')


def last_commit_number(source: Connection) -> int:
    return source.execute('SELECT COALESCE(MAX(number), 0) FROM ledger_commits').fetchone()[0]


def commit_exists(source: Connection, number: int) -> bool:
    return source.execute('SELECT 1 FROM ledger_commits WHERE number = ?', (number,)).fetchone() is not None


def _restore_image(record_id: str, partition: str | None, reading) -> LedgerRow | None:
    return None if partition is None else LedgerRow(record_id, partition, decode_measurement(reading))


def fetch_commit_blocks(source: Connection, after: int, through: int) -> tuple[CommitBlock, ...]:
    blocks = source.execute('''
        SELECT number, change_count FROM ledger_commits
        WHERE number > ? AND number <= ? ORDER BY number
    ''', (after, through)).fetchall()
    changes = {number: [] for number, _ in blocks}
    for number, ordinal, record_id, bp, br, ap, ar in source.execute('''
        SELECT commit_number, ordinal, record_id, before_partition, before_reading,
               after_partition, after_reading
        FROM ledger_changes WHERE commit_number > ? AND commit_number <= ?
        ORDER BY commit_number, ordinal
    ''', (after, through)):
        members = changes.get(number)
        if members is None or ordinal != len(members):
            raise EvidenceError(f'inconsistent change order for commit {number}')
        members.append(LedgerChange(record_id, _restore_image(record_id, bp, br), _restore_image(record_id, ap, ar)))
    result = []
    for number, count in blocks:
        if count != len(changes[number]):
            raise EvidenceError(f'incomplete changes for sealed commit {number}')
        result.append(CommitBlock(number, tuple(changes[number])))
    return tuple(result)


def fetch_current_row(source: Connection, record_id: str) -> LedgerRow | None:
    row = source.execute('SELECT partition, reading FROM ledger_current WHERE record_id = ?', (record_id,)).fetchone()
    return _restore_image(record_id, *row) if row is not None else None


def _image_columns(row: LedgerRow | None):
    return (None, None) if row is None else (row.partition, encode_measurement(row.reading))


def write_commit(source: Connection, block: CommitBlock, working: dict[str, LedgerRow | None]) -> None:
    source.execute('INSERT INTO ledger_commits VALUES (?, ?)', (block.number, len(block.changes)))
    source.executemany('INSERT INTO ledger_changes VALUES (?, ?, ?, ?, ?, ?, ?)', (
        (block.number, ordinal, change.record_id, *_image_columns(change.before), *_image_columns(change.after))
        for ordinal, change in enumerate(block.changes)
    ))
    for record_id, row in working.items():
        if row is None:
            source.execute('DELETE FROM ledger_current WHERE record_id = ?', (record_id,))
        else:
            source.execute('''
                INSERT INTO ledger_current VALUES (?, ?, ?)
                ON CONFLICT (record_id) DO UPDATE SET partition = excluded.partition, reading = excluded.reading
            ''', (record_id, row.partition, encode_measurement(row.reading)))

from sqlite3 import Connection

from ledger.entries import require_number


def initialize_package_schema(source: Connection) -> None:
    source.execute('''
        CREATE TABLE IF NOT EXISTS transfer_packages (
            package_id INTEGER PRIMARY KEY,
            target INTEGER NOT NULL REFERENCES ledger_commits(number),
            payload BLOB NOT NULL
        )
    ''')
    source.execute('''
        CREATE TABLE IF NOT EXISTS transfer_receipts (
            package_id INTEGER PRIMARY KEY REFERENCES transfer_packages(package_id),
            receipt_id TEXT NOT NULL UNIQUE
        )
    ''')


def allocate_package_id(source: Connection) -> int:
    return source.execute('SELECT COALESCE(MAX(package_id), 0) FROM transfer_packages').fetchone()[0] + 1


def save_package(source: Connection, package_id: int, target: int, payload: bytes) -> None:
    source.execute('INSERT INTO transfer_packages VALUES (?, ?, ?)', (package_id, target, payload))
    source.execute('INSERT INTO transfer_receipts VALUES (?, ?)', (package_id, f'issue-{package_id}'))


def fetch_package_bytes(source: Connection, package_id: int) -> bytes:
    require_number(package_id, 'package_id')
    row = source.execute('''
        SELECT p.payload FROM transfer_packages AS p JOIN transfer_receipts USING (package_id)
        WHERE p.package_id = ?
    ''', (package_id,)).fetchone()
    if row is None:
        raise KeyError(package_id)
    return row[0]

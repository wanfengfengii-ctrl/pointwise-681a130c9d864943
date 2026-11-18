from sqlite3 import Connection

from intake.requests import RequestBinding


def initialize_request_schema(source: Connection) -> None:
    source.execute('''
        CREATE TABLE IF NOT EXISTS request_bindings (
            request_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            commit_number INTEGER NOT NULL UNIQUE REFERENCES ledger_commits(number)
        )
    ''')


def fetch_request(source: Connection, request_id: str) -> RequestBinding | None:
    found = source.execute(
        'SELECT request_id, payload, commit_number FROM request_bindings WHERE request_id = ?',
        (request_id,),
    ).fetchone()
    return RequestBinding(*found) if found is not None else None


def bind_request(source: Connection, binding: RequestBinding) -> None:
    source.execute(
        'INSERT INTO request_bindings VALUES (?, ?, ?)',
        (binding.request_id, binding.payload, binding.commit_number),
    )

from sqlite3 import Connection

from intake.records import InvalidInputError, parse_changes
from intake.requests import RequestBinding, RequestConflictError, encode_ordered_request
from intake.store import bind_request, fetch_request
from ledger.apply import apply_commit
from ledger.entries import require_label
from ledger.transactions import source_write


def apply(source: Connection, request_id: str, changes) -> int:
    try:
        require_label(request_id, 'request_id')
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    ordered = parse_changes(changes)
    payload = encode_ordered_request(ordered)
    with source_write(source):
        previous = fetch_request(source, request_id)
        if previous is not None:
            if previous.payload != payload:
                raise RequestConflictError(f'request {request_id!r} already binds different changes')
            return previous.commit_number
        number = apply_commit(source, ordered)
        bind_request(source, RequestBinding(request_id, payload, number))
        return number

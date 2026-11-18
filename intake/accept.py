from sqlite3 import Connection

from intake.records import InvalidInputError, parse_changes
from intake.requests import APPLY_KIND, RequestBinding, RequestConflictError, encode_ordered_request
from intake.store import bind_request, fetch_request
from intake.merge import merge_sample  # re-exported as intake.accept.merge_sample
from ledger.apply import apply_commit
from ledger.entries import LedgerChange, LedgerRow, require_label
from ledger.identity import resolve_identity
from ledger.transactions import source_write


def _resolve_operand(source: Connection, change, effective: dict[str, str]) -> str:
    record_id = change.record_id
    main_id = effective.get(record_id)
    if main_id is None:
        main_id = resolve_identity(source, record_id)
        effective[record_id] = main_id
    return main_id


def _normalize_changes(source: Connection, changes: tuple[LedgerChange, ...]) -> tuple[LedgerChange, ...]:
    """Rewrite retired numbers onto their current main numbers.

    A retired number submitted for a fresh insertion has no current row and is
    rejected; ordinary changes are verified and written against the surviving
    main number, so a batch mixing old and main numbers sees one shared row.
    """
    effective: dict[str, str] = {}
    normalized = []
    for change in changes:
        main_id = _resolve_operand(source, change, effective)
        if main_id != change.record_id:
            if change.before is None:
                raise ValueError(f'retired number {change.record_id!r} cannot open a new row')
            before = LedgerRow(main_id, change.before.partition, change.before.reading)
            after = None if change.after is None else LedgerRow(
                main_id, change.after.partition, change.after.reading)
            change = LedgerChange(main_id, before, after)
        normalized.append(change)
    return tuple(normalized)


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
            if previous.kind != APPLY_KIND or previous.payload != payload:
                raise RequestConflictError(f'request {request_id!r} already binds different changes')
            return previous.commit_number
        normalized = _normalize_changes(source, ordered)
        number = apply_commit(source, normalized)
        bind_request(source, RequestBinding(request_id, payload, number, APPLY_KIND))
        return number

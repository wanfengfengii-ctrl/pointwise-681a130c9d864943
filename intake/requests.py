import json
from dataclasses import dataclass

from ledger.entries import LedgerChange, LedgerRow, encode_measurement


class RequestConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RequestBinding:
    request_id: str
    payload: str
    commit_number: int


def _image_key(row: LedgerRow | None):
    if row is None:
        return None
    return [row.partition, encode_measurement(row.reading)]


def encode_ordered_request(changes: tuple[LedgerChange, ...]) -> str:
    content = [[change.record_id, _image_key(change.before), _image_key(change.after)] for change in changes]
    return json.dumps(content, ensure_ascii=False, separators=(',', ':'))

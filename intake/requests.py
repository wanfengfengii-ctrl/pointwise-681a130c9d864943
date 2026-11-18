import json
from dataclasses import dataclass

from ledger.entries import LedgerChange, LedgerRow, encode_measurement


class RequestConflictError(ValueError):
    pass


APPLY_KIND = 'apply'
MERGE_KIND = 'merge'


@dataclass(frozen=True, slots=True)
class RequestBinding:
    request_id: str
    payload: str
    commit_number: int
    kind: str = 'apply'


def _image_key(row: LedgerRow | None):
    if row is None:
        return None
    return [row.partition, encode_measurement(row.reading)]


def encode_ordered_request(changes: tuple[LedgerChange, ...]) -> str:
    content = [[change.record_id, _image_key(change.before), _image_key(change.after)] for change in changes]
    return json.dumps(content, ensure_ascii=False, separators=(',', ':'))


def encode_merge_request(retired_id: str, kept_id: str,
                         retired_before: LedgerRow, kept_before: LedgerRow, reading: int) -> str:
    # Mapping keys are sorted so the rendered request identity never depends on
    # the order in which the caller built its image dictionaries.
    content = {
        'kept_before': _image_key(kept_before),
        'kept_id': kept_id,
        'reading': encode_measurement(reading),
        'retired_before': _image_key(retired_before),
        'retired_id': retired_id,
    }
    return json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

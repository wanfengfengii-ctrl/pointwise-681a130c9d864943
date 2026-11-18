from collections.abc import Mapping, Sequence

from ledger.entries import LedgerChange, LedgerRow, validate_change_shape


class InvalidInputError(ValueError):
    pass


def _image(record_id: str, data) -> LedgerRow | None:
    if data is None:
        return None
    if not isinstance(data, Mapping) or set(data) != {'partition', 'reading'}:
        raise InvalidInputError('an image requires partition and reading')
    return LedgerRow(record_id, data['partition'], data['reading'])


def parse_changes(changes) -> tuple[LedgerChange, ...]:
    if not isinstance(changes, Sequence) or isinstance(changes, (str, bytes, bytearray)):
        raise InvalidInputError('changes must be an ordered sequence')
    parsed = []
    for index, data in enumerate(changes):
        try:
            if not isinstance(data, Mapping) or set(data) != {'record_id', 'before', 'after'}:
                raise InvalidInputError('a change requires record_id, before and after')
            record_id = data['record_id']
            change = LedgerChange(record_id, _image(record_id, data['before']), _image(record_id, data['after']))
            validate_change_shape(change)
        except ValueError as exc:
            raise InvalidInputError(f'change {index}: {exc}') from exc
        parsed.append(change)
    return tuple(parsed)

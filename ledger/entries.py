import re
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class LedgerRow:
    record_id: str
    partition: str
    reading: int


@dataclass(frozen=True, slots=True)
class LedgerChange:
    record_id: str
    before: LedgerRow | None
    after: LedgerRow | None


@dataclass(frozen=True, slots=True)
class CommitBlock:
    number: int
    changes: tuple[LedgerChange, ...]


class PriorMismatchError(ValueError):
    pass


class EvidenceError(ValueError):
    pass


def require_label(value, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f'{field} must be a nonempty string')
    value.encode('utf-8')
    return value


def require_number(value, field: str) -> int:
    if type(value) is not int or not 1 <= value <= (1 << 63) - 1:
        raise ValueError(f'{field} must be a positive SQLite integer')
    return value


def encode_measurement(value: int) -> str:
    return str(Decimal(value))


def decode_measurement(value) -> int:
    if type(value) is int:
        return value
    if type(value) is str and re.fullmatch(r'0|-?[1-9][0-9]*', value):
        return int(Decimal(value))
    raise ValueError('measurement must be an integer or its canonical decimal text')


def validate_row(row: LedgerRow) -> None:
    require_label(row.record_id, 'record_id')
    require_label(row.partition, 'partition')
    if type(row.reading) is not int:
        raise ValueError('reading must be an integer in micrograms per litre')


def validate_change_shape(change: LedgerChange) -> None:
    require_label(change.record_id, 'record_id')
    if change.before is None and change.after is None:
        raise ValueError('a change requires at least one image')
    for image in (change.before, change.after):
        if image is not None:
            validate_row(image)
            if image.record_id != change.record_id:
                raise ValueError('image record does not match the change record')


def require_prior(actual: LedgerRow | None, expected: LedgerRow | None, *, context: str) -> None:
    if actual != expected:
        raise PriorMismatchError(f'prior image disagrees with the ledger at {context}')


def require_commit_span(blocks: tuple[CommitBlock, ...], after: int, through: int) -> None:
    if through < after or len(blocks) != through - after:
        raise EvidenceError(f'incomplete sealed commit span ({after}, {through}]')
    for number, block in enumerate(blocks, start=after + 1):
        if block.number != number:
            raise EvidenceError(f'expected sealed commit {number}, got {block.number}')

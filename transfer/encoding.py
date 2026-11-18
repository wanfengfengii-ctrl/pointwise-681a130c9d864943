import json
from decimal import Decimal

from inventory.reconcile import InventorySnapshot
from inventory.rollup import PartitionTotals
from ledger.entries import CommitBlock, LedgerChange, LedgerRow
from ledger.entries import decode_measurement, encode_measurement, require_label, require_number, validate_change_shape
from transfer.manifest import PACKAGE_FORMAT_VERSION, PackageManifest


def _image_data(row: LedgerRow | None):
    return None if row is None else {'partition': row.partition, 'reading': encode_measurement(row.reading)}


def _change_data(change: LedgerChange) -> dict:
    return {'record_id': change.record_id, 'before': _image_data(change.before), 'after': _image_data(change.after)}


def _origin_data(origin: InventorySnapshot) -> dict:
    return {
        'snapshot_id': origin.snapshot_id,
        'partition': origin.partition,
        'cut': origin.cut,
        'rows': [dict(record_id=row.record_id, **_image_data(row)) for row in origin.rows],
        'totals': {'row_count': origin.totals.row_count, 'reading_sum': encode_measurement(origin.totals.reading_sum)},
    }


def encode_package(manifest: PackageManifest) -> bytes:
    data = {
        'version': PACKAGE_FORMAT_VERSION,
        'package_id': manifest.package_id,
        'target': manifest.target,
        'origins': [_origin_data(origin) for origin in manifest.origins],
        'commits': [
            {'number': block.number, 'change_count': len(block.changes),
             'changes': [_change_data(change) for change in block.changes]}
            for block in manifest.commits
        ],
    }
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON field {key!r}')
        result[key] = value
    return result


def _invalid_constant(token: str):
    raise ValueError(f'invalid JSON constant {token!r}')


def _fields(data, *names: str) -> None:
    if type(data) is not dict or data.keys() != set(names):
        raise ValueError(f'expected object fields {", ".join(names)}')


def _array(data, field: str) -> list:
    if type(data) is not list:
        raise ValueError(f'{field} must be an array')
    return data


def _read_image(record_id: str, data) -> LedgerRow | None:
    if data is None:
        return None
    _fields(data, 'partition', 'reading')
    return LedgerRow(record_id, data['partition'], decode_measurement(data['reading']))


def _read_change(data) -> LedgerChange:
    _fields(data, 'record_id', 'before', 'after')
    record_id = data['record_id']
    change = LedgerChange(record_id, _read_image(record_id, data['before']), _read_image(record_id, data['after']))
    validate_change_shape(change)
    return change


def _read_origin(data) -> InventorySnapshot:
    _fields(data, 'snapshot_id', 'partition', 'cut', 'rows', 'totals')
    totals = data['totals']
    _fields(totals, 'row_count', 'reading_sum')
    rows = []
    for row in _array(data['rows'], 'rows'):
        _fields(row, 'record_id', 'partition', 'reading')
        rows.append(LedgerRow(row['record_id'], row['partition'], decode_measurement(row['reading'])))
    return InventorySnapshot(
        require_number(data['snapshot_id'], 'snapshot_id'),
        require_label(data['partition'], 'partition'),
        require_number(data['cut'], 'snapshot cut'),
        tuple(rows),
        PartitionTotals(totals['row_count'], decode_measurement(totals['reading_sum'])),
    )


def _read_block(data) -> CommitBlock:
    _fields(data, 'number', 'change_count', 'changes')
    number = require_number(data['number'], 'commit number')
    changes = _array(data['changes'], 'changes')
    if type(data['change_count']) is not int or data['change_count'] != len(changes):
        raise ValueError(f'inconsistent change count for commit {number}')
    return CommitBlock(number, tuple(_read_change(change) for change in changes))


def decode_package(payload: bytes) -> PackageManifest:
    if type(payload) is not bytes:
        raise ValueError('package payload must be bytes')
    data = json.loads(payload.decode('utf-8'), object_pairs_hook=_object,
                      parse_int=lambda text: int(Decimal(text)), parse_constant=_invalid_constant)
    _fields(data, 'version', 'package_id', 'target', 'origins', 'commits')
    if type(data['version']) is not int or data['version'] != PACKAGE_FORMAT_VERSION:
        raise ValueError('unsupported package version')
    return PackageManifest(
        require_number(data['package_id'], 'package_id'),
        require_number(data['target'], 'target'),
        tuple(_read_origin(origin) for origin in _array(data['origins'], 'origins')),
        tuple(_read_block(block) for block in _array(data['commits'], 'commits')),
    )

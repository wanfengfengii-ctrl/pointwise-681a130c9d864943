import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intake.accept import apply
from inventory.capture import capture
from ledger.transactions import open_source
from replica.store import open_replica


def change(record_id, before, after):
    def image(value):
        return None if value is None else {'partition': value[0], 'reading': value[1]}

    return {'record_id': record_id, 'before': image(before), 'after': image(after)}


def mixed_origins(source):
    for number in range(1, 10):
        apply(source, f'empty-{number}', [])
    apply(source, 'observation', [change('x', None, ('A', 17))])
    apply(source, 'move-to-b', [change('x', ('A', 17), ('B', 17))])
    apply(source, 'correct-at-b', [change('x', ('B', 17), ('B', 19))])
    apply(source, 'return-to-a', [change('x', ('B', 19), ('A', 23))])
    return capture(source, 'A', 10), capture(source, 'B', 12)


def external_mixed(target):
    path = Path(__file__).with_name('fixtures') / 'mixed_cut.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['target'] = target
    data['commits'] = [block for block in data['commits'] if block['number'] <= target]
    return data


def external_same_cut(target=11):
    return {
        'version': 1, 'package_id': 41, 'target': target,
        'origins': [
            {'snapshot_id': 51, 'partition': 'A', 'cut': 10,
             'rows': [{'record_id': 'x', 'partition': 'A', 'reading': 17}],
             'totals': {'row_count': 1, 'reading_sum': 17}},
            {'snapshot_id': 52, 'partition': 'B', 'cut': 10, 'rows': [],
             'totals': {'row_count': 0, 'reading_sum': 0}},
        ],
        'commits': [{'number': 11, 'change_count': 0, 'changes': []}] if target == 11 else [],
    }


def raw(data):
    return json.dumps(data, separators=(', ', ': ')).encode('utf-8')


def accounts(result):
    return ([(row.partition, row.record_id, row.reading) for row in result.rows],
            [(partition, totals.row_count, totals.reading_sum) for partition, totals in result.totals])


def issued_counts(source):
    return (source.execute('SELECT COUNT(*) FROM transfer_packages').fetchone()[0],
            source.execute('SELECT COUNT(*) FROM transfer_receipts').fetchone()[0])


def restore_counts(replica):
    return (replica.execute('SELECT COUNT(*) FROM restore_results').fetchone()[0],
            replica.execute('SELECT COUNT(*) FROM restore_rows').fetchone()[0],
            replica.execute('SELECT COUNT(*) FROM restore_totals').fetchone()[0],
            replica.execute('SELECT COUNT(*) FROM restore_receipts').fetchone()[0])


class LakeTestCase(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.source_path = Path(directory) / 'source.sqlite'
        self.replica_path = Path(directory) / 'replica.sqlite'

    def source(self, partitions=('A', 'B')):
        connection = open_source(self.source_path, partitions)
        self.addCleanup(connection.close)
        return connection

    def replica(self):
        connection = open_replica(self.replica_path)
        self.addCleanup(connection.close)
        return connection

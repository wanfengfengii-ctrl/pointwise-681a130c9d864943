import argparse
import json
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from intake.accept import apply
from inventory.capture import capture
from ledger.transactions import open_source
from replica.load import restore
from replica.reports import report
from replica.store import open_replica
from transfer.publish import issue, package


def _account(result):
    return {
        'target': result.target,
        'rows': [[row.partition, row.record_id, row.reading] for row in result.rows],
        'totals': [[partition, total.row_count, total.reading_sum] for partition, total in result.totals],
    }


def run_day(directory: Path, sample: dict, verify_only: bool) -> int:
    source_path = directory / 'source.sqlite'
    replica_path = directory / 'night.sqlite'
    with closing(open_source(source_path, sample['partitions'])) as source:
        for request_id in sample['opening_empty_requests']:
            apply(source, request_id, [])
        for request in sample['requests']:
            apply(source, request['request_id'], request['changes'])
        a, b = capture(source, 'A', 10), capture(source, 'B', 12)
        ids = [issue(source, 12, [a.snapshot_id, b.snapshot_id]),
               issue(source, 13, [a.snapshot_id, b.snapshot_id]),
               issue(source, 12, [a.snapshot_id])]
        originals = [package(source, number) for number in ids]

    expected = [
        {'target': 12, 'rows': [['B', 'x', 19]], 'totals': [['A', 0, 0], ['B', 1, 19]]},
        {'target': 13, 'rows': [['A', 'x', 23]], 'totals': [['A', 1, 23], ['B', 0, 0]]},
        {'target': 12, 'rows': [], 'totals': [['A', 0, 0]]},
    ]
    actual = []
    results = []
    rejected = None
    with closing(open_replica(replica_path)) as replica:
        for index, payload in enumerate(originals):
            try:
                result = restore(replica, payload)
            except ValueError as exc:
                actual.append({'target': expected[index]['target'], 'rejected': str(exc)})
                rejected = True
            else:
                actual.append(_account(result))
                results.append(result)
    if rejected or actual != expected:
        print('REPLICA_CUT_MISMATCH ' + json.dumps({'expected': expected, 'actual': actual}), file=sys.stderr)
        return 1

    if not verify_only:
        with closing(open_source(source_path, sample['partitions'])) as source:
            edit = sample['follow_up']
            cut = apply(source, edit['request_id'], edit['changes'])
            follow_up = capture(source, 'A', cut)
            if [(row.record_id, row.reading) for row in follow_up.rows] != [('x', 29)]:
                raise AssertionError('the follow-up correction did not produce the expected source inventory')
            if [package(source, number) for number in ids] != originals:
                raise AssertionError('retained package bytes changed after the follow-up')

    with closing(open_replica(replica_path)) as replica:
        retained = [report(replica, result.result_id) for result in results]
    downstream = [_account(result) for result in retained]
    if downstream != expected or retained != results:
        raise AssertionError('retained accounts or receipts differ from the published values')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v5',
        'input': {'origins': [[a.partition, a.cut], [b.partition, b.cut]],
                  'requests': sample['requests'], 'targets': [12, 13], 'selected_only': ['A']},
        'expected': expected,
        'actual': actual,
        'downstream_expected': expected,
        'downstream_actual': downstream,
        'code_paths': ['intake/accept.py', 'inventory/capture.py', 'transfer/publish.py', 'replica/rebuild.py',
                       'replica/reports.py', 'replica/store.py'],
        'oracle': '手算 x 在提交 11 转到 B，12 读数为 19，13 转回 A 且为 23，未持有 x 的所选分区汇总为零。',
    }, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='运行清川水质资料湖的固定交接样例')
    parser.add_argument('--verify-handoff', action='store_true', help='核验目标分区账和原号领取')
    args = parser.parse_args()
    sample = json.loads((Path(__file__).resolve().parent / 'samples' / 'day.json').read_text(encoding='utf-8'))
    with TemporaryDirectory() as directory:
        return run_day(Path(directory), sample, args.verify_handoff)


if __name__ == '__main__':
    raise SystemExit(main())

"""Run the documented sample-number handoff through the public APIs."""
import argparse
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from intake.accept import apply
from intake.requests import RequestConflictError
from inventory.capture import capture
from ledger.entries import LedgerRow, PriorMismatchError
from ledger.store import last_commit_number
from ledger.transactions import open_source
from replica.load import restore
from replica.reports import report
from replica.store import open_replica
from tests.support import accounts, change, restore_counts
from transfer.publish import issue, package


EXPECTED = {
    "mixed-12": ([("B", "y", 21), ("B", "z", 5)], [("A", 0, 0), ("B", 2, 26)],
                 {"x": LedgerRow("y", "B", 21), "y": LedgerRow("y", "B", 21)}),
    "late-12": ([("B", "y", 21), ("B", "z", 5)], [("A", 0, 0), ("B", 2, 26)],
                {"x": LedgerRow("y", "B", 21)}),
    "partial-12": ([], [("A", 0, 0)], {"x": None, "y": None}),
    "chain-14": ([("B", "z", 23)], [("A", 0, 0), ("B", 1, 23)],
                 {"x": LedgerRow("z", "B", 23), "y": LedgerRow("z", "B", 23)}),
}


def same(actual, expected, context):
    if actual != expected:
        raise AssertionError(f"{context}: expected {expected!r}, got {actual!r}")


def rejects(exception, call, *args):
    try:
        call(*args)
    except exception:
        return
    raise AssertionError(f"{call.__name__} should raise {exception.__name__}")


@contextmanager
def reject_commit(db):
    def authorize(action, operation, *_):
        if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(authorize)
    try:
        yield
    finally:
        db.set_authorizer(None)


def prepare(source, merge_sample, issue_identity):
    for number in range(1, 10):
        apply(source, f"opening-{number}", [])
    apply(source, "initial", [change("x", None, ("A", 17)),
                              change("y", None, ("B", 19)), change("z", None, ("B", 5))])
    a10 = capture(source, "A", 10).snapshot_id
    x_before = {"partition": "A", "reading": 17}
    y_before = {"partition": "B", "reading": 19}
    first_merge = ("merge-xy", "x", "y", x_before, y_before, 21)
    with reject_commit(source):
        rejects(sqlite3.DatabaseError, merge_sample, source, *first_merge)
    same(last_commit_number(source), 10, "failed merge leaves sealed cut")
    rejects(PriorMismatchError, merge_sample, source, "wrong-prior", "x", "y",
            {"partition": "A", "reading": 16}, y_before, 21)
    same(merge_sample(source, *first_merge), 11, "first successful merge")
    apply(source, "seal-12", [])
    a12 = capture(source, "A", 12).snapshot_id
    b12 = capture(source, "B", 12).snapshot_id
    same(merge_sample(source, "merge-yz", "y", "z", {"partition": "B", "reading": 21},
                      {"partition": "B", "reading": 5}, 22), 13, "merge surviving main ID")
    edits = [change("x", ("B", 22), ("B", 23))]
    same(apply(source, "edit-through-x", edits), 14, "correction through x")
    same(merge_sample(source, *first_merge), 11, "retry after both merges")
    rejects(RequestConflictError, merge_sample, source, *first_merge[:-1], 20)
    rejects(ValueError, apply, source, "reuse-x", [change("x", None, ("A", 9))])
    same(last_commit_number(source), 14, "retries and rejected requests leave sealed cut")
    ids = {
        "mixed-12": issue_identity(source, 12, [a10, b12]),
        "late-12": issue_identity(source, 12, [a12, b12]),
        "partial-12": issue_identity(source, 12, [a10]),
        "chain-14": issue_identity(source, 14, [a10, b12]),
        "legacy-12": issue(source, 12, [a10, b12]),
    }
    return ids, {name: package(source, number) for name, number in ids.items()}


def measure(replica, name, result, lookup_sample):
    expected_rows, expected_totals, expected_lookup = EXPECTED[name]
    rows, totals = accounts(result)
    lookups = {key: lookup_sample(replica, result.result_id, key) for key in expected_lookup}
    same((rows, totals, lookups), (expected_rows, expected_totals, expected_lookup), name)
    return {"case": name, "target": result.target, "rows": rows, "totals": totals,
            "lookup": {key: None if row is None else [row.partition, row.record_id, row.reading]
                       for key, row in lookups.items()}}


def run(directory):
    from intake.accept import merge_sample
    from replica.reports import lookup_sample
    from transfer.publish import issue_identity

    source_path = directory / "source.sqlite"
    replica_path = directory / "night.sqlite"
    with closing(open_source(source_path, ("A", "B"))) as source:
        ids, originals = prepare(source, merge_sample, issue_identity)
    with closing(open_replica(replica_path)) as replica:
        legacy = restore(replica, originals["legacy-12"])
        same(accounts(legacy), EXPECTED["mixed-12"][:2], "V1 sees the sealed rows")
        rejects(ValueError, lookup_sample, replica, legacy.result_id, "x")
        before = restore_counts(replica)
        with reject_commit(replica):
            rejects(sqlite3.DatabaseError, restore, replica, originals["mixed-12"])
        same(restore_counts(replica), before, "failed restore leaves no published result")
        same(report(replica, legacy.result_id), legacy, "previous report after failed restore")
        results = {name: restore(replica, originals[name]) for name in EXPECTED}
        for name, result in results.items():
            measure(replica, name, result, lookup_sample)
    with closing(open_source(source_path, ("A", "B"))) as source:
        moved = [change("x", ("B", 23), ("A", 29))]
        same(apply(source, "move-after-issue", moved), 15, "source reopen and move through x")
        same({name: package(source, number) for name, number in ids.items()}, originals, "original bytes")
    with closing(open_replica(replica_path)) as replica:
        output = []
        for name, result in results.items():
            retained = report(replica, result.result_id)
            same(retained, result, "report and receipt after reopen")
            output.append(measure(replica, name, retained, lookup_sample))
        return output


def main():
    parser = argparse.ArgumentParser(description="核验样本号合并和固定件查询")
    parser.add_argument("--demo", action="store_true", help="展示重开后实际读取的目标账与旧号查询")
    args = parser.parse_args()
    with TemporaryDirectory() as work:
        rows = run(Path(work))
    if args.demo:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"已核对 {len(rows)} 份固定身份件及原版兼容")


if __name__ == "__main__":
    main()

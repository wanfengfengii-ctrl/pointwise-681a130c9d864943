from sqlite3 import Connection

from inventory.rollup import rollup_rows
from ledger.entries import LedgerRow
from replica.store import ReportDraft, RestoredReport, fetch_report


def build_report(target: int, working: dict[str, dict[str, LedgerRow]]) -> ReportDraft:
    seen = set()
    rows = []
    totals = []
    for partition, partition_rows in sorted(working.items()):
        ordered = [partition_rows[record_id] for record_id in sorted(partition_rows)]
        for row in ordered:
            if row.record_id in seen:
                raise ValueError(f'record {row.record_id!r} has multiple owners at target {target}')
            seen.add(row.record_id)
        rows.extend(ordered)
        totals.append((partition, rollup_rows(ordered)))
    return ReportDraft(target, tuple(rows), tuple(totals))


def report(replica: Connection, result_id: int) -> RestoredReport:
    return fetch_report(replica, result_id)

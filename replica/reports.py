from sqlite3 import Connection

from inventory.rollup import rollup_rows
from ledger.entries import LedgerRow, decode_measurement, require_number
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


def _identity_index(replica: Connection, result_id: int) -> dict[str, str]:
    return dict(replica.execute('''
        SELECT retired_id, kept_id FROM restore_identities WHERE result_id = ?
    ''', (result_id,)))


def lookup_sample(replica: Connection, result_id: int, record_id: str):
    """Return the effective main row visible under ``record_id`` at the result cut.

    Only a main number holding an active row inside the selected partitions
    counts.  Retired numbers follow the sealed merge chain; unknown numbers,
    withdrawn main numbers and numbers outside the selection return ``None``.
    V1 results carry no identity evidence and therefore raise ``ValueError``.
    """
    require_number(result_id, 'result_id')
    head = replica.execute(
        'SELECT 1 FROM restore_results WHERE result_id = ?', (result_id,)).fetchone()
    if head is None:
        raise KeyError(result_id)
    indexed = replica.execute(
        'SELECT 1 FROM restore_identity_results WHERE result_id = ?', (result_id,)).fetchone()
    if indexed is None:
        raise ValueError(f'result {result_id} has no identity evidence')
    index = _identity_index(replica, result_id)

    seen = set()
    current = record_id
    while current in index:
        if current in seen:
            raise ValueError(f'identity chain for {record_id!r} contains a cycle')
        seen.add(current)
        current = index[current]

    row = replica.execute('''
        SELECT partition, reading FROM restore_rows
        WHERE result_id = ? AND record_id = ?
    ''', (result_id, current)).fetchone()
    if row is None:
        return None
    return LedgerRow(current, row[0], decode_measurement(row[1]))

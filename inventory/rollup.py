from collections.abc import Iterable
from dataclasses import dataclass

from ledger.entries import LedgerRow


@dataclass(frozen=True, slots=True)
class PartitionTotals:
    row_count: int
    reading_sum: int


def rollup_rows(rows: Iterable[LedgerRow]) -> PartitionTotals:
    count = 0
    total = 0
    for row in rows:
        count += 1
        total += row.reading
    return PartitionTotals(count, total)

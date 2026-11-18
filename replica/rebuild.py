from ledger.entries import LedgerRow, require_prior
from replica.verify import verify_evidence_span, verify_origins
from transfer.manifest import PackageManifest


def rebuild(manifest: PackageManifest) -> dict[str, dict[str, LedgerRow]]:
    verify_origins(manifest)
    cuts = {origin.partition: origin.cut for origin in manifest.origins}
    verify_evidence_span(manifest, min(cuts.values()))
    working = {origin.partition: {row.record_id: row for row in origin.rows} for origin in manifest.origins}

    for block in manifest.commits:
        for ordinal, change in enumerate(block.changes):
            affected = {row.partition for row in (change.before, change.after) if row is not None}
            for partition in sorted(affected):
                if partition not in cuts or block.number <= cuts[partition]:
                    continue
                before = change.before if change.before and change.before.partition == partition else None
                after = change.after if change.after and change.after.partition == partition else None
                rows = working[partition]
                record_id = change.record_id
                require_prior(rows.get(record_id), before,
                              context=f'commit {block.number}, change {ordinal}, partition {partition!r}, record {record_id!r}')
                if after is None:
                    del rows[record_id]
                else:
                    rows[record_id] = after
    return working

from inventory.reconcile import verify_snapshot
from ledger.entries import require_commit_span
from transfer.manifest import PackageManifest


def verify_origins(manifest: PackageManifest) -> None:
    if not manifest.origins:
        raise ValueError('package requires origin snapshots')
    partitions = set()
    identifiers = set()
    for origin in manifest.origins:
        verify_snapshot(origin)
        if origin.partition in partitions or origin.snapshot_id in identifiers:
            raise ValueError('package has a duplicate origin partition or snapshot identifier')
        if origin.cut > manifest.target:
            raise ValueError(f'origin {origin.snapshot_id} is later than target {manifest.target}')
        partitions.add(origin.partition)
        identifiers.add(origin.snapshot_id)


def verify_evidence_span(manifest: PackageManifest, after: int) -> None:
    require_commit_span(manifest.commits, after, manifest.target)

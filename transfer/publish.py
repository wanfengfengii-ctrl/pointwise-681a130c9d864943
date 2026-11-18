from collections.abc import Iterable
from sqlite3 import Connection

from inventory.reconcile import verify_snapshot
from inventory.store import fetch_snapshot
from ledger.history import require_sealed_cut
from ledger.transactions import source_write
from transfer.coverage import collect_commit_evidence
from transfer.encoding import encode_package
from transfer.manifest import PackageManifest
from transfer.store import allocate_package_id, fetch_package_bytes, save_package


def issue(source: Connection, target: int, snapshot_ids: Iterable[int]) -> int:
    identifiers = tuple(snapshot_ids)
    if not identifiers:
        raise ValueError('at least one origin snapshot is required')
    with source_write(source):
        require_sealed_cut(source, target)
        origins = tuple(sorted((fetch_snapshot(source, number) for number in identifiers),
                               key=lambda origin: origin.partition))
        seen = set()
        for origin in origins:
            verify_snapshot(origin)
            if origin.partition in seen:
                raise ValueError(f'multiple origins for partition {origin.partition!r}')
            if origin.cut > target:
                raise ValueError(f'origin {origin.snapshot_id} is later than target {target}')
            seen.add(origin.partition)
        # The span from the oldest origin contains every selected partition's window.
        after = min(origin.cut for origin in origins)
        blocks = collect_commit_evidence(source, after, target)
        package_id = allocate_package_id(source)
        manifest = PackageManifest(package_id, target, origins, blocks)
        save_package(source, package_id, target, encode_package(manifest))
    return package_id


def package(source: Connection, package_id: int) -> bytes:
    return fetch_package_bytes(source, package_id)

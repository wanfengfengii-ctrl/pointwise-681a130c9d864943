from collections.abc import Iterable
from sqlite3 import Connection

from inventory.reconcile import verify_snapshot
from inventory.store import fetch_snapshot
from ledger.history import require_sealed_cut
from ledger.identity import fetch_links_at
from ledger.transactions import source_write
from transfer.coverage import collect_commit_evidence
from transfer.encoding import encode_package
from transfer.identity_encoding import encode_identity_package
from transfer.identity_engine import verify_identity_evidence
from transfer.identity_manifest import IdentityManifest
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


def issue_identity(source: Connection, target: int, snapshot_ids: Iterable[int]) -> int:
    """Issue a V2 fixed package carrying merge identity evidence."""
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
        after = min(origin.cut for origin in origins)
        blocks = collect_commit_evidence(source, after, target)
        after = min(origin.cut for origin in origins)
        blocks = collect_commit_evidence(source, after, target)
        # Links effective no later than the target, including merges earlier than
        # every selected origin cut whose chain still reaches a selected row.
        links = fetch_links_at(source, target)
        package_id = allocate_package_id(source)
        manifest = IdentityManifest(package_id, target, origins, blocks, links)
        # Full self-consistency check before the package bytes are sealed.
        verify_identity_evidence(manifest)
        save_package(source, package_id, target, encode_identity_package(manifest))
    return package_id

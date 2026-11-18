"""Evidence verification for V2 identity packages.

The same pure checks run on the source while issuing and on the offline replica
while restoring: origin snapshots, sealed commit span, identity declarations,
the two full merge images and the resulting row effects must all be complete and
mutually consistent.  Nothing here trusts an outside ledger; every fact used in
a decision comes from the package itself.
"""
from ledger.entries import LedgerChange
from replica.rebuild import rebuild as rebuild_rows
from replica.verify import verify_evidence_span, verify_origins
from transfer.identity_manifest import IdentityManifest


class IdentityEvidenceError(ValueError):
    pass


def _block_index(manifest: IdentityManifest) -> dict[int, dict[str, LedgerChange]]:
    return {block.number: {change.record_id: change for change in block.changes}
            for block in manifest.commits}


def verify_identity_evidence(manifest: IdentityManifest) -> None:
    verify_origins(manifest.base())
    selected = {origin.partition: origin.cut for origin in manifest.origins}
    verify_evidence_span(manifest, min(selected.values()))

    # Declarations themselves: unique direction, no cycles, sound images.
    direct: dict[str, str] = {}
    for link in manifest.identities:
        if link.retired_id in direct:
            raise IdentityEvidenceError(
                f'number {link.retired_id!r} has contradictory identity destinations')
        if link.retired_id == link.kept_id:
            raise IdentityEvidenceError('an identity link needs two different numbers')
        if link.kept_before.partition != link.kept_after.partition:
            raise IdentityEvidenceError(
                f'merge at commit {link.effective_commit} moves the surviving partition')
        direct[link.retired_id] = link.kept_id
    for start in direct:
        seen = set()
        current = start
        while current in direct:
            if current in seen:
                raise IdentityEvidenceError('identity declarations contain a cycle')
            seen.add(current)
            current = direct[current]

    blocks = _block_index(manifest)
    for link in manifest.identities:
        block_changes = blocks.get(link.effective_commit)
        if block_changes is None:
            # Earlier than every carried origin: its row effects sit inside the
            # origin inventories, so the declaration must not claim a row there.
            if link.effective_commit >= min(selected.values()):
                raise IdentityEvidenceError(
                    f'merge at commit {link.effective_commit} lacks row-effect evidence')
            continue
        retired_change = block_changes.get(link.retired_id)
        kept_change = block_changes.get(link.kept_id)
        expected_retired = LedgerChange(link.retired_id, link.retired_before, None)
        expected_kept = LedgerChange(link.kept_id, link.kept_before, link.kept_after)
        if retired_change != expected_retired or kept_change != expected_kept:
            raise IdentityEvidenceError(
                f'identity evidence at commit {link.effective_commit} disagrees with the row ledger')

    # Replay row effects from the package's own evidence.
    working = rebuild_rows(manifest.base())
    origin_owners = {row.record_id for origin in manifest.origins for row in origin.rows}

    # Pre-origin merges: an origin whose cut is at or after the merge must never
    # still carry the retired number in the partition it was withdrawn from; the
    # surviving row stays in the kept number's own partition and is already
    # attested by that partition's origin inventory.
    for link in manifest.identities:
        if link.effective_commit >= min(selected.values()):
            continue
        for origin in manifest.origins:
            if origin.cut < link.effective_commit:
                continue
            owners = {row.record_id: row for row in origin.rows}
            if link.retired_id in owners:
                raise IdentityEvidenceError(
                    f'pre-origin merge at {link.effective_commit}: retired number '
                    f'{link.retired_id!r} still owns an origin row')
            if origin.partition == link.kept_before.partition:
                survivor = owners.get(link.kept_id)
                if survivor is None or survivor.reading != link.kept_after.reading:
                    raise IdentityEvidenceError(
                        f'pre-origin merge at {link.effective_commit}: surviving row '
                        f'for {link.kept_id!r} is missing or disagrees with the identity evidence')

    # Final state: aliases cannot own rows, every chain must terminate sanely.
    final_owners = {record_id for rows in working.values() for record_id in rows}
    for retired_id in direct:
        if retired_id in final_owners:
            raise IdentityEvidenceError(
                f'retired number {retired_id!r} still owns a row at the target cut')
        seen = set()
        current = retired_id
        while current in direct:
            if current in seen:
                raise IdentityEvidenceError('identity declarations contain a cycle')
            seen.add(current)
            current = direct[current]

    # Missing-evidence guard.  An origin owner can only leave the selected rows
    # by an ordinary withdrawal or a declared merge.  A merge retirement is
    # structurally paired, in the same commit, with another record's image being
    # rewritten to the confirmed reading; an ordinary withdrawal has no such
    # companion.  A merge-shaped carried commit without a matching declaration is
    # therefore missing required evidence.  (A merge earlier than every origin
    # leaves no carried row effect and an already-consistent origin inventory:
    # with no declaration present there is nothing internally inconsistent to
    # reject, matching the boundary that signer identity is an external duty.)
    declared_commits = {link.effective_commit for link in manifest.identities}
    structural_merge_commits = set()
    for block in manifest.commits:
        retirements = [change for change in block.changes
                       if change.after is None and change.before is not None]
        rewrites = [change for change in block.changes if change.after is not None]
        if retirements and rewrites:
            structural_merge_commits.add(block.number)
    undeclared = structural_merge_commits - declared_commits
    if undeclared:
        raise IdentityEvidenceError(
            f'merge-shaped commit(s) {sorted(undeclared)} lack identity declarations')

    explicit_withdrawals = set()
    for block in manifest.commits:
        if block.number in structural_merge_commits:
            continue
        for change in block.changes:
            if change.after is None and change.before is not None \
                    and change.before.record_id == change.record_id:
                partition = change.before.partition
                if partition in selected and block.number > selected[partition]:
                    explicit_withdrawals.add(change.record_id)
    declared = set(direct)
    for owner in origin_owners:
        if owner not in final_owners and owner not in declared and owner not in explicit_withdrawals:
            raise IdentityEvidenceError(
                f'number {owner!r} leaves the ledger without identity or withdrawal evidence')

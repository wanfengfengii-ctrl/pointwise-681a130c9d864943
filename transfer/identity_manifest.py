from dataclasses import dataclass

from inventory.reconcile import InventorySnapshot
from ledger.entries import CommitBlock
from ledger.identity import IdentityLink
from transfer.manifest import PackageManifest


IDENTITY_FORMAT_VERSION = 2


@dataclass(frozen=True, slots=True)
class IdentityManifest:
    package_id: int
    target: int
    origins: tuple[InventorySnapshot, ...]
    commits: tuple[CommitBlock, ...]
    identities: tuple[IdentityLink, ...]

    def base(self) -> PackageManifest:
        return PackageManifest(self.package_id, self.target, self.origins, self.commits)

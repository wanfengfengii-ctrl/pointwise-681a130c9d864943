from dataclasses import dataclass

from inventory.reconcile import InventorySnapshot
from ledger.entries import CommitBlock


PACKAGE_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class PackageManifest:
    package_id: int
    target: int
    origins: tuple[InventorySnapshot, ...]
    commits: tuple[CommitBlock, ...]

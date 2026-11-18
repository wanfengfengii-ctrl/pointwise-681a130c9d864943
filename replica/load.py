from hashlib import sha256
from sqlite3 import Connection

from replica.rebuild import rebuild
from replica.reports import build_report
from replica.store import RestoredReport, publish_report
from transfer.identity_encoding import decode_identity_payload, package_format_version
from transfer.identity_engine import verify_identity_evidence
from transfer.encoding import decode_package


def restore(replica: Connection, payload: bytes) -> RestoredReport:
    version = package_format_version(payload)
    if version == 1:
        manifest = decode_package(payload)
        working = rebuild(manifest)
        draft = build_report(manifest.target, working)
        return publish_report(replica, draft, manifest.package_id, sha256(payload).hexdigest())

    manifest = decode_identity_payload(payload)
    # Rejects incomplete or contradictory evidence before any row is published.
    verify_identity_evidence(manifest)
    working = rebuild(manifest.base())
    draft = build_report(manifest.target, working)
    return publish_report(replica, draft, manifest.package_id, sha256(payload).hexdigest(),
                          identities=manifest.identities, identity_result=True)

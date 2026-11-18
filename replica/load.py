from hashlib import sha256
from sqlite3 import Connection

from replica.rebuild import rebuild
from replica.reports import build_report
from replica.store import RestoredReport, publish_report
from transfer.encoding import decode_package


def restore(replica: Connection, payload: bytes) -> RestoredReport:
    manifest = decode_package(payload)
    working = rebuild(manifest)
    draft = build_report(manifest.target, working)
    return publish_report(replica, draft, manifest.package_id, sha256(payload).hexdigest())

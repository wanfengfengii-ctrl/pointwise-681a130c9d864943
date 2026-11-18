"""Human-confirmed merge sealing (V2).

A merge occupies one formal commit: both full prior images must match the
current rows, the retired number's row is withdrawn, and the surviving number
keeps its original partition with the confirmed reading.  The identity link,
the row changes and the request receipt become visible in one commit.
"""
from collections.abc import Mapping
from sqlite3 import Connection

from intake.records import InvalidInputError
from intake.requests import MERGE_KIND, RequestBinding, RequestConflictError, encode_merge_request
from intake.store import bind_request, fetch_request
from ledger.entries import CommitBlock, LedgerChange, LedgerRow, PriorMismatchError, require_label, require_prior
from ledger.identity import IdentityLink, direct_identity, save_identity_link
from ledger.partitions import require_partition
from ledger.store import fetch_current_row, last_commit_number, write_commit
from ledger.transactions import source_write


def _image(record_id: str, data) -> LedgerRow:
    if not isinstance(data, Mapping) or set(data) != {'partition', 'reading'}:
        raise InvalidInputError('an image requires partition and reading')
    return LedgerRow(record_id, data['partition'], data['reading'])


def merge_sample(source: Connection, request_id: str, retired_id: str, kept_id: str,
                 retired_before, kept_before, reading: int) -> int:
    try:
        require_label(request_id, 'request_id')
        require_label(retired_id, 'retired_id')
        require_label(kept_id, 'kept_id')
        retired_image = _image(retired_id, retired_before)
        kept_image = _image(kept_id, kept_before)
        if type(reading) is not int:
            raise InvalidInputError('reading must be an integer in micrograms per litre')
    except ValueError as exc:
        if isinstance(exc, InvalidInputError):
            raise
        raise InvalidInputError(str(exc)) from exc

    payload = encode_merge_request(retired_id, kept_id, retired_image, kept_image, reading)
    with source_write(source):
        previous = fetch_request(source, request_id)
        if previous is not None:
            if previous.kind != MERGE_KIND or previous.payload != payload:
                raise RequestConflictError(f'request {request_id!r} already binds different content')
            return previous.commit_number

        if retired_id == kept_id:
            raise ValueError('a merge needs two different main numbers')
        # An already retired number cannot be a merge operand again.
        if direct_identity(source, retired_id) is not None or direct_identity(source, kept_id) is not None:
            raise ValueError('only currently active main numbers can be merged')

        require_partition(source, retired_image.partition)
        require_partition(source, kept_image.partition)
        retired_row = fetch_current_row(source, retired_id)
        kept_row = fetch_current_row(source, kept_id)
        number = last_commit_number(source) + 1
        # Missing operands fail here too: None never equals a full prior image.
        require_prior(retired_row, retired_image,
                      context=f'merge commit {number}, retired record {retired_id!r}')
        require_prior(kept_row, kept_image,
                      context=f'merge commit {number}, kept record {kept_id!r}')

        kept_after = LedgerRow(kept_id, kept_image.partition, reading)
        changes = (
            LedgerChange(retired_id, retired_image, None),
            LedgerChange(kept_id, kept_image, kept_after),
        )
        write_commit(source, CommitBlock(number, changes),
                     {retired_id: None, kept_id: kept_after})
        save_identity_link(source, IdentityLink(
            retired_id, kept_id, number, retired_image, kept_image, kept_after))
        bind_request(source, RequestBinding(request_id, payload, number, MERGE_KIND))
        return number

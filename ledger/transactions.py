import sqlite3
from contextlib import contextmanager

from intake.store import initialize_request_schema
from inventory.store import initialize_inventory_schema
from ledger.identity import initialize_identity_schema
from ledger.partitions import initialize_partition_catalog
from ledger.store import initialize_ledger_schema
from transfer.store import initialize_package_schema


@contextmanager
def source_write(source: sqlite3.Connection):
    source.execute('BEGIN IMMEDIATE')
    try:
        yield
        source.commit()
    except BaseException:
        source.rollback()
        raise


def open_source(path, partitions) -> sqlite3.Connection:
    source = sqlite3.connect(path, isolation_level=None)
    try:
        source.execute('PRAGMA foreign_keys = ON')
        with source_write(source):
            initialize_partition_catalog(source, partitions)
            initialize_ledger_schema(source)
            initialize_identity_schema(source)
            initialize_request_schema(source)
            initialize_inventory_schema(source)
            initialize_package_schema(source)
    except BaseException:
        source.close()
        raise
    return source

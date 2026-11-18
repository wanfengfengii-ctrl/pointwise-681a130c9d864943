from sqlite3 import Connection

from ledger.entries import require_label


def initialize_partition_catalog(source: Connection, partitions) -> None:
    if isinstance(partitions, (str, bytes, bytearray)):
        raise ValueError('partitions must be a collection of names')
    names = tuple(require_label(partition, 'partition') for partition in partitions)
    requested = set(names)
    if not requested or len(requested) != len(names):
        raise ValueError('partition configuration must be nonempty and unique')
    source.execute('CREATE TABLE IF NOT EXISTS partitions (partition TEXT PRIMARY KEY)')
    existing = {row[0] for row in source.execute('SELECT partition FROM partitions')}
    if existing:
        if existing != requested:
            raise ValueError('partition configuration conflicts with the stored catalog')
        return
    source.executemany('INSERT INTO partitions VALUES (?)', ((name,) for name in sorted(requested)))


def require_partition(source: Connection, partition: str) -> None:
    require_label(partition, 'partition')
    if source.execute('SELECT 1 FROM partitions WHERE partition = ?', (partition,)).fetchone() is None:
        raise KeyError(partition)

"""SQLite connection settings and short, explicit write transactions."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

MIN_SQLITE = (3, 51, 3)


def create_database(url: str, *, enforce_sqlite_version: bool = True):
    if not url.startswith('sqlite:///') or url.endswith(':memory:'):
        raise ValueError('A local SQLite database file is required')
    path = Path(url.removeprefix('sqlite:///')).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 5})

    @event.listens_for(engine, 'connect')
    def configure(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA busy_timeout=5000')
        cursor.execute('PRAGMA synchronous=FULL')
        cursor.close()

    with engine.connect() as connection:
        version = tuple(map(int, connection.exec_driver_sql('SELECT sqlite_version()').scalar_one().split('.')))
        if enforce_sqlite_version and version < MIN_SQLITE:
            engine.dispose()
            raise RuntimeError(f'SQLite >= 3.51.3 required; Python uses {sqlite3.sqlite_version}')
        mode = connection.exec_driver_sql('PRAGMA journal_mode=WAL').scalar_one()
        if mode.lower() != 'wal':
            raise RuntimeError('SQLite WAL could not be enabled')
    return engine


def migrate(engine):
    """Apply the Alembic migration to an explicitly supplied engine."""
    from alembic import command
    from alembic.config import Config
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    config.attributes['connection_engine'] = engine
    command.upgrade(config, 'head')


@contextmanager
def immediate_write_transaction(engine):
    with engine.connect() as connection:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        session = Session(bind=connection, expire_on_commit=False)
        try:
            yield session
            session.flush()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            session.close()

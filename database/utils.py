# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Utility functions for using the database."""

import os
import threading
from contextlib import contextmanager

import sqlalchemy

# pylint: disable=invalid-name,no-member
engine = None
session = None
lock = None


# How long a connection waits for another writer to release the database
# before giving up with "database is locked". SQLite serializes writers, and
# an experiment has at least two writer processes: the scheduler (in the
# dispatcher process) and the measurer (its own process). Python's sqlite3
# default of five seconds is far too short for that -- a single lost race
# raises out of measure_manager_inner_loop and kills measurement for the rest
# of the run.
SQLITE_BUSY_TIMEOUT_SECONDS = 5 * 60


def _configure_sqlite(sql_engine):
    """Puts |sql_engine|'s SQLite database in WAL mode and gives every
    connection a long busy timeout.

    WAL lets the measurer's readers run while the scheduler writes, instead of
    the two locking each other out; it is a property of the file, so setting it
    on any connection is enough. busy_timeout is per connection and has to be
    set on each one, hence the connect hook.
    """

    @sqlalchemy.event.listens_for(sql_engine, 'connect')
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # pylint: disable=unused-variable
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute(
                f'PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SECONDS * 1000}')
            # WAL already gives durability across process crashes; NORMAL only
            # risks the most recent commits if the host itself dies, which is
            # the right trade for a few hundred snapshot writes a minute.
            cursor.execute('PRAGMA synchronous=NORMAL')
        finally:
            cursor.close()


def initialize():
    """Initialize the database for use. Sets the database engine and session.
    Since this function is called when this module is imported one should rarely
    need to call it (tests are an exception)."""
    database_url = os.getenv('SQL_DATABASE_URL')
    if not database_url:
        raise RuntimeError(
            'SQL_DATABASE_URL must be set (e.g. sqlite:////path/to/local.db).')

    global engine
    engine = sqlalchemy.create_engine(database_url)
    if engine.dialect.name == 'sqlite':
        _configure_sqlite(engine)
    global session
    Session = sqlalchemy.orm.sessionmaker(bind=engine)
    session = Session()
    global lock
    lock = threading.Lock()
    return engine, session


def cleanup():
    """Close the session and dispose of the engine. This is useful for avoiding
    having too many connections and other weirdness when using
    multiprocessing."""
    global session
    if session:
        session.commit()
        session.close()
        session = None
    global engine
    if engine:
        engine.dispose()
    engine = None
    global lock
    lock = None


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    # pylint: disable=global-variable-not-assigned
    global session
    global engine
    global lock
    if session is None or engine is None or lock is None:
        initialize()
    lock.acquire()
    try:
        yield session
    except Exception as e:
        session.rollback()
        raise e
    finally:
        lock.release()


def add_all(entities):
    """Save all |entities| to the database connected to by session."""
    with session_scope() as scoped_session:
        scoped_session.add_all(entities)
        scoped_session.commit()


def bulk_save(entities):
    """Save all |entities| to the database connected to by session."""
    with session_scope() as scoped_session:
        scoped_session.bulk_save_objects(entities)
        scoped_session.commit()


def get_or_create(model, **kwargs):
    """If a |model| with the conditions specified by |kwargs| exists, then it is
    retrieved from the database. If not, it is created and saved to the
    database."""
    with session_scope() as scoped_session:
        instance = scoped_session.query(model).filter_by(**kwargs).first()
        if instance:
            return instance
        instance = model(**kwargs)
        scoped_session.add(instance)
        return instance

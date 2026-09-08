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
"""Tests for database/utils.py."""

import os
import sqlalchemy

from database import utils as db_utils


def _make_engine(database_url):
    """Returns an engine configured the way initialize() configures one."""
    engine = sqlalchemy.create_engine(database_url)
    if engine.dialect.name == 'sqlite':
        db_utils._configure_sqlite(engine)  # pylint: disable=protected-access
    return engine


def test_sqlite_file_database_uses_wal(tmp_path):
    """Tests that a file-backed SQLite database is put in WAL mode, so that the
    measurer's reads don't lock the scheduler's writes out."""
    engine = _make_engine(f'sqlite:///{os.path.join(str(tmp_path), "t.db")}')
    with engine.connect() as connection:
        journal_mode = connection.exec_driver_sql(
            'PRAGMA journal_mode').scalar()
    assert journal_mode == 'wal'


def test_sqlite_connections_get_a_long_busy_timeout(tmp_path):
    """Tests that connections wait for a contended write rather than failing
    with "database is locked" after sqlite3's five second default."""
    engine = _make_engine(f'sqlite:///{os.path.join(str(tmp_path), "t.db")}')
    with engine.connect() as connection:
        busy_timeout = connection.exec_driver_sql(
            'PRAGMA busy_timeout').scalar()
    assert busy_timeout == db_utils.SQLITE_BUSY_TIMEOUT_SECONDS * 1000

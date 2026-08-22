from contextlib import contextmanager

import pytest

import config
import db


class FakeCursor:
    def __init__(self, rows=None, row=None, lastrowid=7, fail=False):
        self.rows = rows or []
        self.row = row
        self.lastrowid = lastrowid
        self.fail = fail
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, args):
        self.executed.append((sql, args))
        if self.fail:
            raise RuntimeError("db failure")

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


def test_connect_kwargs_and_connection_modes(monkeypatch):
    kwargs = db._connect_kwargs()
    assert kwargs["host"] == config.DB_HOST
    assert kwargs["database"] == config.DB_NAME
    assert db._connect_kwargs(False)["database"] is None

    direct = object()
    monkeypatch.setattr(db.pymysql, "connect", lambda **_kwargs: direct)
    assert db.get_connection(False) is direct

    pooled = object()
    pool = type("Pool", (), {"connection": lambda self: pooled})()
    monkeypatch.setattr(db, "_get_pool", lambda: pool)
    assert db.get_connection() is pooled


def test_pool_created_once(monkeypatch):
    created = []

    class FakePool:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(db, "PooledDB", FakePool)
    first = db._get_pool()
    second = db._get_pool()
    assert first is second
    assert len(created) == 1
    assert created[0]["maxconnections"] == config.DB_POOL_SIZE


def test_db_cursor_commit_and_rollback(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: conn)
    with db.db_cursor(commit=True) as yielded:
        assert yielded is cursor
    assert (conn.commits, conn.rollbacks, conn.closes) == (1, 0, 1)

    failing_cursor = FakeCursor(fail=True)
    failing_conn = FakeConnection(failing_cursor)
    monkeypatch.setattr(db, "get_connection", lambda: failing_conn)
    with pytest.raises(RuntimeError):
        with db.db_cursor(commit=True) as yielded:
            yielded.execute("bad", ())
    assert (failing_conn.commits, failing_conn.rollbacks, failing_conn.closes) == (0, 1, 1)


def test_query_helpers(monkeypatch):
    cursor = FakeCursor(rows=[{"id": 1}], row={"id": 2}, lastrowid=9)

    @contextmanager
    def fake_cursor(commit=False):
        assert isinstance(commit, bool)
        yield cursor

    monkeypatch.setattr(db, "db_cursor", fake_cursor)
    assert db.query("SELECT rows") == [{"id": 1}]
    assert db.query_one("SELECT row", [2]) == {"id": 2}
    assert db.execute("INSERT row", (1,)) == 9
    assert cursor.executed == [
        ("SELECT rows", ()),
        ("SELECT row", [2]),
        ("INSERT row", (1,)),
    ]

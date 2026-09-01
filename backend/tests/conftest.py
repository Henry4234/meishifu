import sys
from pathlib import Path

import jwt
import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config  # noqa: E402
import db  # noqa: E402
from app import create_app  # noqa: E402

config.SECRET_KEY = "meishifu-ci-test-secret-key-at-least-32-bytes"


def _no_real_db(*_args, **_kwargs):
    """測試環境沒有 MySQL:任何漏 mock 的 DB 存取都要立刻失敗並指出原因,
    而不是去連真的資料庫 (在 CI 會變成 Connection refused)。"""
    raise AssertionError(
        "測試嘗試連線真的資料庫,請用 monkeypatch 把 db.query / db.query_one / "
        "db.execute / db.get_connection 換掉"
    )


@pytest.fixture(autouse=True)
def reset_db_pool(monkeypatch):
    db._pool = None
    monkeypatch.setattr(db.pymysql, "connect", _no_real_db)
    monkeypatch.setattr(db, "PooledDB", _no_real_db)
    yield
    db._pool = None


@pytest.fixture()
def app():
    flask_app = create_app()
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_headers():
    def make(role="super", sub="1"):
        token = jwt.encode(
            {
                "sub": str(sub),
                "username": "tester",
                "display_name": "Tester",
                "role": role,
            },
            config.SECRET_KEY,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    return make

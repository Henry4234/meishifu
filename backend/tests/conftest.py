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


@pytest.fixture(autouse=True)
def reset_db_pool():
    db._pool = None
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

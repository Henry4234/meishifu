import os
import sys
from pathlib import Path

import jwt
import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ---------------------------------------------------------------------------
# 測試環境的固定設定。
#
# config.py 在 import 時就會 load_dotenv() 讀專案根目錄的 .env,開發機上那是
# 正式金鑰與 ECPAY_ENV=production;CI 則根本沒有 .env (檔案在 .gitignore 內)。
# 兩邊跑出不同結果,測試就失去意義。
#
# load_dotenv() 的預設是 override=False —— 已存在的環境變數不會被 .env 覆寫。
# 因此只要在 import config 之前把值放進 os.environ,兩邊就都會拿到這裡的值,
# 而且 config 裡由這些值推導出來的常數 (ECPAY_AIO_URL、PAY_*_URL、
# PUBLIC_ORIGIN…) 也會跟著正確,不必逐一補寫。
#
# 這些不是「設定」而是「測試的預期值」:CheckMacValue 的測資就是綠界文件公開
# 的測試商店參數,寫死在這裡才能讓斷言有意義。所以不從 .env 讀取。
# ---------------------------------------------------------------------------
TEST_ENV = {
    "SECRET_KEY": "meishifu-ci-test-secret-key-at-least-32-bytes",
    # 綠界文件公開的測試商店 (金流)
    "ECPAY_ENV": "stage",
    "ECPAY_MERCHANT_ID": "2000132",
    "ECPAY_HASH_KEY": "5294y06JbISpM5x9",
    "ECPAY_HASH_IV": "v77hoKGq4kWxNNIS",
    # 綠界 C2C 測試特店 (物流)
    "ECPAY_LOGISTICS_ENV": "stage",
    "ECPAY_LOGISTICS_MERCHANT_ID": "2000933",
    # 回呼與導轉網址由這兩個推導出來
    "BACKEND_BASE_URL": "http://localhost:5001",
    "FRONTEND_BASE_URL": "http://localhost:5500/frontend",
    # 空字串 = 不寄信,避免測試打到開發機 .env 裡設定的真實 SMTP
    "SMTP_HOST": "",
}
for _key, _value in TEST_ENV.items():
    os.environ[_key] = _value

import config  # noqa: E402
import db  # noqa: E402
from app import create_app  # noqa: E402


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

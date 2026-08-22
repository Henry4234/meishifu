from datetime import datetime

import db
from routes import payment, shop


def test_categories_and_packages(client, monkeypatch):
    def fake_query(sql, _args=None):
        if "DISTINCT category FROM package WHERE" in sql:
            return [{"category": "蛋黃酥系列"}, {"category": "季節限定"}]
        if "DISTINCT c.category" in sql:
            return [{"category": "綜合系列"}]
        if "FROM package WHERE is_active" in sql:
            return [
                {
                    "id": 1,
                    "name": "禮盒",
                    "description": "desc",
                    "spec": "6入",
                    "category": "蛋黃酥系列",
                    "price": 600,
                    "image": "x.jpg",
                    "tag": "new",
                    "sort_order": 0,
                }
            ]
        if "FROM package_categories" in sql:
            return [{"package_id": 1, "category": "綜合系列"}]
        if "FROM package_products_map" in sql:
            return [{"package_id": 1, "name": "蛋黃酥", "unit": "顆", "quantity": 6}]
        raise AssertionError(sql)

    monkeypatch.setattr(db, "query", fake_query)
    categories = client.get("/api/categories").get_json()["categories"]
    assert categories == ["蛋黃酥系列", "綜合系列", "季節限定"]

    packages = client.get("/api/packages?category=綜合系列").get_json()["packages"]
    assert packages[0]["items"][0]["quantity"] == 6.0
    assert packages[0]["categories"] == ["蛋黃酥系列", "綜合系列"]

    assert client.get("/api/packages?category=鳳凰酥系列").get_json() == {"packages": []}


def test_package_lookup_variants(client, monkeypatch):
    assert client.get("/api/package?product_id=bad").status_code == 400

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.get("/api/package?product_id=99").status_code == 404
    assert client.get("/api/packages/99").status_code == 404

    monkeypatch.setattr(
        db,
        "query_one",
        lambda *_args, **_kwargs: {
            "id": 1,
            "name": "禮盒",
            "description": "",
            "spec": "",
            "category": "蛋黃酥系列",
            "price": 500,
            "image": "",
            "tag": "",
            "sort_order": 0,
        },
    )

    def fake_query(sql, _args=None):
        if "package_products_map" in sql:
            return [{"name": "蛋黃酥", "unit": "顆", "quantity": 2}]
        return [{"package_id": 1, "category": "綜合系列"}]

    monkeypatch.setattr(db, "query", fake_query)
    result = client.get("/api/packages/1")
    assert result.status_code == 200
    assert result.get_json()["items"][0]["quantity"] == 2.0


def test_create_order_validations(client, monkeypatch):
    assert client.post("/api/orders", json={}).status_code == 400
    assert client.post(
        "/api/orders",
        json={"customer": {"name": "A", "phone": "1"}, "items": []},
    ).status_code == 400
    assert client.post(
        "/api/orders",
        json={
            "customer": {"name": "A", "phone": "1"},
            "shipping_method": "pickup",
            "payment_method": "cash",
            "items": [{"package_id": 1, "quantity": 1}],
        },
    ).status_code == 400

    base = {
        "customer": {"name": "A", "phone": "1"},
        "shipping_method": "pickup",
        "items": [{"package_id": 1, "quantity": 0}],
    }
    assert client.post("/api/orders", json=base).status_code == 400
    base["items"] = [{"package_id": "bad", "quantity": 1}]
    assert client.post("/api/orders", json=base).status_code == 400
    base["items"] = [{"package_id": 1, "quantity": 1}]
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.post("/api/orders", json=base).status_code == 400


class OrderCursor:
    lastrowid = 42

    def __init__(self):
        self.executed = []
        self.many = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, args):
        self.executed.append((sql, args))

    def executemany(self, sql, rows):
        self.many.append((sql, rows))


class OrderConnection:
    def __init__(self):
        self.cur = OrderCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_create_and_fetch_order(client, monkeypatch):
    conn = OrderConnection()
    monkeypatch.setattr(
        db,
        "query_one",
        lambda *_args, **_kwargs: {"id": 3, "name": "禮盒", "price": 500},
    )
    monkeypatch.setattr(db, "get_connection", lambda: conn)
    monkeypatch.setattr(shop, "_gen_order_no", lambda: "MS-TEST")

    response = client.post(
        "/api/orders",
        json={
            "customer": {"name": "王小明", "phone": "0912", "address": "台北"},
            "shipping_method": "delivery",
            "payment_method": "credit",
            "items": [{"package_id": 3, "quantity": 2}],
        },
    )
    body = response.get_json()
    assert response.status_code == 201
    assert body["order_no"] == "MS-TEST"
    assert body["subtotal"] == 1000
    assert body["shipping_fee"] == 120
    assert body["payment"]["mock"] is True
    assert conn.committed and conn.closed
    assert conn.cur.many

    order = {
        "order_no": "MS-TEST",
        "customer_name": "王小明",
        "shipping_method": "delivery",
        "payment_method": "credit",
        "payment_status": "unpaid",
        "status": "pending",
        "subtotal": 1000,
        "shipping_fee": 120,
        "total": 1120,
        "created_at": datetime(2026, 8, 22, 10, 30),
    }
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: order.copy())
    monkeypatch.setattr(db, "query", lambda *_args, **_kwargs: [{"package_name": "禮盒"}])
    fetched = client.get("/api/orders/MS-TEST?phone=0912").get_json()
    assert fetched["created_at"] == "2026-08-22 10:30"
    assert fetched["items"][0]["package_name"] == "禮盒"

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.get("/api/orders/none?phone=0").status_code == 404


def test_payment_endpoints(client, monkeypatch):
    payload = payment.build_payment_payload("MS1", 900, "credit")
    assert payload["order_no"] == "MS1"
    assert payload["mock"] is True

    calls = []
    monkeypatch.setattr(db, "execute", lambda sql, args=None: calls.append((sql, args)) or 1)
    ok = client.post("/api/payment/notify", data={"MerchantTradeNo": "MS1", "RtnCode": "1"})
    assert ok.get_data(as_text=True) == "1|OK"
    assert calls
    assert client.post("/api/payment/notify", json={"status": "failed"}).get_data(as_text=True) == "0|FAIL"

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.post("/api/payment/mock-pay", json={"order_no": "missing"}).status_code == 404
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"payment_status": "paid", "status": "paid"})
    paid = client.post("/api/payment/mock-pay", json={"order_no": "MS1"})
    assert paid.get_json()["status"] == "paid"

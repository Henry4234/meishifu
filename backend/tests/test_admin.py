from datetime import date, datetime

from werkzeug.security import generate_password_hash

import db


def test_login_variants(client, monkeypatch):
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.post("/api/admin/login", json={"username": "bad", "password": "x"}).status_code == 401

    inactive = {
        "id": 1,
        "username": "admin",
        "display_name": "Admin",
        "password_hash": generate_password_hash("password123"),
        "role": "super",
        "is_active": 0,
    }
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: inactive)
    assert client.post("/api/admin/login", json={"username": "admin", "password": "password123"}).status_code == 403

    active = {**inactive, "is_active": 1}
    executed = []
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: active)
    monkeypatch.setattr(db, "execute", lambda *args, **_kwargs: executed.append(args))
    response = client.post("/api/admin/login", json={"username": "admin", "password": "password123"})
    assert response.status_code == 200
    assert response.get_json()["role_label"] == "超級管理員"
    assert executed


def test_authentication_guards(client, auth_headers):
    assert client.get("/api/admin/dashboard").status_code == 401
    assert client.get("/api/admin/dashboard", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.get("/api/admin/users", headers=auth_headers("staff")).status_code == 403


def test_dashboard(client, monkeypatch, auth_headers):
    def fake_query_one(sql, _args=None):
        if "SUM(total)" in sql:
            return {"v": 2500}
        if "status = 'pending'" in sql:
            return {"v": 2}
        if "COUNT(*)" in sql:
            return {"v": 4}
        if "package_name AS product_name" in sql:
            return {"product_name": "禮盒", "qty": 8, "image": "x.jpg"}
        raise AssertionError(sql)

    def fake_query(sql, _args=None):
        if "GROUP BY DATE" in sql:
            return [{"d": date.today(), "v": 1000}]
        if "LIMIT 5" in sql:
            return [{
                "order_no": "MS1",
                "customer_name": "王小明",
                "total": 500,
                "status": "pending",
                "created_at": datetime(2026, 8, 22, 9, 0),
            }]
        raise AssertionError(sql)

    monkeypatch.setattr(db, "query_one", fake_query_one)
    monkeypatch.setattr(db, "query", fake_query)
    body = client.get("/api/admin/dashboard", headers=auth_headers()).get_json()
    assert body["revenue_today"] == 2500
    assert body["recent_orders"][0]["status_label"] == "待處理"
    assert len(body["trend"]) == 7


def test_order_management(client, monkeypatch, auth_headers):
    monkeypatch.setattr(db, "query_one", lambda sql, _args=None: {"c": 1} if "COUNT" in sql else {"id": 7})
    monkeypatch.setattr(
        db,
        "query",
        lambda *_args, **_kwargs: [{
            "id": 7,
            "order_no": "MS1",
            "customer_name": "王",
            "phone": "0912",
            "total": 500,
            "status": "pending",
            "payment_status": "unpaid",
            "payment_method": "credit",
            "shipping_method": "pickup",
            "created_at": datetime(2026, 8, 22, 9, 0),
        }],
    )
    listing = client.get(
        "/api/admin/orders?status=pending&q=MS&page=1&per_page=5",
        headers=auth_headers(),
    )
    assert listing.get_json()["orders"][0]["status_label"] == "待處理"

    order = {
        "id": 7,
        "status": "paid",
        "created_at": datetime(2026, 8, 22, 9, 0),
    }
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: order.copy())
    detail = client.get("/api/admin/orders/7", headers=auth_headers())
    assert detail.get_json()["status_label"] == "已付款"

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.get("/api/admin/orders/99", headers=auth_headers()).status_code == 404
    assert client.patch("/api/admin/orders/7/status", json={"status": "bad"}, headers=auth_headers()).status_code == 400

    executed = []
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"id": 7})
    monkeypatch.setattr(db, "execute", lambda *args, **_kwargs: executed.append(args))
    updated = client.patch("/api/admin/orders/7/status", json={"status": "paid"}, headers=auth_headers())
    assert updated.status_code == 200
    assert len(executed) == 2

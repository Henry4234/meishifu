from contextlib import contextmanager
from datetime import date, datetime, timedelta

import db
from routes import manage


def test_cost_and_demand_helpers(monkeypatch):
    def fake_query(sql, _args=None):
        if "GROUP BY pm.product_id" in sql:
            return [{"product_id": 1, "cost": 12.5}]
        if "SELECT package_id, product_id" in sql:
            return [{"package_id": 10, "product_id": 1, "quantity": 2}]
        if "packaging_qty" in sql and "JOIN materials" in sql:
            return [{"id": 10, "packaging_qty": 1, "unit_cost": 5}]
        if "SUM(oi.quantity * ppm.quantity" in sql:
            return [{"material_id": 4, "d": 3}]
        if "packaging_material_id AS mid" in sql:
            return [{"mid": 4, "d": 2}, {"mid": 5, "d": 1}]
        raise AssertionError(sql)

    monkeypatch.setattr(db, "query", fake_query)
    assert manage.product_costs() == {1: 12.5}
    assert manage.package_costs() == {10: 30.0}
    assert manage._material_demand() == {4: 5.0, 5: 1.0}
    assert manage._material_status(1, 10, 0) == "shortage"
    assert manage._material_status(7, 10, 0) == "low"
    assert manage._material_status(12, 10, 0) == "ok"


def test_material_endpoints(client, monkeypatch, auth_headers):
    headers = auth_headers()

    def list_query(sql, _args=None):
        if "SUM(oi.quantity * ppm.quantity" in sql:
            return [{"material_id": 1, "d": 8}]
        if "packaging_material_id AS mid" in sql:
            return []
        if "FROM materials WHERE is_active" in sql:
            return [{
                "id": 1,
                "name": "奶油",
                "category": "乳品",
                "batch_no": "B1",
                "unit": "kg",
                "stock": 5,
                "safety_stock": 10,
                "unit_cost": 300,
                "expiry_date": date.today() + timedelta(days=10),
                "created_at": date(2026, 1, 1),
            }]
        raise AssertionError(sql)

    monkeypatch.setattr(db, "query", list_query)
    listed = client.get("/api/admin/materials", headers=headers).get_json()
    assert listed["materials"][0]["status"] == "shortage"
    assert listed["stats"] == {"total": 1, "shortage": 1, "expiring": 1}

    assert client.post("/api/admin/materials", json={}, headers=headers).status_code == 400
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: 11)
    assert client.post("/api/admin/materials", json={"name": "麵粉"}, headers=headers).status_code == 201

    assert client.patch("/api/admin/materials/1", json={}, headers=headers).status_code == 400
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.patch("/api/admin/materials/1", json={"name": "X"}, headers=headers).status_code == 404
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"id": 1})
    executed = []
    monkeypatch.setattr(db, "execute", lambda *args, **_kwargs: executed.append(args) or 1)
    assert client.patch("/api/admin/materials/1", json={"stock": 20}, headers=headers).status_code == 200
    assert len(executed) == 2

    assert client.post("/api/admin/materials/1/purchase", json={}, headers=headers).status_code == 400
    assert client.post("/api/admin/materials/1/purchase", json={"quantity": 0}, headers=headers).status_code == 400
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.post("/api/admin/materials/1/purchase", json={"quantity": 2}, headers=headers).status_code == 404

    query_results = iter([{"id": 1, "unit_cost": 100}, {"stock": 7, "unit_cost": 120}])
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: next(query_results))
    bought = client.post(
        "/api/admin/materials/1/purchase",
        json={"quantity": 2, "unit_cost": 120, "note": "補貨"},
        headers=headers,
    )
    assert bought.get_json() == {"id": 1, "stock": 7.0, "unit_cost": 120.0}

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.delete("/api/admin/materials/1", headers=headers).status_code == 404
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"id": 1, "name": "奶油"})
    monkeypatch.setattr(db, "query", lambda sql, _args=None: [{"name": "蛋黃酥"}] if "product_materials" in sql else [])
    used = client.delete("/api/admin/materials/1", headers=headers)
    assert used.status_code == 400
    assert used.get_json()["used_by_products"] == ["蛋黃酥"]

    monkeypatch.setattr(db, "query", lambda *_args, **_kwargs: [])
    assert client.delete("/api/admin/materials/1", headers=headers).get_json()["deleted"] is True


class ManyCursor:
    def __init__(self):
        self.calls = []

    def executemany(self, sql, rows):
        self.calls.append((sql, list(rows)))


class DeleteCursor:
    """記錄 db_cursor 內執行的 DELETE,確認刪除範圍正確。"""

    def __init__(self):
        self.calls = []

    def execute(self, sql, args=None):
        self.calls.append((sql, args))


def _cursor_factory(cursor):
    """產生可取代 db.db_cursor 的 context manager。"""
    @contextmanager
    def factory(commit=False):
        assert commit is True
        yield cursor

    return factory


def _order_row(payment_status="unpaid", status="pending"):
    return {"id": 7, "order_no": "MS-7", "customer_name": "王小明",
            "payment_status": payment_status, "status": status}


class ManualOrderCursor:
    lastrowid = 55

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


class ManualOrderConnection:
    def __init__(self):
        self.cur = ManualOrderCursor()
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


PACKAGES = {1: {"id": 1, "name": "蛋黃酥禮盒", "price": 650},
            2: {"id": 2, "name": "堅果塔禮盒", "price": 480}}


def _manual_payload(**overrides):
    payload = {
        "customer_name": "市集現場",
        "items": [{"package_id": 1, "quantity": 2}],
    }
    payload.update(overrides)
    return payload


def test_create_manual_order(client, monkeypatch, auth_headers):
    """後台手動建單:寫入 source=manual,電話/Email 可留空,不碰綠界。"""
    headers = auth_headers()
    conn = ManualOrderConnection()
    monkeypatch.setattr(db, "get_connection", lambda: conn)
    monkeypatch.setattr(
        db, "query_one",
        lambda _sql, args=None: PACKAGES.get(args[0]) if args else None)
    monkeypatch.setattr(manage, "_gen_manual_order_no", lambda: "MO-TEST")

    r = client.post("/api/admin/orders", headers=headers, json=_manual_payload(
        items=[{"package_id": 1, "quantity": 2}, {"package_id": 2, "quantity": 1}],
        shipping_fee=50, paid=True, note="中秋市集"))
    body = r.get_json()
    assert r.status_code == 201
    assert body["order_no"] == "MO-TEST"
    assert body["source"] == "manual"
    assert body["subtotal"] == 650 * 2 + 480          # 單價取自資料庫
    assert body["total"] == 1780 + 50
    assert body["payment_status"] == "paid"
    assert conn.committed and conn.closed

    sql, args = conn.cur.executed[0]
    assert "'manual'" in sql
    assert args[0] == "MO-TEST"
    assert args[2] == "" and args[3] == ""            # 電話與 Email 留空存空字串
    assert conn.cur.many[0][1] == [
        (55, 1, "蛋黃酥禮盒", 650, 2, 1300), (55, 2, "堅果塔禮盒", 480, 1, 480)]

    # 未收款時 paid_at 留空
    conn2 = ManualOrderConnection()
    monkeypatch.setattr(db, "get_connection", lambda: conn2)
    unpaid = client.post("/api/admin/orders", headers=headers,
                         json=_manual_payload(paid=False))
    assert unpaid.get_json()["payment_status"] == "unpaid"
    assert conn2.cur.executed[0][1][-1] is None

    # 單價可覆寫 (批發價)
    conn3 = ManualOrderConnection()
    monkeypatch.setattr(db, "get_connection", lambda: conn3)
    wholesale = client.post("/api/admin/orders", headers=headers, json=_manual_payload(
        items=[{"package_id": 1, "quantity": 10, "unit_price": 500}]))
    assert wholesale.get_json()["subtotal"] == 5000


def test_create_manual_order_validation(client, monkeypatch, auth_headers):
    headers = auth_headers()
    monkeypatch.setattr(
        db, "query_one",
        lambda _sql, args=None: PACKAGES.get(args[0]) if args else None)

    def err(payload):
        r = client.post("/api/admin/orders", headers=headers, json=payload)
        assert r.status_code == 400
        return r.get_json()["error"]

    assert "姓名" in err(_manual_payload(customer_name="  "))
    assert "Email" in err(_manual_payload(email="not-an-email"))
    assert "配送方式" in err(_manual_payload(shipping_method="fami"))
    assert "付款方式" in err(_manual_payload(payment_method="atm"))
    assert "狀態" in err(_manual_payload(status="cancelled"))
    assert "地址" in err(_manual_payload(shipping_method="delivery"))
    assert "至少" in err(_manual_payload(items=[]))
    assert "數量" in err(_manual_payload(items=[{"package_id": 1, "quantity": 0}]))
    assert "重複" in err(_manual_payload(
        items=[{"package_id": 1, "quantity": 1}, {"package_id": 1, "quantity": 2}]))
    assert "格式" in err(_manual_payload(items=[{"package_id": "x", "quantity": 1}]))
    assert "單價" in err(_manual_payload(
        items=[{"package_id": 1, "quantity": 1, "unit_price": -5}]))
    assert "運費" in err(_manual_payload(shipping_fee=-1))
    assert "不存在" in err(_manual_payload(items=[{"package_id": 99, "quantity": 1}]))

    # 僅訂單管理員 / 超級管理員可建單
    assert client.post("/api/admin/orders", headers=auth_headers(role="staff"),
                       json=_manual_payload()).status_code == 403
    assert client.post("/api/admin/orders", json=_manual_payload()).status_code == 401


def test_delete_order_conditions(client, monkeypatch, auth_headers):
    headers = auth_headers()

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.delete("/api/admin/orders/7", headers=headers).status_code == 404

    # 已付款且已離開待處理 → 不得刪除
    monkeypatch.setattr(db, "query_one",
                        lambda *_a, **_k: _order_row("paid", "shipped"))
    blocked = client.delete("/api/admin/orders/7", headers=headers)
    assert blocked.status_code == 400
    assert blocked.get_json()["status"] == "shipped"

    cursor = DeleteCursor()
    monkeypatch.setattr(db, "db_cursor", _cursor_factory(cursor))

    # 未付款 (即使已出貨) → 可刪除,明細與主檔都要刪掉
    monkeypatch.setattr(db, "query_one", lambda *_a, **_k: _order_row("unpaid", "shipped"))
    unpaid = client.delete("/api/admin/orders/7", headers=headers)
    assert unpaid.get_json() == {
        "id": 7, "order_no": "MS-7", "customer_name": "王小明", "deleted": True}

    # 已付款但仍是待處理 → 也放行
    monkeypatch.setattr(db, "query_one", lambda *_a, **_k: _order_row("paid", "pending"))
    assert client.delete("/api/admin/orders/7", headers=headers).status_code == 200

    assert [sql.split()[2] for sql, _args in cursor.calls] == [
        "order_items", "orders", "order_items", "orders"]


def test_delete_package_conditions(client, monkeypatch, auth_headers):
    headers = auth_headers()

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.delete("/api/admin/packages/5", headers=headers).status_code == 404

    # 仍在上架中 → 擋下
    monkeypatch.setattr(db, "query_one",
                        lambda *_a, **_k: {"id": 5, "name": "珍味禮盒", "is_active": 1})
    active = client.delete("/api/admin/packages/5", headers=headers)
    assert active.status_code == 400
    assert "先下架" in active.get_json()["error"]

    # 已下架但還在未完成訂單裡 → 擋下並列出訂單編號
    monkeypatch.setattr(db, "query_one",
                        lambda *_a, **_k: {"id": 5, "name": "珍味禮盒", "is_active": 0})
    monkeypatch.setattr(db, "query", lambda *_a, **_k: [
        {"order_no": "MS-1", "status": "paid"}, {"order_no": "MS-2", "status": "shipped"}])
    used = client.delete("/api/admin/packages/5", headers=headers)
    assert used.status_code == 400
    assert used.get_json()["blocking_orders"] == ["MS-1", "MS-2"]

    # 已下架且沒有未完成訂單 → 刪除禮盒與兩張關聯表
    monkeypatch.setattr(db, "query", lambda *_a, **_k: [])
    cursor = DeleteCursor()
    monkeypatch.setattr(db, "db_cursor", _cursor_factory(cursor))
    done = client.delete("/api/admin/packages/5", headers=headers)
    assert done.get_json() == {"id": 5, "name": "珍味禮盒", "deleted": True}
    assert [sql.split()[2] for sql, _args in cursor.calls] == [
        "package_products_map", "package_categories", "package"]


def test_replace_helpers(monkeypatch):
    executed = []
    cursor = ManyCursor()
    monkeypatch.setattr(db, "execute", lambda *args, **_kwargs: executed.append(args))

    @contextmanager
    def fake_db_cursor(commit=False):
        assert commit is True
        yield cursor

    monkeypatch.setattr(db, "db_cursor", fake_db_cursor)

    manage._replace_bom(1, [{"material_id": "2", "quantity": "1.5"}, {"bad": 1}])
    manage._replace_package_items(3, '[{"product_id": 4, "quantity": 2}]')
    manage._replace_package_categories(3, '["綜合系列", "主分類", "綜合系列"]', "主分類")
    assert len(executed) == 3
    assert len(cursor.calls) == 3

    before = (len(executed), len(cursor.calls))
    manage._replace_bom(1, None)
    manage._replace_package_items(1, "not-json")
    manage._replace_package_categories(1, "not-json", "主")
    assert (len(executed), len(cursor.calls)) == before


def test_product_endpoints(client, monkeypatch, auth_headers):
    headers = auth_headers()

    def list_query(sql, _args=None):
        if "GROUP BY pm.product_id" in sql:
            return [{"product_id": 1, "cost": 30}]
        if "FROM products ORDER" in sql:
            return [{"id": 1, "name": "蛋黃酥", "description": "", "category": "糕餅", "unit": "顆", "is_active": 1}]
        if "FROM product_materials" in sql:
            return [{"product_id": 1, "material_id": 2, "quantity": 0.1, "name": "奶油", "unit": "kg", "unit_cost": 300}]
        raise AssertionError(sql)

    monkeypatch.setattr(db, "query", list_query)
    product = client.get("/api/admin/products", headers=headers).get_json()["products"][0]
    assert product["cost"] == 30
    assert product["materials"][0]["cost"] == 30

    assert client.post("/api/admin/products", json={}, headers=headers).status_code == 400
    replaced = []
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: 8)
    monkeypatch.setattr(manage, "_replace_bom", lambda *args: replaced.append(args))
    created = client.post("/api/admin/products", json={"name": "鳳凰酥", "materials": []}, headers=headers)
    assert created.get_json()["id"] == 8
    assert replaced

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.patch("/api/admin/products/1", json={"name": "X"}, headers=headers).status_code == 404
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"id": 1})
    assert client.patch(
        "/api/admin/products/1",
        json={"name": "新名稱", "is_active": False, "materials": []},
        headers=headers,
    ).status_code == 200

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"c": 2})
    assert client.delete("/api/admin/products/1", headers=headers).status_code == 400
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"c": 0})
    assert client.delete("/api/admin/products/1", headers=headers).get_json()["deleted"] is True


def test_package_endpoints(client, monkeypatch, auth_headers):
    headers = auth_headers()
    monkeypatch.setattr(manage, "package_costs", lambda: {1: 400})

    def package_query(sql, _args=None):
        if "FROM package ORDER" in sql:
            return [{
                "id": 1,
                "name": "禮盒",
                "description": "",
                "spec": "6入",
                "category": "蛋黃酥系列",
                "price": 600,
                "image": "",
                "tag": "",
                "packaging_material_id": None,
                "packaging_qty": 1,
                "sort_order": 0,
                "is_active": 1,
                "created_at": date(2026, 1, 1),
            }]
        if "package_products_map" in sql:
            return [{"package_id": 1, "product_id": 2, "quantity": 6, "name": "蛋黃酥", "unit": "顆"}]
        if "SELECT package_id, category" in sql:
            return [{"package_id": 1, "category": "綜合系列"}]
        if "SELECT DISTINCT category FROM package_categories" in sql:
            return [{"category": "季節限定"}]
        if "SELECT DISTINCT category FROM package" in sql:
            return [{"category": "蛋黃酥系列"}]
        raise AssertionError(sql)

    monkeypatch.setattr(db, "query", package_query)
    result = client.get("/api/admin/packages", headers=headers).get_json()
    assert result["packages"][0]["profit"] == 200
    assert "季節限定" in result["all_categories"]

    assert client.post("/api/admin/packages", data={"name": "X", "price": "bad"}, headers=headers).status_code == 400
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: 9)
    monkeypatch.setattr(manage, "_replace_package_items", lambda *_args: None)
    monkeypatch.setattr(manage, "_replace_package_categories", lambda *_args: None)
    created = client.post(
        "/api/admin/packages",
        data={"name": "新禮盒", "price": "700", "category": "綜合系列"},
        headers=headers,
    )
    assert created.status_code == 201

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.post("/api/admin/packages/1/update", data={}, headers=headers).status_code == 404
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"id": 1, "category": "蛋黃酥系列"})
    assert client.post("/api/admin/packages/1/update", data={"sort_order": "bad"}, headers=headers).status_code == 400
    updated = client.post(
        "/api/admin/packages/1/update",
        data={"name": "改名", "price": "800", "sort_order": "2", "is_active": "true"},
        headers=headers,
    )
    assert updated.get_json()["updated"] is True

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.patch("/api/admin/packages/1/active", json={"is_active": True}, headers=headers).status_code == 404
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"id": 1})
    assert client.patch("/api/admin/packages/1/active", json={"is_active": False}, headers=headers).get_json()["is_active"] == 0


def test_user_and_finance_endpoints(client, monkeypatch, auth_headers):
    super_headers = auth_headers("super", sub="1")
    user_rows = [{
        "id": 1,
        "username": "admin",
        "display_name": "Admin",
        "email": "admin@example.com",
        "role": "super",
        "is_active": 1,
        "last_login": datetime(2026, 8, 22, 9, 0),
        "created_at": date(2026, 1, 1),
    }]
    monkeypatch.setattr(db, "query", lambda *_args, **_kwargs: [r.copy() for r in user_rows])
    listed = client.get("/api/admin/users", headers=super_headers).get_json()
    assert listed["stats"]["active"] == 1

    assert client.post("/api/admin/users", json={}, headers=super_headers).status_code == 400
    assert client.post(
        "/api/admin/users",
        json={"username": "x", "password": "password123", "role": "bad"},
        headers=super_headers,
    ).status_code == 400
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"id": 1})
    assert client.post(
        "/api/admin/users",
        json={"username": "admin", "password": "password123", "role": "super"},
        headers=super_headers,
    ).status_code == 400
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: 3)
    created = client.post(
        "/api/admin/users",
        json={"username": "staff", "password": "password123", "role": "staff"},
        headers=super_headers,
    )
    assert created.status_code == 201

    assert client.patch("/api/admin/users/9", json={"email": "x"}, headers=super_headers).status_code == 404
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {"id": 1, "username": "admin"})
    assert client.patch("/api/admin/users/1", json={"is_active": False}, headers=super_headers).status_code == 400
    assert client.patch("/api/admin/users/1", json={"role": "bad"}, headers=super_headers).status_code == 400
    assert client.patch("/api/admin/users/1", json={}, headers=super_headers).status_code == 400
    changed = client.patch(
        "/api/admin/users/1",
        json={"display_name": "新管理員", "email": "new@example.com", "password": "newpassword"},
        headers=super_headers,
    )
    assert changed.status_code == 200

    monkeypatch.setattr(manage, "package_costs", lambda: {1: 100})

    def finance_query_one(sql, _args=None):
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 2, "rev": 1000}
        if "SUM(total)" in sql:
            return {"v": 2000}
        if "COUNT(*) AS v" in sql:
            return {"v": 4}
        raise AssertionError(sql)

    monkeypatch.setattr(db, "query_one", finance_query_one)
    monkeypatch.setattr(db, "query", lambda *_args, **_kwargs: [{"package_id": 1, "qty": 2}])
    finance = client.get("/api/admin/finance?period=quarter", headers=auth_headers("finance")).get_json()
    assert finance["revenue"] == 2000
    assert finance["cost"] == 200
    assert len(finance["weeks"]) == 4
